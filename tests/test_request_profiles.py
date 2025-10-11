from __future__ import annotations

from request_profiles import get_profile_headers


def test_request_profiles_normalize_bare_domain() -> None:
    headers = get_profile_headers("example.com/news/latest")

    referer = headers["Referer"]
    assert referer.startswith("http")
    assert "example.com" in referer


def test_request_profiles_skip_referer_for_relative_url() -> None:
    headers = get_profile_headers("/relative/path")

    assert "Referer" not in headers
