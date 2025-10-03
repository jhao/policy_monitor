from __future__ import annotations

import logging
import random
import threading

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

LOGGER = logging.getLogger(__name__)

__all__ = ["ProxyConfigService", "proxy_manager"]

from database import SessionLocal, engine
from models import ProxyEndpoint


class ProxyConfigService:
    """Proxy configuration service that supports round-robin rotation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proxies: list[dict[str, str]] = []
        self._index = 0
        self.reload()

    def reload(self) -> None:
        """Reload proxy information from the database."""

        proxies: list[dict[str, str]] = []

        try:
            inspector = inspect(engine)
            if not inspector.has_table(ProxyEndpoint.__tablename__):
                LOGGER.debug("代理配置表尚未创建，暂不加载代理配置")
            else:
                session = SessionLocal()
                try:
                    entries = (
                        session.execute(
                            select(ProxyEndpoint)
                            .where(ProxyEndpoint.is_active.is_(True))
                            .order_by(ProxyEndpoint.created_at)
                        )
                        .scalars()
                        .all()
                    )
                    for entry in entries:
                        mapping = entry.to_requests_mapping()
                        if mapping:
                            proxies.append(mapping)
                finally:
                    session.close()
        except SQLAlchemyError as exc:
            LOGGER.warning("加载代理配置失败: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning("加载代理配置时出现未知错误: %s", exc)

        random.shuffle(proxies)
        with self._lock:
            self._proxies = proxies
            self._index = 0

    def get_next_proxy(self) -> dict[str, str] | None:
        """Return the next proxy configuration, or ``None`` if unavailable."""

        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index].copy()
            self._index = (self._index + 1) % len(self._proxies)
            return proxy

    def has_proxies(self) -> bool:
        with self._lock:
            return bool(self._proxies)


proxy_manager = ProxyConfigService()

