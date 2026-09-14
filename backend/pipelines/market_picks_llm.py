"""LLM call helpers for the Market Picks pipeline: provider/model resolution,
the retrying litellm wrapper (`_llm_call`), and best-effort JSON extraction
from a raw LLM response (`_parse_json_from`). Split out of
market_picks_pipeline.py — see that module for the pipeline itself, which
calls these from `_phase_extract` and `_phase_analyze`.
"""

import json
import os
import re
import time

# ── LLM helpers ───────────────────────────────────────────────────────────────

_PROVIDER_KEYS = {
    "anthropic":  "ANTHROPIC_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "groq":       "GROQ_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
_ANALYST_MODELS = {
    "anthropic":  "claude-sonnet-4-6",
    "openai":     "gpt-4o",
    "groq":       "groq/llama-3.3-70b-versatile",
    "google":     "gemini/gemini-2.5-flash",
    "openrouter": "openrouter/meta-llama/llama-3.3-70b-instruct",
}


def _resolve_llm() -> tuple[str, str | None]:
    """Return (model_id, api_key_or_None)."""
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if not provider:
        for p, env in _PROVIDER_KEYS.items():
            if os.getenv(env):
                provider = p
                break
    model = os.getenv("ANALYST_MODEL", _ANALYST_MODELS.get(provider, "claude-sonnet-4-6"))
    api_key = os.getenv(_PROVIDER_KEYS.get(provider, ""), "") or None
    return model, api_key


def _llm_call(prompt: str, temperature: float = 0.3, _retries: int = 3) -> str:
    import litellm
    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    model, api_key = _resolve_llm()
    for attempt in range(_retries):
        try:
            resp = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except litellm.RateLimitError as exc:
            if attempt == _retries - 1:
                raise
            # Extract retry-after from message if available, else exponential backoff
            delay = 10 * (2 ** attempt)
            m = re.search(r'retry in ([\d.]+)s', str(exc))
            if m:
                delay = float(m.group(1)) + 1
            time.sleep(delay)
    return ""


def _parse_json_from(text: str) -> dict | list | None:
    m = re.search(r'\{.*\}|\[.*\]', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None
