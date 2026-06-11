import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from python_scripts.health_store import load_health, temporary_disabled_models, upsert_health


class TestHealthStoreDisableUntil(unittest.TestCase):
    def test_auth_failure_is_not_temporary_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'health.json'
            now_ts = int(datetime(2026, 6, 10, 22, 30).astimezone().timestamp())

            upsert_health('custom-1', 'gpt-5.2', False, 'auth', path=path, now_ts=now_ts)

            entry = load_health(path)['custom-1/gpt-5.2']
            self.assertIsNone(entry['disabled_until'])
            self.assertIsNone(entry['disabled_reason'])
            self.assertNotIn('custom-1/gpt-5.2', temporary_disabled_models(path, now_ts=now_ts))

    def test_rate_limit_failure_is_temporary_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'health.json'
            now_ts = int(datetime(2026, 6, 10, 22, 30).astimezone().timestamp())

            upsert_health('custom-1', 'glm-5', False, 'rate_limit', path=path, now_ts=now_ts)

            entry = load_health(path)['custom-1/glm-5']
            disabled_until = datetime.fromtimestamp(entry['disabled_until']).astimezone()
            self.assertEqual(disabled_until.hour, 0)
            self.assertEqual(disabled_until.minute, 0)
            self.assertEqual(entry['disabled_reason'], 'rate_limit')
            self.assertIn('custom-1/glm-5', temporary_disabled_models(path, now_ts=now_ts))


if __name__ == '__main__':
    unittest.main()
