from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base

DATABASE_URL = "sqlite:///data.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()


def init_db() -> None:
    """Create database tables."""
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        existing_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(websites)").fetchall()
        }

        website_alter_statements = {
            "title_selector_config": "ALTER TABLE websites ADD COLUMN title_selector_config TEXT",
            "content_selector_config": "ALTER TABLE websites ADD COLUMN content_selector_config TEXT",
            "content_area_selector_config": "ALTER TABLE websites ADD COLUMN content_area_selector_config TEXT",
            "is_json_api": "ALTER TABLE websites ADD COLUMN is_json_api BOOLEAN DEFAULT 0",
            "api_list_path": "ALTER TABLE websites ADD COLUMN api_list_path TEXT",
            "api_title_path": "ALTER TABLE websites ADD COLUMN api_title_path TEXT",
            "api_url_path": "ALTER TABLE websites ADD COLUMN api_url_path TEXT",
            "api_url_template": "ALTER TABLE websites ADD COLUMN api_url_template TEXT",
            "api_detail_url_base": "ALTER TABLE websites ADD COLUMN api_detail_url_base TEXT",
            "use_proxy": "ALTER TABLE websites ADD COLUMN use_proxy BOOLEAN DEFAULT 0",
            "proxy_request_interval": "ALTER TABLE websites ADD COLUMN proxy_request_interval INTEGER DEFAULT 0",
            "proxy_user_agent": "ALTER TABLE websites ADD COLUMN proxy_user_agent VARCHAR(255)",
        }

        for column_name, statement in website_alter_statements.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(statement)

        notification_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(notification_logs)").fetchall()
        }
        if "payload" not in notification_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notification_logs ADD COLUMN payload TEXT"
            )

    from models import ProxyEndpoint

    session = SessionLocal()
    try:
        if session.query(ProxyEndpoint).count() == 0:
            defaults = [
                {
                    "name": "本地示例代理",
                    "http_url": "http://127.0.0.1:7890",
                    "https_url": "http://127.0.0.1:7890",
                },
                {
                    "name": "备用代理 A",
                    "http_url": "http://192.0.2.10:8080",
                    "https_url": "http://192.0.2.10:8080",
                },
                {
                    "name": "备用代理 B",
                    "http_url": "http://198.51.100.23:3128",
                    "https_url": "http://198.51.100.23:3128",
                },
                {
                    "name": "备用代理 C",
                    "http_url": "http://203.0.113.45:8000",
                    "https_url": "http://203.0.113.45:8000",
                },
                {
                    "name": "备用代理 D",
                    "http_url": "http://203.0.113.99:9000",
                    "https_url": "http://203.0.113.99:9000",
                },
            ]
            for item in defaults:
                session.add(ProxyEndpoint(**item))
            session.commit()
    finally:
        session.close()
