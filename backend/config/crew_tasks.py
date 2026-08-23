import json
from pathlib import Path

_analyst_cfg = json.loads((Path(__file__).parent / "analyst.json").read_text())

ANALYST_SECTIONS: dict[str, str] = _analyst_cfg["sections"]
_OUTPUT_SCHEMA: dict = _analyst_cfg["output_schema"]
# analyst.json previously also carried an "expected_output" string -- a prose summary of
# output_schema's own fields with no reader anywhere in this codebase. Removed rather than
# wired in, since it duplicated output_schema (now the single source of truth below) with
# no distinct content of its own.
# "instructions"/"valuation_guidance" were previously parsed here and never
# referenced anywhere else in this module -- an operator editing either list
# in analyst.json (e.g. to tune the filings-materiality guidance or the P/E
# calibration bands) had zero effect on the actual LLM prompt below, since
# build_analysis_prompt() only ever pulled ANALYST_SECTIONS out of this
# config. Wired into the prompt itself (see the "ADDITIONAL ANALYST
# INSTRUCTIONS"/"VALUATION GUIDANCE" sections below) so both actually reach
# the model, matching analyst/crew.py's own comment (right above its
# filings-handling code) that already assumed this was happening.
_INSTRUCTIONS: list[str] = _analyst_cfg.get("instructions", [])
_VALUATION_GUIDANCE: list[str] = _analyst_cfg.get("valuation_guidance", [])


def build_analysis_prompt(symbol: str, all_data: dict[str, dict]) -> str:
    parts = []
    for name, label in ANALYST_SECTIONS.items():
        clean = {k: v for k, v in (all_data.get(name, {}) or {}).items() if k != "_meta"}
        parts.append(f"### {label}\n{json.dumps(clean, indent=2)}")
    instructions_block = "\n".join(f"- {line}" for line in _INSTRUCTIONS)
    valuation_guidance_block = "\n".join(f"- {line}" for line in _VALUATION_GUIDANCE)
    # Rendered from config/analyst.json's own "output_schema" -- previously this was a
    # second, hand-duplicated copy of the schema hardcoded here, which meant editing
    # analyst.json's output_schema had zero effect on what the LLM actually saw. The
    # "<SYMBOL>" placeholder in that config is swapped for the real symbol, matching
    # this prompt's previous behavior of showing the actual ticker in the illustration.
    schema_block = json.dumps(_OUTPUT_SCHEMA, indent=2).replace("<SYMBOL>", symbol)
    return (
        f"""You are a professional equity research analyst.
            You are given structured data for the NSE-listed stock: {symbol}.

            Your task is to produce a STRICTLY FORMATTED JSON analysis.

            =====================
            DATA PROVIDED
            =====================
            You will receive:
            - Stock fundamentals
            - Financial ratios
            - News summaries
            - Shareholding data
            - Recent corporate filings/announcements (if any)
            - Quantitative signals (if provided)

            =====================
            STRICT OUTPUT RULES
            =====================

            - Output MUST be a valid JSON object
            - Do NOT include markdown, explanations, or extra text
            - Do NOT include trailing commas
            - Do NOT include comments
            - Do NOT wrap JSON in code blocks

            =====================
            REQUIRED JSON SCHEMA
            =====================

            {schema_block}

            =====================
            CRITICAL CONSTRAINTS
            =====================

            1. GROUNDING (VERY IMPORTANT)
            - ONLY use information explicitly present in the provided data
            - DO NOT introduce external knowledge
            - DO NOT assume industry risks unless mentioned in the input
            - If no risks are found, say:
            "No specific risks identified from available data"

            2. SIGNAL USAGE
            - If quantitative signals are provided:
            - Use them to SUPPORT reasoning
            - DO NOT contradict strong signals without explanation
            - DO NOT change schema or output format

            3. ENUM STRICTNESS
            - recommendation MUST be exactly one of: BUY, SELL, HOLD
            - confidence MUST be exactly one of: HIGH, MEDIUM, LOW

            4. MINIMUM CONTENT
            - bull_factors MUST have at least 3 items
            - bear_factors MUST have at least 2 items
            - key_risks MUST have at least 3 items

            5. NO GENERIC FILLER
            - Avoid generic phrases like:
            - "regulatory risk"
            - "competition risk"
            - "macro environment"
            UNLESS explicitly supported by the data

            6. INSTITUTIONAL TREND
            - DO NOT infer trends (rising/falling) from a single snapshot
            - Only describe what is explicitly visible

            =====================
            ADDITIONAL ANALYST INSTRUCTIONS
            =====================
            {instructions_block}

            =====================
            VALUATION GUIDANCE (Indian equities)
            =====================
            {valuation_guidance_block}

            =====================
            REASONING APPROACH
            =====================

            - Step 1: Assess fundamentals (growth, margins, ratios)
            - Step 2: Evaluate valuation vs fundamentals
            - Step 3: Incorporate signals (if provided)
            - Step 4: Check for consistency between narrative and numbers
            - Step 5: Produce final recommendation

            =====================
            FINAL INSTRUCTION
            =====================

            Return ONLY the JSON object. No additional text.\n\n"""
        + "\n\n".join(parts)
    )
