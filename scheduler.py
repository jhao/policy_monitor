from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from database import SessionLocal
from models import MonitorTask
from sqlalchemy.orm import selectinload
from crawler import run_task

LOGGER = logging.getLogger(__name__)


class MonitorScheduler:
    def __init__(self, poll_interval: int = 60) -> None:
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        LOGGER.info("Monitor scheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        LOGGER.info("Monitor scheduler stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._process_tasks()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Scheduler encountered an error")
            time.sleep(self.poll_interval)

    def _process_tasks(self) -> None:
        session = SessionLocal()
        try:
            tasks = (
                session.query(MonitorTask)
                .options(selectinload(MonitorTask.website))
                .filter(MonitorTask.is_active.is_(True))
                .all()
            )
            now = datetime.utcnow()
            scheduled_task_ids: list[int] = []
            for task in tasks:
                website = task.website
                if not website:
                    continue
                interval = timedelta(minutes=website.interval_minutes or 60)
                if not task.last_run_at or now - task.last_run_at >= interval:
                    scheduled_task_ids.append(task.id)
        finally:
            session.close()

        for task_id in scheduled_task_ids:
            LOGGER.info("Scheduling task %s", task_id)
            run_task(task_id)
