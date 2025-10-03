from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

__all__ = ["get_profile_headers", "RequestProfile"]


@dataclass(frozen=True)
class RequestProfile:
    """Lightweight representation of a rotating request header profile."""

    user_agent: str
    referer_template: str
    accept_language: str
    accept: str

    def build_headers(self, url: str) -> dict[str, str]:
        parts = urlsplit(url)
        mapping = {
            "scheme": parts.scheme,
            "netloc": parts.netloc,
            "hostname": parts.hostname or parts.netloc,
            "url": url,
            "path": parts.path or "/",
        }
        referer = self.referer_template.format(**mapping)
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Accept": self.accept,
        }
        if referer:
            headers["Referer"] = referer
        return headers


def _desktop_chrome(version: int, windows_version: str) -> str:
    return (
        "Mozilla/5.0 (Windows NT "
        f"{windows_version}; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version}.0.0.0 Safari/537.36"
    )


def _mac_chrome(version: int, os_version: str) -> str:
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X "
        f"{os_version}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version}.0.0.0 Safari/537.36"
    )


def _firefox(version: int, platform: str) -> str:
    return (
        f"Mozilla/5.0 ({platform}; rv:{version}.0) Gecko/20100101 Firefox/{version}.0"
    )


def _edge(version: int, windows_version: str) -> str:
    return (
        "Mozilla/5.0 (Windows NT "
        f"{windows_version}; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version}.0.0.0 Safari/537.36 Edg/{version}.0.0.0"
    )


def _safari(version: int, os_version: str) -> str:
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X "
        f"{os_version}) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        f"Version/{version}.0 Safari/605.1.15"
    )


def _ios_safari(version: int, device: str, os_version: str) -> str:
    return (
        "Mozilla/5.0 (" + device + f"; CPU iPhone OS {os_version} like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        f"Version/{version}.0 Mobile/15E148 Safari/604.1"
    )


def _android_chrome(version: int, android_version: str, device: str) -> str:
    return (
        "Mozilla/5.0 (Linux; Android "
        f"{android_version}; {device}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version}.0.0.0 Mobile Safari/537.36"
    )


_WINDOWS_VERSIONS = ["10.0", "11.0", "6.3", "6.1"]
_MAC_OS_VERSIONS = [
    "10_15_7",
    "11_6_8",
    "12_6_7",
    "13_5_2",
    "14_0",
]
_FIREFOX_PLATFORMS = [
    "Windows NT 10.0; Win64; x64",
    "Windows NT 6.1; Win64; x64",
    "Macintosh; Intel Mac OS X 10.15",
    "X11; Ubuntu; Linux x86_64",
]
_EDGE_WINDOWS_VERSIONS = ["10.0", "11.0"]
_IOS_DEVICES = [
    "iPhone",
    "iPhone; CPU iPhone OS",
    "iPad",
]
_IOS_OS_VERSIONS = ["14_7", "15_6", "16_5", "17_3"]
_ANDROID_VERSIONS = ["10", "11", "12", "13", "14"]
_ANDROID_DEVICES = [
    "Pixel 5",
    "Pixel 7",
    "Mi 11",
    "Mate 40",
    "SM-G998B",
    "OnePlus 9",
    "PCLM10",
]

_REFERER_TEMPLATES = [
    "{scheme}://{netloc}/",
    "{scheme}://{netloc}{path}",
    "https://www.google.com/search?q={netloc}",
    "https://www.google.com.hk/search?q={netloc}",
    "https://www.baidu.com/s?wd={netloc}",
    "https://cn.bing.com/search?q={netloc}",
    "https://search.yahoo.com/search?p={netloc}",
    "https://duckduckgo.com/?q={netloc}",
    "https://www.sogou.com/web?query={netloc}",
    "https://m.baidu.com/s?wd={netloc}",
    "https://www.sm.cn/s?q={netloc}",
    "https://m.sm.cn/s?q={netloc}",
    "https://so.com/s?q={netloc}",
    "https://www.ecosia.org/search?q={netloc}",
    "https://yandex.com/search/?text={netloc}",
    "https://www.zhihu.com/search?type=content&q={netloc}",
    "https://weixin.sogou.com/weixin?type=2&query={netloc}",
    "https://www.qwant.com/?q={netloc}",
    "https://www.bilibili.com/search?keyword={netloc}",
    "https://m.sogou.com/web/searchList.jsp?keyword={netloc}",
]

_ACCEPT_LANGUAGES = [
    "zh-CN,zh;q=0.9,en;q=0.7",
    "zh-CN,zh;q=0.8,en-US;q=0.6",
    "en-US,en;q=0.9,zh-CN;q=0.6",
    "zh-TW,zh;q=0.8,en;q=0.5",
    "en-GB,en;q=0.9,zh-CN;q=0.4",
    "ja-JP,ja;q=0.9,en-US;q=0.6",
    "ko-KR,ko;q=0.9,en-US;q=0.6",
    "de-DE,de;q=0.9,en-US;q=0.6",
    "fr-FR,fr;q=0.9,en;q=0.6",
    "es-ES,es;q=0.9,en;q=0.5",
]

_ACCEPT_HEADERS = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "text/html,application/json;q=0.9,*/*;q=0.8",
    "text/html,application/xhtml+xml;q=0.9,*/*;q=0.7",
    "text/html,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
]


def _iter_user_agents() -> Iterable[str]:
    for idx, version in enumerate(range(114, 144)):
        windows_version = _WINDOWS_VERSIONS[idx % len(_WINDOWS_VERSIONS)]
        yield _desktop_chrome(version, windows_version)
    for idx, version in enumerate(range(96, 116)):
        os_version = _MAC_OS_VERSIONS[idx % len(_MAC_OS_VERSIONS)]
        yield _mac_chrome(version, os_version)
    for idx, version in enumerate(range(88, 108)):
        platform = _FIREFOX_PLATFORMS[idx % len(_FIREFOX_PLATFORMS)]
        yield _firefox(version, platform)
    for idx, version in enumerate(range(100, 118)):
        windows_version = _EDGE_WINDOWS_VERSIONS[idx % len(_EDGE_WINDOWS_VERSIONS)]
        yield _edge(version, windows_version)
    for idx, version in enumerate(range(14, 30)):
        os_version = _MAC_OS_VERSIONS[idx % len(_MAC_OS_VERSIONS)]
        yield _safari(version, os_version)
    for idx, version in enumerate(range(14, 26)):
        device = _IOS_DEVICES[idx % len(_IOS_DEVICES)]
        os_version = _IOS_OS_VERSIONS[idx % len(_IOS_OS_VERSIONS)]
        yield _ios_safari(version, device, os_version)
    for idx, version in enumerate(range(96, 122)):
        android_version = _ANDROID_VERSIONS[idx % len(_ANDROID_VERSIONS)]
        device = _ANDROID_DEVICES[idx % len(_ANDROID_DEVICES)]
        yield _android_chrome(version, android_version, device)


def _build_profiles() -> list[RequestProfile]:
    profiles: list[RequestProfile] = []
    for idx, user_agent in enumerate(_iter_user_agents()):
        referer_template = _REFERER_TEMPLATES[idx % len(_REFERER_TEMPLATES)]
        accept_language = _ACCEPT_LANGUAGES[idx % len(_ACCEPT_LANGUAGES)]
        accept = _ACCEPT_HEADERS[idx % len(_ACCEPT_HEADERS)]
        profiles.append(
            RequestProfile(
                user_agent=user_agent,
                referer_template=referer_template,
                accept_language=accept_language,
                accept=accept,
            )
        )
        if len(profiles) >= 100:
            break
    return profiles


_REQUEST_PROFILES: list[RequestProfile] = _build_profiles()
if len(_REQUEST_PROFILES) < 100:  # pragma: no cover - configuration guard
    raise RuntimeError("未能生成足够的请求配置，至少需要 100 个")
_RANDOM = random.SystemRandom()


def get_profile_headers(url: str) -> dict[str, str]:
    """Return a randomized set of headers tailored for the given URL."""

    profile = _RANDOM.choice(_REQUEST_PROFILES)
    return profile.build_headers(url)

