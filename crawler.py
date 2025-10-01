from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from database import SessionLocal
from email_utils import send_email
from models import CrawlLog, CrawlLogDetail, CrawlResult, MonitorTask, WatchContent
from nlp import similarity

SIMILARITY_THRESHOLD = 0.6
LOGGER = logging.getLogger(__name__)


class CrawlError(RuntimeError):
    pass


def fetch_html(url: str) -> str:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.text


def extract_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if href:
            absolute = urljoin(base_url, href)
            links.append(absolute)
    return links


def summarize_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    paragraphs = " ".join(p.get_text(strip=True) for p in soup.find_all("p"))
    if not paragraphs:
        paragraphs = soup.get_text(" ", strip=True)
    summary = paragraphs[:1000]
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


def notify(task: MonitorTask, title: str, url: str, summary: str, matches: list[tuple[WatchContent, float]]) -> None:
    recipient = task.notification_email
    if not recipient:
        LOGGER.warning("Task %s has no notification email", task.id)
        return

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
    send_email(subject=f"监控任务 {task.name} 有新内容", recipients=[recipient], html_body=html_body, text_body=text_body)


def run_task(task_id: int) -> None:
    session = SessionLocal()
    log_details: list[Tuple[str, str]] = []
    log_entry_id: int | None = None
    try:
        task = session.get(MonitorTask, task_id)
        if not task:
            LOGGER.error("Task %s not found", task_id)
            return

        log_entry = CrawlLog(task=task)
        session.add(log_entry)
        session.commit()
        log_entry_id = log_entry.id

        def add_detail(message: str, level: str = "info") -> None:
            log_details.append((message, level))

        add_detail(f"开始执行任务《{task.name}》", "info")

        website = task.website
        if not website:
            raise CrawlError("监控任务未配置网站")

        add_detail(f"准备抓取网站：{website.url}")
        LOGGER.info("Running task %s on %s", task.name, website.url)
        new_html = fetch_html(website.url)
        add_detail("主页面抓取成功")
        matched_results: list[tuple[str, str, str, list[tuple[WatchContent, float]]]] = []

        if website.fetch_subpages:
            new_links = compare_links(website.last_snapshot, new_html, website.url)
            LOGGER.debug("Found %d new links", len(new_links))
            add_detail(f"发现新链接 {len(new_links)} 个")

            for link in new_links:
                add_detail(f"抓取子链接：{link}")
                try:
                    link_html = fetch_html(link)
                    add_detail(f"子链接抓取成功：{link}")
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Failed to fetch sub link %s", link)
                    add_detail(f"子链接抓取失败：{link}", "warning")
                    continue
                title, summary = summarize_html(link_html)
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
            has_changed = website.last_snapshot != new_html
            add_detail("检测到页面发生变化" if has_changed else "页面内容无变化")
            if has_changed:
                title, summary = summarize_html(new_html)
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

        website.last_snapshot = new_html
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

        for message, level in log_details:
            detail = CrawlLogDetail(log_id=log_entry.id, message=message, level=level)
            session.add(detail)

        log_entry.status = task.last_status
        log_entry.run_finished_at = datetime.utcnow()
        log_entry.message = f"发现匹配结果 {len(matched_results)} 条"
        session.add(log_entry)
        session.commit()

        for title, link, summary, matches in matched_results:
            notify(task, title, link, summary, matches)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        LOGGER.exception("Task %s failed", task_id)
        log_details.append(("任务执行失败，已回滚未完成操作", "error"))
        log_details.append((f"错误信息：{exc}", "error"))

        task = session.get(MonitorTask, task_id)
        if task:
            task.last_status = "failed"
            task.last_run_at = datetime.utcnow()
            session.add(task)

        if log_entry_id is None:
            log_entry = CrawlLog(task_id=task_id)
            session.add(log_entry)
            session.flush()
            log_entry_id = log_entry.id

        log_entry = session.get(CrawlLog, log_entry_id)
        if log_entry is None:
            log_entry = CrawlLog(task_id=task_id)
            session.add(log_entry)
            session.flush()
            log_entry_id = log_entry.id

        for message, level in log_details:
            detail = CrawlLogDetail(log_id=log_entry.id, message=message, level=level)
            session.add(detail)

        log_entry.status = "failed"
        log_entry.run_finished_at = datetime.utcnow()
        log_entry.message = str(exc)
        session.add(log_entry)
        session.commit()
    finally:
        session.close()
