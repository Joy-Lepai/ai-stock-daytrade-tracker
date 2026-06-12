from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceConfig:
    executable_min_confidence: float = 70.0
    block_executable_below: float = 60.0
    unreliable_below: float = 40.0
    min_sample_size: int = 20
    trusted_sample_size: int = 100
    conflict_penalty: float = 10.0
    missing_data_penalty: float = 20.0
    stale_data_penalty: float = 20.0
    below_vwap_penalty: float = 25.0
    high_risk_penalty: float = 25.0


DEFAULT_CONFIDENCE_CONFIG = ConfidenceConfig()
