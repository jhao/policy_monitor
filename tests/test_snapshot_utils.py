from __future__ import annotations

import json

from crawler import build_snapshot, parse_snapshot, summarize_html


def test_summarize_html_prefers_heading_over_title() -> None:
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
    assert title == "Rendered Heading"
    assert "第一段内容" in summary


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

    parsed_main_html, entries, main_title = parse_snapshot(snapshot)

    assert parsed_main_html == main_html
    assert main_title == "主标题"
    assert entries[0]["url"] == "https://example.com/sub"
    assert entries[0]["title"] == "子标题"


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

    parsed_main_html, entries, main_title = parse_snapshot(legacy_payload)

    assert parsed_main_html == main_html
    assert main_title == "旧标题"
    assert entries[0]["title"] == "旧子标题"
