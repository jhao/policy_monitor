import unittest
from pathlib import Path

from database import Base, SessionLocal, engine
import app
from models import ContentCategory, WatchContent


class ContentCategoryRoutesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data.db")
        engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()
        Base.metadata.create_all(bind=engine)
        self.original_setup_flag = app._setup_complete
        app._setup_complete = True
        app.app.testing = True
        self.client = app.app.test_client()

    def tearDown(self) -> None:
        SessionLocal.remove()
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        if self.db_path.exists():
            self.db_path.unlink()
        app._setup_complete = self.original_setup_flag

    def test_bulk_edit_replaces_category_contents(self) -> None:
        response = self.client.post(
            "/content-categories",
            data={"name": "政策解读"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        session = SessionLocal()
        category = session.query(ContentCategory).filter_by(name="政策解读").one()
        session.close()

        response = self.client.post(
            f"/content-categories/{category.id}/bulk",
            data={"bulk_text": "宏观政策\n产业规划"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        session = SessionLocal()
        contents = (
            session.query(WatchContent)
            .filter(WatchContent.category_id == category.id)
            .order_by(WatchContent.text)
            .all()
        )
        session.close()
        self.assertEqual([item.text for item in contents], ["产业规划", "宏观政策"])

        response = self.client.post(
            f"/content-categories/{category.id}/bulk",
            data={"bulk_text": "宏观政策"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        session = SessionLocal()
        remaining = session.query(WatchContent).filter_by(category_id=category.id).all()
        session.close()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].text, "宏观政策")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
