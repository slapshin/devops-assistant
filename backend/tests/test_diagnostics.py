"""Scope diagnostics (T022): what each source's filter selected and which part removes data."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.domain.common import LabelMatcher, Scope, SourceKind
from app.domain.diagnostics import (
    MAX_AVAILABLE,
    MAX_REQUESTS,
    MAX_SELECTED,
    CountUnit,
    FilterField,
    ScopeDiagnostics,
    SelectedEntity,
    capped_available,
    capped_requests,
    capped_selection,
    counted,
)
from app.domain.interfaces import CancellationToken
from app.domain.projects import SentryTag, WazuhLabel
from app.domain.report import AnalysisReport, AnalysisWindows
from app.sources.grafana.api import AlertInstance
from app.sources.grafana.diagnostics import grafana_diagnostics
from app.sources.prometheus.diagnostics import matcher_checks
from app.sources.prometheus.promql import ScopeViolation, assert_leave_one_out, assert_scoped
from app.sources.prometheus.source import PrometheusMetricsSource
from app.sources.prometheus.synthetic import SyntheticMetricsSource
from app.sources.sentry.source import SentryMetricsSource
from app.sources.wazuh.api import AgentList
from app.sources.wazuh.catalog import discovery_body
from app.sources.wazuh.source import WazuhMetricsSource
from app.sources.wazuh.synthetic import SyntheticWazuhApi
from tests import test_cloudflare, test_sentry, test_wazuh
from tests.helpers import make_scope
from tests.test_grafana import rule
from tests.test_metrics import FakeProm, Progress, ok, vector

SCOPE = make_scope()
T = datetime(2026, 9, 30, 10, 5, tzinfo=UTC)
WINDOWS = AnalysisWindows.for_end(T)

NAMES = "node_cpu_seconds_total|up"
ENV = LabelMatcher(name="env", value="prod")
PROJECT = LabelMatcher(name="project", value="shop")
WRONG = Scope(project_id="p", project_name="shop", matchers=[ENV, PROJECT])


def universe(path: str, params: dict[str, list[str]]) -> httpx.Response:
    """Series exist only with env=production: env="prod" matches nothing."""
    if path.endswith("/values"):
        match = params["match[]"][0]
        if path.endswith("/env/values"):
            return ok(["production", "staging"])
        return ok(["blog", "shop"] if 'env="prod"' not in match else [])
    query = params["query"][0]
    if 'env="prod"' in query:
        return vector()
    if 'project="shop"' in query:
        return vector(({}, "120"))
    return vector(({}, "900"))


# --- scoping ------------------------------------------------------------------------------


def test_leave_one_out_needs_every_other_matcher() -> None:
    assert_leave_one_out('count({__name__=~"up", project="shop"})', WRONG, ENV)

    with pytest.raises(ScopeViolation):
        assert_leave_one_out('count({__name__=~"up"})', WRONG, ENV)
    with pytest.raises(ScopeViolation):
        assert_leave_one_out(
            'count({__name__=~"up", env="prod"})', WRONG, LabelMatcher(name="x", value="y")
        )
    with pytest.raises(ScopeViolation):
        assert_scoped('count({__name__=~"up", project="shop"})', WRONG, ())


async def test_a_wrong_matcher_matches_nothing_while_its_label_has_other_values() -> None:
    fake = FakeProm(universe)

    checks, notes, requests = await matcher_checks(fake.client(), WRONG, T, NAMES)

    env, project = checks
    assert (env.part, env.field, env.unit) == ('env="prod"', FilterField.MATCHER, CountUnit.SERIES)
    assert (env.matched, env.matched_without) == (0, 120)
    assert (env.available, env.matching) == (["production", "staging"], [])
    assert (project.matched, project.matched_without) == (0, 0)
    assert notes == []
    assert requests[0] == f'count({{__name__=~"{NAMES}", env="prod", project="shop"}})'


async def test_only_counts_and_label_values_leave_the_scope() -> None:
    fake = FakeProm(universe)

    await matcher_checks(fake.client(), WRONG, T, NAMES)

    sent = [
        (path, params.get("query", params.get("match[]", ""))[0]) for path, params in fake.requests
    ]
    assert len(sent) == 1 + 2 * len(WRONG.matchers)
    for path, text in sent:
        if path.endswith("/api/v1/query"):
            assert text.startswith(f'count({{__name__=~"{NAMES}"')
        else:
            assert path.endswith(("/label/env/values", "/label/project/values"))
            assert text.startswith(f'{{__name__=~"{NAMES}"')
        assert 'project="shop"' in text or 'env="prod"' in text


async def test_single_matcher_projects_count_the_whole_catalog_without_it() -> None:
    scope = Scope(project_id="p", project_name="p", matchers=[PROJECT])
    fake = FakeProm(universe)

    checks, _, _ = await matcher_checks(fake.client(), scope, T, NAMES)

    assert (checks[0].matched, checks[0].matched_without) == (120, 900)
    assert checks[0].matching == ["shop"]
    assert fake.requests[1][1]["query"] == [f'count({{__name__=~"{NAMES}"}})']


async def test_a_failing_check_is_a_note_and_never_fails_collection() -> None:
    def refuse_label_values(path: str, params: dict[str, list[str]]) -> httpx.Response:
        if path.endswith("/values"):
            return httpx.Response(503, text="overloaded")
        if "query_range" in path:
            return ok({"resultType": "matrix", "result": []})
        return universe(path, params)

    source = PrometheusMetricsSource(FakeProm(refuse_label_values).client(retries=0))

    result = await source.collect(SCOPE, WINDOWS, [], Progress(), CancellationToken())

    (diag,) = result.diagnostics
    assert [n.code for n in diag.notes] == [
        "nothing_selected",
        "diagnostic_failed",
        "diagnostic_failed",
    ]
    assert [(c.matched_without, c.available) for c in diag.checks] == [(120, []), (900, [])]
    assert result.exclusions == []


async def test_prometheus_selection_groups_series_by_entity() -> None:
    source = SyntheticMetricsSource("incident")

    caps = await source.capabilities(SCOPE, WINDOWS)
    result = await source.collect(SCOPE, WINDOWS, caps, Progress(), CancellationToken())

    (diag,) = result.diagnostics
    assert diag.kind is SourceKind.PROMETHEUS
    assert sum(e.count for e in diag.selection) == len(result.series)
    assert diag.selection_total == len({s.entity.key for s in result.series})
    assert diag.summary.endswith(f"{len(result.series)} series")
    assert [c.part for c in diag.checks] == ['env="production"', 'project="shop"']
    assert all(c.matching == c.available for c in diag.checks)
    assert len({json.dumps(s, sort_keys=True) for s in diag.samples}) == len(diag.samples)


# --- grafana ------------------------------------------------------------------------------


def test_grafana_shows_existing_folders_for_a_wrong_folder() -> None:
    instances = [
        AlertInstance({"project": "shop", "instance": "web-1"}, "Alerting"),
        AlertInstance({"project": "blog", "instance": "web-2"}, "Normal"),
    ]
    rules = [
        rule("a", labels={"team": "web"}, instances=instances),
        rule("b", folder="Platform", folder_uid="platform", labels={"team": "ops"}),
    ]
    team = LabelMatcher(name="team", value="web")
    project = LabelMatcher(name="project", value="shop")

    diag = grafana_diagnostics(rules, [], ["Shopp"], [], [team], [project])

    folder, label, inst = diag.checks
    assert (folder.field, folder.matched, folder.matched_without) == (FilterField.FOLDER, 0, None)
    assert (folder.available, folder.matching) == (["Shop"], [])
    assert (label.matched, label.matched_without, label.available) == (0, 0, [])
    assert (inst.matched, inst.matched_without) == (0, 0)
    assert [n.code for n in diag.notes] == ["nothing_selected"]

    diag = grafana_diagnostics(rules, [rules[0]], ["Shop"], [], [team], [project])

    folder, label, inst = diag.checks
    assert (folder.matched, folder.matching) == (1, ["Shop"])
    assert (label.matched, label.matched_without, label.available) == (1, 1, ["web"])
    assert (inst.matched, inst.matched_without, inst.matching) == (1, 2, ["shop"])
    assert diag.summary == "1 of 2 rules, 1 current instance"
    assert diag.selection[0].count == 1
    assert diag.samples == [{"project": "shop", "instance": "web-1"}]


# --- wazuh --------------------------------------------------------------------------------


async def test_wazuh_drops_each_label_and_lists_agents_for_names() -> None:
    class Labelled(SyntheticWazuhApi):
        """Only web-1 carries ``env=prod``."""

        async def agents(self, names: Any, start: int, end: int, labels: Any = None) -> AgentList:
            found = await super().agents(names, start, end, labels)
            chosen = self.labels if labels is None else labels
            if ("env", "prod") in chosen:
                return AgentList([a for a in found.agents if a.name == "web-1"])
            return found

    labels = [WazuhLabel(key="env", value="prod"), WazuhLabel(key="team", value="shop")]
    api = Labelled("healthy", test_wazuh.INDEX, ["web-1", "web-2"], labels)

    result, _ = await test_wazuh.collect(
        WazuhMetricsSource(api, test_wazuh.INDEX, ["web-1", "web-2", "typo"], labels)
    )

    (diag,) = result.diagnostics
    by_part = {c.part: c for c in diag.checks}
    assert (by_part["web-1"].matched > 0, by_part["web-1"].matching) == (True, ["web-1"])
    assert (by_part["typo"].matched, by_part["typo"].matching) == (0, [])
    assert by_part["typo"].available == ["web-1"]
    assert (by_part["env=prod"].matched, by_part["env=prod"].matched_without) == (1, 2)
    assert (by_part["team=shop"].matched, by_part["team=shop"].matched_without) == (1, 1)
    assert [e.display_name for e in diag.selection if e.count == 0] == ["typo", "web-2"]
    assert diag.summary == "1 agent with alerts, " + diag.summary.split(", ")[1] + ", 2 silent"


async def test_wazuh_client_sends_the_given_labels() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"aggregations": {"agents": {"buckets": []}}})

    client = test_wazuh.make_client(handler)

    await client.agents(None, 0, 300, [("env", "prod")])

    assert bodies == [discovery_body([], [("env", "prod")], 0, 300)]


# --- sentry -------------------------------------------------------------------------------


async def test_sentry_wrong_environment_shows_the_existing_ones() -> None:
    api = test_sentry.FakeApi()
    source = SentryMetricsSource(
        api, test_sentry.ORG, api.slugs, "prod", [SentryTag(key="team", value="shop")]
    )
    windows = AnalysisWindows.for_end(test_sentry.END)

    caps = await source.capabilities(test_sentry.SCOPE, windows)
    result = await source.collect(test_sentry.SCOPE, windows, caps, Progress(), CancellationToken())

    (diag,) = result.diagnostics
    project, env, tag = diag.checks
    assert (project.field, project.matched) == (FilterField.PROJECT, 0)
    assert (env.matched, env.matched_without) == (0, 3 * 144)
    assert (env.available, env.matching) == (["production", "staging"], [])
    assert (tag.part, tag.matched, tag.matched_without) == ("team:shop", 0, 0)
    assert [n.code for n in diag.notes] == ["nothing_selected"]
    assert diag.summary == "1 project, 0 error events in 24 h"


async def test_sentry_client_lists_environments_and_counts_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/environments/"):
            return httpx.Response(200, json=[{"name": "staging"}, {"name": "production"}, {}])
        return httpx.Response(200, json={"data": [{"count()": 41}]})

    client = test_sentry.make_client(handler)

    assert await client.environments("shop-web") == ["production", "staging"]
    assert await client.error_count(["42"], 0, 300, None, [("team", "a")]) == 41

    params = dict(seen[1].url.params.multi_items())
    assert seen[0].url.path == "/api/0/projects/acme/shop-web/environments/"
    assert (params["field"], params["project"], params["query"]) == ("count()", "42", 'team:"a"')
    assert "environment" not in params
    assert test_sentry.TOKEN not in str(seen[1].url)


# --- cloudflare ---------------------------------------------------------------------------


async def test_cloudflare_lists_busiest_hostnames_beside_the_configured_ones() -> None:
    _, result = await test_cloudflare.collect(test_cloudflare.FakeApi())

    (diag,) = result.diagnostics
    zone, host = diag.checks
    assert (zone.field, zone.matched) == (FilterField.ZONE, 86400 + 10)
    assert (host.part, host.matched) == ("shop.example.com", 86400)
    assert (host.available, host.matching) == (
        ["shop.example.com", "www.example.com"],
        ["shop.example.com"],
    )
    assert [e.display_name for e in diag.selection] == ["shop.example.com"]
    assert diag.summary == "1 hostname, 86,400 requests in 24 h (zone: 86,410)"


async def test_cloudflare_client_groups_requests_by_hostname() -> None:
    documents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        documents.append(json.loads(request.content)["query"])
        rows = [
            {"count": 7, "dimensions": {"clientRequestHTTPHost": "a.example.com"}},
            {"count": 2, "dimensions": {"clientRequestHTTPHost": ""}},
        ]
        return httpx.Response(200, json={"data": {"viewer": {"zones": [{"hosts": rows}]}}})

    client = test_cloudflare.make_client(handler)

    assert await client.host_requests(0, 300, ["a.example.com"], 50) == {"a.example.com": 7.0}
    assert "clientRequestHTTPHost_in" in documents[0]
    assert "orderBy: [count_DESC]" in documents[0]


# --- contract -----------------------------------------------------------------------------


def test_bounds_and_wording() -> None:
    entities = [
        SelectedEntity(display_name=f"e{i}", labels={}, count=i, unit=CountUnit.SERIES)
        for i in range(MAX_SELECTED + 5)
    ]

    assert [e.count for e in capped_selection(entities)][:2] == [MAX_SELECTED + 4, MAX_SELECTED + 3]
    assert len(capped_selection(entities)) == MAX_SELECTED
    assert capped_available([str(i) for i in range(MAX_AVAILABLE + 1)])[1] is True
    assert capped_available(["b", "a", "b"]) == (["a", "b"], False)
    assert len(capped_requests([str(i) for i in range(MAX_REQUESTS + 1)])) == MAX_REQUESTS
    assert capped_requests(["x", "y", "x"]) == ["x", "y"]
    assert (counted(1, "rule"), counted(2, "proxy"), counted(1200, "process")) == (
        "1 rule",
        "2 proxies",
        "1,200 processes",
    )


def test_reports_before_2_8_load_without_diagnostics() -> None:
    with open("../fixtures/reports/report_healthy.json") as f:
        raw = json.load(f)
    raw["schema_version"] = "2.7"
    raw.pop("diagnostics", None)

    report = AnalysisReport.model_validate(raw)

    assert report.diagnostics == []
    assert (
        ScopeDiagnostics.model_validate(
            {"kind": "wazuh", "summary": "", "selection_total": 0}
        ).checks
        == []
    )
