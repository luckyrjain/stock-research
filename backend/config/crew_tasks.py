import json
from pathlib import Path

_analyst_cfg = json.loads((Path(__file__).parent / "analyst.json").read_text())

ANALYST_SECTIONS: dict[str, str] = _analyst_cfg["sections"]
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

            {{
            "symbol": "{symbol}",
            "recommendation": "BUY | SELL | HOLD",
            "confidence": "HIGH | MEDIUM | LOW",
            "summary": "string",
            "valuation": {{
                "verdict": "Undervalued | Fairly Valued | Overvalued",
                "comment": "string"
            }},
            "business_quality": "string",
            "bull_factors": ["string", "string", "string"],
            "bear_factors": ["string", "string"],
            "key_risks": ["string", "string", "string"],
            "news_sentiment": "Positive | Neutral | Negative",
            "news_highlights": "string",
            "institutional_trend": "string"
            }}

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
