import importlib
import socket
import unittest


class GNewsTimeoutTest(unittest.TestCase):
    def test_importing_sets_socket_default_timeout(self) -> None:
        previous = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(None)
            import tools._gnews_timeout
            importlib.reload(tools._gnews_timeout)
            self.assertEqual(socket.getdefaulttimeout(), tools._gnews_timeout.GNEWS_SOCKET_TIMEOUT_SECONDS)
        finally:
            socket.setdefaulttimeout(previous)


if __name__ == "__main__":
    unittest.main()
