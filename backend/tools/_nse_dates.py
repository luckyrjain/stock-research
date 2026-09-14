"""Shared NSE-style multi-format date parsing.

Five modules (tools/nse_insider_trades.py, tools/nse_bulk_block_deals.py,
tools/nse_tools.py, signals/filings_classifier.py, signals/macro.py)
independently reimplemented the same "try N known date formats in order,
return the first that parses, else None" loop against NSE's own drifting
date strings (bare `dd-Mon-yyyy`, sometimes with a time, sometimes
`yyyy-mm-dd` or `dd/mm/yyyy` or `dd-mm-yyyy` depending on the endpoint).
Two of the five even used the identical name `_parse_filing_date` across
different modules with different return types (str vs datetime) - a
latent name-collision trap. This module is the single place the
format-loop now lives.

Each caller keeps its own function name, return type, and tz-awareness
exactly as before - this module only owns the "which format matched"
part.
"""
from datetime import datetime

# Union of all five callers' format lists. A superset is safe here: for a
# given input string, at most one of these formats' strptime regex can
# match (each is pinned by a distinct separator, digit-vs-text month, or
# exact digit count for the year), so adding formats a given caller never
# used cannot change which format wins for that caller's own inputs.
_DEFAULT_FORMATS = (
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
)


def parse_nse_date(date_str: str, formats: tuple[str, ...] = _DEFAULT_FORMATS) -> datetime | None:
    """Try each format in `formats` in order via datetime.strptime, returning
    the first successful parse, or None if none match."""
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, TypeError):
            continue
    return None
