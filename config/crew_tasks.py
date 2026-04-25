import json
from pathlib import Path

_tasks_cfg   = json.loads((Path(__file__).parent / "tasks.json").read_text())
_analyst_cfg = json.loads((Path(__file__).parent / "analyst.json").read_text())

ANALYST_SECTIONS: dict[str, str] = _analyst_cfg["sections"]

ANALYST_DESCRIPTION_SUFFIX = (
    "\n\n---\nINSTRUCTIONS FOR YOUR ANALYSIS:\n\nRules you MUST follow:\n"
    + "\n".join(f"- {r}" for r in _analyst_cfg["instructions"])
    + "\n\nValuation guidance (using only what is in the data):\n"
    + "\n".join(f"- {g}" for g in _analyst_cfg["valuation_guidance"])
    + "\n\nReturn ONLY a valid JSON object — no markdown fences, no prose before or after:\n"
    + json.dumps(_analyst_cfg["output_schema"], indent=2)
)


def build_task_specs(symbol: str, guard_data) -> dict:
    return {
        name: dict(
            description=spec["description"].replace("{symbol}", symbol),
            expected_output=spec["expected_output"],
            guardrail=guard_data(name),
            max_retries=spec["max_retries"],
        )
        for name, spec in _tasks_cfg.items()
    }


def build_analysis_prompt(symbol: str, all_data: dict[str, dict]) -> str:
    parts = []
    for name, label in ANALYST_SECTIONS.items():
        clean = {k: v for k, v in (all_data.get(name, {}) or {}).items() if k != "_meta"}
        parts.append(f"### {label}\n{json.dumps(clean, indent=2)}")
    return (
        f"You have been given all available data on the NSE-listed stock {symbol}.\n\n"
        + "\n\n".join(parts)
        + ANALYST_DESCRIPTION_SUFFIX
    )
