from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


monitor_task_contents = Table(
    "monitor_task_contents",
    Base.metadata,
    Column("task_id", ForeignKey("monitor_tasks.id"), primary_key=True),
    Column("content_id", ForeignKey("watch_contents.id"), primary_key=True),
)


class Website(Base):
    __tablename__ = "websites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    fetch_subpages: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_proxy: Mapped[bool] = mapped_column(Boolean, default=False)
    proxy_request_interval: Mapped[int] = mapped_column(Integer, default=0)
    proxy_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title_selector_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_selector_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_area_selector_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_json_api: Mapped[bool] = mapped_column(Boolean, default=False)
    api_list_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_title_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_url_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_url_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_detail_url_base: Mapped[str | None] = mapped_column(Text, nullable=True)

    tasks: Mapped[List["MonitorTask"]] = relationship("MonitorTask", back_populates="website")


class ContentCategory(Base):
    __tablename__ = "content_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    contents: Mapped[List["WatchContent"]] = relationship(
        "WatchContent",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="WatchContent.created_at.desc()",
    )


class WatchContent(Base):
    __tablename__ = "watch_contents"
    __table_args__ = (
        UniqueConstraint("category_id", "text", name="uq_content_category_text"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    category_id: Mapped[int] = mapped_column(ForeignKey("content_categories.id"), nullable=False)

    category: Mapped[ContentCategory] = relationship("ContentCategory", back_populates="contents")

    tasks: Mapped[List["MonitorTask"]] = relationship(
        "MonitorTask",
        secondary=monitor_task_contents,
        back_populates="watch_contents",
    )


class MonitorTask(Base):
    __tablename__ = "monitor_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id"), nullable=False)
    notification_method: Mapped[str] = mapped_column(String(50), default="email")
    notification_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    website: Mapped[Website] = relationship("Website", back_populates="tasks")
    watch_contents: Mapped[List[WatchContent]] = relationship(
        "WatchContent",
        secondary=monitor_task_contents,
        back_populates="tasks",
        lazy="joined",
    )
    logs: Mapped[List["CrawlLog"]] = relationship(
        "CrawlLog",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="desc(CrawlLog.run_started_at)",
    )
    results: Mapped[List["CrawlResult"]] = relationship(
        "CrawlResult",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="desc(CrawlResult.created_at)",
    )
    notification_logs: Mapped[List["NotificationLog"]] = relationship(
        "NotificationLog",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="desc(NotificationLog.created_at)",
    )


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    smtp_sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("monitor_tasks.id"), nullable=False)
    run_started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    run_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="running")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped[MonitorTask] = relationship("MonitorTask", back_populates="logs")
    entries: Mapped[List["CrawlLogDetail"]] = relationship(
        "CrawlLogDetail",
        back_populates="log",
        cascade="all, delete-orphan",
        order_by="CrawlLogDetail.created_at",
    )


class CrawlResult(Base):
    __tablename__ = "crawl_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("monitor_tasks.id"), nullable=False)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id"), nullable=False)
    content_id: Mapped[int | None] = mapped_column(ForeignKey("watch_contents.id"), nullable=True)
    discovered_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    link_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped[MonitorTask] = relationship("MonitorTask", back_populates="results")
    website: Mapped[Website] = relationship("Website")
    content: Mapped[WatchContent | None] = relationship("WatchContent")


class CrawlLogDetail(Base):
    __tablename__ = "crawl_log_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("crawl_logs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)

    log: Mapped[CrawlLog] = relationship("CrawlLog", back_populates="entries")


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("monitor_tasks.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped[MonitorTask | None] = relationship("MonitorTask", back_populates="notification_logs")


class ProxyEndpoint(Base):
    __tablename__ = "proxy_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    http_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    https_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    socks5_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ftp_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_requests_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if self.http_url:
            mapping["http"] = self.http_url
        if self.https_url:
            mapping["https"] = self.https_url
        if self.socks5_url:
            mapping["socks5"] = self.socks5_url
        if self.ftp_url:
            mapping["ftp"] = self.ftp_url
        return mapping

