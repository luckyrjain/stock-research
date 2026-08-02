"""Aggregates source_quality.py's per-run records into a per-source
signal-quality report, sorted worst-survival-first.

Usage: python source_quality_report.py --days 14
"""

import argparse
from datetime import datetime, timedelta, timezone

from core import state_store
from source_quality import NAMESPACE


def aggregate(days: int, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    totals: dict[str, dict] = {}

    for _run_id, payload in state_store.items(NAMESPACE):
        try:
            ts = datetime.fromisoformat(payload["timestamp"])
            if ts < cutoff:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        for name, stats in payload.get("sources", {}).items():
            t = totals.setdefault(
                name, {"runs": 0, "articles_fetched": 0, "picks_extracted": 0, "picks_validated": 0}
            )
            t["runs"] += 1
            t["articles_fetched"] += stats.get("articles_fetched", 0)
            t["picks_extracted"] += stats.get("picks_extracted", 0)
            t["picks_validated"] += stats.get("picks_validated", 0)

    return totals


def render(totals: dict[str, dict]) -> str:
    rows = []
    for name, t in totals.items():
        yield_rate = (t["picks_extracted"] / t["articles_fetched"]) if t["articles_fetched"] else None
        survival_rate = (t["picks_validated"] / t["picks_extracted"]) if t["picks_extracted"] else None
        rows.append((name, t, yield_rate, survival_rate))

    # Worst survival first; sources with no survival data (None) sort last.
    rows.sort(key=lambda r: (r[3] is None, r[3] if r[3] is not None else 0.0))

    header = f"{'Source':<45}{'Runs':>6}{'Articles':>10}{'Extracted':>11}{'Validated':>11}{'Yield%':>9}{'Survival%':>11}"
    lines = [header]
    for name, t, yield_rate, survival_rate in rows:
        yield_str = f"{yield_rate * 100:.1f}%" if yield_rate is not None else "—"
        survival_str = f"{survival_rate * 100:.1f}%" if survival_rate is not None else "—"
        lines.append(
            f"{name:<45}{t['runs']:>6}{t['articles_fetched']:>10}{t['picks_extracted']:>11}"
            f"{t['picks_validated']:>11}{yield_str:>9}{survival_str:>11}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate market-picks source-quality telemetry.")
    parser.add_argument("--days", type=int, default=14, help="Lookback window in days (default: 14)")
    args = parser.parse_args()
    print(render(aggregate(args.days)))


if __name__ == "__main__":
    main()
