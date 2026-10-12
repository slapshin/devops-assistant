"""Grafana alerting signals, rule selection, and the state-history format.

Rules come from Grafana's Prometheus-compatible rules API
(``/api/prometheus/grafana/api/v1/rules``), with their current instances and structured
labels. Their history comes from the annotations state-history backend
(``/api/annotations?type=alert&alertUID=<uid>``): one annotation per state change of one
instance. An annotation has the previous and new state, and the instance's labels appear only
inside its text, ``<title> {a=1, b=2} - A=1.0``. Grafana prints label sets sorted by name, so the
label text is a stable identity of an instance. Annotation tags repeat the labels as ``key:value``
but are lossy (``:`` in values becomes ``_``, long values are left out; verified on Grafana 12.4),
so they only fill in labels the text does not yield.

Selection only uses exact equality on configured values, so a configured value can never
widen the selection.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

from app.domain.common import LabelMatcher, SignalFamily, Unit
from app.sources.grafana.api import AlertRule

CATALOG_VERSION = "grafana-2026.10.12"
RULES_PATH = "/api/prometheus/grafana/api/v1/rules"
ANNOTATIONS_PATH = "/api/annotations"
HEALTH_PATH = "/api/health"
MAX_RULES = 100
"""Rules analysed per source; more are reported as truncated, never silently cut."""
PAGE_SIZE = 5000
MAX_TRANSITIONS_PER_RULE = 50_000
MAX_LISTED = 10
"""Names listed in one exclusion message."""
MAX_ATTRIBUTE_INSTANCES = 5
MAX_ATTRIBUTE_VALUE_CHARS = 64
SYSTEM_LABELS = frozenset({"alertname", "grafana_folder", "__alert_rule_uid__"})
"""Labels Grafana adds to every instance; they never tell instances apart."""
SEVERITY_LABEL = "severity"
FOLDER_ATTRIBUTE = "alert_folder"
GROUP_ATTRIBUTE = "alert_group"
INSTANCES_ATTRIBUTE = "alert_instances"
ATTRIBUTE_LABELS = (FOLDER_ATTRIBUTE, GROUP_ATTRIBUTE, INSTANCES_ATTRIBUTE)
"""Series labels copied into finding attributes."""


class State(StrEnum):
    FIRING = "firing"
    NODATA = "nodata"
    """No data or an evaluation error: the rule cannot say whether it should fire."""
    PAUSED = "paused"
    NORMAL = "normal"


def classify(state: str) -> State:
    """``Alerting``, ``Normal (MissingSeries)``, ``Pending (NoData)``, ``Normal (Paused)``..."""
    base, _, reason = state.partition("(")
    if reason.rstrip(") ").strip().lower() == "paused":
        return State.PAUSED
    match base.strip().lower():
        case "alerting" | "firing" | "recovering":
            return State.FIRING
        case "nodata" | "error":
            return State.NODATA
        case _:
            return State.NORMAL


@dataclass(frozen=True)
class GrafanaSignal:
    signal: str
    state: State
    description: str
    severities: frozenset[str] = frozenset()
    """Rule ``severity`` label values analysed under this signal; empty for the default."""
    family: SignalFamily = SignalFamily.ALERTING
    unit: Unit = Unit.COUNT


_CRITICAL = frozenset({"critical", "high", "page", "p1", "emergency"})
_INFO = frozenset({"info", "informational", "low", "none", "p4", "p5"})
SIGNALS = (
    GrafanaSignal("grafana_alert_firing", State.FIRING, "Firing instances"),
    GrafanaSignal(
        "grafana_alert_firing_critical",
        State.FIRING,
        "Firing instances of rules labelled severity=critical",
        _CRITICAL,
    ),
    GrafanaSignal(
        "grafana_alert_firing_info",
        State.FIRING,
        "Firing instances of rules labelled severity=info",
        _INFO,
    ),
    GrafanaSignal(
        "grafana_alert_nodata", State.NODATA, "Instances in NoData or Error (cannot evaluate)"
    ),
)
BY_SIGNAL = {s.signal: s for s in SIGNALS}


def firing_signal(rule: AlertRule) -> GrafanaSignal:
    """The firing signal of a rule, chosen by its ``severity`` label (default: medium)."""
    severity = rule.labels.get(SEVERITY_LABEL, "").strip().lower()
    return next(
        (s for s in SIGNALS if s.state is State.FIRING and severity in s.severities), SIGNALS[0]
    )


def signals_of(rule: AlertRule) -> tuple[GrafanaSignal, GrafanaSignal]:
    return firing_signal(rule), BY_SIGNAL["grafana_alert_nodata"]


# --- selection -----------------------------------------------------------------------------


def selects(
    rule: AlertRule,
    folders: Sequence[str],
    rule_uids: Sequence[str],
    rule_labels: Sequence[LabelMatcher],
) -> bool:
    """Folder or UID listed (or neither configured), and every rule label matcher equal."""
    listed = rule.uid in rule_uids or rule.folder_uid in folders or rule.folder in folders
    if (folders or rule_uids) and not listed:
        return False
    return all(rule.labels.get(m.name) == m.value for m in rule_labels)


def instance_matches(labels: Mapping[str, str], matchers: Sequence[LabelMatcher]) -> bool:
    return all(labels.get(m.name) == m.value for m in matchers)


# --- state history text --------------------------------------------------------------------

_LABEL_SPLIT = re.compile(r", (?=[A-Za-z_][A-Za-z0-9_]*=)")
_LABEL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VALUES_SEPARATOR = "} - "


def label_text(labels: Mapping[str, str]) -> str:
    """Grafana's rendering of a label set: ``{a=1, b=2}``, sorted by name."""
    return "{" + ", ".join(f"{k}={labels[k]}" for k in sorted(labels)) + "}"


def parse_text(text: str, title: str) -> tuple[str, dict[str, str]] | None:
    """(label text, labels) of a state-history annotation text, or None without a label set.

    Values may contain ``, ``, braces and newlines; a pair is only split before ``, name=``.
    """
    body = text[len(title) :] if text.startswith(title) else text
    start = body.find("{")
    end = body.rfind(_VALUES_SEPARATOR)
    if end < 0 and body.rstrip().endswith("}"):
        end = len(body.rstrip()) - 1
    if start < 0 or end < start:
        return None
    block = body[start : end + 1]
    labels: dict[str, str] = {}
    inner = block[1:-1]
    for part in _LABEL_SPLIT.split(inner) if inner else []:
        name, sep, value = part.partition("=")
        if sep and _LABEL_NAME.match(name):
            labels[name] = value
    return block, labels


def tag_labels(tags: Sequence[str]) -> dict[str, str]:
    """Labels of ``key:value`` annotation tags."""
    labels: dict[str, str] = {}
    for tag in tags:
        name, sep, value = tag.partition(":")
        if sep and _LABEL_NAME.match(name):
            labels[name] = value
    return labels


def instance_summary(labels: Mapping[str, str], hidden: set[str]) -> str:
    """Short ``name=value`` text of the labels that tell instances apart."""
    parts = [
        f"{k}={v}"
        for k, v in sorted(labels.items())
        if k not in SYSTEM_LABELS
        and k not in hidden
        and "\n" not in v
        and len(v) <= MAX_ATTRIBUTE_VALUE_CHARS
    ]
    return ", ".join(parts) or "(single instance)"


# --- requests ------------------------------------------------------------------------------


def history_path(uid: str, start_ms: int, end_ms: int) -> str:
    """Alert state annotations of one rule with ``start_ms <= time <= end_ms``."""
    query = urlencode(
        {"type": "alert", "alertUID": uid, "from": start_ms, "to": end_ms, "limit": PAGE_SIZE}
    )
    return f"{ANNOTATIONS_PATH}?{query}"


def display_query(rule: AlertRule, signal: GrafanaSignal, matchers: Sequence[LabelMatcher]) -> str:
    """The requests behind one rule's series (evidence text)."""
    selection = " AND ".join(f'{m.name}="{m.value}"' for m in matchers) or "every instance"
    return (
        f"# Grafana alert rule {rule.uid}: {signal.description.lower()}, instances: {selection}\n"
        f"GET {RULES_PATH}?rule_uid={rule.uid}\n"
        f"GET {ANNOTATIONS_PATH}?type=alert&alertUID={rule.uid}&from=$start&to=$end"
        f"&limit={PAGE_SIZE}"
    )
