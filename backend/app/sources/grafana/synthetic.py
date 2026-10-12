"""Deterministic synthetic Grafana alerting API for tests, UI development and offline demos.

It answers the same calls as the Grafana client (rules with current instances, and state
history as annotation-style transitions with label text), so a ``synthetic://<scenario>``
Grafana source runs the real selection, parsing and replay code. Scenario names match the other
synthetic sources; episodes are placed relative to the analysis end time T.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.interfaces import SourceError, SourceErrorKind
from app.sources.base import HISTORY_DAYS
from app.sources.grafana.api import (
    SECONDS_PER_DAY,
    AlertInstance,
    AlertRule,
    History,
    Transition,
)
from app.sources.grafana.catalog import label_text, parse_text, tag_labels

HOUR = 3600
MINUTE = 60
PENDING_SECONDS = 2 * MINUTE
MAX_TAG_VALUE_CHARS = 100
SHOP = ("shop-folder", "Shop")
PLATFORM = ("platform-folder", "Platform")
WORKER_EXCEPTION = "Lost connection to the queue, retrying\n  at consume (worker.js:42)"


@dataclass(frozen=True)
class _RuleSpec:
    uid: str
    title: str
    folder: tuple[str, str]
    group: str
    labels: dict[str, str]
    instances: tuple[dict[str, str], ...]
    summary: str = ""
    """``summary`` annotation; ``{{ $labels.instance }}`` is rendered per instance."""


RULES = (
    _RuleSpec(
        "shop-5xx",
        "Checkout 5xx errors",
        SHOP,
        "Sites",
        {"severity": "critical", "team": "web"},
        (
            {"env": "production", "instance": "shop-web-1", "project": "shop"},
            {"env": "production", "instance": "shop-web-2", "project": "shop"},
            {"env": "production", "instance": "blog-web-1", "project": "blog"},
        ),
        "{{ $labels.instance }} answers more than 5% of requests with 5xx",
    ),
    _RuleSpec(
        "shop-disk",
        "Disk almost full",
        SHOP,
        "Node",
        {"team": "ops"},
        ({"env": "production", "instance": "shop-db-1", "project": "shop"},),
        "Less than 10% of the data disk is free",
    ),
    _RuleSpec(
        "shop-queue",
        "Queue consumer lag",
        SHOP,
        "Jobs",
        {"severity": "warning"},
        ({"env": "production", "project": "shop", "queue": "orders"},),
    ),
    _RuleSpec(
        "shop-worker",
        "Worker exceptions",
        SHOP,
        "Jobs",
        {"severity": "info"},
        ({"env": "production", "exception": WORKER_EXCEPTION, "project": "shop"},),
    ),
    _RuleSpec(
        "platform-cpu",
        "Node high CPU",
        PLATFORM,
        "Node",
        {},
        ({"env": "production", "instance": "platform-1", "project": "platform"},),
    ),
)
BY_UID = {r.uid: r for r in RULES}


@dataclass(frozen=True)
class _Episode:
    """One instance in ``state`` for ``seconds``, ending ``ends_before`` seconds before T."""

    uid: str
    instance: int
    ends_before: int
    seconds: int
    state: str = "Alerting"


def _daily(uid: str, instance: int, hour: int, minutes: int, days: int) -> list[_Episode]:
    """A recurring episode at ``hour`` UTC-ish offset on each of the last ``days`` days."""
    return [
        _Episode(uid, instance, d * SECONDS_PER_DAY + hour * HOUR, minutes * MINUTE)
        for d in range(days)
    ]


@dataclass(frozen=True)
class GrafanaScenario:
    episodes: tuple[_Episode, ...] = ()
    pending_flaps: bool = True
    """Daily Pending -> Normal flaps of the worker rule (never firing)."""
    history_days: int = HISTORY_DAYS
    """Days of retained history; older changes are missing (the history is truncated)."""
    failing_rules: frozenset[str] = field(default_factory=frozenset)


_CHRONIC = tuple(_daily("shop-disk", 0, 3, 20, HISTORY_DAYS))
"""The disk rule fires for 20 minutes every day (a chronic, recurring alert)."""
_INCIDENT = (
    _Episode("shop-5xx", 0, 2 * HOUR, 90 * MINUTE),
    _Episode("shop-5xx", 1, 2 * HOUR + 10 * MINUTE, 60 * MINUTE),
    _Episode("shop-5xx", 2, 20 * HOUR, 30 * MINUTE),
    _Episode("shop-queue", 0, 5 * HOUR, 45 * MINUTE, "NoData"),
    _Episode("shop-worker", 0, 8 * HOUR, 20 * MINUTE),
    _Episode("shop-5xx", 0, 0, 30 * MINUTE),
)
SCENARIOS: dict[str, GrafanaScenario] = {
    "healthy": GrafanaScenario(),
    "incident": GrafanaScenario(episodes=_CHRONIC + _INCIDENT),
    "short-history": GrafanaScenario(episodes=_CHRONIC, history_days=5),
    "degraded": GrafanaScenario(
        episodes=_CHRONIC + _INCIDENT[:3], failing_rules=frozenset({"shop-queue"})
    ),
}


def _labels(spec: _RuleSpec, index: int) -> dict[str, str]:
    return {"alertname": spec.title, "grafana_folder": spec.folder[1]} | spec.instances[index]


def _annotations(spec: _RuleSpec, labels: dict[str, str] | None = None) -> dict[str, str]:
    """The rule's template, or rendered with an instance's labels."""
    if not spec.summary:
        return {}
    if labels is None:
        return {"summary": spec.summary}
    return {"summary": spec.summary.replace("{{ $labels.instance }}", labels.get("instance", ""))}


class SyntheticGrafanaApi:
    def __init__(self, scenario: str) -> None:
        self.name = scenario
        self.scenario = SCENARIOS[scenario]
        self._anchor: int | None = None
        self._changes: dict[str, list[Transition]] = {}

    @property
    def base_url(self) -> str:
        return f"synthetic://{self.name}"

    @property
    def backend(self) -> str | None:
        return "synthetic"

    async def version(self) -> str | None:
        return None

    def begin(self, end_time: datetime) -> None:
        anchor = int(end_time.timestamp())
        if anchor != self._anchor:
            self._anchor = anchor
            self._changes = self._generate(anchor)

    async def aclose(self) -> None:
        pass

    # --- generation -----------------------------------------------------------------------

    def _generate(self, end: int) -> dict[str, list[Transition]]:
        changes: dict[str, list[Transition]] = {spec.uid: [] for spec in RULES}
        next_id = 1

        def add(spec: _RuleSpec, index: int, at: int, prev: str, new: str) -> None:
            nonlocal next_id
            labels = _labels(spec, index)
            text = f"{spec.title} {label_text(labels)} - A=1.000000"
            tags = [f"{k}:{v}" for k, v in labels.items() if len(v) <= MAX_TAG_VALUE_CHARS]
            parsed = parse_text(text, spec.title)
            changes[spec.uid].append(
                Transition(
                    id=next_id,
                    time=at,
                    prev=prev,
                    next=new,
                    instance=parsed[0] if parsed else None,
                    labels=tag_labels(tags) | (parsed[1] if parsed else {}),
                )
            )
            next_id += 1

        for ep in self.scenario.episodes:
            spec = BY_UID[ep.uid]
            start = end - ep.ends_before - ep.seconds
            if ep.state == "Alerting":
                add(spec, ep.instance, start - PENDING_SECONDS, "Normal", "Pending")
                add(spec, ep.instance, start, "Pending", "Alerting")
            else:
                add(spec, ep.instance, start, "Normal", ep.state)
            if ep.ends_before > 0:
                add(spec, ep.instance, end - ep.ends_before, ep.state, "Normal")
        if self.scenario.pending_flaps:
            worker = BY_UID["shop-worker"]
            for d in range(HISTORY_DAYS):
                at = end - d * SECONDS_PER_DAY - 13 * HOUR
                add(worker, 0, at, "Normal", "Pending")
                add(worker, 0, at + MINUTE, "Pending", "Normal")
        for found in changes.values():
            found.sort(key=lambda t: (t.time, t.id))
        return changes

    def _end(self) -> int:
        if self._anchor is None:
            raise RuntimeError("begin() must be called before querying synthetic data")
        return self._anchor

    def _state_now(self, uid: str, labels: dict[str, str]) -> tuple[str, int | None]:
        text = label_text(labels)
        last = [t for t in self._changes[uid] if t.instance == text and t.time < self._end()]
        if not last:
            return "Normal", None
        return last[-1].next, last[-1].time

    # --- GrafanaApi -----------------------------------------------------------------------

    async def rules(self) -> list[AlertRule]:
        out = []
        for spec in RULES:
            instances = []
            for i in range(len(spec.instances)):
                labels = _labels(spec, i)
                state, since = self._state_now(spec.uid, labels)
                instances.append(AlertInstance(labels, state, since, _annotations(spec, labels)))
            out.append(
                AlertRule(
                    uid=spec.uid,
                    title=spec.title,
                    folder_uid=spec.folder[0],
                    folder=spec.folder[1],
                    group=spec.group,
                    labels=dict(spec.labels),
                    annotations=_annotations(spec),
                    instances=instances,
                )
            )
        return out

    async def history(self, rule: AlertRule, start: int, end: int) -> History:
        if rule.uid in self.scenario.failing_rules:
            raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 503 (synthetic)")
        oldest = self._end() - self.scenario.history_days * SECONDS_PER_DAY
        found = [t for t in self._changes.get(rule.uid, []) if max(start, oldest) <= t.time < end]
        return History(found, known_from=oldest if oldest > start else None)
