"""Deterministic identifiers.

Analysis (T005) owns finding/episode/evidence IDs; metrics (T004) owns series IDs. IDs derive
from the frozen job inputs, so re-running identical inputs yields identical IDs.
"""

import hashlib
import uuid

DIGEST_HEX_CHARS = 16
# ASCII unit separator, absent from real label values, keeps ("a", "bc") and ("ab", "c") apart.
_PART_SEPARATOR = "\x1f"


def _digest(*parts: str) -> str:
    return hashlib.sha256(_PART_SEPARATOR.join(parts).encode()).hexdigest()[:DIGEST_HEX_CHARS]


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
