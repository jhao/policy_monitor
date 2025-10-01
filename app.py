from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from crawler import SIMILARITY_THRESHOLD, run_task
from database import SessionLocal, init_db
from models import (
    ContentCategory,
    CrawlLog,
    CrawlResult,
    MonitorTask,
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


@app.route("/websites/new", methods=["GET", "POST"])
def create_website() -> Any:
    session = SessionLocal()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        url = request.form.get("url", "").strip()
        interval = int(request.form.get("interval", "60") or 60)
        fetch_subpages = bool(request.form.get("fetch_subpages"))
        if not name or not url:
            flash("请输入网站名称和URL", "danger")
        else:
            website = Website(
                name=name,
                url=url,
                interval_minutes=interval,
                fetch_subpages=fetch_subpages,
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

    if request.method == "POST":
        config_type = request.form.get("config_type")
        if config_type == "email":
            smtp_host = request.form.get("smtp_host", "").strip()
            smtp_port_raw = request.form.get("smtp_port", "").strip()
            smtp_username = request.form.get("smtp_username", "").strip()
            smtp_password = request.form.get("smtp_password", "").strip()
            smtp_sender = request.form.get("smtp_sender", "").strip()
            smtp_use_tls = request.form.get("smtp_use_tls") == "on"

            port_value: int | None = None
            if smtp_port_raw:
                try:
                    port_value = int(smtp_port_raw)
                except ValueError:
                    flash("SMTP端口必须为数字", "danger")
                    return render_template(
                        "notifications/manage.html",
                        email_setting=email_setting,
                        dingtalk_setting=dingtalk_setting,
                    )

            if not (smtp_host and smtp_username and smtp_password):
                flash("请完整填写SMTP主机、账号和密码", "danger")
            else:
                setting = email_setting or NotificationSetting(channel="email")
                setting.smtp_host = smtp_host
                setting.smtp_port = port_value if port_value is not None else 587
                setting.smtp_username = smtp_username
                setting.smtp_password = smtp_password
                setting.smtp_sender = smtp_sender or smtp_username
                setting.smtp_use_tls = smtp_use_tls
                session.add(setting)
                session.commit()
                email_setting = setting
                flash("SMTP配置已更新", "success")
        elif config_type == "dingtalk":
            webhook_url = request.form.get("webhook_url", "").strip()
            if not webhook_url:
                flash("请填写钉钉Webhook地址", "danger")
            else:
                setting = dingtalk_setting or NotificationSetting(channel="dingtalk")
                setting.webhook_url = webhook_url
                session.add(setting)
                session.commit()
                dingtalk_setting = setting
                flash("钉钉配置已更新", "success")

    return render_template(
        "notifications/manage.html",
        email_setting=email_setting,
        dingtalk_setting=dingtalk_setting,
    )


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
    logs = (
        session.query(CrawlLog)
        .options(joinedload(CrawlLog.entries))
        .filter(CrawlLog.task_id == task_id)
        .order_by(CrawlLog.run_started_at.desc())
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
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "level": entry.level,
            "message": entry.message,
        }
        for entry in log.entries
        if after_id is None or entry.id > after_id
    ]
    response = {
        "log_id": log.id,
        "status": log.status,
        "run_started_at": log.run_started_at.isoformat() if log.run_started_at else None,
        "run_finished_at": log.run_finished_at.isoformat() if log.run_finished_at else None,
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
    query = session.query(CrawlResult)

    task_id = request.args.get("task_id")
    website_id = request.args.get("website_id")
    content_id = request.args.get("content_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if task_id:
        query = query.filter(CrawlResult.task_id == int(task_id))
    if website_id:
        query = query.filter(CrawlResult.website_id == int(website_id))
    if content_id:
        query = query.filter(CrawlResult.content_id == int(content_id))
    if start_date:
        query = query.filter(CrawlResult.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.filter(CrawlResult.created_at <= datetime.fromisoformat(end_date))

    query = query.options(
        selectinload(CrawlResult.task),
        selectinload(CrawlResult.website),
        selectinload(CrawlResult.content).selectinload(WatchContent.category),
    )

    page = int(request.args.get("page", "1") or 1)
    per_page = 10
    total = query.count()
    results = (
        query.order_by(CrawlResult.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    total_pages = (total + per_page - 1) // per_page

    tasks = session.query(MonitorTask).all()
    websites = session.query(Website).all()
    categories = (
        session.query(ContentCategory)
        .options(selectinload(ContentCategory.contents))
        .order_by(ContentCategory.name)
        .all()
    )

    return render_template(
        "results/list.html",
        results=results,
        tasks=tasks,
        websites=websites,
        categories=categories,
        page=page,
        total_pages=total_pages,
        query_args={
            "task_id": task_id or "",
            "website_id": website_id or "",
            "content_id": content_id or "",
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


if __name__ == "__main__":
    init_db()
    scheduler.start()
    app.run(host="0.0.0.0", port=5000, debug=True)
