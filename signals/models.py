"""Data models for individual and aggregated trading signals."""
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class Signal:
    """Represents one computed signal and its weighted impact."""

    name: str
    value: str
    score: float
    meta: Dict[str, Any]

@dataclass
class SignalResult:
    """Holds all signal outputs plus final score and verdict."""

    symbol: str
    signals: Dict[str, Signal]
    final_score: float
    verdict: str
