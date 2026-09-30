"""Detector configuration with the provisional defaults from docs/DECISIONS.md §5.

All thresholds are diagnostic heuristics, never SLOs. Any change yields a new config_hash.
"""

import hashlib
import json

from pydantic import Field

from app.domain.common import STEP_SECONDS, Contract

DETECTOR_VERSION = "detectors-2026.09.1"


class SignalThresholds(Contract):
    min_abs_effect: float | None = None
    min_rel_factor: float | None = None
    abs_floor: float = Field(gt=0, description="Scale floor for zero/low MAD.")
    absolute_high: float | None = None
    absolute_critical: float | None = None
    absolute_min_minutes: int = 15


class DetectorConfig(Contract):
    version: str = DETECTOR_VERSION
    step_seconds: int = STEP_SECONDS
    rate_window_seconds: int = 300
    baseline_max_days: int = 14
    baseline_min_adequate_days: int = 3
    baseline_day_min_coverage: float = 0.7
    bucket_min_coverage: float = 0.5
    time_of_day_min_days: int = 7
    time_of_day_half_width_hours: int = 1
    mad_scale: float = 1.4826
    relative_scale_floor: float = 0.05
    z_threshold: float = 4.0
    min_episode_steps: int = 3
    max_merge_gap_steps: int = 2
    min_ratio_requests_per_step: int = 30
    top_routes_per_service: int = 20
    client_error_max_severity: str = "medium"
    signals: dict[str, SignalThresholds] = Field(
        default_factory=lambda: {
            "cpu_utilization": SignalThresholds(
                min_abs_effect=0.10, abs_floor=0.01, absolute_high=0.90
            ),
            "cpu_iowait": SignalThresholds(
                min_abs_effect=0.05, abs_floor=0.005, absolute_high=0.20
            ),
            "memory_utilization": SignalThresholds(
                min_abs_effect=0.10, abs_floor=0.01, absolute_high=0.90
            ),
            "filesystem_used_ratio": SignalThresholds(
                min_abs_effect=0.05,
                abs_floor=0.005,
                absolute_high=0.90,
                absolute_critical=0.95,
                absolute_min_minutes=5,
            ),
            "filesystem_inodes_used_ratio": SignalThresholds(
                min_abs_effect=0.05, abs_floor=0.005, absolute_high=0.90, absolute_min_minutes=5
            ),
            "disk_busy_ratio": SignalThresholds(
                min_abs_effect=0.20, abs_floor=0.01, absolute_high=0.90
            ),
            "network_bytes": SignalThresholds(min_rel_factor=2.0, abs_floor=1024.0),
            "network_errors": SignalThresholds(min_abs_effect=0.0, abs_floor=0.01),
            "container_cpu": SignalThresholds(min_rel_factor=1.25, abs_floor=0.01),
            "container_memory_limit_ratio": SignalThresholds(
                min_rel_factor=1.25, abs_floor=0.01, absolute_high=0.90
            ),
            "container_throttling_ratio": SignalThresholds(
                min_rel_factor=1.25, abs_floor=0.01, absolute_high=0.25
            ),
            "request_rate": SignalThresholds(
                min_rel_factor=2.0, min_abs_effect=0.2, abs_floor=0.05
            ),
            "server_error_ratio": SignalThresholds(
                min_abs_effect=0.02, abs_floor=0.002, absolute_high=0.05, absolute_min_minutes=5
            ),
            "client_error_rate": SignalThresholds(min_rel_factor=2.0, abs_floor=0.05),
            "client_error_ratio": SignalThresholds(min_abs_effect=0.01, abs_floor=0.002),
            "latency_quantile": SignalThresholds(
                min_rel_factor=1.5, min_abs_effect=0.05, abs_floor=0.005
            ),
        }
    )

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:12]
