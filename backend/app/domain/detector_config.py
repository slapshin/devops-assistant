"""Detector configuration with the provisional defaults from docs/DECISIONS.md §5.

All thresholds are diagnostic heuristics, never SLOs. Any change yields a new config_hash.
"""

import hashlib
import json

from pydantic import Field

from app.domain.common import STEP_SECONDS, Contract

DETECTOR_VERSION = "detectors-2026.10.8"
CONFIG_HASH_HEX_CHARS = 12


class SignalThresholds(Contract):
    min_abs_effect: float | None = None
    min_rel_factor: float | None = None
    abs_floor: float = Field(gt=0, description="Scale floor for zero/low MAD.")
    absolute_high: float | None = None
    absolute_critical: float | None = None
    absolute_min_minutes: int = 15


_T = SignalThresholds
_MIB = 1024.0 * 1024.0
_DEFAULT_SIGNALS: dict[str, SignalThresholds] = {
    "cpu_utilization": _T(min_abs_effect=0.10, abs_floor=0.01, absolute_high=0.90),
    "cpu_iowait": _T(min_abs_effect=0.05, abs_floor=0.005, absolute_high=0.20),
    "memory_utilization": _T(min_abs_effect=0.10, abs_floor=0.01, absolute_high=0.90),
    "memory_pressure": _T(min_abs_effect=0.05, abs_floor=0.005, absolute_high=0.20),
    "io_pressure": _T(min_abs_effect=0.05, abs_floor=0.005, absolute_high=0.30),
    "filesystem_used_ratio": _T(
        min_abs_effect=0.05,
        abs_floor=0.005,
        absolute_high=0.90,
        absolute_critical=0.95,
        absolute_min_minutes=5,
    ),
    "filesystem_inodes_used_ratio": _T(
        min_abs_effect=0.05,
        abs_floor=0.005,
        absolute_high=0.90,
        absolute_critical=0.95,
        absolute_min_minutes=5,
    ),
    "disk_busy_ratio": _T(min_abs_effect=0.20, abs_floor=0.01, absolute_high=0.90),
    "disk_io_bytes": _T(min_rel_factor=2.0, min_abs_effect=_MIB, abs_floor=100 * 1024.0),
    "network_bytes": _T(min_rel_factor=2.0, min_abs_effect=100 * 1024.0, abs_floor=1024.0),
    "network_errors": _T(min_abs_effect=1.0, abs_floor=0.01),
    "container_cpu": _T(min_rel_factor=1.25, min_abs_effect=0.1, abs_floor=0.01),
    "container_memory_working_set": _T(
        min_rel_factor=1.25, min_abs_effect=50 * _MIB, abs_floor=_MIB
    ),
    "container_memory_limit_ratio": _T(min_rel_factor=1.25, abs_floor=0.01, absolute_high=0.90),
    "container_throttling_ratio": _T(min_rel_factor=1.25, abs_floor=0.01, absolute_high=0.25),
    "request_rate": _T(min_rel_factor=2.0, min_abs_effect=0.2, abs_floor=0.05),
    "server_error_ratio": _T(
        min_abs_effect=0.02, abs_floor=0.002, absolute_high=0.05, absolute_min_minutes=5
    ),
    "rpc_error_ratio": _T(
        min_abs_effect=0.02, abs_floor=0.002, absolute_high=0.05, absolute_min_minutes=5
    ),
    "not_found_rate": _T(min_rel_factor=2.0, min_abs_effect=0.1, abs_floor=0.05),
    "client_error_rate": _T(min_rel_factor=2.0, min_abs_effect=0.1, abs_floor=0.05),
    "latency_quantile": _T(min_rel_factor=1.5, min_abs_effect=0.05, abs_floor=0.005),
    "proxy_connections_active": _T(min_rel_factor=2.0, min_abs_effect=20.0, abs_floor=1.0),
    "proxy_connections_dropped": _T(min_abs_effect=0.1, abs_floor=0.01),
    "database_connections_ratio": _T(
        min_abs_effect=0.10,
        abs_floor=0.01,
        absolute_high=0.80,
        absolute_critical=0.95,
        absolute_min_minutes=5,
    ),
    "database_replication_lag": _T(
        min_rel_factor=2.0, min_abs_effect=30.0, abs_floor=1.0, absolute_high=300.0
    ),
    "database_transaction_rate": _T(min_rel_factor=2.0, min_abs_effect=1.0, abs_floor=0.1),
    "database_rollback_ratio": _T(min_rel_factor=2.0, min_abs_effect=0.05, abs_floor=0.005),
    "database_temp_bytes": _T(min_rel_factor=2.0, min_abs_effect=_MIB, abs_floor=100 * 1024.0),
    "database_longest_transaction": _T(min_rel_factor=2.0, min_abs_effect=60.0, abs_floor=1.0),
    "database_query_rate": _T(min_rel_factor=2.0, min_abs_effect=5.0, abs_floor=0.5),
    "database_slow_query_ratio": _T(min_rel_factor=2.0, min_abs_effect=0.01, abs_floor=0.001),
    "database_lock_waits": _T(min_rel_factor=2.0, min_abs_effect=1.0, abs_floor=0.1),
    "database_tmp_disk_tables": _T(min_rel_factor=2.0, min_abs_effect=1.0, abs_floor=0.1),
    "database_memory_ratio": _T(min_abs_effect=0.10, abs_floor=0.01),
    "database_evictions": _T(min_rel_factor=2.0, min_abs_effect=1.0, abs_floor=0.1),
    "database_command_latency": _T(min_rel_factor=2.0, min_abs_effect=0.0005, abs_floor=0.00001),
    "database_cache_miss_ratio": _T(min_rel_factor=1.5, min_abs_effect=0.10, abs_floor=0.01),
    "edge_origin_error_ratio": _T(
        min_abs_effect=0.01, abs_floor=0.001, absolute_high=0.02, absolute_min_minutes=5
    ),
    "edge_cache_hit_ratio": _T(min_abs_effect=0.15, abs_floor=0.01),
    "security_event_rate": _T(min_rel_factor=3.0, min_abs_effect=0.1, abs_floor=0.01),
    "app_error_rate": _T(min_rel_factor=3.0, min_abs_effect=0.02, abs_floor=0.005),
    "app_unhandled_error_rate": _T(min_rel_factor=3.0, min_abs_effect=0.01, abs_floor=0.002),
    "app_error_users": _T(min_rel_factor=2.0, min_abs_effect=5.0, abs_floor=1.0),
    "app_transaction_failure_ratio": _T(
        min_abs_effect=0.03, abs_floor=0.003, absolute_high=0.10, absolute_min_minutes=10
    ),
}


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
    shortfall_min_minutes: int = 15
    relation_slack_minutes: int = 30
    signals: dict[str, SignalThresholds] = Field(default_factory=lambda: dict(_DEFAULT_SIGNALS))

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:CONFIG_HASH_HEX_CHARS]
