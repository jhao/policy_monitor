import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import crawler  # noqa: E402


class TaskCancellationHelpersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.task_id = 12345
        with crawler._RUNNING_TASKS_LOCK:  # type: ignore[attr-defined]
            crawler._RUNNING_TASKS.pop(self.task_id, None)  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        with crawler._RUNNING_TASKS_LOCK:  # type: ignore[attr-defined]
            crawler._RUNNING_TASKS.pop(self.task_id, None)  # type: ignore[attr-defined]

    def test_request_stop_returns_false_when_not_running(self) -> None:
        self.assertFalse(crawler.request_stop_task(self.task_id))

    def test_request_stop_sets_event(self) -> None:
        event = threading.Event()
        with crawler._RUNNING_TASKS_LOCK:  # type: ignore[attr-defined]
            crawler._RUNNING_TASKS[self.task_id] = event  # type: ignore[attr-defined]
        self.assertTrue(crawler.request_stop_task(self.task_id))
        self.assertTrue(event.is_set())

    def test_is_task_running_reflects_registry(self) -> None:
        self.assertFalse(crawler.is_task_running(self.task_id))
        event = threading.Event()
        with crawler._RUNNING_TASKS_LOCK:  # type: ignore[attr-defined]
            crawler._RUNNING_TASKS[self.task_id] = event  # type: ignore[attr-defined]
        self.assertTrue(crawler.is_task_running(self.task_id))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
