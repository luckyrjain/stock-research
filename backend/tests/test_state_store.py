"""Tests for state_store.py — the durable-JSON-state layer that replaced this
codebase's per-family JSON files under output/.

Runs against SQLite in memory (StaticPool so every thread shares one DB), the
same approach tests/test_portfolio_aggregator.py already uses for these
tables. That matters more than usual here: mutate()'s "upsert, take the row
lock, RETURN the prior payload" idiom is the one piece of SQL in this module
that isn't obvious, and mocking the engine would verify nothing about whether
it actually round-trips.
"""
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import state_store
from db.models import app_state, metadata


class StateStoreTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        metadata.create_all(self.engine)
        self._patches = [
            patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}),
            patch("state_store._get_engine", return_value=self.engine),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.engine.dispose()


class LoadSaveTest(StateStoreTestBase):
    def test_missing_key_is_none_not_empty_dict(self) -> None:
        self.assertIsNone(state_store.load("ns", "nope"))

    def test_save_then_load_round_trips_nested_json(self) -> None:
        payload = {"picks": [{"symbol": "TCS", "confidence": 71.5}], "date": "2026-08-02"}
        state_store.save("history", "2026-08-02", payload)
        self.assertEqual(state_store.load("history", "2026-08-02"), payload)

    def test_save_is_an_upsert_not_a_duplicate_row(self) -> None:
        state_store.save("ns", "k", {"v": 1})
        state_store.save("ns", "k", {"v": 2})
        self.assertEqual(state_store.load("ns", "k"), {"v": 2})
        with self.engine.connect() as conn:
            self.assertEqual(len(conn.execute(app_state.select()).fetchall()), 1)

    def test_namespaces_are_isolated(self) -> None:
        state_store.save("a", "k", {"from": "a"})
        state_store.save("b", "k", {"from": "b"})
        self.assertEqual(state_store.load("a", "k"), {"from": "a"})
        self.assertEqual(state_store.load("b", "k"), {"from": "b"})


class ItemsTest(StateStoreTestBase):
    def setUp(self) -> None:
        super().setUp()
        for day in ("2026-07-30", "2026-07-31", "2026-08-01"):
            state_store.save("history", day, {"date": day})
        state_store.save("other", "2026-08-01", {"date": "elsewhere"})

    def test_key_ascending_by_default_and_scoped_to_namespace(self) -> None:
        self.assertEqual(
            [k for k, _ in state_store.items("history")],
            ["2026-07-30", "2026-07-31", "2026-08-01"],
        )

    def test_newest_first_with_limit_is_the_last_n_days_read(self) -> None:
        self.assertEqual(
            [k for k, _ in state_store.items("history", limit=2, newest_first=True)],
            ["2026-08-01", "2026-07-31"],
        )

    def test_payloads_come_back_deserialized(self) -> None:
        self.assertEqual(dict(state_store.items("history", limit=1))["2026-07-30"], {"date": "2026-07-30"})

    def test_empty_namespace_is_empty_list(self) -> None:
        self.assertEqual(state_store.items("never-written"), [])


class MutateTest(StateStoreTestBase):
    @staticmethod
    def _bump(current: dict) -> dict:
        return {"count": current.get("count", 0) + 1}

    def test_creates_the_row_from_default_when_absent(self) -> None:
        self.assertEqual(state_store.mutate("c", "k", self._bump, {"count": 0}), {"count": 1})
        self.assertEqual(state_store.load("c", "k"), {"count": 1})

    def test_reads_prior_payload_not_the_default_when_present(self) -> None:
        state_store.save("c", "k", {"count": 7})
        self.assertEqual(state_store.mutate("c", "k", self._bump, {"count": 0}), {"count": 8})

    def test_default_is_not_mutated_by_the_callback(self) -> None:
        default = {"count": 0}
        state_store.mutate("c", "k", lambda cur: cur.__setitem__("count", 99) or cur, default)
        self.assertEqual(default, {"count": 0})


class MutateConcurrencyTest(unittest.TestCase):
    """The whole reason mutate() exists — the lost-update race the three
    fcntl.flock helpers it replaced were each written to prevent.

    Needs its own engine: the in-memory StaticPool the other tests use shares
    a single DBAPI connection across every thread, which can't hold concurrent
    transactions at all, so it would fail this for a reason that has nothing
    to do with the code under test. A file-backed SQLite with a normal pool
    gives each thread its own connection and real write locking.
    """

    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="stock-research-state-store-test-")
        url = f"sqlite:///{Path(self._dir) / 'state.db'}"
        self.engine = create_engine(url, connect_args={"timeout": 30})
        metadata.create_all(self.engine)
        self._patches = [
            patch.dict(os.environ, {"DATABASE_URL": url}),
            patch("state_store._get_engine", return_value=self.engine),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.engine.dispose()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_concurrent_mutations_do_not_lose_updates(self) -> None:
        def bump() -> None:
            state_store.mutate("c", "k", lambda cur: {"count": cur.get("count", 0) + 1}, {"count": 0})

        threads = [threading.Thread(target=bump) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(state_store.load("c", "k"), {"count": 12})


class NoDatabaseUrlTest(unittest.TestCase):
    """Every entry point degrades to a documented no-op rather than raising —
    same convention as verdict_history.py."""

    def setUp(self) -> None:
        self._patch = patch.dict(os.environ, {}, clear=True)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_all_entry_points_degrade_without_raising(self) -> None:
        state_store.save("ns", "k", {"v": 1})            # must not raise
        self.assertIsNone(state_store.load("ns", "k"))
        self.assertEqual(state_store.items("ns"), [])
        self.assertIsNone(state_store.mutate("ns", "k", lambda c: c, {}))


class BrokenEngineTest(unittest.TestCase):
    """A DB hiccup is logged and swallowed — these are telemetry/audit paths
    that must never break the request or pipeline run they observe."""

    def setUp(self) -> None:
        self._patches = [
            patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}),
            patch("state_store._get_engine", side_effect=RuntimeError("db down")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    def test_all_entry_points_swallow_the_failure(self) -> None:
        state_store.save("ns", "k", {"v": 1})            # must not raise
        self.assertIsNone(state_store.load("ns", "k"))
        self.assertEqual(state_store.items("ns"), [])
        self.assertIsNone(state_store.mutate("ns", "k", lambda c: c, {}))


if __name__ == "__main__":
    unittest.main()
