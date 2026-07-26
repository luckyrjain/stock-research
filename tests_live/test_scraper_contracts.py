"""Live contract checks against the four scrapers this codebase's own
CLAUDE.md repeatedly flags as "not verified against a live response in this
sandbox" — Screener.in's peer table, Trendlyne's symbol resolution, NSE's
FII/DII flow, and RBI's rate/inflation table. These are the highest-blast-
radius of the ~10 standalone scrapers outside ALL_DATA_TASKS' own
schema_drift.py coverage.

Deliberately **not** part of tests/ — `python -m pytest tests/` (the command
this repo's CI and CLAUDE.md both document) must never make a live network
call, matching this repo's own stated convention that no test does. This
directory is a second, independent test root, run only by a separate
low-frequency scheduled workflow (.github/workflows/live-contract-check.yml)
with RUN_LIVE_TESTS=1 set explicitly.

What this closes: `schema_drift.py`/`source_health.py` only catch drift
*after* it's already shipped to users, from production traffic. This gives
an earlier, narrower signal — a handful of real scraper calls, on a schedule,
independent of whether any user happened to trigger that code path recently.

What this does NOT close: every scraped tool function in this codebase
follows the "tools never raise" convention (see CLAUDE.md's "Important
Rules for Claude") — a connectivity failure and a genuine site-layout
change both surface the same way, as a returned `{"error": ...}` dict, not
a raised exception. Telling those two apart from the tool's return value
alone is unreliable (the exact wording of a connection-refused message
isn't a stable contract to pattern-match against). So each test here first
makes its own minimal, direct connectivity probe to the target host —
if *that* fails, the test skips outright, before ever calling the scraper;
only once the host is confirmed reachable does a returned "error"/missing
field count as a real, hard contract failure. This sandbox's own outbound
proxy blocks every one of these hosts (confirmed while writing this file:
all four return a 403 at the proxy's own CONNECT tunnel), which is exactly
why none of these were ever run here — the probe correctly skips all four
in this environment rather than reporting a false pass or a false failure.
"""
import json
import os
import unittest

import requests


def _require_live_tests_opt_in() -> None:
    if os.environ.get("RUN_LIVE_TESTS") != "1":
        raise unittest.SkipTest(
            "Live contract checks are opt-in — set RUN_LIVE_TESTS=1 to run them "
            "(they make real network calls to third-party sites). "
            "See .github/workflows/live-contract-check.yml for the scheduled run."
        )


def _require_reachable(url: str) -> None:
    """Skips the test outright if `url`'s host can't be reached at all —
    this must run BEFORE the scraper call, since every tool function in
    this codebase swallows its own connection failure into a returned
    {"error": ...} dict rather than raising (see module docstring). A
    connectivity failure is this runner's own environment, not a contract
    violation by the target site, and must never be reported as either a
    false pass or a false failure."""
    try:
        requests.head(url, timeout=8, allow_redirects=True)
    except requests.exceptions.RequestException as exc:
        raise unittest.SkipTest(f"Target host unreachable from this runner ({url}): {exc}")


class _LiveContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _require_live_tests_opt_in()


class ScreenerPeerComparisonLiveTest(_LiveContractTestCase):
    def test_get_peer_comparison_returns_expected_shape(self) -> None:
        # A @tool-decorated function (see tools/screener_tools.py) — called
        # via .run(), same convention main._fetch_task() uses, returning a
        # JSON string rather than a dict directly.
        _require_reachable("https://www.screener.in/company/TCS/")
        from tools.screener_tools import get_peer_comparison

        result = json.loads(get_peer_comparison.run(symbol="TCS"))
        self.assertNotIn("error", result, f"Screener.in peer comparison scraper contract broke: {result.get('error')}")
        self.assertIn("self", result)
        self.assertIn("peers", result)
        self.assertIsInstance(result["peers"], list)


class TrendlyneResolutionLiveTest(_LiveContractTestCase):
    def test_numeric_consensus_resolves_a_known_symbol(self) -> None:
        _require_reachable("https://trendlyne.com/")
        from tools.trendlyne_scraper import fetch_trendlyne_numeric_consensus

        result = fetch_trendlyne_numeric_consensus("TCS")
        self.assertNotIn("error", result, f"Trendlyne resolution/scrape contract broke: {result.get('error')}")
        self.assertEqual(result["symbol"], "TCS")
        # A well-known large-cap should resolve to a real page even if none
        # of the four numeric fields happen to be cleanly parseable this
        # run — source_url is the actual resolution contract being checked.
        self.assertIsNotNone(result["source_url"], "Trendlyne symbol resolution returned no URL at all for TCS")


class NseFiiDiiFlowLiveTest(_LiveContractTestCase):
    def test_get_fii_dii_flow_returns_expected_shape(self) -> None:
        _require_reachable("https://www.nseindia.com/")
        from tools.nse_fii_dii_tools import get_fii_dii_flow

        result = get_fii_dii_flow()
        self.assertNotIn("error", result, f"NSE FII/DII flow scraper contract broke: {result.get('error')}")


class RbiMacroContextLiveTest(_LiveContractTestCase):
    def test_get_macro_context_returns_expected_shape(self) -> None:
        _require_reachable("https://www.rbi.org.in/")
        from tools.macro_context_tools import get_macro_context

        result = get_macro_context()
        self.assertNotIn("error", result, f"RBI macro-context scraper contract broke: {result.get('error')}")


if __name__ == "__main__":
    unittest.main()
