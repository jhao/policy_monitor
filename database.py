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
        if "title_selector_config" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN title_selector_config TEXT"
            )
        if "content_selector_config" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN content_selector_config TEXT"
            )
        if "content_area_selector_config" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN content_area_selector_config TEXT"
            )
        if "is_json_api" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN is_json_api BOOLEAN DEFAULT 0"
            )
        if "api_list_path" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN api_list_path TEXT"
            )
        if "api_title_path" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN api_title_path TEXT"
            )
        if "api_url_path" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN api_url_path TEXT"
            )
        if "api_url_template" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN api_url_template TEXT"
            )
        if "api_detail_url_base" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE websites ADD COLUMN api_detail_url_base TEXT"
            )

        notification_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(notification_logs)").fetchall()
        }
        if "payload" not in notification_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notification_logs ADD COLUMN payload TEXT"
            )
