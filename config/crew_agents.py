import json
from pathlib import Path

from tools.nse_tools import get_mf_holdings, get_stock_quote
from tools.news_tools import get_latest_news
from tools.screener_tools import get_fundamentals, get_holdings

_TOOL_MAP = {
    "get_stock_quote":  get_stock_quote,
    "get_fundamentals": get_fundamentals,
    "get_latest_news":  get_latest_news,
    "get_holdings":     get_holdings,
    "get_mf_holdings":  get_mf_holdings,
}

_cfg = json.loads((Path(__file__).parent / "agents.json").read_text())

AGENTS_FOR_TASK: dict[str, tuple[str, list]] = {
    name: (spec["role"], [_TOOL_MAP[spec["tool"]]])
    for name, spec in _cfg.items()
}

BACKSTORIES: dict[str, str] = {
    spec["role"]: spec["backstory"]
    for spec in _cfg.values()
}
