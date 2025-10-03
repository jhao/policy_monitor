import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import record_notification_log  # noqa: E402
from models import NotificationLog  # noqa: E402


class DummySession:
    def __init__(self) -> None:
        self.added: list[NotificationLog] = []
        self.commit_count = 0

    def add(self, obj: NotificationLog) -> None:  # pragma: no cover - simple delegator
        self.added.append(obj)

    def commit(self) -> None:  # pragma: no cover - simple delegator
        self.commit_count += 1


class NotificationLogPayloadTestCase(unittest.TestCase):
    def test_record_notification_log_stores_payload_json(self) -> None:
        payload = {
            "format": "email",
            "subject": "测试",
            "html": "<p>示例</p>",
            "recipients": ["user@example.com"],
        }
        session = DummySession()

        record_notification_log(
            session,
            channel="email",
            status="success",
            target="user@example.com",
            message="发送成功",
            payload=payload,
        )

        self.assertEqual(session.commit_count, 1)
        self.assertEqual(len(session.added), 1)
        stored = session.added[0]
        self.assertIsInstance(stored, NotificationLog)
        self.assertIsNotNone(stored.payload)
        parsed = json.loads(stored.payload or "{}")
        self.assertEqual(parsed.get("format"), "email")
        self.assertEqual(parsed.get("subject"), "测试")
        self.assertEqual(parsed.get("recipients"), ["user@example.com"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
