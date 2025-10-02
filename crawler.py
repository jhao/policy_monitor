from __future__ import annotations

import json
import logging
import re
from collections import Counter, OrderedDict
from datetime import datetime
from html import escape as html_escape
from typing import Any, Callable, Iterable, List, Sequence
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:  # noqa: SIM105
    from lxml import etree, html as lxml_html  # type: ignore import-not-found
except ImportError:  # pragma: no cover - optional dependency
    etree = None  # type: ignore[assignment]
    lxml_html = None  # type: ignore[assignment]

from database import SessionLocal
from sqlalchemy.orm import Session
from email_utils import NotificationConfigError, send_dingtalk_message, send_email
from models import (
    CrawlLog,
    CrawlLogDetail,
    CrawlResult,
    MonitorTask,
    NotificationLog,
    WatchContent,
    Website,
)

from nlp import similarity

SIMILARITY_THRESHOLD = 0.6
LOGGER = logging.getLogger(__name__)


def _is_non_empty_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def extract_body_text(html: str | None) -> str:
    """Return a flattened body text similar to ``$("body")[0].innerText``."""

    if not isinstance(html, str):
        return ""

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    for tag in body.find_all(["script", "style", "noscript", "nav", "aside", "footer"]):
        tag.decompose()
    keywords = ("menu", "nav", "breadcrumb", "pagination", "footer")

    def _contains_keyword(value: str | list[str] | None) -> bool:
        if not value:
            return False
        if isinstance(value, str):
            candidates = value.split()
        else:
            candidates = [item for item in value if isinstance(item, str)]
        lowered = [candidate.lower() for candidate in candidates]
        return any(any(keyword in item for keyword in keywords) for item in lowered)

    for tag in body.find_all(class_=_contains_keyword):
        tag.decompose()
    for tag in body.find_all(id=lambda value: isinstance(value, str) and any(keyword in value.lower() for keyword in keywords)):
        tag.decompose()
    for tag in body.find_all(attrs={"role": ["navigation", "contentinfo", "menubar"]}):
        tag.decompose()
    text = body.get_text(" ", strip=True)
    return _normalize_whitespace(text)


def parse_snapshot(
    snapshot: str | None,
) -> tuple[str | None, list[dict[str, str | None]], str | None, str | None]:
    """Parse a stored snapshot payload.

    Returns a tuple containing the main page HTML, a list of subpage entries,
    the detected title of the main page, and the flattened body text. Each
    entry is a mapping with ``url``, ``html``, optional ``title`` and ``text``
    keys. The helper is backward-compatible with legacy snapshots that stored
    the main HTML as a plain string.
    """

    if not snapshot:
        return None, [], None, None

    try:
        data = json.loads(snapshot)
    except json.JSONDecodeError:
        return snapshot, [], None, extract_body_text(snapshot)

    def _add_entry(
        entries: list[dict[str, str | None]],
        url: str | None,
        html: str | None,
        title: str | None = None,
        text: str | None = None,
    ) -> None:
        if not isinstance(url, str) or not isinstance(html, str):
            return
        normalized_title = title.strip() if isinstance(title, str) else ""
        if not normalized_title:
            normalized_title = summarize_html(html)[0]
        normalized_text = text.strip() if isinstance(text, str) else ""
        if not normalized_text:
            normalized_text = extract_body_text(html)
        entries.append(
            {
                "url": url,
                "html": html,
                "title": normalized_title or None,
                "text": normalized_text or None,
            }
        )

    if isinstance(data, dict) and ("main_html" in data or "subpages" in data):
        main_html = data.get("main_html") if isinstance(data.get("main_html"), str) else None
        main_title = data.get("main_title") if isinstance(data.get("main_title"), str) else None
        main_text = data.get("main_text") if isinstance(data.get("main_text"), str) else None
        subpages_data = data.get("subpages", [])
        entries: list[dict[str, str | None]] = []
        if isinstance(subpages_data, dict):
            for url, html in subpages_data.items():
                if isinstance(html, dict):
                    _add_entry(entries, url, html.get("html"), html.get("title"), html.get("text"))
                else:
                    _add_entry(entries, url, html)
        elif isinstance(subpages_data, list):
            for item in subpages_data:
                if isinstance(item, dict):
                    url = item.get("url")
                    html = item.get("html")
                    title = item.get("title")
                    text = item.get("text")
                    _add_entry(entries, url, html, title, text)
        if not _is_non_empty_text(main_title) and isinstance(main_html, str):
            main_title = summarize_html(main_html)[0]
        if not _is_non_empty_text(main_text) and isinstance(main_html, str):
            main_text = extract_body_text(main_html)
        return main_html, entries, main_title, main_text

    if isinstance(data, str):
        title, _ = summarize_html(data)
        text = extract_body_text(data)
        return data, [], title, text

    title, _ = summarize_html(snapshot)
    text = extract_body_text(snapshot)
    return snapshot, [], title, text


def build_snapshot(
    main_html: str,
    subpages: list[dict[str, str | None]],
    main_title: str | None = None,
) -> str:
    serialized_subpages: list[dict[str, str | None]] = []
    for item in subpages:
        url = item.get("url") if isinstance(item, dict) else None
        html = item.get("html") if isinstance(item, dict) else None
        title = item.get("title") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if isinstance(url, str) and isinstance(html, str):
            serialized: dict[str, str | None] = {"url": url, "html": html}
            if _is_non_empty_text(title):
                serialized["title"] = title.strip()
            normalized_text = text.strip() if isinstance(text, str) else ""
            if not normalized_text:
                normalized_text = extract_body_text(html)
            if normalized_text:
                serialized["text"] = normalized_text
            serialized_subpages.append(serialized)

    payload = {
        "version": 3,
        "main_html": main_html,
        "main_title": main_title.strip() if _is_non_empty_text(main_title) else None,
        "main_text": extract_body_text(main_html),
        "subpages": serialized_subpages,
    }
    return json.dumps(payload, ensure_ascii=False)


class CrawlError(RuntimeError):
    pass


DEFAULT_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _fetch_html_with_requests(url: str) -> str:
    response = requests.get(url, timeout=20, headers=DEFAULT_REQUEST_HEADERS)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = response.status_code
        message = f"请求 {url} 失败，状态码 {status}"
        if status == 403:
            message += "，可能需要浏览器访问或额外的身份验证"
        raise CrawlError(message) from exc
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        apparent = response.apparent_encoding
        if apparent:
            response.encoding = apparent
    return response.text


def fetch_html(url: str) -> str:
    try:
        from playwright.sync_api import (  # type: ignore import-not-found
            Error as PlaywrightError,
            TimeoutError as PlaywrightTimeoutError,
            sync_playwright,
        )
    except ImportError:
        LOGGER.warning("Playwright 未安装，回退到 requests 抓取 %s", url)
        return _fetch_html_with_requests(url)

    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PlaywrightTimeoutError:
                LOGGER.debug("等待 %s 的 networkidle 状态超时，继续尝试获取页面内容", url)
            page.wait_for_load_state("load", timeout=30_000)
            page.wait_for_timeout(2_000)
            html = page.content()
            return html
    except PlaywrightTimeoutError as exc:
        LOGGER.warning("使用无头浏览器抓取 %s 超时：%s，改用 requests", url, exc)
        return _fetch_html_with_requests(url)
    except PlaywrightError as exc:
        LOGGER.warning("使用无头浏览器抓取 %s 失败：%s，改用 requests", url, exc)
        return _fetch_html_with_requests(url)
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()


def extract_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if href:
            absolute = urljoin(base_url, href)
            links.append(absolute)
    return links


def _extract_display_title(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    heading_levels = ["h1", "h2", "h3", "h4"]
    for level in heading_levels:
        for heading in soup.find_all(level):
            text = heading.get_text(" ", strip=True)
            if _is_non_empty_text(text):
                return text

    for heading in soup.find_all(attrs={"role": "heading"}):
        text = heading.get_text(" ", strip=True)
        if _is_non_empty_text(text):
            return text

    for attribute in ("og:title", "twitter:title"):  # Meta fallbacks
        meta = soup.find("meta", attrs={"property": attribute}) or soup.find("meta", attrs={"name": attribute})
        if meta and _is_non_empty_text(meta.get("content")):
            return meta.get("content", "").strip()

    if soup.title and _is_non_empty_text(soup.title.string):
        return soup.title.string.strip()

    return ""


def _tokenize_for_summary(text: str) -> list[str]:
    if not text:
        return []
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", text.lower())
    return tokens


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    separators = r"(?<=[。！？!?])\s+|[\n\r]+"
    sentences = [segment.strip() for segment in re.split(separators, text) if segment.strip()]
    if sentences:
        return sentences
    return [text.strip()]


def _select_representative_sentence(sentences: Sequence[str], tokens: Counter[str]) -> str:
    best_sentence = ""
    best_score = float("-inf")
    for sentence in sentences:
        sentence_tokens = _tokenize_for_summary(sentence)
        if not sentence_tokens:
            continue
        score = sum(tokens[token] for token in sentence_tokens) / len(sentence_tokens)
        length_bonus = min(len(sentence) / 120.0, 1.0)
        score += length_bonus
        if score > best_score:
            best_score = score
            best_sentence = sentence
    return best_sentence or (sentences[0] if sentences else "")


def _generate_main_idea(text: str, fallback_title: str) -> str:
    normalized_text = text.strip()
    if not normalized_text:
        return fallback_title

    sentences = _split_sentences(normalized_text)
    tokens = Counter(_tokenize_for_summary(normalized_text))
    if not tokens:
        return sentences[0] if sentences else fallback_title

    representative = _select_representative_sentence(sentences, tokens)
    representative = representative.strip()
    if representative:
        return representative[:120]
    return fallback_title


def _text_from_element(element: object) -> str:
    try:
        text = element.get_text(" ", strip=True)  # type: ignore[attr-defined]
    except AttributeError:
        text = ""
    if not _is_non_empty_text(text):
        for attribute in ("content", "value", "title", "alt"):
            try:
                candidate = element.get(attribute)  # type: ignore[attr-defined]
            except AttributeError:
                candidate = None
            if _is_non_empty_text(candidate):
                text = str(candidate).strip()
                break
    return _normalize_whitespace(text)


def _parse_selector_config(config: str | None) -> list[tuple[str, str]]:
    if not _is_non_empty_text(config):
        return []
    selectors: list[tuple[str, str]] = []
    for raw_line in config.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            prefix, value = line.split("=", 1)
            key = prefix.strip().lower()
            selector_value = value.strip()
            if not selector_value:
                continue
            if key in {"id", "#"}:
                selectors.append(("css", f"#{selector_value}"))
            elif key in {"class", "."}:
                selectors.append(("css", f".{selector_value}"))
            elif key == "name":
                selectors.append(("css", f"[name='{selector_value}']"))
            elif key in {"css", "selector"}:
                selectors.append(("css", selector_value))
            elif key == "xpath":
                selectors.append(("xpath", selector_value))
            else:
                selectors.append(("css", selector_value))
        else:
            selectors.append(("css", line))
    return selectors


def _extract_text_by_selectors(html: str, selectors: list[tuple[str, str]]) -> str | None:
    if not selectors:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for method, value in selectors:
        element_text = ""
        if method == "css":
            try:
                element = soup.select_one(value)
            except Exception:  # noqa: BLE001
                LOGGER.debug("CSS 选择器 %s 解析失败", value, exc_info=True)
                continue
            if element is None:
                continue
            element_text = _text_from_element(element)
        elif method == "xpath":
            if lxml_html is None or etree is None:
                LOGGER.debug("XPath 规则 %s 被忽略，缺少 lxml 依赖", value)
                continue
            try:
                tree = lxml_html.fromstring(html)
            except Exception:  # noqa: BLE001
                LOGGER.debug("解析 HTML 失败，无法应用 XPath %s", value, exc_info=True)
                continue
            try:
                results = tree.xpath(value)
            except Exception:  # noqa: BLE001
                LOGGER.debug("执行 XPath %s 失败", value, exc_info=True)
                continue
            for result in results:
                if hasattr(result, "itertext"):
                    element_text = _normalize_whitespace(" ".join(result.itertext()))  # type: ignore[arg-type]
                else:
                    element_text = _normalize_whitespace(str(result))
                if _is_non_empty_text(element_text):
                    break
        else:
            continue
        if _is_non_empty_text(element_text):
            return element_text
    return None


def _summarize_without_preferences(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    fallback_title = _extract_display_title(soup)
    text_content = extract_body_text(html)
    main_idea = _generate_main_idea(text_content, fallback_title)
    summary = text_content[:1000]
    return main_idea, summary


def summarize_html(html: str, website: Website | None = None) -> tuple[str, str]:
    fallback_title, fallback_summary = _summarize_without_preferences(html)

    if not website:
        return fallback_title, fallback_summary

    title_selectors = _parse_selector_config(website.title_selector_config)
    content_selectors = _parse_selector_config(website.content_selector_config)

    preferred_title = _extract_text_by_selectors(html, title_selectors)
    preferred_body = _extract_text_by_selectors(html, content_selectors)

    title = fallback_title
    summary = fallback_summary

    if _is_non_empty_text(preferred_body):
        summary = preferred_body[:1000]
        idea = _generate_main_idea(preferred_body, preferred_title or fallback_title)
        if _is_non_empty_text(preferred_title):
            title = preferred_title
        elif _is_non_empty_text(idea):
            title = idea
    elif _is_non_empty_text(preferred_title):
        title = preferred_title

    return title, summary


def compare_links(old_html: str | None, new_html: str, base_url: str) -> List[str]:
    current_links = set(extract_links(new_html, base_url))
    if not old_html:
        return list(current_links)
    previous_links = set(extract_links(old_html, base_url))
    return list(current_links - previous_links)


KEYWORD_SPLIT_PATTERN = re.compile(r"[,\u3001，;；、\s]+")


def _extract_keywords(text: str) -> list[str]:
    """Split watch content text into keywords.

    Returns the original text as a single keyword if no separators are found.
    """

    parts = [part.strip() for part in KEYWORD_SPLIT_PATTERN.split(text) if part.strip()]
    return parts or [text.strip()]


def score_contents(
    article_title: str | None,
    article_summary: str | None,
    contents: Iterable[WatchContent],
) -> list[tuple[WatchContent, float]]:
    contents_list = list(contents)
    if not contents_list:
        return []

    normalized_title = (article_title or "").lower()
    normalized_summary = (article_summary or "").lower()
    results: list[tuple[WatchContent, float]] = []
    unmatched_contents: list[tuple[int, WatchContent]] = []

    for index, content in enumerate(contents_list):
        text = content.text.strip()
        if not text:
            results.append((content, 0.0))
            continue

        keywords = _extract_keywords(text)
        matched = False
        for keyword in keywords:
            keyword_lower = keyword.lower()
            if not keyword_lower:
                continue
            if keyword_lower in normalized_title or keyword_lower in normalized_summary:
                results.append((content, 1.0))
                matched = True
                break

        if not matched:
            results.append((content, 0.0))
            unmatched_contents.append((index, content))

    if unmatched_contents:
        candidate_texts = [content.text for _, content in unmatched_contents]
        scores = similarity(normalized_summary, candidate_texts)
        for (index, content), score in zip(unmatched_contents, scores):
            results[index] = (content, score)

    return results


def _extract_first_image_url(html: str | None, base_url: str | None) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    image = body.find("img")
    if not image:
        return None
    src = image.get("src")
    if not src:
        return None
    if base_url:
        return urljoin(base_url, src)
    return src


def _build_notification_email_html(task: MonitorTask, items: list[dict[str, str]]) -> str:
    blocks: list[str] = [
        f"<h3 style=\"margin:0 0 16px 0;\">监控任务：{html_escape(task.name)}</h3>",
        "<p style=\"margin:0 0 16px 0;\">发现以下符合关注内容的更新：</p>",
    ]
    for item in items:
        image_html = (
            f"<div style=\"flex:0 0 120px;margin-right:12px;\"><img src=\"{html_escape(item['pic'])}\" alt=\"预览图\" style=\"max-width:120px;border-radius:4px;\"/></div>"
            if item["pic"]
            else ""
        )
        summary = html_escape(item["summary"])
        matches = html_escape(item["matches"])
        blocks.append(
            """
<div style="display:flex;align-items:flex-start;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:12px;background:#fafafa;">
  {image}
  <div style="flex:1;min-width:0;">
    <h4 style="margin:0 0 8px 0;font-size:16px;">{title}</h4>
    <p style="margin:0 0 8px 0;color:#555;">匹配关注项：{matches}</p>
    <p style="margin:0 0 8px 0;white-space:pre-wrap;color:#333;">{summary}</p>
    <p style="margin:0;">
      <a href="{url}" style="color:#0d6efd;text-decoration:none;">查看详情</a>
    </p>
  </div>
</div>
            """.format(
                image=image_html,
                title=html_escape(item["title"]),
                matches=matches or "无",
                summary=summary,
                url=html_escape(item["url"]),
            )
        )
    return "".join(blocks)


def _record_notification_log(
    session: Session,
    task: MonitorTask | None,
    channel: str,
    target: str | None,
    status: str,
    message: str | None,
) -> None:
    log_entry = NotificationLog(
        task=task,
        channel=channel,
        target=target,
        status=status,
        message=message,
    )
    session.add(log_entry)
    session.commit()


def _send_task_notifications(
    session: Session,
    task: MonitorTask,
    payload_items: list[dict[str, str]],
    detail_callback: Callable[[str, str], None] | None = None,
) -> None:
    if task.notification_method == "dingtalk":
        links = [
            {
                "title": item["title"],
                "messageURL": item["url"],
                "picURL": item["pic"],
            }
            for item in payload_items
        ]
        if detail_callback:
            detail_callback(
                f"准备发送钉钉通知，共 {len(payload_items)} 条", "info"
            )
        LOGGER.info("Task %s sending DingTalk notification", task.id)
        try:
            webhook_url = send_dingtalk_message(
                {
                    "msgtype": "feedCard",
                    "title": f"监控任务：{task.name}",
                    "feedCard": {"links": links},
                }
            )
        except NotificationConfigError as exc:
            LOGGER.warning("钉钉通知配置缺失，任务 %s 无法发送", task.id)
            if detail_callback:
                detail_callback("钉钉通知配置缺失，未能发送", "warning")
            _record_notification_log(
                session,
                task,
                channel="dingtalk",
                target=None,
                status="failed",
                message=str(exc) or "钉钉通知配置缺失",
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("任务 %s 发送钉钉通知失败", task.id)
            if detail_callback:
                detail_callback("钉钉通知发送失败", "error")
            _record_notification_log(
                session,
                task,
                channel="dingtalk",
                target=None,
                status="failed",
                message=str(exc) or "钉钉通知发送失败",
            )
        else:
            if detail_callback:
                detail_callback("钉钉通知发送成功", "success")
            _record_notification_log(
                session,
                task,
                channel="dingtalk",
                target=webhook_url,
                status="success",
                message=f"已成功发送 {len(payload_items)} 条更新",
            )
        return

    recipients = [
        email.strip()
        for email in (task.notification_email or "").split(",")
        if email.strip()
    ]
    if not recipients:
        LOGGER.warning("Task %s has no notification email", task.id)
        if detail_callback:
            detail_callback("任务未配置通知邮箱，无法发送邮件", "warning")
        _record_notification_log(
            session,
            task,
            channel="email",
            target=None,
            status="failed",
            message="未配置通知邮箱",
        )
        return

    html_body = _build_notification_email_html(task, payload_items)
    text_lines = [f"监控任务《{task.name}》发现 {len(payload_items)} 条匹配内容："]
    for item in payload_items:
        text_lines.append(f"- {item['title']} -> {item['url']}")
        if item["matches"]:
            text_lines.append(f"  匹配关注项：{item['matches']}")
        if item["summary"]:
            text_lines.append(f"  摘要：{item['summary']}")

    if detail_callback:
        detail_callback(
            f"准备发送邮件通知至：{', '.join(recipients)}", "info"
        )
    LOGGER.info(
        "Task %s sending email notification to %s", task.id, ", ".join(recipients)
    )
    try:
        send_email(
            subject=f"监控任务 {task.name} 有新内容",
            recipients=recipients,
            html_body=html_body,
            text_body="\n".join(text_lines),
        )
    except NotificationConfigError as exc:
        LOGGER.warning("邮件通知配置缺失，任务 %s 无法发送", task.id)
        if detail_callback:
            detail_callback("邮件通知配置缺失，未能发送", "warning")
        _record_notification_log(
            session,
            task,
            channel="email",
            target=", ".join(recipients),
            status="failed",
            message=str(exc) or "邮件通知配置缺失",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("任务 %s 发送邮件失败", task.id)
        if detail_callback:
            detail_callback("邮件通知发送失败", "error")
        _record_notification_log(
            session,
            task,
            channel="email",
            target=", ".join(recipients),
            status="failed",
            message=str(exc) or "邮件发送失败",
        )
    else:
        if detail_callback:
            detail_callback("邮件通知发送成功", "success")
        _record_notification_log(
            session,
            task,
            channel="email",
            target=", ".join(recipients),
            status="success",
            message=f"已成功发送 {len(payload_items)} 条更新",
        )


def run_task(task_id: int) -> None:
    session = SessionLocal()
    log_entry_id: int | None = None

    def add_detail(message: str, level: str = "info") -> None:
        if log_entry_id is None:
            return
        detail = CrawlLogDetail(log_id=log_entry_id, message=message, level=level)
        session.add(detail)
        session.commit()

    try:
        task = session.get(MonitorTask, task_id)
        if not task:
            LOGGER.error("Task %s not found", task_id)
            return

        log_entry = CrawlLog(task=task)
        session.add(log_entry)
        session.commit()
        log_entry_id = log_entry.id

        add_detail(f"开始执行任务《{task.name}》", "info")

        website = task.website
        if not website:
            raise CrawlError("监控任务未配置网站")

        add_detail(f"准备抓取网站：{website.url}")
        LOGGER.info("Running task %s on %s", task.name, website.url)
        previous_main_html, _, _, previous_main_text = parse_snapshot(website.last_snapshot)
        new_html = fetch_html(website.url)
        add_detail("主页面抓取成功")

        main_title, main_summary = summarize_html(new_html, website)
        current_main_text = extract_body_text(new_html)
        LOGGER.info("Task %s fetched main page title: %s", task.name, main_title or "<无标题>")
        if main_title:
            add_detail(f"主页面标题：{main_title}")
        else:
            add_detail("主页面未发现标题", "warning")
        matched_results: list[dict[str, Any]] = []

        subpage_errors: list[str] = []

        subpage_snapshots: list[dict[str, str | None]] = []

        if website.fetch_subpages:
            new_links = compare_links(previous_main_html, new_html, website.url)
            LOGGER.debug("Found %d new links", len(new_links))
            add_detail(f"发现新链接 {len(new_links)} 个")

            for link in new_links:
                add_detail(f"抓取子链接：{link}")
                try:
                    link_html = fetch_html(link)
                    add_detail(f"子链接抓取成功：{link}")
                    subpage_snapshots.append(
                        {
                            "url": link,
                            "html": link_html,
                            "text": extract_body_text(link_html),
                        }
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Failed to fetch sub link %s", link)
                    add_detail(f"子链接抓取失败：{link}", "warning")
                    subpage_errors.append(link)
                    continue
                title, summary = summarize_html(link_html, website)
                LOGGER.info(
                    "Task %s fetched sub page %s title: %s",
                    task.name,
                    link,
                    title or "<无标题>",
                )
                if title:
                    add_detail(f"子链接标题：{title}")
                else:
                    add_detail("子链接未发现标题", "warning")
                subpage_snapshots[-1]["title"] = title
                # 关键关注内容仅匹配子页面标题
                scores = score_contents(title, title, task.watch_contents)
                matches = [(content, score) for content, score in scores if score >= SIMILARITY_THRESHOLD]
                if matches:
                    matched_contents = ", ".join(f"{content.text}({score:.2f})" for content, score in matches)
                    add_detail(f"子链接命中关注项：{matched_contents}", "success")
                    best_match = max(matches, key=lambda item: item[1])
                    result = CrawlResult(
                        task=task,
                        website=website,
                        content=best_match[0],
                        discovered_url=link,
                        link_title=title,
                        content_summary=summary,
                        similarity_score=best_match[1],
                    )
                    session.add(result)
                    matched_results.append(
                        {
                            "title": title or link,
                            "url": link,
                            "summary": summary or "",
                            "matches": matches,
                            "html": link_html,
                            "base_url": link,
                        }
                    )
        else:
            previous_text_to_compare = previous_main_text
            if not _is_non_empty_text(previous_text_to_compare) and isinstance(previous_main_html, str):
                previous_text_to_compare = extract_body_text(previous_main_html)
            has_changed = (previous_text_to_compare or "") != current_main_text
            add_detail("检测到页面发生变化" if has_changed else "页面内容无变化")
            if has_changed:
                title, summary = main_title, main_summary
                scores = score_contents(title, summary, task.watch_contents)
                matches = [(content, score) for content, score in scores if score >= SIMILARITY_THRESHOLD]
                if matches:
                    matched_contents = ", ".join(f"{content.text}({score:.2f})" for content, score in matches)
                    add_detail(f"主页面命中关注项：{matched_contents}", "success")
                    best_match = max(matches, key=lambda item: item[1])
                    result = CrawlResult(
                        task=task,
                        website=website,
                        content=best_match[0],
                        discovered_url=website.url,
                        link_title=title,
                        content_summary=summary,
                        similarity_score=best_match[1],
                    )
                    session.add(result)
                    matched_results.append(
                        {
                            "title": title or website.url,
                            "url": website.url,
                            "summary": summary or "",
                            "matches": matches,
                            "html": new_html,
                            "base_url": website.url,
                        }
                    )

        if matched_results:
            add_detail(f"发现匹配结果 {len(matched_results)} 条", "success")
        else:
            add_detail("未发现符合条件的内容")

        website.last_snapshot = build_snapshot(new_html, subpage_snapshots, main_title)
        website.last_fetched_at = datetime.utcnow()
        task.last_run_at = datetime.utcnow()
        task.last_status = "success" if matched_results else "completed"

        session.add(website)
        session.add(task)

        if log_entry_id is None:
            session.flush()
            log_entry_id = log_entry.id

        log_entry = session.get(CrawlLog, log_entry_id)
        if log_entry is None:
            raise CrawlError("日志记录不存在")

        log_entry.status = task.last_status
        log_entry.run_finished_at = datetime.utcnow()
        message_parts = [f"发现匹配结果 {len(matched_results)} 条"]
        if subpage_errors:
            message_parts.append(f"子链接抓取失败 {len(subpage_errors)} 个: {'; '.join(subpage_errors)}")
        log_entry.message = "；".join(message_parts)
        session.add(log_entry)
        session.commit()

        if matched_results:
            merged_payload: OrderedDict[str, dict[str, Any]] = OrderedDict()
            for item in matched_results:
                url = item["url"]
                summary_text = (item["summary"] or "")[:200]
                image_url = _extract_first_image_url(item.get("html"), item.get("base_url")) or ""
                if url not in merged_payload:
                    merged_payload[url] = {
                        "title": item["title"] or url,
                        "url": url,
                        "summary": summary_text,
                        "pic": image_url,
                        "matches_map": OrderedDict(),
                    }
                entry = merged_payload[url]
                if entry["title"] == entry["url"] and item["title"]:
                    entry["title"] = item["title"]
                if not entry["summary"] and summary_text:
                    entry["summary"] = summary_text
                if image_url and not entry["pic"]:
                    entry["pic"] = image_url
                matches_map: OrderedDict[Any, dict[str, Any]] = entry["matches_map"]
                for content, score in item["matches"]:
                    identifier = getattr(content, "id", None) or content.text
                    existing = matches_map.get(identifier)
                    if existing is None:
                        matches_map[identifier] = {"text": content.text, "score": score}
                    elif score > existing["score"]:
                        existing["score"] = score

            payload_items: list[dict[str, str]] = []
            for entry in merged_payload.values():
                matches_label = "、".join(
                    f"{value['text']}({value['score']:.2f})" for value in entry["matches_map"].values()
                )
                payload_items.append(
                    {
                        "title": entry["title"],
                        "url": entry["url"],
                        "summary": entry["summary"],
                        "matches": matches_label,
                        "pic": entry["pic"],
                    }
                )
            _send_task_notifications(session, task, payload_items, add_detail)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        LOGGER.exception("Task %s failed", task_id)

        if log_entry_id is None:
            log_entry = CrawlLog(task_id=task_id)
            session.add(log_entry)
            session.commit()
            log_entry_id = log_entry.id

        add_detail("任务执行失败，已回滚未完成操作", "error")
        add_detail(f"错误信息：{exc}", "error")

        task = session.get(MonitorTask, task_id)
        if task:
            task.last_status = "failed"
            task.last_run_at = datetime.utcnow()
            session.add(task)

        log_entry = session.get(CrawlLog, log_entry_id)
        if log_entry is None:
            log_entry = CrawlLog(task_id=task_id)
            session.add(log_entry)
            session.commit()
            log_entry_id = log_entry.id

        log_entry.status = "failed"
        log_entry.run_finished_at = datetime.utcnow()
        log_entry.message = str(exc)
        session.add(log_entry)
        session.commit()
    finally:
        session.close()
