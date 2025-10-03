from crawler import (
    _lookup_json_path,
    _render_api_template,
    _load_previous_api_urls,
    build_json_api_snapshot,
    parse_snapshot,
)


def test_lookup_json_path_supports_nested_and_index_access():
    data = {
        "data": {
            "items": [
                {"id": 1, "info": {"title": "First"}},
                {"id": 2, "info": {"title": "Second"}},
            ]
        }
    }

    assert _lookup_json_path(data, "data.items[0].info.title") == "First"
    assert _lookup_json_path(data, "data.items[1].id") == 2
    assert _lookup_json_path(data, "") == data
    assert _lookup_json_path(data, "data.missing") is None


def test_render_api_template_replaces_placeholders_with_path_values():
    item = {"id": 9, "meta": {"slug": "abc"}}
    template = "https://example.com/{meta.slug}?id={id}&from={base_url}"
    rendered = _render_api_template(template, item, "https://api.example.com/list")
    assert rendered == "https://example.com/abc?id=9&from=https://api.example.com/list"


def test_build_json_api_snapshot_integrates_with_parse_snapshot():
    snapshot = build_json_api_snapshot(
        api_raw="{\"items\": []}",
        items=[
            {
                "url": "https://example.com/detail/1",
                "title": "Detail Title",
                "raw": {"id": 1, "title": "Detail Title"},
            }
        ],
        detail_snapshots=[
            {
                "url": "https://example.com/detail/1",
                "html": "<p>content</p>",
                "title": "Detail Title",
                "text": "content",
            }
        ],
    )

    main_html, entries, main_title, main_text = parse_snapshot(snapshot)

    assert "items" in (main_html or "")
    assert entries and entries[0]["url"] == "https://example.com/detail/1"
    assert entries[0]["html"] == "<p>content</p>"
    assert main_title is None
    assert main_text is None


def test_load_previous_api_urls_extracts_urls_from_snapshot():
    snapshot = build_json_api_snapshot(
        api_raw="{}",
        items=[
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.com/b", "title": "B"},
        ],
    )

    urls = _load_previous_api_urls(snapshot)
    assert urls == {"https://example.com/a", "https://example.com/b"}
