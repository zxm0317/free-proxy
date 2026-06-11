import unittest

from python_scripts.provider_transport import HttpxTransport


class TestHttpxTransportTimeout(unittest.TestCase):
    def test_httpx_timeout_uses_requested_read_timeout(self):
        timeout = HttpxTransport._httpx_timeout(12)

        self.assertEqual(timeout.connect, 12)
        self.assertEqual(timeout.read, 12)
        self.assertEqual(timeout.write, 12)
        self.assertEqual(timeout.pool, 5)

    def test_httpx_timeout_keeps_connect_and_write_cap(self):
        timeout = HttpxTransport._httpx_timeout(45)

        self.assertEqual(timeout.connect, 15)
        self.assertEqual(timeout.read, 45)
        self.assertEqual(timeout.write, 15)
        self.assertEqual(timeout.pool, 5)


if __name__ == "__main__":
    unittest.main()
