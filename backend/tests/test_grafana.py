"""Grafana alerts source (T019): HTTP client, history replay, detection, and project API."""

import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.ai.providers import FakeExplanationProvider
from app.analysis.engine import RobustDetector
from app.domain.auth import NoAuth
from app.domain.common import EntityKind, LabelMatcher, Scope, Severity, SignalFamily, SourceKind
from app.domain.detector_config import DetectorConfig
from app.domain.findings import FindingState, Recurrence
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    OpenedSource,
    SourceError,
    SourceErrorKind,
)
from app.domain.jobs import StageProgress
from app.domain.metrics import ActiveInterval, CapabilityStatus, MetricCapability, MetricSeries
from app.domain.projects import GrafanaConnection, GrafanaSourceInput
from app.domain.report import AnalysisReport, AnalysisRequest, AnalysisWindows, ReportState
from app.main import create_app
from app.service import AnalysisPipeline
from app.settings import load_settings
from app.sources.base import HISTORY_DAYS, ClientLimits
from app.sources.grafana.api import AlertInstance, AlertRule, History, Transition
from app.sources.grafana.catalog import PAGE_SIZE, State, classify, label_text, parse_text
from app.sources.grafana.client import GrafanaClient
from app.sources.grafana.connect import connect_grafana, probe_grafana
from app.sources.grafana.source import GrafanaMetricsSource
from app.sources.grafana.synthetic import SyntheticGrafanaApi
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from tests.helpers import (
    StaticSources,
    create_credential,
    credential_ref,
    credential_view,
    make_scope,
)

TOKEN = "glsa_s3cr3t-grafana-token"
END = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
SCOPE = Scope(project_id="p-grafana", project_name="Shop alerts", matchers=[])
SHOP = [LabelMatcher(name="project", value="shop")]
Handler = Callable[[httpx.Request], httpx.Response]


class Progress:
    async def update(self, progress: StageProgress) -> None:
        pass


def make_client(handler: Handler, api_url: str = "https://grafana.example.com") -> GrafanaClient:
    conn = GrafanaConnection(api_url=api_url, org_id=3, folders=["Shop"], token=SecretStr(TOKEN))
    return GrafanaClient(
        conn,
        limits=ClientLimits(timeout_seconds=5, retries=1),
        transport=httpx.MockTransport(handler),
    )


def annotation(
    ident: int, at: datetime, prev: str, new: str, labels: dict[str, str]
) -> dict[str, Any]:
    title = labels["alertname"]
    return {
        "id": ident,
        "alertId": 7,
        "alertName": title,
        "time": int(at.timestamp() * 1000),
        "timeEnd": int(at.timestamp() * 1000),
        "prevState": prev,
        "newState": new,
        "text": f"{title} {label_text(labels)} - A=1.000000",
        "tags": [f"{k}:{v}" for k, v in labels.items()],
    }


def rule(uid: str = "r1", **overrides: Any) -> AlertRule:
    fields: dict[str, Any] = {
        "uid": uid,
        "title": f"Rule {uid}",
        "folder_uid": "shop-folder",
        "folder": "Shop",
        "group": "Sites",
    }
    return AlertRule(**(fields | overrides))


# --- source input ---------------------------------------------------------------------------


def test_source_input_normalises_and_validates() -> None:
    source = GrafanaSourceInput.model_validate(
        {
            "api_url": "https://grafana.example.com/",
            "folders": [" Shop", "Infrastructure", "Shop"],
            "rule_uids": ["b2", "a1"],
            "instance_labels": [
                {"name": "project", "value": "shop"},
                {"name": "env", "value": "production"},
            ],
        }
    )

    assert source.api_url == "https://grafana.example.com"
    assert source.folders == ["Infrastructure", "Shop"]
    assert source.rule_uids == ["a1", "b2"]
    assert [m.name for m in source.instance_labels] == ["env", "project"]
    assert (source.org_id, source.tls_verify, source.auth) == (1, True, NoAuth())

    with pytest.raises(ValidationError, match="at least one is required"):
        GrafanaSourceInput(api_url="https://g", instance_labels=SHOP)
    with pytest.raises(ValidationError, match="invalid rule UIDs"):
        GrafanaSourceInput(api_url="https://g", rule_uids=["a b"])
    with pytest.raises(ValidationError, match="invalid folder names"):
        GrafanaSourceInput(api_url="https://g", folders=["a\nb"])
    with pytest.raises(ValidationError):
        GrafanaSourceInput(api_url="https://g", rule_uids=[f"r{i}" for i in range(101)])
    with pytest.raises(ValidationError, match="duplicate label names"):
        GrafanaSourceInput(api_url="https://g", folders=["Shop"], instance_labels=SHOP * 2)
    with pytest.raises(ValidationError):
        GrafanaSourceInput(api_url="https://g", folders=["Shop"], org_id=0)

    by_label = GrafanaSourceInput(
        api_url="https://g", rule_labels=[LabelMatcher(name="team", value="web")]
    )
    assert by_label.rule_labels[0].value == "web"


# --- state history format -------------------------------------------------------------------


def test_label_text_round_trips_through_the_history_text() -> None:
    labels = {"alertname": "Disk", "project": "shop", "env": "prod"}

    parsed = parse_text(f"Disk {label_text(labels)} - A=1.000000, B=0.000000", "Disk")

    assert parsed == ("{alertname=Disk, env=prod, project=shop}", labels)


def test_values_with_commas_braces_and_newlines_stay_whole() -> None:
    exception = "Lost connection, retrying {x}\n  at e.code() = 2013 (db:3306)"
    labels = {"alertname": "MV", "exception": exception, "view": "orders_mv"}
    text = f"MV {label_text(labels)} - A=1.000000"

    parsed = parse_text(text, "MV")

    assert parsed is not None
    assert parsed[1] == labels
    assert parsed[0] == label_text(labels)


def test_history_text_edge_cases() -> None:
    assert parse_text("Domain {alertname=Domain, type=infra} - ", "Domain") == (
        "{alertname=Domain, type=infra}",
        {"alertname": "Domain", "type": "infra"},
    )
    assert parse_text("Renamed {a=1}", "Old title") == ("{a=1}", {"a": "1"})
    assert parse_text("No labels at all", "No labels at all") is None


def test_states_are_classified() -> None:
    assert classify("Alerting") is State.FIRING
    assert classify("Recovering") is State.FIRING
    assert classify("NoData") is State.NODATA
    assert classify("Error") is State.NODATA
    assert classify("Pending (NoData)") is State.NORMAL
    assert classify("Normal (MissingSeries)") is State.NORMAL
    assert classify("Normal (Paused)") is State.PAUSED
    assert classify("") is State.NORMAL


# --- client ---------------------------------------------------------------------------------


async def test_rules_are_read_with_token_org_and_pagination() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        token = request.url.params.get("group_next_token")
        group = {
            "name": "Sites" if token is None else "Jobs",
            "file": "Shop",
            "folderUid": "shop-folder",
            "rules": [
                {
                    "uid": "r1" if token is None else "r2",
                    "name": "Site down",
                    "type": "alerting",
                    "labels": {"severity": "critical"},
                    "annotations": {"summary": "{{ $labels.project }} is down"},
                    "isPaused": token is not None,
                    "alerts": [
                        {
                            "labels": {"alertname": "Site down", "project": "shop"},
                            "annotations": {"summary": "shop is down"},
                            "state": "Alerting",
                            "activeAt": "2026-10-06T09:00:00Z",
                        },
                        {
                            "labels": {"project": "blog"},
                            "state": "Normal",
                            "activeAt": "0001-01-01T00:00:00Z",
                        },
                    ],
                },
                {"uid": "rec", "name": "recorded", "type": "recording"},
            ],
        }
        next_token = "page 2" if token is None else None
        return httpx.Response(
            200,
            json={"status": "success", "data": {"groups": [group], "groupNextToken": next_token}},
        )

    client = make_client(handler, "https://proxy.internal/grafana")

    rules = await client.rules()
    await client.aclose()

    assert [r.uid for r in rules] == ["r1", "r2"]
    assert rules[0].folder == "Shop" and rules[0].group == "Sites"
    assert rules[0].labels == {"severity": "critical"}
    assert rules[0].annotations == {"summary": "{{ $labels.project }} is down"}
    assert rules[0].instances[0].annotations == {"summary": "shop is down"}
    assert rules[0].instances[1].annotations == {}
    assert rules[1].paused
    assert rules[0].instances[0].active_at == int(datetime(2026, 10, 6, 9, tzinfo=UTC).timestamp())
    assert rules[0].instances[1].active_at is None
    assert seen[0].url.path == "/grafana/api/prometheus/grafana/api/v1/rules"
    assert seen[1].url.params["group_next_token"] == "page 2"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].headers["x-grafana-org-id"] == "3"
    assert {r.method for r in seen} == {"GET"}


async def test_history_pages_backwards_and_deduplicates() -> None:
    labels = {"alertname": "Rule r1", "project": "shop"}
    newest = END - timedelta(minutes=5)
    full_page = [
        annotation(10_000 - i, newest - timedelta(seconds=i), "Normal", "Alerting", labels)
        for i in range(PAGE_SIZE)
    ]
    oldest_ms = full_page[-1]["time"]
    second_page = [
        full_page[-1],
        annotation(1, END - timedelta(days=3), "Normal", "Pending", labels),
    ]
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlsplit(str(request.url)).query)
        seen.append(query)
        return httpx.Response(200, json=full_page if len(seen) == 1 else second_page)

    client = make_client(handler)
    start = int((END - timedelta(days=HISTORY_DAYS)).timestamp())

    history = await client.history(rule(), start, int(END.timestamp()))
    await client.aclose()

    assert len(history.transitions) == PAGE_SIZE + 1
    assert history.transitions[0].id == 1
    assert not history.truncated and history.known_from is None
    assert seen[0]["alertUID"] == ["r1"] and seen[0]["type"] == ["alert"]
    assert seen[0]["to"] == [str(int(END.timestamp()) * 1000 - 1)]
    assert seen[1]["to"] == [str(oldest_ms)]
    assert history.transitions[-1].instance == "{alertname=Rule r1, project=shop}"


async def test_a_full_page_within_one_millisecond_truncates_the_history() -> None:
    labels = {"alertname": "Rule r1", "project": "shop"}
    at = END - timedelta(hours=1)
    page = [annotation(i, at, "Normal", "Alerting", labels) for i in range(PAGE_SIZE)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    client = make_client(handler)

    history = await client.history(rule(), 0, int(END.timestamp()))
    await client.aclose()

    assert history.truncated
    assert history.known_from == int(at.timestamp())


async def test_tags_fill_in_labels_the_text_lacks() -> None:
    item = {
        "id": 1,
        "time": int(END.timestamp() * 1000) - 1000,
        "prevState": "Normal",
        "newState": "Alerting",
        "alertName": "Rule r1",
        "text": "Rule r1",
        "tags": ["project:shop", "env:prod"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[item])

    client = make_client(handler)

    history = await client.history(rule(), 0, int(END.timestamp()))
    await client.aclose()

    assert history.transitions[0].instance is None
    assert history.transitions[0].labels == {"project": "shop", "env": "prod"}


@pytest.mark.parametrize(
    ("response", "kind", "text"),
    [
        (httpx.Response(401), SourceErrorKind.AUTH, "rejected the service account token"),
        (
            httpx.Response(403, json={"message": "permissions needed: alert.rules:read"}),
            SourceErrorKind.AUTH,
            "needs the Viewer role",
        ),
        (httpx.Response(404), SourceErrorKind.BAD_QUERY, "alerting is enabled"),
        (
            httpx.Response(302, headers={"location": "/login"}),
            SourceErrorKind.BAD_QUERY,
            "redirect (HTTP 302) to /login",
        ),
        (httpx.Response(200, text="<html>"), SourceErrorKind.BAD_QUERY, "is this the Grafana URL"),
        (httpx.Response(200, json={"data": {}}), SourceErrorKind.BAD_QUERY, "data.groups"),
    ],
)
async def test_errors_are_classified_without_the_token(
    response: httpx.Response, kind: SourceErrorKind, text: str
) -> None:
    client = make_client(lambda request: response)

    with pytest.raises(SourceError) as caught:
        await client.rules()
    await client.aclose()

    assert caught.value.kind is kind
    assert text in caught.value.message
    assert TOKEN not in caught.value.message


async def test_server_errors_are_retried_once_and_certificates_are_explained() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"groups": []}})

    client = make_client(handler)

    assert await client.rules() == []
    assert calls == 2
    await client.aclose()

    def untrusted(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")

    client = make_client(untrusted)

    with pytest.raises(SourceError, match="turn off TLS verification"):
        await client.rules()
    assert await client.version() is None
    await client.aclose()


# --- selection and replay ---------------------------------------------------------------------


class FakeApi:
    """Fixed rules and histories; ``failing`` rules raise."""

    def __init__(
        self,
        rules: Sequence[AlertRule],
        histories: dict[str, History] | None = None,
        failing: Sequence[str] = (),
    ) -> None:
        self._rules = list(rules)
        self._histories = histories or {}
        self._failing = set(failing)

    @property
    def base_url(self) -> str:
        return "https://grafana.example.com"

    @property
    def backend(self) -> str | None:
        return "grafana"

    def begin(self, end_time: datetime) -> None:
        pass

    async def version(self) -> str | None:
        return "12.4.5"

    async def rules(self) -> list[AlertRule]:
        return self._rules

    async def history(self, rule: AlertRule, start: int, end: int) -> History:
        if rule.uid in self._failing:
            raise SourceError(SourceErrorKind.TIMEOUT, "no response within 30 s")
        return self._histories.get(rule.uid, History([]))

    async def aclose(self) -> None:
        pass


async def collect(source: GrafanaMetricsSource) -> tuple[CollectionResult, list[MetricCapability]]:
    windows = AnalysisWindows.for_end(END)
    caps = await source.capabilities(SCOPE, windows)
    result = await source.collect(SCOPE, windows, caps, Progress(), CancellationToken())
    return result, caps


def change(ident: int, at: datetime, prev: str, new: str, labels: dict[str, str]) -> Transition:
    return Transition(ident, int(at.timestamp()), prev, new, label_text(labels), labels)


def step_of(at: datetime) -> int:
    start = END - timedelta(days=HISTORY_DAYS)
    return int((at - start).total_seconds()) // 300


def by_signal(series: list[MetricSeries]) -> dict[str, MetricSeries]:
    return {s.signal: s for s in series}


async def test_rules_are_selected_by_folder_uid_and_rule_labels() -> None:
    rules = [
        rule("a", folder="Shop"),
        rule("b", folder="Infra", folder_uid="infra-folder"),
        rule("c", folder="Other", folder_uid="other"),
        rule("d", folder="Shop", labels={"team": "db"}),
    ]
    api = FakeApi(rules)

    by_folder = await GrafanaMetricsSource(api, ["Shop", "infra-folder"]).selection()
    by_uid = await GrafanaMetricsSource(api, rule_uids=["c"]).selection()
    narrowed = await GrafanaMetricsSource(
        api, ["Shop"], rule_labels=[LabelMatcher(name="team", value="db")]
    ).selection()
    labels_only = await GrafanaMetricsSource(
        api, rule_labels=[LabelMatcher(name="team", value="db")]
    ).selection()

    assert [r.uid for r in by_folder.rules] == ["b", "a", "d"]
    assert [r.uid for r in by_uid.rules] == ["c"]
    assert [r.uid for r in narrowed.rules] == ["d"]
    assert [r.uid for r in labels_only.rules] == ["d"]


async def test_too_many_rules_are_truncated_and_disclosed() -> None:
    rules = [rule(f"r{i:03}", instances=[AlertInstance({"a": "1"}, "Normal")]) for i in range(105)]

    result, _ = await collect(GrafanaMetricsSource(FakeApi(rules), ["Shop"]))

    assert len({s.entity.key for s in result.series}) == 100
    assert "series_truncated" in {e.code for e in result.exclusions}


async def test_firing_is_counted_per_instance_and_bucket() -> None:
    shop_1 = {"alertname": "Rule r1", "instance": "web-1", "project": "shop"}
    shop_2 = {"alertname": "Rule r1", "instance": "web-2", "project": "shop"}
    blog = {"alertname": "Rule r1", "instance": "blog-1", "project": "blog"}
    t0 = END - timedelta(hours=3)
    history = History(
        [
            change(1, t0, "Pending", "Alerting", shop_1),
            change(2, t0 + timedelta(minutes=2), "Normal", "Alerting", shop_2),
            change(3, t0, "Normal", "Alerting", blog),
            change(4, t0 + timedelta(minutes=20), "Alerting", "Normal", shop_1),
            change(5, t0 + timedelta(minutes=30), "Alerting", "Normal (MissingSeries)", shop_2),
        ]
    )
    api = FakeApi([rule(labels={"severity": "critical"})], {"r1": history})

    result, caps = await collect(GrafanaMetricsSource(api, ["Shop"], instance_labels=SHOP))

    assert {c.signal for c in caps} == {"grafana_alert_firing_critical", "grafana_alert_nodata"}
    series = by_signal(result.series)
    firing = series["grafana_alert_firing_critical"]
    i = step_of(t0)
    assert firing.values[i - 1 : i + 7] == [0.0, 2.0, 2.0, 2.0, 2.0, 1.0, 1.0, 0.0]
    assert firing.coverage == 1.0
    assert firing.entity.kind is EntityKind.ALERT_RULE
    assert firing.entity.key == "alert_rule|uid=r1"
    assert [(a.labels, a.active) for a in firing.alert_instances] == [
        (shop_1, [ActiveInterval(start=t0, end=t0 + timedelta(minutes=20))]),
        (shop_2, [ActiveInterval(start=t0 + timedelta(minutes=2), end=t0 + timedelta(minutes=30))]),
    ]
    assert firing.labels["severity"] == "critical"
    assert set(series["grafana_alert_nodata"].values) == {0.0}
    assert result.exclusions == []


async def test_instances_list_merged_intervals_annotations_and_open_ends() -> None:
    web = {"alertname": "Rule r1", "instance": "web-1", "project": "shop"}
    db = {"alertname": "Rule r1", "instance": "db-1", "project": "shop"}
    t0 = END - timedelta(hours=2)
    history = History(
        [
            change(1, END - timedelta(days=2), "Normal", "Alerting", web),
            change(2, END - timedelta(days=2, minutes=-5), "Alerting", "Normal", web),
            change(3, t0, "Normal", "Alerting", web),
            change(4, t0 + timedelta(minutes=5), "Alerting", "Recovering", web),
            change(5, t0 + timedelta(minutes=10), "Recovering", "Normal", web),
            change(6, t0, "Normal", "Alerting", db),
        ]
    )
    current = AlertInstance(db, "Alerting", int(t0.timestamp()), {"summary": "db-1 rebooted"})
    annotations = {"summary": "Host rebooted", "description": "{{ $labels.instance }} rebooted"}
    api = FakeApi([rule(annotations=annotations, instances=[current])], {"r1": history})

    result, _ = await collect(GrafanaMetricsSource(api, ["Shop"], instance_labels=SHOP))

    firing = by_signal(result.series)["grafana_alert_firing"]
    assert [(a.labels["instance"], a.annotations, a.active) for a in firing.alert_instances] == [
        ("db-1", {"summary": "db-1 rebooted"}, [ActiveInterval(start=t0, end=None)]),
        (
            "web-1",
            {"summary": "Host rebooted"},
            [ActiveInterval(start=t0, end=t0 + timedelta(minutes=10))],
        ),
    ]
    assert firing.labels["alert_summary"] == "Host rebooted"
    assert by_signal(result.series)["grafana_alert_nodata"].alert_instances == []


async def test_state_before_the_first_change_comes_from_its_previous_state() -> None:
    labels = {"alertname": "Rule r1", "project": "shop"}
    resolved = END - timedelta(days=2)
    history = History([change(1, resolved, "Alerting", "Normal", labels)])

    result, _ = await collect(
        GrafanaMetricsSource(FakeApi([rule()], {"r1": history}), ["Shop"], instance_labels=SHOP)
    )

    firing = by_signal(result.series)["grafana_alert_firing"].values
    assert set(firing[: step_of(resolved)]) == {1.0}
    assert set(firing[step_of(resolved) + 1 :]) == {0.0}


async def test_instances_without_changes_keep_their_current_state() -> None:
    firing_now = AlertInstance({"alertname": "Rule r1", "project": "shop"}, "Alerting", 0)
    later = AlertInstance(
        {"alertname": "Rule r2", "project": "shop"}, "Alerting", int(END.timestamp()) + 60
    )
    rules = [rule("r1", instances=[firing_now]), rule("r2", instances=[later])]

    result, _ = await collect(GrafanaMetricsSource(FakeApi(rules), ["Shop"], instance_labels=SHOP))

    values = {s.entity.display_name: set(s.values) for s in result.series if "firing" in s.signal}
    assert values == {"Rule r1": {1.0}, "Rule r2": {0.0}}


async def test_paused_rules_and_unretained_history_are_unknown_not_quiet() -> None:
    labels = {"alertname": "Rule r1", "project": "shop"}
    paused_at = END - timedelta(days=1)
    history = History([change(1, paused_at, "Normal", "Normal (Paused)", labels)])
    known_from = END - timedelta(days=5)
    short = History([], known_from=int(known_from.timestamp()))
    rules = [
        rule("r1"),
        rule("r2", instances=[AlertInstance({"project": "shop"}, "Normal")]),
        rule("r3", paused=True, instances=[AlertInstance({"project": "shop"}, "Normal (Paused)")]),
    ]

    result, _ = await collect(
        GrafanaMetricsSource(
            FakeApi(rules, {"r1": history, "r2": short}), ["Shop"], instance_labels=SHOP
        )
    )

    firing = {s.entity.display_name: s.values for s in result.series if "firing" in s.signal}
    assert firing["Rule r1"][step_of(paused_at) + 1 :] == [None] * (
        len(firing["Rule r1"]) - step_of(paused_at) - 1
    )
    assert firing["Rule r1"][step_of(paused_at) - 1] == 0.0
    assert set(firing["Rule r2"][: step_of(known_from)]) == {None}
    assert set(firing["Rule r2"][step_of(known_from) :]) == {0.0}
    assert set(firing["Rule r3"]) == {None}


async def test_unmatched_and_failed_rules_are_disclosed() -> None:
    blog = AlertInstance({"alertname": "Rule r1", "project": "blog"}, "Alerting")
    unlabelled = Transition(
        9, int((END - timedelta(hours=1)).timestamp()), "Normal", "Alerting", None, {}
    )
    rules = [
        rule("r1", instances=[blog]),
        rule("r2", instances=[AlertInstance({"project": "shop"}, "Normal")]),
        rule("r3"),
    ]
    api = FakeApi(rules, {"r2": History([unlabelled])}, failing=["r3"])

    result, _ = await collect(GrafanaMetricsSource(api, ["Shop"], instance_labels=SHOP))

    assert {s.entity.display_name for s in result.series} == {"Rule r2"}
    messages = [e.message for e in result.exclusions if e.code == "no_data"]
    assert any('"Rule r1" have no instance matching project="shop"' in m for m in messages)
    assert any("1 Grafana state changes have no readable instance labels" in m for m in messages)
    timeout = next(e for e in result.exclusions if e.code == "query_timeout")
    assert timeout.family is SignalFamily.ALERTING
    assert '"Rule r3"' in timeout.message


async def test_no_matching_rule_is_unsupported_not_healthy() -> None:
    result, caps = await collect(GrafanaMetricsSource(FakeApi([rule()]), ["Infra"]))

    assert {c.status for c in caps} == {CapabilityStatus.UNSUPPORTED}
    assert caps[0].reason and "No Grafana-managed alert rule" in caps[0].reason
    assert result.series == []


# --- synthetic scenarios through the pipeline -------------------------------------------------


async def run(*sources: OpenedSource) -> AnalysisReport:
    config = DetectorConfig()
    pipeline = AnalysisPipeline(
        StaticSources(*sources), RobustDetector(), config, FakeExplanationProvider()
    )
    request = AnalysisRequest(
        scope=make_scope(),
        end_time=END,
        detector_version=config.version,
        config_hash=config.config_hash,
    )
    return await pipeline.run(
        "01999a2b-0000-7000-8000-0000000000f2", request, Progress(), CancellationToken()
    )


def synthetic(scenario: str) -> OpenedSource:
    source = GrafanaMetricsSource(SyntheticGrafanaApi(scenario), ["Shop"], instance_labels=SHOP)
    return OpenedSource(SourceKind.GRAFANA, source)


async def test_incident_yields_alert_findings_by_severity() -> None:
    report = await run(synthetic("incident"))

    found = {(f.entity.display_name, f.signal, f.severity, f.state) for f in report.findings}
    assert found == {
        ("Checkout 5xx errors", "alert_firing_critical", Severity.HIGH, FindingState.RESOLVED),
        ("Checkout 5xx errors", "alert_firing_critical", Severity.HIGH, FindingState.ONGOING),
        ("Disk almost full", "alert_firing", Severity.MEDIUM, FindingState.RESOLVED),
        ("Worker exceptions", "alert_firing_info", Severity.LOW, FindingState.RESOLVED),
        ("Queue consumer lag", "alert_nodata", Severity.LOW, FindingState.RESOLVED),
    }
    disk = next(f for f in report.findings if f.entity.display_name == "Disk almost full")
    assert disk.recurrence is Recurrence.RECURRING
    critical = next(f for f in report.findings if f.signal == "alert_firing_critical")
    assert critical.attributes["alert_folder"] == "Shop"
    assert {a.labels["instance"] for a in critical.alert_instances} <= {"shop-web-1", "shop-web-2"}
    ongoing = next(f for f in report.findings if f.state is FindingState.ONGOING)
    assert [(a.labels["instance"], a.active[-1].end) for a in ongoing.alert_instances] == [
        ("shop-web-1", None)
    ]
    assert ongoing.alert_instances[0].annotations == {
        "summary": "shop-web-1 answers more than 5% of requests with 5xx"
    }
    assert report.state is ReportState.COMPLETED
    assert {t.status.value for t in report.trends} == {"ok"}


async def test_healthy_yields_no_findings_and_short_history_is_disclosed() -> None:
    healthy = await run(synthetic("healthy"))

    assert healthy.findings == []
    assert healthy.state is ReportState.COMPLETED

    short = await run(synthetic("short-history"))

    assert {f.entity.display_name for f in short.findings} == {"Disk almost full"}
    assert short.trends[-1].status.value == "insufficient_data"


async def test_degraded_is_partial() -> None:
    report = await run(synthetic("degraded"))

    assert report.state is ReportState.PARTIAL
    assert {e.code for e in report.exclusions} == {"query_failed"}


async def test_grafana_auth_failure_makes_a_mixed_report_partial() -> None:
    conn = GrafanaConnection(
        api_url="https://grafana:3000", folders=["Shop"], token=SecretStr(TOKEN)
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(401))

    async with connect_grafana(conn, transport=transport) as grafana:
        report = await run(
            OpenedSource(SourceKind.PROMETHEUS, SyntheticMetricsSource("incident")),
            OpenedSource(SourceKind.GRAFANA, grafana),
        )

    assert report.state is ReportState.PARTIAL
    assert report.findings
    failed = [e for e in report.exclusions if e.code == "source_unavailable"]
    assert {e.family for e in failed} == {SignalFamily.ALERTING}
    assert TOKEN not in report.model_dump_json()


async def test_probe_reports_rules_instances_and_families() -> None:
    conn = GrafanaConnection(api_url="synthetic://incident", folders=["Shop"], instance_labels=SHOP)

    async with connect_grafana(conn) as source:
        test = await probe_grafana(source, SCOPE)

    assert test.kind is SourceKind.GRAFANA
    assert test.reachable and test.auth_ok
    assert test.matched_series == 5
    assert [f.family for f in test.families] == [SignalFamily.ALERTING]

    synthetic_api = SyntheticGrafanaApi("healthy")
    synthetic_api.begin(END)
    live_source = GrafanaMetricsSource(
        FakeApi(await synthetic_api.rules()), ["Shop"], instance_labels=SHOP
    )

    live = await probe_grafana(live_source, SCOPE)

    assert live.message == (
        "4 alert rules selected, 5 matching instances, 0 rules firing now. Grafana 12.4.5."
    )

    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    denied_conn = GrafanaConnection(api_url="https://g", folders=["Shop"], token=SecretStr(TOKEN))

    async with connect_grafana(denied_conn, transport=transport) as source:
        denied = await probe_grafana(source, SCOPE)

    assert denied.reachable and denied.auth_ok is False
    assert TOKEN not in (denied.message or "")


# --- project API ------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path) -> Iterator[TestClient]:
    settings = load_settings(_env_file=None, data_dir=tmp_path, ai_provider="fake")
    with TestClient(create_app(settings)) as client:
        yield client


def grafana_body(**source: Any) -> dict[str, Any]:
    return {
        "name": "Shop alerts",
        "sources": [
            {
                "kind": "grafana",
                "api_url": "https://grafana.example.com",
                "folders": ["Shop"],
                **source,
            }
        ],
    }


def test_grafana_source_uses_a_credential(api: TestClient, tmp_path: Path) -> None:
    missing = api.post("/api/projects", json=grafana_body())

    assert missing.status_code == 422
    assert [e["field"] for e in missing.json()["errors"]] == ["body.sources.0.auth.credential_id"]

    credential = create_credential(api, "grafana", {"type": "token", "token": TOKEN})
    instance_labels = [{"name": "project", "op": "=", "value": "shop"}]
    res = api.post(
        "/api/projects",
        json=grafana_body(
            auth=credential_ref(credential),
            instance_labels=instance_labels,
            org_id=2,
            tls_verify=False,
        ),
    )

    assert res.status_code == 201, res.text
    assert res.json()["sources"] == [
        {
            "kind": "grafana",
            "api_url": "https://grafana.example.com",
            "org_id": 2,
            "folders": ["Shop"],
            "rule_uids": [],
            "rule_labels": [],
            "instance_labels": instance_labels,
            "tls_verify": False,
            "auth": credential_view(credential),
        }
    ]
    assert TOKEN not in api.get("/api/projects").text

    selectless = api.post(
        "/api/projects",
        json=grafana_body(folders=[], name="x", auth=credential_ref(credential)),
    )
    assert selectless.status_code == 422


def test_synthetic_grafana_project_end_to_end(api: TestClient) -> None:
    bad = api.post("/api/projects", json=grafana_body(api_url="synthetic://nope"))

    assert [e["field"] for e in bad.json()["errors"]] == ["body.sources.0.api_url"]

    body = grafana_body(
        api_url="synthetic://incident", instance_labels=[{"name": "project", "value": "shop"}]
    )
    project = api.post("/api/projects", json=body).json()

    health = api.get(f"/api/projects/{project['project_id']}/health").json()

    assert [h["kind"] for h in health] == ["grafana"]
    assert health[0]["reachable"] and health[0]["auth_ok"]

    submitted = api.post("/api/analyses", json={"project_id": project["project_id"]})
    analysis_id = submitted.json()["analysis"]["analysis_id"]
    deadline = time.monotonic() + 60
    while (job := api.get(f"/api/analyses/{analysis_id}").json())["state"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.05)

    assert job["state"] == "completed", job

    report = api.get(f"/api/analyses/{analysis_id}/report").json()
    assert [s["kind"] for s in report["sources"]] == ["grafana"]
    assert any(f["family"] == "alerting" for f in report["findings"])
