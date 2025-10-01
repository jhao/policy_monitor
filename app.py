from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for

from crawler import SIMILARITY_THRESHOLD
from database import SessionLocal, init_db
from models import CrawlLog, CrawlResult, MonitorTask, NotificationSetting, WatchContent, Website
from scheduler import MonitorScheduler

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config.update(SECRET_KEY="monitor-secret-key")

scheduler = MonitorScheduler()
_setup_complete = False


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
    contents = session.query(WatchContent).order_by(WatchContent.created_at.desc()).all()
    return render_template("contents/list.html", contents=contents)


@app.route("/contents/new", methods=["GET", "POST"])
def create_content() -> Any:
    session = SessionLocal()
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if not text:
            flash("请输入关注内容", "danger")
        elif len(text) > 50:
            flash("关注内容不能超过50个字符", "danger")
        else:
            content = WatchContent(text=text)
            session.add(content)
            try:
                session.commit()
                flash("关注内容已添加", "success")
                return redirect(url_for("list_contents"))
            except Exception:  # noqa: BLE001
                session.rollback()
                flash("关注内容已存在", "warning")
    return render_template("contents/form.html")


@app.route("/contents/<int:content_id>/delete", methods=["POST"])
def delete_content(content_id: int) -> Any:
    session = SessionLocal()
    content = session.get(WatchContent, content_id)
    if content:
        session.delete(content)
        session.commit()
        flash("关注内容已删除", "success")
    return redirect(url_for("list_contents"))


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
    tasks = session.query(MonitorTask).order_by(MonitorTask.created_at.desc()).all()
    websites = session.query(Website).all()
    contents = session.query(WatchContent).all()
    return render_template("tasks/list.html", tasks=tasks, websites=websites, contents=contents)


@app.route("/tasks/new", methods=["GET", "POST"])
def create_task() -> Any:
    session = SessionLocal()
    websites = session.query(Website).all()
    contents = session.query(WatchContent).all()
    form_data: dict[str, Any] = {}
    selected_content_ids: list[int] = []
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        website_id = int(request.form.get("website_id", "0") or 0)
        notification_method = request.form.get("notification_method", "email").strip() or "email"
        notification_email_raw = request.form.get("notification_email", "").strip()
        selected_content_ids = [int(content_id) for content_id in request.form.getlist("content_ids")]

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
        contents=contents,
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
    contents = session.query(WatchContent).all()
    selected_content_ids = [content.id for content in task.watch_contents]
    form_data: dict[str, Any] = {
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
        selected_content_ids = [int(content_id) for content_id in request.form.getlist("content_ids")]
        recipients = [email.strip() for email in notification_email_raw.replace(";", ",").split(",") if email.strip()]

        form_data.update(
            {
                "name": name,
                "website_id": website_id,
                "notification_method": notification_method,
                "notification_email": notification_email_raw,
            }
        )

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
            task.notification_email = ", ".join(recipients) if notification_method == "email" else ""
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
        contents=contents,
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
    logs = session.query(CrawlLog).filter(CrawlLog.task_id == task_id).order_by(CrawlLog.run_started_at.desc()).all()
    results = (
        session.query(CrawlResult)
        .filter(CrawlResult.task_id == task_id)
        .order_by(CrawlResult.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template("tasks/detail.html", task=task, logs=logs, results=results, threshold=SIMILARITY_THRESHOLD)


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
    contents = session.query(WatchContent).all()

    return render_template(
        "results/list.html",
        results=results,
        tasks=tasks,
        websites=websites,
        contents=contents,
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
