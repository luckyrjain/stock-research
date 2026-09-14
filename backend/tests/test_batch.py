import unittest
from contextlib import contextmanager

from db.batch import batched_execute


class _FakeConn:
    def __init__(self, log: list) -> None:
        self._log = log

    def execute(self, sql, params) -> None:
        self._log.append(list(params))


class _FakeEngine:
    """Records how many times begin() opens a transaction (should be exactly
    one per batched_execute call, not once per chunk) and every chunk of rows
    passed to execute()."""

    def __init__(self) -> None:
        self.begin_calls = 0
        self.execute_log: list[list[dict]] = []

    @contextmanager
    def begin(self):
        self.begin_calls += 1
        yield _FakeConn(self.execute_log)


class BatchedExecuteTest(unittest.TestCase):
    def test_exact_multiple_splits_into_full_chunks(self) -> None:
        engine = _FakeEngine()
        rows = [{"i": i} for i in range(6)]
        batched_execute(engine, "SQL", rows, batch_size=2)
        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(engine.execute_log, [
            [{"i": 0}, {"i": 1}], [{"i": 2}, {"i": 3}], [{"i": 4}, {"i": 5}],
        ])

    def test_remainder_chunk_is_short(self) -> None:
        engine = _FakeEngine()
        rows = [{"i": i} for i in range(5)]
        batched_execute(engine, "SQL", rows, batch_size=2)
        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(engine.execute_log, [
            [{"i": 0}, {"i": 1}], [{"i": 2}, {"i": 3}], [{"i": 4}],
        ])

    def test_single_batch_when_rows_fit_in_one_chunk(self) -> None:
        engine = _FakeEngine()
        rows = [{"i": 0}, {"i": 1}]
        batched_execute(engine, "SQL", rows, batch_size=500)
        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(engine.execute_log, [rows])

    def test_empty_rows_is_a_no_op(self) -> None:
        engine = _FakeEngine()
        batched_execute(engine, "SQL", [], batch_size=500)
        self.assertEqual(engine.begin_calls, 0)
        self.assertEqual(engine.execute_log, [])

    def test_all_chunks_share_one_transaction(self) -> None:
        engine = _FakeEngine()
        rows = [{"i": i} for i in range(1001)]
        batched_execute(engine, "SQL", rows, batch_size=500)
        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(len(engine.execute_log), 3)


if __name__ == "__main__":
    unittest.main()
