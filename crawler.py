from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Iterable, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from database import SessionLocal
from email_utils import NotificationConfigError, send_dingtalk_message, send_email
from models import CrawlLog, CrawlLogDetail, CrawlResult, MonitorTask, WatchContent

from nlp import similarity

SIMILARITY_THRESHOLD = 0.6
LOGGER = logging.getLogger(__name__)


def _is_non_empty_text(value: str | None) -> bool:
    return bool(value and value.strip())


def parse_snapshot(snapshot: str | None) -> tuple[str | None, list[dict[str, str]], str | None]:
    """Parse a stored snapshot payload.

    Returns a tuple containing the main page HTML, a list of subpage entries,
    and the detected title of the main page. Each entry is a mapping with
    ``url``, ``html`` and optional ``title`` keys. The helper is
    backward-compatible with legacy snapshots that stored the main HTML as a
    plain string.
    """

    if not snapshot:
        return None, [], None

    try:
        data = json.loads(snapshot)
    except json.JSONDecodeError:
        return snapshot, [], None

    def _add_entry(entries: list[dict[str, str | None]], url: str | None, html: str | None, title: str | None = None) -> None:
        if not isinstance(url, str) or not isinstance(html, str):
            return
        normalized_title = title.strip() if isinstance(title, str) else ""
        if not normalized_title:
            normalized_title = summarize_html(html)[0]
        entries.append({"url": url, "html": html, "title": normalized_title or None})

    if isinstance(data, dict) and ("main_html" in data or "subpages" in data):
        main_html = data.get("main_html") if isinstance(data.get("main_html"), str) else None
        main_title = data.get("main_title") if isinstance(data.get("main_title"), str) else None
        subpages_data = data.get("subpages", [])
        entries: list[dict[str, str | None]] = []
        if isinstance(subpages_data, dict):
            for url, html in subpages_data.items():
                if isinstance(html, dict):
                    _add_entry(entries, url, html.get("html"), html.get("title"))
                else:
                    _add_entry(entries, url, html)
        elif isinstance(subpages_data, list):
            for item in subpages_data:
                if isinstance(item, dict):
                    url = item.get("url")
                    html = item.get("html")
                    title = item.get("title")
                    _add_entry(entries, url, html, title)
        if not _is_non_empty_text(main_title) and isinstance(main_html, str):
            main_title = summarize_html(main_html)[0]
        return main_html, entries, main_title

    if isinstance(data, str):
        return data, [], summarize_html(data)[0]

    return snapshot, [], summarize_html(snapshot)[0]


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
        if isinstance(url, str) and isinstance(html, str):
            serialized: dict[str, str | None] = {"url": url, "html": html}
            if _is_non_empty_text(title):
                serialized["title"] = title.strip()
            serialized_subpages.append(serialized)

    payload = {
        "version": 2,
        "main_html": main_html,
        "main_title": main_title.strip() if _is_non_empty_text(main_title) else None,
        "subpages": serialized_subpages,
    }
    return json.dumps(payload, ensure_ascii=False)


class CrawlError(RuntimeError):
    pass


def _fetch_html_with_requests(url: str) -> str:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
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

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=20_000)
            html = page.content()
            context.close()
            browser.close()
            return html
    except PlaywrightTimeoutError as exc:
        LOGGER.warning("使用无头浏览器抓取 %s 超时：%s，改用 requests", url, exc)
        return _fetch_html_with_requests(url)
    except PlaywrightError as exc:
        LOGGER.warning("使用无头浏览器抓取 %s 失败：%s，改用 requests", url, exc)
        return _fetch_html_with_requests(url)


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


def summarize_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = _extract_display_title(soup)

    main_container = soup.find("main") or soup.find("article") or soup
    paragraphs = [
        p.get_text(" ", strip=True)
        for p in main_container.find_all("p")
        if _is_non_empty_text(p.get_text(strip=True))
    ]
    if not paragraphs:
        text_content = main_container.get_text(" ", strip=True)
    else:
        text_content = " ".join(paragraphs)

    summary = text_content[:1000]
    return title, summary


def compare_links(old_html: str | None, new_html: str, base_url: str) -> List[str]:
    current_links = set(extract_links(new_html, base_url))
    if not old_html:
        return list(current_links)
    previous_links = set(extract_links(old_html, base_url))
    return list(current_links - previous_links)


def score_contents(article_summary: str, contents: Iterable[WatchContent]) -> list[tuple[WatchContent, float]]:
    if not contents:
        return []
    candidate_texts = [content.text for content in contents]
    scores = similarity(article_summary, candidate_texts)
    return list(zip(contents, scores))


def notify(
    task: MonitorTask,
    title: str,
    url: str,
    summary: str,
    matches: list[tuple[WatchContent, float]],
) -> None:
    rows = "".join(
        f"<li><strong>{content.text}</strong> - 相似度: {score:.2f}</li>" for content, score in matches
    )
    html_body = f"""
        <h3>监控任务: {task.name}</h3>
        <p>发现新的内容匹配关注项：</p>
        <ul>{rows}</ul>
        <p><strong>标题:</strong> {title or '未提供'}</p>
        <p><strong>链接:</strong> <a href='{url}'>{url}</a></p>
        <p><strong>摘要:</strong> {summary}</p>
    """
    text_body = f"监控任务 {task.name} 发现匹配内容: {title or '未提供'} - {url}\n摘要: {summary}"

    if task.notification_method == "dingtalk":
        content_lines = [f"监控任务：{task.name}"]
        if matches:
            content_lines.append("发现新的内容匹配关注项：")
            for watch_content, score in matches:
                content_lines.append(f"- {watch_content.text}（相似度 {score:.2f}）")
        else:
            content_lines.append("发现新的内容：")
        content_lines.append(f"标题：{title or '未提供'}")
        content_lines.append(f"摘要：{summary}")
        content = "\n".join(content_lines)
        try:
            send_dingtalk_message(
                title=f"监控任务 {task.name} 有新内容",
                content=content,
                url=url,
            )
        except NotificationConfigError:
            LOGGER.warning("钉钉通知配置缺失，任务 %s 无法发送", task.id)
        except Exception:  # noqa: BLE001
            LOGGER.exception("任务 %s 发送钉钉通知失败", task.id)
        return

    recipients = [email.strip() for email in (task.notification_email or "").split(",") if email.strip()]
    if not recipients:
        LOGGER.warning("Task %s has no notification email", task.id)
        return

    try:
        send_email(
            subject=f"监控任务 {task.name} 有新内容",
            recipients=recipients,
            html_body=html_body,
            text_body=text_body,
        )
    except NotificationConfigError:
        LOGGER.warning("邮件通知配置缺失，任务 %s 无法发送", task.id)
    except Exception:  # noqa: BLE001
        LOGGER.exception("任务 %s 发送邮件失败", task.id)


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
        previous_main_html, _, _ = parse_snapshot(website.last_snapshot)
        new_html = fetch_html(website.url)
        add_detail("主页面抓取成功")

        main_title, main_summary = summarize_html(new_html)
        LOGGER.info("Task %s fetched main page title: %s", task.name, main_title or "<无标题>")
        if main_title:
            add_detail(f"主页面标题：{main_title}")
        else:
            add_detail("主页面未发现标题", "warning")
        matched_results: list[tuple[str, str, str, list[tuple[WatchContent, float]]]] = []

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
                    subpage_snapshots.append({"url": link, "html": link_html})
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Failed to fetch sub link %s", link)
                    add_detail(f"子链接抓取失败：{link}", "warning")
                    subpage_errors.append(link)
                    continue
                title, summary = summarize_html(link_html)
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
                scores = score_contents(summary, task.watch_contents)
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
                    matched_results.append((title, link, summary, matches))
        else:
            has_changed = previous_main_html != new_html
            add_detail("检测到页面发生变化" if has_changed else "页面内容无变化")
            if has_changed:
                title, summary = main_title, main_summary
                scores = score_contents(summary, task.watch_contents)
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
                    matched_results.append((title, website.url, summary, matches))

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

        for title, link, summary, matches in matched_results:
            notify(task, title, link, summary, matches)
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
