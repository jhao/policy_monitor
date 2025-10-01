from __future__ import annotations

import json

from crawler import build_snapshot, parse_snapshot, summarize_html
from models import Website


def test_summarize_html_extracts_main_idea() -> None:
    html = """
    <html>
      <head><title>HTML Title</title></head>
      <body>
        <h1>Rendered Heading</h1>
        <p>第一段内容。</p>
      </body>
    </html>
    """
    title, summary = summarize_html(html)
    assert "Rendered Heading" in title
    assert "第一段内容" in title
    assert "第一段内容" in summary


def test_summarize_html_ignores_navigation_text() -> None:
    html = """
    <html>
      <body>
        <nav class="main-menu"><a href="#">菜单项</a></nav>
        <div id="content"><h1>政策更新</h1><p>这里是正文内容。</p></div>
        <footer>底部信息</footer>
      </body>
    </html>
    """
    title, summary = summarize_html(html)

    assert "菜单项" not in summary
    assert "底部信息" not in summary
    assert "政策更新" in title


def test_summarize_html_prefers_configured_selectors() -> None:
    html = """
    <html>
      <body>
        <div class="header"><h1>站点标题</h1></div>
        <article>
          <h2 id="article-title">最新通知标题</h2>
          <div class="article-body">
            <p>第一段正文。</p>
            <p>第二段正文。</p>
          </div>
        </article>
      </body>
    </html>
    """
    website = Website(
        title_selector_config="id=article-title",
        content_selector_config="css=.article-body",
    )

    title, summary = summarize_html(html, website)

    assert title == "最新通知标题"
    assert "第一段正文" in summary
    assert "站点标题" not in title


def test_build_and_parse_snapshot_preserve_titles() -> None:
    main_html = "<html><body><h1>主标题</h1><p>内容A</p></body></html>"
    sub_html = "<html><body><h1>子标题</h1><p>内容B</p></body></html>"

    snapshot = build_snapshot(
        main_html,
        [
            {
                "url": "https://example.com/sub",
                "html": sub_html,
                "title": "子标题",
            }
        ],
        main_title="主标题",
    )

    parsed_main_html, entries, main_title, main_text = parse_snapshot(snapshot)

    assert parsed_main_html == main_html
    assert main_title == "主标题"
    assert "内容A" in (main_text or "")
    assert entries[0]["url"] == "https://example.com/sub"
    assert entries[0]["title"] == "子标题"
    assert "内容B" in (entries[0]["text"] or "")


def test_parse_snapshot_legacy_payloads_fill_missing_titles() -> None:
    main_html = "<html><body><h1>旧标题</h1><p>旧内容</p></body></html>"
    sub_html = "<html><body><h2>旧子标题</h2><p>子内容</p></body></html>"

    legacy_payload = json.dumps(
        {
            "main_html": main_html,
            "subpages": [
                {
                    "url": "https://legacy.example.com/sub",
                    "html": sub_html,
                }
            ],
        }
    )

    parsed_main_html, entries, main_title, main_text = parse_snapshot(legacy_payload)

    assert parsed_main_html == main_html
    assert main_title and main_title.startswith("旧标题")
    assert entries[0]["title"] and entries[0]["title"].startswith("旧子标题")
    assert "旧内容" in (main_text or "")
    assert "子内容" in (entries[0]["text"] or "")
