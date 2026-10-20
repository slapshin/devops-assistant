"""Scope diagnostics (T022): what a source's filter selected and which filter part removes data.

Recorded during collection, stored with the report and never fed to detection or AI.
"""

from enum import StrEnum
from typing import Final

from pydantic import Field

from app.domain.common import Contract, Labels, Reason, SignalFamily, SourceKind

MAX_SELECTED: Final = 50
MAX_SAMPLES: Final = 5
MAX_AVAILABLE: Final = 20
MAX_REQUESTS: Final = 120
MAX_CHECKS: Final = 160


class CountUnit(StrEnum):
    SERIES = "series"
    REQUESTS = "requests"
    EVENTS = "events"
    ALERTS = "alerts"
    AGENTS = "agents"
    RULES = "rules"
    INSTANCES = "instances"


class FilterField(StrEnum):
    MATCHER = "matcher"
    ZONE = "zone"
    HOSTNAME = "hostname"
    PROJECT = "project"
    ENVIRONMENT = "environment"
    TAG = "tag"
    AGENT = "agent"
    GROUP = "group"
    LABEL = "label"
    FOLDER = "folder"
    RULE_UID = "rule_uid"
    RULE_LABEL = "rule_label"
    INSTANCE_LABEL = "instance_label"


class SelectedEntity(Contract):
    """One thing the filter selected, e.g. a host, a Sentry project, an agent or an alert rule."""

    display_name: str = Field(min_length=1)
    labels: Labels = Field(description="Identity labels or identifying fields.")
    count: int = Field(ge=0, description="Data the entity had, in `unit`.")
    unit: CountUnit
    families: list[SignalFamily] = Field(default_factory=list)


class FilterCheck(Contract):
    """One filter part: data matched with the full filter versus with this part dropped.

    A listed item (a folder, rule UID, Sentry project, Wazuh agent or group, Cloudflare
    hostname) widens the selection instead of narrowing it: its `matched` is what the item
    selects within the rest of the filter and `matched_without` is null.
    """

    part: str = Field(min_length=1, description='The part as configured, e.g. env=~"prod|qa".')
    field: FilterField
    matched: int | None = Field(ge=0, description="With the full filter; null when unknown.")
    matched_without: int | None = Field(
        ge=0, description="With this part dropped; null when not applicable or failed."
    )
    unit: CountUnit
    available: list[str] = Field(
        default_factory=list,
        max_length=MAX_AVAILABLE,
        description="Values that exist for this field within the rest of the filter.",
    )
    available_truncated: bool = False
    matching: list[str] = Field(
        default_factory=list, description="The values of `available` this part accepts."
    )


class ScopeDiagnostics(Contract):
    """Snapshot of one source's selection at analysis time (2.8)."""

    kind: SourceKind
    summary: str = Field(description="E.g. '3 jobs, 12 instances, 418 series'.")
    selection: list[SelectedEntity] = Field(default_factory=list, max_length=MAX_SELECTED)
    selection_total: int = Field(ge=0, description="Selected entities before the cap.")
    samples: list[Labels] = Field(default_factory=list, max_length=MAX_SAMPLES)
    checks: list[FilterCheck] = Field(default_factory=list, max_length=MAX_CHECKS)
    requests: list[str] = Field(
        default_factory=list, max_length=MAX_REQUESTS, description="Requests sent; no secrets."
    )
    notes: list[Reason] = Field(default_factory=list)


_IRREGULAR = {"proxy": "proxies", "process": "processes", "custom": "custom"}


def counted(n: int, noun: str) -> str:
    """``1 rule``, ``2 rules``, ``3 proxies``, with thousands separators."""
    plural = _IRREGULAR.get(noun, f"{noun}s")
    return f"{n:,} {noun if n == 1 else plural}"


def capped_selection(entities: list[SelectedEntity]) -> list[SelectedEntity]:
    """The largest entities first, at most MAX_SELECTED."""
    return sorted(entities, key=lambda e: (-e.count, e.display_name))[:MAX_SELECTED]


def capped_available(values: list[str]) -> tuple[list[str], bool]:
    """Sorted unique values, at most MAX_AVAILABLE, and whether some were cut."""
    unique = sorted(set(values))
    return unique[:MAX_AVAILABLE], len(unique) > MAX_AVAILABLE


def capped_requests(requests: list[str]) -> list[str]:
    """Deduplicated in first-seen order, at most MAX_REQUESTS."""
    return list(dict.fromkeys(requests))[:MAX_REQUESTS]


def diagnostic_failed(what: str, error: Exception) -> Reason:
    message = getattr(error, "message", None) or type(error).__name__
    return Reason(code="diagnostic_failed", message=f"{what}: {message}")
