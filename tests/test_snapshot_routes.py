import unittest
from datetime import datetime
from pathlib import Path

import app
from database import Base, SessionLocal, engine
from models import Website


class WebsiteSnapshotRoutesTestCase(unittest.TestCase):
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

    def test_clear_snapshot_resets_state(self) -> None:
        session = SessionLocal()
        website = Website(
            name="Example",
            url="https://example.com",
            last_snapshot="{}",
            last_fetched_at=datetime.utcnow(),
        )
        session.add(website)
        session.commit()
        website_id = website.id
        session.close()

        response = self.client.post(
            f"/websites/{website_id}/snapshot/clear",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)

        session = SessionLocal()
        refreshed = session.get(Website, website_id)
        session.close()

        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertIsNone(refreshed.last_snapshot)
        self.assertIsNone(refreshed.last_fetched_at)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

