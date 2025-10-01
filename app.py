from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from crawler import SIMILARITY_THRESHOLD, parse_snapshot, run_task
from email_utils import (
    NotificationConfigError,
    send_dingtalk_message,
    send_email,
)
from database import SessionLocal, init_db
from sqlalchemy.orm import Session
from models import (
    ContentCategory,
    CrawlLog,
    CrawlResult,
    MonitorTask,
    NotificationLog,
    NotificationSetting,
    WatchContent,
    Website,
)
from scheduler import MonitorScheduler
from logging_utils import configure_logging
from time_utils import format_local_datetime, get_local_timezone, to_local

configure_logging()


app = Flask(__name__)
app.config.update(SECRET_KEY="monitor-secret-key")

scheduler = MonitorScheduler()
_setup_complete = False


@app.template_filter("format_datetime")
def format_datetime_filter(value: datetime | None) -> str:
    return format_local_datetime(value)


@app.context_processor
def inject_timezone_context() -> dict[str, str]:
    tz = get_local_timezone()
    now = datetime.now(tz)
    tz_name = tz.tzname(now) if hasattr(tz, "tzname") else None
    tz_display = now.strftime("%Z%z") if now.tzinfo else "UTC"
    if tz_name and tz_name not in tz_display:
        tz_display = f"{tz_name} ({tz_display})"
    return {"current_timezone_display": tz_display}


def record_notification_log(
    session: Session,
    *,
    channel: str,
    status: str,
    target: str | None = None,
    message: str | None = None,
    task: MonitorTask | None = None,
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


def ensure_setup() -> None:
    """Initialize database connections and start the scheduler once."""
    global _setup_complete
    if _setup_complete:
        return
    init_db()
    scheduler.start()
    _setup_complete = True


@app.before_request
def setup_before_request() -> None:
    ensure_setup()


@app.teardown_appcontext
def shutdown_session(exception: Exception | None = None) -> None:  # noqa: ARG001
    SessionLocal.remove()


@app.route("/")
def index() -> Any:
    return redirect(url_for("list_tasks"))


@app.route("/websites")
def list_websites() -> Any:
    session = SessionLocal()
    websites = session.query(Website).all()
    return render_template("websites/list.html", websites=websites)


@app.route("/websites/<int:website_id>/snapshot")
def view_website_snapshot(website_id: int) -> Any:
    session = SessionLocal()
    try:
        website = session.get(Website, website_id)
        if not website:
            flash("未找到网站", "danger")
            return redirect(url_for("list_websites"))

        main_snapshot, subpage_snapshots, main_title, main_text = parse_snapshot(website.last_snapshot)
        snapshot_entries: list[dict[str, str | None]] = []
        if main_snapshot:
            snapshot_entries.append(
                {
                    "url": website.url,
                    "html": main_snapshot,
                    "title": main_title,
                    "text": main_text,
                    "label": "主页面",
                }
            )

        for index, entry in enumerate(subpage_snapshots, start=1):
            if not entry.get("url") or not entry.get("html"):
                continue
            snapshot_entries.append(
                {
                    "url": entry.get("url"),
                    "html": entry.get("html"),
                    "title": entry.get("title"),
                    "text": entry.get("text"),
                    "label": f"子链接 {index}",
                }
            )

        snapshot_entries = [item for item in snapshot_entries if item.get("url") and item.get("html")]

        related_tasks = [
            {"id": task.id, "name": task.name}
            for task in website.tasks
        ]

        return render_template(
            "websites/snapshot.html",
            website=website,
            snapshot_entries=snapshot_entries,
            related_tasks=related_tasks,
        )
    finally:
        session.close()


@app.route("/websites/new", methods=["GET", "POST"])
def create_website() -> Any:
    session = SessionLocal()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        url = request.form.get("url", "").strip()
        interval = int(request.form.get("interval", "60") or 60)
        fetch_subpages = bool(request.form.get("fetch_subpages"))
        title_selectors = request.form.get("title_selectors", "").strip()
        content_selectors = request.form.get("content_selectors", "").strip()
        if not name or not url:
            flash("请输入网站名称和URL", "danger")
        else:
            website = Website(
                name=name,
                url=url,
                interval_minutes=interval,
                fetch_subpages=fetch_subpages,
                title_selector_config=title_selectors or None,
                content_selector_config=content_selectors or None,
            )
            session.add(website)
            session.commit()
            flash("网站配置已保存", "success")
            return redirect(url_for("list_websites"))
    return render_template("websites/form.html", website=None)


@app.route("/websites/<int:website_id>/edit", methods=["GET", "POST"])
def edit_website(website_id: int) -> Any:
    session = SessionLocal()
    website = session.get(Website, website_id)
    if not website:
        flash("未找到网站", "danger")
        return redirect(url_for("list_websites"))
    if request.method == "POST":
        website.name = request.form.get("name", website.name)
        website.url = request.form.get("url", website.url)
        website.interval_minutes = int(request.form.get("interval", website.interval_minutes))
        website.fetch_subpages = bool(request.form.get("fetch_subpages"))
        website.title_selector_config = (
            request.form.get("title_selectors", "").strip() or None
        )
        website.content_selector_config = (
            request.form.get("content_selectors", "").strip() or None
        )
        session.add(website)
        session.commit()
        flash("网站信息已更新", "success")
        return redirect(url_for("list_websites"))
    return render_template("websites/form.html", website=website)


@app.route("/websites/<int:website_id>/delete", methods=["POST"])
def delete_website(website_id: int) -> Any:
    session = SessionLocal()
    website = session.get(Website, website_id)
    if website:
        session.delete(website)
        session.commit()
        flash("网站已删除", "success")
    return redirect(url_for("list_websites"))


@app.route("/contents")
def list_contents() -> Any:
    session = SessionLocal()
    categories = (
        session.query(ContentCategory)
        .options(selectinload(ContentCategory.contents))
        .order_by(ContentCategory.name)
        .all()
    )
    total_count = session.query(WatchContent).count()
    selected_category_id = request.args.get("category_id", type=int)
    selected_category = None

    query = (
        session.query(WatchContent)
        .options(selectinload(WatchContent.category))
        .order_by(WatchContent.created_at.desc())
    )
    if selected_category_id:
        selected_category = next(
            (category for category in categories if category.id == selected_category_id),
            None,
        )
        if not selected_category:
            flash("选择的分类不存在", "warning")
            return redirect(url_for("list_contents"))
        query = query.filter(WatchContent.category_id == selected_category_id)

    contents = query.all()
    return render_template(
        "contents/list.html",
        contents=contents,
        categories=categories,
        selected_category=selected_category,
        selected_category_id=selected_category_id,
        total_count=total_count,
    )


@app.route("/content-categories", methods=["POST"])
def create_content_category() -> Any:
    session = SessionLocal()
    name = request.form.get("name", "").strip()
    if not name:
        flash("请输入分类名称", "danger")
        return redirect(url_for("list_contents"))

    category = ContentCategory(name=name)
    session.add(category)
    try:
        session.commit()
        flash("分类已创建", "success")
    except Exception:  # noqa: BLE001
        session.rollback()
        flash("分类名称已存在", "warning")
    return redirect(url_for("list_contents"))


@app.route("/content-categories/<int:category_id>/delete", methods=["POST"])
def delete_content_category(category_id: int) -> Any:
    session = SessionLocal()
    category = session.get(ContentCategory, category_id)
    if not category:
        flash("分类不存在", "warning")
        return redirect(url_for("list_contents"))

    if category.contents:
        flash("请先清空该分类下的关注内容", "warning")
        return redirect(url_for("list_contents", category_id=category.id))

    session.delete(category)
    session.commit()
    flash("分类已删除", "success")
    return redirect(url_for("list_contents"))


@app.route("/content-categories/<int:category_id>/bulk", methods=["GET", "POST"])
def bulk_edit_category_contents(category_id: int) -> Any:
    session = SessionLocal()
    category = session.get(ContentCategory, category_id)
    if not category:
        flash("未找到分类", "danger")
        return redirect(url_for("list_contents"))

    if request.method == "POST":
        raw_text = request.form.get("bulk_text", "")
        lines = [line.strip() for line in raw_text.splitlines()]
        new_texts: list[str] = []
        unique_texts: set[str] = set()

        for line in lines:
            if not line:
                continue
            if len(line) > 50:
                flash("每条关注内容不能超过50个字符", "danger")
                return render_template(
                    "contents/bulk_edit.html",
                    category=category,
                    bulk_text=raw_text,
                )
            if line in unique_texts:
                continue
            unique_texts.add(line)
            new_texts.append(line)

        existing_by_text = {content.text: content for content in list(category.contents)}

        for text, content in existing_by_text.items():
            if text not in unique_texts:
                session.delete(content)

        for text in new_texts:
            if text not in existing_by_text:
                session.add(WatchContent(text=text, category=category))

        try:
            session.commit()
            flash("分类关注内容已更新", "success")
            return redirect(url_for("list_contents", category_id=category.id))
        except Exception:  # noqa: BLE001
            session.rollback()
            flash("保存关注内容时发生错误，请检查是否存在重复记录", "danger")
            return render_template(
                "contents/bulk_edit.html",
                category=category,
                bulk_text=raw_text,
            )

    bulk_text = "\n".join(content.text for content in sorted(category.contents, key=lambda c: c.created_at))
    return render_template("contents/bulk_edit.html", category=category, bulk_text=bulk_text)


@app.route("/contents/new", methods=["GET", "POST"])
def create_content() -> Any:
    session = SessionLocal()
    categories = (
        session.query(ContentCategory)
        .options(selectinload(ContentCategory.contents))
        .order_by(ContentCategory.name)
        .all()
    )
    selected_category_id = request.args.get("category_id", type=int)

    if request.method == "POST":
        text = request.form.get("text", "").strip()
        category_id = request.form.get("category_id", type=int)
        if not text:
            flash("请输入关注内容", "danger")
        elif len(text) > 50:
            flash("关注内容不能超过50个字符", "danger")
        elif not category_id:
            flash("请选择分类", "danger")
        else:
            category = session.get(ContentCategory, category_id)
            if not category:
                flash("分类不存在", "danger")
            else:
                content = WatchContent(text=text, category=category)
                session.add(content)
                try:
                    session.commit()
                    flash("关注内容已添加", "success")
                    return redirect(url_for("list_contents", category_id=category.id))
                except Exception:  # noqa: BLE001
                    session.rollback()
                    flash("该分类下已存在相同的关注内容", "warning")
        selected_category_id = category_id or selected_category_id

    return render_template(
        "contents/form.html",
        categories=categories,
        selected_category_id=selected_category_id,
    )


@app.route("/contents/<int:content_id>/delete", methods=["POST"])
def delete_content(content_id: int) -> Any:
    session = SessionLocal()
    content = session.get(WatchContent, content_id)
    category_id = content.category_id if content else None
    if content:
        session.delete(content)
        session.commit()
        flash("关注内容已删除", "success")
    return redirect(url_for("list_contents", category_id=category_id))


@app.route("/notifications", methods=["GET", "POST"])
def manage_notifications() -> Any:
    session = SessionLocal()
    try:
        email_setting = (
            session.query(NotificationSetting)
            .filter(NotificationSetting.channel == "email")
            .one_or_none()
        )
        dingtalk_setting = (
            session.query(NotificationSetting)
            .filter(NotificationSetting.channel == "dingtalk")
            .one_or_none()
        )

        page = request.args.get("page", 1, type=int)
        per_page = 10

        if request.method == "POST":
            config_type = request.form.get("config_type")
            action = request.form.get("action", "save")
            if config_type == "email" and action == "test":
                recipient = request.form.get("test_recipient", "").strip()
                fallback_recipient = None
                if email_setting:
                    fallback_recipient = (
                        email_setting.smtp_sender or email_setting.smtp_username
                    )
                target_recipient = recipient or fallback_recipient
                if not target_recipient:
                    flash("请先保存 SMTP 配置或填写测试收件人", "danger")
                    record_notification_log(
                        session,
                        channel="email",
                        status="failed",
                        target=None,
                        message="测试邮件发送失败：未提供收件人",
                    )
                else:
                    try:
                        send_email(
                            subject="【测试】政策监控通知",
                            recipients=[target_recipient],
                            html_body="<p>这是一封测试邮件，用于验证通知配置是否生效。</p>",
                            text_body="这是一封测试邮件，用于验证通知配置是否生效。",
                        )
                    except NotificationConfigError as exc:
                        flash(f"测试邮件发送失败：{exc}", "danger")
                        record_notification_log(
                            session,
                            channel="email",
                            status="failed",
                            target=target_recipient,
                            message=str(exc) or "测试邮件发送失败",
                        )
                    except Exception as exc:  # noqa: BLE001
                        flash(f"测试邮件发送失败：{exc}", "danger")
                        record_notification_log(
                            session,
                            channel="email",
                            status="failed",
                            target=target_recipient,
                            message=str(exc) or "测试邮件发送失败",
                        )
                    else:
                        flash(
                            f"测试邮件已发送至 {target_recipient}",
                            "success",
                        )
                        record_notification_log(
                            session,
                            channel="email",
                            status="success",
                            target=target_recipient,
                            message="测试邮件发送成功",
                        )
                return redirect(url_for("manage_notifications"))
            if config_type == "dingtalk" and action == "test":
                try:
                    webhook_url = send_dingtalk_message(
                        {
                            "msgtype": "text",
                            "text": {"content": "【测试】政策监控钉钉通知已触发"},
                        }
                    )
                except NotificationConfigError as exc:
                    flash(f"测试钉钉通知失败：{exc}", "danger")
                    record_notification_log(
                        session,
                        channel="dingtalk",
                        status="failed",
                        target=(dingtalk_setting.webhook_url if dingtalk_setting else None),
                        message=str(exc) or "测试钉钉通知发送失败",
                    )
                except Exception as exc:  # noqa: BLE001
                    flash(f"测试钉钉通知失败：{exc}", "danger")
                    record_notification_log(
                        session,
                        channel="dingtalk",
                        status="failed",
                        target=(dingtalk_setting.webhook_url if dingtalk_setting else None),
                        message=str(exc) or "测试钉钉通知发送失败",
                    )
                else:
                    flash("测试钉钉通知已发送", "success")
                    record_notification_log(
                        session,
                        channel="dingtalk",
                        status="success",
                        target=webhook_url,
                        message="测试钉钉通知发送成功",
                    )
                return redirect(url_for("manage_notifications"))
            if config_type == "email":
                smtp_host = request.form.get("smtp_host", "").strip()
                smtp_port_raw = request.form.get("smtp_port", "").strip()
                smtp_username = request.form.get("smtp_username", "").strip()
                smtp_password = request.form.get("smtp_password", "").strip()
                smtp_sender = request.form.get("smtp_sender", "").strip()
                smtp_use_tls = request.form.get("smtp_use_tls") == "on"

                port_value: int | None = None
                has_error = False
                if smtp_port_raw:
                    try:
                        port_value = int(smtp_port_raw)
                    except ValueError:
                        has_error = True
                        flash("SMTP端口必须为数字", "danger")

                if not (smtp_host and smtp_username and smtp_password):
                    has_error = True
                    flash("请完整填写SMTP主机、账号和密码", "danger")

                if not has_error:
                    setting = email_setting or NotificationSetting(channel="email")
                    setting.smtp_host = smtp_host
                    setting.smtp_port = port_value if port_value is not None else 587
                    setting.smtp_username = smtp_username
                    setting.smtp_password = smtp_password
                    setting.smtp_sender = smtp_sender or smtp_username
                    setting.smtp_use_tls = smtp_use_tls
                    session.add(setting)
                    session.commit()
                    flash("SMTP配置已更新", "success")
                    return redirect(url_for("manage_notifications"))
            elif config_type == "dingtalk":
                webhook_url = request.form.get("webhook_url", "").strip()
                if not webhook_url:
                    flash("请填写钉钉Webhook地址", "danger")
                else:
                    setting = dingtalk_setting or NotificationSetting(channel="dingtalk")
                    setting.webhook_url = webhook_url
                    session.add(setting)
                    session.commit()
                    flash("钉钉配置已更新", "success")
                    return redirect(url_for("manage_notifications"))

        log_query = session.query(NotificationLog).order_by(NotificationLog.created_at.desc())
        total_logs = log_query.count()
        total_pages = max((total_logs + per_page - 1) // per_page, 1)
        if page < 1:
            page = 1
        if page > total_pages:
            page = total_pages
        logs = (
            log_query.offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        return render_template(
            "notifications/manage.html",
            email_setting=email_setting,
            dingtalk_setting=dingtalk_setting,
            notification_logs=logs,
            log_page=page,
            log_total_pages=total_pages,
            log_total=total_logs,
            log_per_page=per_page,
        )
    finally:
        session.close()


@app.route("/tasks")
def list_tasks() -> Any:
    session = SessionLocal()
    try:
        tasks = (
            session.query(MonitorTask)
            .options(
                selectinload(MonitorTask.watch_contents).selectinload(WatchContent.category),
                selectinload(MonitorTask.website),
                selectinload(MonitorTask.logs),
            )
            .order_by(MonitorTask.created_at.desc())
            .all()
        )
        websites = session.query(Website).all()
        local_timezone = get_local_timezone()
        task_rows: list[dict[str, Any]] = []

        for task in tasks:
            website = task.website
            interval = None
            if website and website.interval_minutes:
                interval = timedelta(minutes=website.interval_minutes)

            now_local = datetime.now(local_timezone)
            latest_log = task.logs[0] if task.logs else None
            is_running = bool(
                latest_log and latest_log.status == "running" and latest_log.run_finished_at is None
            )

            last_finished_log = next((log for log in task.logs if log.run_finished_at), None)
            last_run_time = task.last_run_at
            if last_finished_log and last_finished_log.run_finished_at:
                last_run_time = last_finished_log.run_finished_at

            next_run_at = None
            if task.is_active and interval:
                reference_time = last_run_time or task.created_at
                if reference_time:
                    reference_time_local = to_local(reference_time)
                else:
                    reference_time_local = None
                if reference_time_local is None:
                    reference_time_local = now_local
                next_run_at = reference_time_local + interval

            if next_run_at and next_run_at < now_local:
                next_run_at = now_local

            if last_finished_log:
                last_result_status = last_finished_log.status
            elif task.last_status:
                last_result_status = task.last_status
            else:
                last_result_status = None

            if last_result_status in {"success", "completed"}:
                last_result_label = "成功"
            elif last_result_status == "failed":
                last_result_label = "失败"
            else:
                last_result_label = "未执行"

            task_rows.append(
                {
                    "task": task,
                    "next_run_at": next_run_at,
                    "is_running": is_running,
                    "last_run_at": last_run_time,
                    "last_result_label": last_result_label,
                }
            )

        return render_template(
            "tasks/list.html",
            task_rows=task_rows,
            websites=websites,
        )
    finally:
        session.close()


@app.route("/tasks/new", methods=["GET", "POST"])
def create_task() -> Any:
    session = SessionLocal()
    websites = session.query(Website).all()
    categories = (
        session.query(ContentCategory)
        .options(selectinload(ContentCategory.contents))
        .order_by(ContentCategory.name)
        .all()
    )
    has_contents = any(category.contents for category in categories)
    form_data = {
        "name": "",
        "website_id": None,
        "notification_method": "email",
        "notification_email": "",
    }
    selected_content_ids: set[int] = set()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        website_id = int(request.form.get("website_id", "0") or 0)
        notification_method = request.form.get("notification_method", "email").strip() or "email"
        notification_email_raw = request.form.get("notification_email", "").strip()
        selected_content_ids = {int(content_id) for content_id in request.form.getlist("content_ids")}

        recipients = [email.strip() for email in notification_email_raw.replace(";", ",").split(",") if email.strip()]
        form_data = {
            "name": name,
            "website_id": website_id,
            "notification_method": notification_method,
            "notification_email": notification_email_raw,
        }

        if not name or not website_id:
            flash("请填写任务名称并选择网站", "danger")
        elif not selected_content_ids:
            flash("请至少选择一个关注内容", "danger")
        elif notification_method == "email" and not recipients:
            flash("请选择邮件通知时，请填写接收邮箱地址", "danger")
        else:
            task = MonitorTask(
                name=name,
                website_id=website_id,
                notification_method=notification_method,
                notification_email=", ".join(recipients) if notification_method == "email" else "",
            )
            for content_id in selected_content_ids:
                content = session.get(WatchContent, int(content_id))
                if content:
                    task.watch_contents.append(content)
            session.add(task)
            session.commit()
            flash("监控任务已创建", "success")
            return redirect(url_for("list_tasks"))
    return render_template(
        "tasks/form.html",
        websites=websites,
        categories=categories,
        has_contents=has_contents,
        form_data=form_data,
        selected_content_ids=selected_content_ids,
        task=None,
    )


@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
def edit_task(task_id: int) -> Any:
    session = SessionLocal()
    task = session.get(MonitorTask, task_id)
    if not task:
        flash("未找到任务", "danger")
        return redirect(url_for("list_tasks"))

    websites = session.query(Website).all()
    categories = (
        session.query(ContentCategory)
        .options(selectinload(ContentCategory.contents))
        .order_by(ContentCategory.name)
        .all()
    )
    has_contents = any(category.contents for category in categories)

    selected_content_ids: set[int] = {content.id for content in task.watch_contents}
    form_data = {
        "name": task.name,
        "website_id": task.website_id,
        "notification_method": task.notification_method,
        "notification_email": task.notification_email or "",
    }

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        website_id = int(request.form.get("website_id", "0") or 0)
        notification_method = request.form.get("notification_method", "email").strip() or "email"
        notification_email_raw = request.form.get("notification_email", "").strip()
        selected_content_ids = {int(content_id) for content_id in request.form.getlist("content_ids")}

        recipients = [
            email.strip()
            for email in notification_email_raw.replace(";", ",").split(",")
            if email.strip()
        ]
        form_data = {
            "name": name,
            "website_id": website_id,
            "notification_method": notification_method,
            "notification_email": notification_email_raw,
        }

        if not name or not website_id:
            flash("请填写任务名称并选择网站", "danger")
        elif not selected_content_ids:
            flash("请至少选择一个关注内容", "danger")
        elif notification_method == "email" and not recipients:
            flash("请选择邮件通知时，请填写接收邮箱地址", "danger")
        else:
            task.name = name
            task.website_id = website_id
            task.notification_method = notification_method
            task.notification_email = (
                ", ".join(recipients) if notification_method == "email" else ""
            )
            task.watch_contents.clear()
            for content_id in selected_content_ids:
                content = session.get(WatchContent, int(content_id))
                if content:
                    task.watch_contents.append(content)
            session.add(task)
            session.commit()
            flash("监控任务已更新", "success")
            return redirect(url_for("list_tasks"))

    return render_template(
        "tasks/form.html",
        websites=websites,
        categories=categories,
        has_contents=has_contents,
        form_data=form_data,
        selected_content_ids=selected_content_ids,
        task=task,
    )


@app.route("/tasks/<int:task_id>/toggle", methods=["POST"])
def toggle_task(task_id: int) -> Any:
    session = SessionLocal()
    task = session.get(MonitorTask, task_id)
    if task:
        task.is_active = not task.is_active
        session.add(task)
        session.commit()
        flash("任务状态已更新", "success")
    return redirect(url_for("list_tasks"))


@app.route("/tasks/<int:task_id>")
def view_task(task_id: int) -> Any:
    session = SessionLocal()
    task = session.get(MonitorTask, task_id)
    if not task:
        flash("未找到任务", "danger")
        return redirect(url_for("list_tasks"))
    page = request.args.get("page", default=1, type=int)
    if page < 1:
        page = 1

    per_page = 5

    log_query = (
        session.query(CrawlLog)
        .options(joinedload(CrawlLog.entries))
        .filter(CrawlLog.task_id == task_id)
        .order_by(CrawlLog.run_started_at.desc())
    )

    total_logs = log_query.count()
    total_pages = (total_logs + per_page - 1) // per_page or 1
    if page > total_pages:
        page = total_pages

    logs = (
        log_query.offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    results = (
        session.query(CrawlResult)
        .filter(CrawlResult.task_id == task_id)
        .order_by(CrawlResult.created_at.desc())
        .limit(20)
        .all()
    )
    active_log = next((log for log in logs if log.status == "running"), None)
    next_run_at: datetime | None = None
    if not active_log:
        if task.website and task.website.interval_minutes:
            interval = timedelta(minutes=task.website.interval_minutes)
        else:
            interval = timedelta(minutes=60)
        if task.is_active and task.website:
            if task.last_run_at:
                next_run_at = task.last_run_at + interval
            else:
                next_run_at = datetime.utcnow() + interval
    return render_template(
        "tasks/detail.html",
        task=task,
        logs=logs,
        results=results,
        threshold=SIMILARITY_THRESHOLD,
        active_log=active_log,
        next_run_at=next_run_at,
        page=page,
        total_pages=total_pages,
        total_logs=total_logs,
        per_page=per_page,
    )


@app.route("/tasks/<int:task_id>/run-now", methods=["POST"])
def run_task_now(task_id: int) -> Any:
    session = SessionLocal()
    try:
        task = session.get(MonitorTask, task_id)
        if not task:
            flash("未找到任务", "danger")
            return redirect(url_for("list_tasks"))

        running_log = (
            session.query(CrawlLog)
            .filter(CrawlLog.task_id == task_id, CrawlLog.status == "running")
            .first()
        )
        if running_log:
            flash("任务正在执行中，请稍后再试", "warning")
            return redirect(url_for("view_task", task_id=task_id))

        threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
        flash("已开始立即执行任务", "success")
        return redirect(url_for("view_task", task_id=task_id))
    finally:
        session.close()


@app.route("/tasks/<int:task_id>/logs/<int:log_id>/entries")
def stream_task_log_entries(task_id: int, log_id: int) -> Any:
    session = SessionLocal()
    log = (
        session.query(CrawlLog)
        .options(selectinload(CrawlLog.entries))
        .filter(CrawlLog.task_id == task_id, CrawlLog.id == log_id)
        .first()
    )
    if not log:
        session.close()
        return jsonify({"error": "日志不存在"}), 404

    after_id = request.args.get("after", type=int)
    entries = [
        {
            "id": entry.id,
            "created_at": format_local_datetime(entry.created_at) if entry.created_at else None,
            "level": entry.level,
            "message": entry.message,
        }
        for entry in log.entries
        if after_id is None or entry.id > after_id
    ]
    response = {
        "log_id": log.id,
        "status": log.status,
        "run_started_at": format_local_datetime(log.run_started_at) if log.run_started_at else None,
        "run_finished_at": format_local_datetime(log.run_finished_at) if log.run_finished_at else None,
        "message": log.message,
        "entries": entries,
    }
    session.close()
    return jsonify(response)


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id: int) -> Any:
    session = SessionLocal()
    task = session.get(MonitorTask, task_id)
    if task:
        session.delete(task)
        session.commit()
        flash("任务已删除", "success")
    return redirect(url_for("list_tasks"))


@app.route("/results")
def list_results() -> Any:
    session = SessionLocal()
    query = session.query(CrawlLog).join(CrawlLog.task)

    task_id = request.args.get("task_id", type=int)
    website_id = request.args.get("website_id", type=int)
    status = request.args.get("status", type=str)
    start_date_raw = request.args.get("start_date", "") or ""
    end_date_raw = request.args.get("end_date", "") or ""

    if task_id:
        query = query.filter(CrawlLog.task_id == task_id)
    if website_id:
        query = query.join(Website, MonitorTask.website).filter(Website.id == website_id)
    if status:
        query = query.filter(CrawlLog.status == status)

    start_date = start_date_raw
    end_date = end_date_raw

    if start_date_raw:
        try:
            start_dt = datetime.fromisoformat(start_date_raw)
            query = query.filter(CrawlLog.run_started_at >= start_dt)
        except ValueError:
            flash("开始日期格式不正确", "warning")
            start_date = ""
    if end_date_raw:
        try:
            end_dt = datetime.fromisoformat(end_date_raw)
            query = query.filter(CrawlLog.run_started_at <= end_dt)
        except ValueError:
            flash("结束日期格式不正确", "warning")
            end_date = ""

    query = query.order_by(CrawlLog.run_started_at.desc())

    page = int(request.args.get("page", "1") or 1)
    per_page = 10
    total = query.count()
    logs = (
        query.options(
            joinedload(CrawlLog.task).joinedload(MonitorTask.website),
            selectinload(CrawlLog.entries),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    total_pages = (total + per_page - 1) // per_page

    tasks = session.query(MonitorTask).all()
    websites = session.query(Website).all()

    status_choices = {
        "running": "执行中",
        "success": "成功",
        "completed": "已完成",
        "failed": "失败",
    }

    return render_template(
        "results/list.html",
        logs=logs,
        tasks=tasks,
        websites=websites,
        status_choices=status_choices,
        page=page,
        total_pages=total_pages,
        query_args={
            "task_id": str(task_id or ""),
            "website_id": str(website_id or ""),
            "status": status or "",
            "start_date": start_date,
            "end_date": end_date,
        },
    )


if __name__ == "__main__":
    init_db()
    scheduler.start()
    app.run(host="0.0.0.0", port=5000, debug=True)
