import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import auth


class _FakeConn:
    """Fake SQLAlchemy connection: returns queued results in call order and
    records every execute() call for assertions."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class HashTokenTest(unittest.TestCase):
    def test_is_deterministic_and_64_hex_chars(self) -> None:
        h1 = auth._hash_token("abc123")
        h2 = auth._hash_token("abc123")
        self.assertEqual(h1, h2)
        self.assertRegex(h1, r"^[0-9a-f]{64}$")

    def test_different_tokens_hash_differently(self) -> None:
        self.assertNotEqual(auth._hash_token("a"), auth._hash_token("b"))


class CreateMagicLinkTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_inserts_hashed_token_and_returns_raw_token(self) -> None:
        conn = _FakeConn([MagicMock(), MagicMock(), MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            token = auth.create_magic_link("User@Example.com")

        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 20)
        # The INSERT is the last of the three statements (two prune DELETEs
        # run first — see test_prunes_expired_rows_before_inserting).
        args, _kwargs = conn.calls[-1]
        _stmt, params = args
        self.assertEqual(params["email"], "user@example.com")
        self.assertEqual(params["token_hash"], auth._hash_token(token))
        self.assertNotEqual(params["token_hash"], token)

    def test_prunes_expired_rows_before_inserting(self) -> None:
        conn = _FakeConn([MagicMock(), MagicMock(), MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            auth.create_magic_link("user@example.com")

        self.assertEqual(len(conn.calls), 3)
        first_stmt = str(conn.calls[0][0][0])
        second_stmt = str(conn.calls[1][0][0])
        self.assertIn("DELETE FROM magic_links", first_stmt)
        self.assertIn("expires_at < NOW()", first_stmt)
        self.assertIn("DELETE FROM sessions", second_stmt)
        self.assertIn("expires_at < NOW()", second_stmt)

    def test_raises_on_db_failure(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError):
                auth.create_magic_link("user@example.com")


class VerifyMagicLinkTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_returns_none_for_unknown_or_expired_or_used_token(self) -> None:
        update_result = MagicMock()
        update_result.mappings.return_value.first.return_value = None
        conn = _FakeConn([update_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            result = auth.verify_magic_link("bad-token")

        self.assertIsNone(result)

    def test_consumes_token_and_gets_or_creates_user(self) -> None:
        update_result = MagicMock()
        update_result.mappings.return_value.first.return_value = {"email": "user@example.com"}
        user_result = MagicMock()
        user_result.mappings.return_value.first.return_value = {"id": 42, "email": "user@example.com"}
        conn = _FakeConn([update_result, user_result])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            result = auth.verify_magic_link("good-token")

        self.assertEqual(result, {"id": 42, "email": "user@example.com"})
        # First call marks the token used; second gets-or-creates the user.
        self.assertEqual(len(conn.calls), 2)

    def test_raises_on_db_failure(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError):
                auth.verify_magic_link("token")


class CreateSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_inserts_hashed_session_token_for_user(self) -> None:
        conn = _FakeConn([MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            token = auth.create_session(42)

        self.assertIsInstance(token, str)
        args, _kwargs = conn.calls[0]
        _stmt, params = args
        self.assertEqual(params["user_id"], 42)
        self.assertEqual(params["token_hash"], auth._hash_token(token))

    def test_raises_on_db_failure(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError):
                auth.create_session(1)


class GetUserForSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_returns_none_for_empty_token(self) -> None:
        self.assertIsNone(auth.get_user_for_session(""))
        self.assertIsNone(auth.get_user_for_session(None))

    def test_returns_user_for_valid_session(self) -> None:
        result_mock = MagicMock()
        result_mock.mappings.return_value.first.return_value = {"id": 7, "email": "a@b.com"}
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            user = auth.get_user_for_session("some-token")

        self.assertEqual(user, {"id": 7, "email": "a@b.com"})

    def test_returns_none_for_expired_or_unknown_session(self) -> None:
        result_mock = MagicMock()
        result_mock.mappings.return_value.first.return_value = None
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            self.assertIsNone(auth.get_user_for_session("some-token"))

    def test_swallows_db_errors_and_returns_none(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            self.assertIsNone(auth.get_user_for_session("some-token"))


class DeleteSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_noop_for_empty_token(self) -> None:
        with patch("auth._get_engine") as get_engine:
            auth.delete_session("")
        get_engine.assert_not_called()

    def test_deletes_by_hashed_token(self) -> None:
        conn = _FakeConn([MagicMock()])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            auth.delete_session("some-token")

        args, _kwargs = conn.calls[0]
        _stmt, params = args
        self.assertEqual(params["token_hash"], auth._hash_token("some-token"))

    def test_swallows_db_errors(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            # Must not raise.
            auth.delete_session("some-token")


class TtlConstantsTest(unittest.TestCase):
    def test_magic_link_ttl_is_short(self) -> None:
        self.assertLessEqual(auth.MAGIC_LINK_TTL, timedelta(minutes=30))

    def test_session_ttl_is_much_longer_than_magic_link_ttl(self) -> None:
        self.assertGreater(auth.SESSION_TTL, auth.MAGIC_LINK_TTL * 10)


class CreateApiKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_inserts_hashed_key_and_returns_raw_key_once(self) -> None:
        row = MagicMock()
        row.mappings.return_value.first.return_value = {
            "id": 3, "label": "my script", "created_at": "2026-01-01T00:00:00Z",
        }
        conn = _FakeConn([row])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            result = auth.create_api_key(7, "my script")

        self.assertTrue(result["key"].startswith("apk_"))
        self.assertEqual(result["key_prefix"], result["key"][:12])
        self.assertEqual(result["id"], 3)
        self.assertEqual(result["label"], "my script")
        # Present explicitly (not omitted) so the shape matches list_api_keys()'
        # rows plus the one-time `key` field.
        self.assertIsNone(result["last_used_at"])
        self.assertIsNone(result["revoked_at"])

        args, _kwargs = conn.calls[0]
        _stmt, params = args
        self.assertEqual(params["user_id"], 7)
        self.assertEqual(params["key_hash"], auth._hash_token(result["key"]))
        self.assertNotEqual(params["key_hash"], result["key"])
        self.assertEqual(params["key_prefix"], result["key_prefix"])

    def test_raises_on_db_failure(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError):
                auth.create_api_key(1)


class ListApiKeysTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_returns_rows_for_user_newest_first(self) -> None:
        rows = [
            {"id": 2, "key_prefix": "apk_bb", "label": None, "created_at": "t2",
             "last_used_at": None, "revoked_at": None},
            {"id": 1, "key_prefix": "apk_aa", "label": "old", "created_at": "t1",
             "last_used_at": "t1.5", "revoked_at": None},
        ]
        result_mock = MagicMock()
        result_mock.mappings.return_value.all.return_value = rows
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.connect.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            keys = auth.list_api_keys(7)

        self.assertEqual(keys, rows)
        args, _kwargs = conn.calls[0]
        _stmt, params = args
        self.assertEqual(params["user_id"], 7)


class RevokeApiKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_returns_true_when_a_row_is_revoked(self) -> None:
        result_mock = MagicMock()
        result_mock.first.return_value = (5,)
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            result = auth.revoke_api_key(7, 5)

        self.assertTrue(result)
        args, _kwargs = conn.calls[0]
        _stmt, params = args
        self.assertEqual(params, {"key_id": 5, "user_id": 7})

    def test_returns_false_when_no_row_matches(self) -> None:
        result_mock = MagicMock()
        result_mock.first.return_value = None
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            result = auth.revoke_api_key(7, 999)

        self.assertFalse(result)


class GetUserForApiKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        auth._ENGINE = None

    def tearDown(self) -> None:
        auth._ENGINE = None

    def test_returns_none_for_empty_key(self) -> None:
        self.assertIsNone(auth.get_user_for_api_key(""))
        self.assertIsNone(auth.get_user_for_api_key(None))

    def test_returns_user_id_and_stamps_last_used(self) -> None:
        result_mock = MagicMock()
        result_mock.mappings.return_value.first.return_value = {"user_id": 7}
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            result = auth.get_user_for_api_key("apk_sometoken")

        self.assertEqual(result, {"user_id": 7})
        args, _kwargs = conn.calls[0]
        stmt, params = args
        self.assertIn("SET last_used_at = NOW()", str(stmt))
        self.assertIn("revoked_at IS NULL", str(stmt))
        self.assertEqual(params["key_hash"], auth._hash_token("apk_sometoken"))

    def test_returns_none_for_revoked_or_unknown_key(self) -> None:
        result_mock = MagicMock()
        result_mock.mappings.return_value.first.return_value = None
        conn = _FakeConn([result_mock])
        fake_engine = MagicMock()
        fake_engine.begin.return_value = conn

        with patch("auth._get_engine", return_value=fake_engine):
            self.assertIsNone(auth.get_user_for_api_key("apk_bad"))

    def test_swallows_db_errors_and_returns_none(self) -> None:
        with patch("auth._get_engine", side_effect=RuntimeError("connection refused: password exposed")):
            self.assertIsNone(auth.get_user_for_api_key("apk_x"))


if __name__ == "__main__":
    unittest.main()
