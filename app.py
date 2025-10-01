from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from crawler import SIMILARITY_THRESHOLD
from database import SessionLocal, init_db
from models import (
    ContentCategory,
    CrawlLog,
    CrawlResult,
    MonitorTask,
    WatchContent,
    Website,
)
from scheduler import MonitorScheduler

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config.update(
    SECRET_KEY="monitor-secret-key",
    SMTP_HOST="smtp.example.com",
    SMTP_PORT="587",
    SMTP_USERNAME="user@example.com",
    SMTP_PASSWORD="password",
    SMTP_USE_TLS="true",
    SMTP_SENDER="monitor@example.com",
)

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


@app.route("/tasks")
def list_tasks() -> Any:
    session = SessionLocal()
    tasks = (
        session.query(MonitorTask)
        .options(
            selectinload(MonitorTask.watch_contents).selectinload(WatchContent.category),
            selectinload(MonitorTask.website),
        )
        .order_by(MonitorTask.created_at.desc())
        .all()
    )
    websites = session.query(Website).all()
    return render_template("tasks/list.html", tasks=tasks, websites=websites)


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
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        website_id = int(request.form.get("website_id", "0") or 0)
        notification_email = request.form.get("notification_email", "").strip()
        selected_content_ids = request.form.getlist("content_ids")
        if not (name and website_id and notification_email and selected_content_ids):
            flash("请完整填写任务信息", "danger")
        else:
            task = MonitorTask(name=name, website_id=website_id, notification_email=notification_email)
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
