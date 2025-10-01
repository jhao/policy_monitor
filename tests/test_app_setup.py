import types
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


class EnsureSetupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.original_init_db = app.init_db
        self.original_scheduler = app.scheduler
        app._setup_complete = False

    def tearDown(self) -> None:
        app.init_db = self.original_init_db
        app.scheduler = self.original_scheduler
        app._setup_complete = False

    def test_ensure_setup_runs_only_once(self) -> None:
        calls: list[str] = []

        def fake_init_db() -> None:
            calls.append("init")

        class DummyScheduler:
            def __init__(self) -> None:
                self.starts = 0

            def start(self) -> None:
                self.starts += 1

        app.init_db = fake_init_db
        app.scheduler = DummyScheduler()

        app.ensure_setup()
        self.assertEqual(calls.count("init"), 1)
        self.assertEqual(app.scheduler.starts, 1)
        self.assertTrue(app._setup_complete)

        app.ensure_setup()
        self.assertEqual(calls.count("init"), 1)
        self.assertEqual(app.scheduler.starts, 1)

    def test_setup_before_request_invokes_ensure_setup(self) -> None:
        marker = types.SimpleNamespace(count=0)

        def fake_ensure_setup() -> None:
            marker.count += 1

        original = app.ensure_setup
        app.ensure_setup = fake_ensure_setup
        try:
            app.setup_before_request()
        finally:
            app.ensure_setup = original

        self.assertEqual(marker.count, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
