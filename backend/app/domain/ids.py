"""Deterministic identifiers.

Analysis (T005) owns finding/episode/evidence IDs; metrics (T004) owns series IDs. IDs derive
from the frozen job inputs, so re-running identical inputs yields identical IDs.
"""

import hashlib
import uuid


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def new_analysis_id() -> str:
    return str(uuid.uuid7())


def series_id(query: str, label_key: str) -> str:
    return f"ser_{_digest(query, label_key)}"


def finding_id(analysis_key: str, entity_key: str, signal: str, start_iso: str) -> str:
    return f"fnd_{_digest(analysis_key, entity_key, signal, start_iso)}"


def episode_id(analysis_key: str, entity_key: str, signal: str, start_iso: str) -> str:
    return f"eps_{_digest(analysis_key, entity_key, signal, start_iso)}"


def evidence_id(finding: str, series: str) -> str:
    return f"evd_{_digest(finding, series)}"
