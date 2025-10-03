from pathlib import Path

from database import SessionLocal, engine, init_db
from models import ProxyEndpoint
from proxy_service import ProxyConfigService


def setup_function(function):
    engine.dispose()
    db_path = Path("data.db")
    if db_path.exists():
        db_path.unlink()
    init_db()


def teardown_function(function):
    SessionLocal.remove()
    engine.dispose()
    db_path = Path("data.db")
    if db_path.exists():
        db_path.unlink()


def test_default_proxy_seed_data_present():
    session = SessionLocal()
    try:
        proxies = session.query(ProxyEndpoint).all()
        assert proxies, "Expected default proxy endpoints to be seeded"
        assert any(proxy.is_active for proxy in proxies)
        assert all(proxy.name for proxy in proxies)
    finally:
        session.close()


def test_proxy_service_reflects_database_updates():
    session = SessionLocal()
    try:
        # create a dedicated proxy for this test
        test_proxy = ProxyEndpoint(
            name="测试代理",
            http_url="http://127.0.0.1:9999",
            https_url="http://127.0.0.1:9999",
            is_active=True,
        )
        session.add(test_proxy)
        session.commit()

        service = ProxyConfigService()
        service.reload()

        active_proxies = (
            session.query(ProxyEndpoint)
            .filter(ProxyEndpoint.is_active.is_(True))
            .all()
        )
        expected = [proxy.to_requests_mapping() for proxy in active_proxies if proxy.to_requests_mapping()]
        collected = []
        for _ in range(len(expected)):
            proxy_mapping = service.get_next_proxy()
            assert proxy_mapping is not None
            collected.append(proxy_mapping)

        assert any(
            mapping.get("http") == "http://127.0.0.1:9999" for mapping in collected
        )

        # disable the test proxy and ensure service reload reflects it
        test_proxy.is_active = False
        session.commit()
        service.reload()
        # the service should still rotate without raising errors
        for _ in range(max(1, len(expected) - 1)):
            service.get_next_proxy()
    finally:
        if "test_proxy" in locals():
            try:
                session.delete(test_proxy)
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
        session.close()
