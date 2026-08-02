# Analyst Numeric-Misread Guardrail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch analyst-LLM numeric misreads (e.g. dividend yield 0.46 cited as "47%") before they reach the user, by comparing numbers the analyst cites in prose against the actual source data.

**Architecture:** A new guardrail check, `_analysis_numeric_issues()`, sits alongside the existing `_analysis_support_issues()` in `crew.py`. For five fields (dividend yield, P/E, ROE, ROCE, book value) it regex-matches the first number cited near the field's name in the analyst's prose, compares it against the real value already present in `all_data`, and flags a 2x-or-worse mismatch. Its issues are merged into the same list that already drives the guardrail's reject → one corrective LLM retry → safe HOLD fallback path — no new control flow, no output schema change.

**Tech Stack:** Python 3.13, `re` (stdlib), `unittest` (existing test framework in `tests/test_analysis_guardrails.py`).

## Global Constraints

- Tools/guardrail functions must not raise — return/skip, never throw, on unparseable input (per `CLAUDE.md`: "Tools must not raise").
- Never add fields to the analyst JSON output schema (per `CLAUDE.md`) — this feature reads existing fields only, no schema change.
- Match existing code style in `crew.py`: `snake_case`, private helpers prefixed `_`, type hints on signatures.

---

### Task 1: `_parse_ratio_number` + `_analysis_numeric_issues` (unit-level)

**Files:**
- Modify: `crew.py` (insert after `_analysis_support_issues`, i.e. after line 265, before `def _validate_analysis_payload` at line 268)
- Test: `tests/test_analysis_guardrails.py`

**Interfaces:**
- Produces:
  - `_parse_ratio_number(value: Any) -> float | None` — parses a scraped ratio string (e.g. `"18.5 %"`, `"1,234.5"`, `"-"`) into a float, or `None` if unparseable/empty.
  - `_analysis_numeric_issues(data: dict | None, all_data: dict[str, dict] | None) -> list[str]` — returns a list of human-readable mismatch descriptions (empty list if none found).
- Consumes: nothing from other tasks (this task is self-contained; `Any` is already imported in `crew.py` via `from typing import Any` — verify this import exists before writing, and add it if missing).

- [ ] **Step 1: Write the failing unit tests**

Add to `tests/test_analysis_guardrails.py`, as new methods on the existing `AnalysisGuardrailFallbackTest` class (append after the last test method, before the class ends):

```python
    def test_parse_ratio_number_strips_percent_and_commas(self) -> None:
        self.assertEqual(crew._parse_ratio_number("18.5 %"), 18.5)
        self.assertEqual(crew._parse_ratio_number("1,234.5"), 1234.5)
        self.assertIsNone(crew._parse_ratio_number("-"))
        self.assertIsNone(crew._parse_ratio_number(""))
        self.assertIsNone(crew._parse_ratio_number(None))

    def test_analysis_numeric_issues_flags_dividend_yield_misread(self) -> None:
        # Reproduces the live QA bug: source dividend_yield_pct=0.46, analyst
        # prose cites "47%" (a ~100x misread).
        data = {
            "summary": "Solid company.",
            "business_quality": "Stable.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["The stock has a high dividend yield of 47%."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"dividend_yield_pct": 0.46}}
        issues = crew._analysis_numeric_issues(data, all_data)
        self.assertEqual(len(issues), 1)
        self.assertIn("dividend yield", issues[0])

    def test_analysis_numeric_issues_allows_within_tolerance_pe(self) -> None:
        data = {
            "summary": "",
            "business_quality": "Trading at a P/E of 25, close to fair value.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Reasonable valuation."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"pe_ratio": 24.8}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_missing_source(self) -> None:
        data = {
            "summary": "",
            "business_quality": "ROE is 18%, well above peers.",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Strong returns."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {}, "research": {"ratios": {}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_uncited_field(self) -> None:
        data = {
            "summary": "No commentary on returns.",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["Good management."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"research": {"ratios": {"ROE": "18.5 %"}}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])

    def test_analysis_numeric_issues_skips_zero_source(self) -> None:
        data = {
            "summary": "",
            "business_quality": "",
            "news_highlights": "",
            "institutional_trend": "",
            "bull_factors": ["No dividend history to speak of."],
            "bear_factors": ["Some risk."],
            "key_risks": ["Some risk."],
        }
        all_data = {"stock_info": {"dividend_yield_pct": 0}}
        self.assertEqual(crew._analysis_numeric_issues(data, all_data), [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_analysis_guardrails.py -v -k "parse_ratio_number or analysis_numeric_issues"`
Expected: FAIL — `AttributeError: module 'crew' has no attribute '_parse_ratio_number'` (and same for `_analysis_numeric_issues`).

- [ ] **Step 3: Write the implementation**

In `crew.py`, insert the following immediately after the end of `_analysis_support_issues` (after line 265, i.e. right before `def _validate_analysis_payload` at line 268):

```python
def _parse_ratio_number(value: Any) -> float | None:
    """Parses a scraped ratio string (e.g. '18.5 %', '1,234.5', '-') into a float."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_cited_number(text: str, field_pattern: str) -> float | None:
    match = re.search(field_pattern, text)
    if not match:
        return None
    window = text[match.end(): match.end() + 20]
    number_match = re.search(r"-?\d+\.?\d*", window)
    if not number_match:
        return None
    try:
        return float(number_match.group(0))
    except ValueError:
        return None


_NUMERIC_FIELD_CHECKS = [
    ("dividend yield", r"dividend\s*yield",
     lambda ad: (ad.get("stock_info", {}) or {}).get("dividend_yield_pct")),
    ("P/E ratio", r"\bp/?e\b(?:\s*ratio)?|price[- ]to[- ]earnings",
     lambda ad: (ad.get("stock_info", {}) or {}).get("pe_ratio")),
    ("ROE", r"\broe\b|return on equity",
     lambda ad: _parse_ratio_number((ad.get("research", {}) or {}).get("ratios", {}).get("ROE"))),
    ("ROCE", r"\broce\b|return on capital employed",
     lambda ad: _parse_ratio_number((ad.get("research", {}) or {}).get("ratios", {}).get("ROCE"))),
    ("book value", r"book value",
     lambda ad: (ad.get("stock_info", {}) or {}).get("book_value")),
]


def _analysis_numeric_issues(data: dict | None, all_data: dict[str, dict] | None) -> list[str]:
    if data is None or not all_data:
        return []

    issues: list[str] = []
    analysis_text = " ".join(
        [
            str(data.get("summary", "")),
            str(data.get("business_quality", "")),
            str(data.get("news_highlights", "")),
            str(data.get("institutional_trend", "")),
            " ".join(str(item) for item in data.get("bull_factors", []) or []),
            " ".join(str(item) for item in data.get("bear_factors", []) or []),
            " ".join(str(item) for item in data.get("key_risks", []) or []),
        ]
    ).lower()

    for label, field_pattern, source_getter in _NUMERIC_FIELD_CHECKS:
        cited = _first_cited_number(analysis_text, field_pattern)
        if cited is None:
            continue
        source = source_getter(all_data)
        if source is None or source == 0:
            continue
        if cited < source / 2 or cited > source * 2:
            issues.append(
                f"{label} cited as {cited} but source data shows {source} — off by more than 2x"
            )

    return issues
```

Before pasting, confirm `crew.py` already has `import re` and `from typing import Any` near the top of the file (both are used elsewhere in `crew.py`, e.g. `_analysis_support_issues` already uses `re.search`) — no new imports should be needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_analysis_guardrails.py -v -k "parse_ratio_number or analysis_numeric_issues"`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add crew.py tests/test_analysis_guardrails.py
git commit -m "$(cat <<'EOF'
feat: add numeric-misread guardrail check

_analysis_numeric_issues compares numbers the analyst LLM cites in
prose (dividend yield, P/E, ROE, ROCE, book value) against the actual
source data, catching transcription errors like a 0.46 dividend yield
being written as "47%". Not yet wired into the guardrail path.
EOF
)"
```

---

### Task 2: Wire into `_validate_analysis_payload` guardrail path

**Files:**
- Modify: `crew.py:306-308`
- Test: `tests/test_analysis_guardrails.py`

**Interfaces:**
- Consumes: `_analysis_numeric_issues(data, all_data) -> list[str]` from Task 1.
- Produces: `_validate_analysis_payload` now also rejects on numeric mismatch, using the exact same `(False, "Unsupported claims found: ...")` return shape it already uses for `_analysis_support_issues` — downstream callers (`_guard_analysis`, `run_analysis_with_fallback`'s retry logic) need no changes since the return type/shape is unchanged.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_analysis_guardrails.py`, as a new method on `AnalysisGuardrailFallbackTest`:

```python
    def test_validate_analysis_payload_rejects_dividend_yield_misread(self) -> None:
        payload = dict(
            self._VALID_PAYLOAD,
            bull_factors=self._VALID_PAYLOAD["bull_factors"] + ["High dividend yield of 47%."],
        )
        all_data = dict(self.all_data)
        all_data["stock_info"] = dict(all_data.get("stock_info", {}), dividend_yield_pct=0.46)

        ok, message = crew._validate_analysis_payload(payload, all_data)
        self.assertFalse(ok)
        self.assertIn("dividend yield", message)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_analysis_guardrails.py -v -k test_validate_analysis_payload_rejects_dividend_yield_misread`
Expected: FAIL — `assertFalse` fails because `ok` is `True` (the check isn't wired in yet).

- [ ] **Step 3: Wire the check into `_validate_analysis_payload`**

In `crew.py`, the current code at lines 306-308 is:

```python
    support_issues = _analysis_support_issues(data, all_data)
    if support_issues:
        return False, f"Unsupported claims found: {'; '.join(support_issues)}."
```

Replace with:

```python
    support_issues = _analysis_support_issues(data, all_data) + _analysis_numeric_issues(data, all_data)
    if support_issues:
        return False, f"Unsupported claims found: {'; '.join(support_issues)}."
```

- [ ] **Step 4: Run the full guardrail test file to verify everything passes**

Run: `python -m pytest tests/test_analysis_guardrails.py -v`
Expected: PASS — all tests, including the pre-existing ones (they use `all_data` fixtures without the numeric fields this check reads, so `source_getter` returns `None` and the check is a no-op for them) and the new ones from Task 1 and Task 2.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: PASS — no regressions elsewhere.

- [ ] **Step 6: Commit**

```bash
git add crew.py tests/test_analysis_guardrails.py
git commit -m "$(cat <<'EOF'
feat: wire numeric-misread check into analyst guardrail

_validate_analysis_payload now rejects (and triggers the existing
one-retry-then-HOLD-fallback path) when the analyst LLM cites a
number that's off by more than 2x from the source data for dividend
yield, P/E, ROE, ROCE, or book value.
EOF
)"
```
