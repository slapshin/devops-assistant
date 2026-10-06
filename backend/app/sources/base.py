"""Limits and windows shared by every source kind."""

from dataclasses import dataclass

HISTORY_DAYS = 28
"""Days collected before T: the 14-day trend plus up to 14 baseline days for its oldest day."""

MAX_RESPONSE_BYTES = 64 * 1024 * 1024
CHUNK_SECONDS = 7 * 86400


@dataclass
class ClientLimits:
    timeout_seconds: float = 30.0
    concurrency: int = 4
    retries: int = 1
    max_response_bytes: int = MAX_RESPONSE_BYTES
    chunk_seconds: int = CHUNK_SECONDS
    cache_entries: int = 256
    cache_ttl_seconds: float = 600.0
