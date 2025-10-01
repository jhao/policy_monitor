from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from database import SessionLocal
from email_utils import NotificationConfigError, send_dingtalk_message, send_email
from models import CrawlLog, CrawlResult, MonitorTask, WatchContent
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
        markdown_rows = "\n".join(
            f"- **{content.text}** 相似度：{score:.2f}" for content, score in matches
        )
        markdown = (
            f"### 监控任务：{task.name}\n"
            f"发现新的内容匹配关注项：\n{markdown_rows}\n\n"
            f"**标题：** {title or '未提供'}\n\n"
            f"**链接：** {url}\n\n"
            f"**摘要：** {summary}"
        )
        try:
            send_dingtalk_message(title=f"监控任务 {task.name} 有新内容", markdown=markdown)
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
    try:
        task = session.get(MonitorTask, task_id)
        if not task:
            LOGGER.error("Task %s not found", task_id)
            return
        log_entry = CrawlLog(task=task)
        session.add(log_entry)
        session.commit()

        website = task.website
        if not website:
            raise CrawlError("监控任务未配置网站")

        LOGGER.info("Running task %s on %s", task.name, website.url)
        new_html = fetch_html(website.url)
        matched_results = []

        subpage_errors: list[str] = []

        if website.fetch_subpages:
            new_links = compare_links(website.last_snapshot, new_html, website.url)
            LOGGER.debug("Found %d new links", len(new_links))

            for link in new_links:
                try:
                    link_html = fetch_html(link)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("Failed to fetch sub link %s", link)
                    subpage_errors.append(f"{link}: {exc}")
                    continue
                title, summary = summarize_html(link_html)
                scores = score_contents(summary, task.watch_contents)
                matches = [(content, score) for content, score in scores if score >= SIMILARITY_THRESHOLD]
                if matches:
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
            if has_changed:
                title, summary = summarize_html(new_html)
                scores = score_contents(summary, task.watch_contents)
                matches = [(content, score) for content, score in scores if score >= SIMILARITY_THRESHOLD]
                if matches:
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

        website.last_snapshot = new_html
        website.last_fetched_at = datetime.utcnow()
        task.last_run_at = datetime.utcnow()
        task.last_status = "success" if matched_results else "completed"

        session.add(website)
        session.add(task)
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
        task = session.get(MonitorTask, task_id)
        if task:
            task.last_status = "failed"
            task.last_run_at = datetime.utcnow()
            session.add(task)
        log_entry = CrawlLog(task_id=task_id, status="failed", message=str(exc), run_finished_at=datetime.utcnow())
        session.add(log_entry)
        session.commit()
    finally:
        session.close()
