"""Shared batched-upsert execution helper.

Consolidates the "chunk rows into batches, execute the same statement per
chunk inside one transaction" loop that was previously hand-rolled at 8 call
sites across pipelines/ (eod_prices_pipeline.py x4, corporate_actions_pipeline.py
x2, screener_pipeline.py, sme_ema_pipeline.py). Chunking exists purely to keep
each executemany call's parameter count bounded — it is one transaction per
call, never one per chunk.
"""


def batched_execute(engine, sql, rows: list[dict], batch_size: int = 500) -> None:
    """Execute `sql` once per `batch_size`-sized slice of `rows`, all inside a
    single `engine.begin()` transaction. No-op if `rows` is empty."""
    if not rows:
        return
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            conn.execute(sql, rows[i:i + batch_size])
