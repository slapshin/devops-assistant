"""Project management routes (T011): CRUD, connection test, and cached per-project health."""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Response

from app.api.problems import ProblemError, problem_responses, validation_problem
from app.api.routes import ServicesDep
from app.container import Services
from app.domain.common import STEP_SECONDS, LabelMatcher, Scope, floor_to_step
from app.domain.custom_rules import CustomRulePreview
from app.domain.jobs import ErrorCode
from app.domain.projects import (
    CloudflareConnection,
    CloudflareSourceInput,
    ConnectionTest,
    ConnectionTestRequest,
    CustomRulePreviewRequest,
    GrafanaConnection,
    GrafanaSourceInput,
    Project,
    ProjectInput,
    ProjectList,
    ProjectSummary,
    PrometheusConnection,
    PrometheusSourceInput,
    SentryConnection,
    SentrySourceInput,
    SourceConnection,
    SourceInput,
    WazuhConnection,
    WazuhSourceInput,
    synthetic_url,
)
from app.sources.cloudflare.connect import connect_cloudflare, probe_cloudflare
from app.sources.cloudflare.synthetic import SCENARIOS as CLOUDFLARE_SCENARIOS
from app.sources.factory import connect
from app.sources.grafana.connect import connect_grafana, probe_grafana
from app.sources.grafana.synthetic import SCENARIOS as GRAFANA_SCENARIOS
from app.sources.prometheus.probe import probe
from app.sources.prometheus.synthetic import SCENARIOS
from app.sources.sentry.connect import connect_sentry, probe_sentry
from app.sources.sentry.synthetic import SCENARIOS as SENTRY_SCENARIOS
from app.sources.wazuh.connect import connect_wazuh, probe_wazuh
from app.sources.wazuh.synthetic import SCENARIOS as WAZUH_SCENARIOS
from app.storage.projects import CredentialInvalid, ProjectNameTaken
from app.storage.secrets import SecretsUnreadable

router = APIRouter(prefix="/api/projects", tags=["projects"])

HEALTH_CACHE_SECONDS = 60


def _not_found(project_id: str) -> ProblemError:
    return ProblemError(
        404, ErrorCode.PROJECT_NOT_FOUND, "Project not found", f"No project {project_id}."
    )


def _invalid(field: str, message: str) -> ProblemError:
    return validation_problem(field, message)


def _unreadable() -> ProblemError:
    return ProblemError(
        409,
        ErrorCode.CREDENTIALS_UNREADABLE,
        "Stored credentials are unreadable",
        "The secret key changed or was lost; re-enter the source credentials.",
    )


DRAFT_PROJECT_ID = "draft"
DRAFT_PROJECT_NAME = "Connection test"


def _check_scenario(source: SourceInput, field_prefix: str) -> None:
    match source:
        case PrometheusSourceInput(url=url):
            field, scenarios = "url", sorted(SCENARIOS)
        case CloudflareSourceInput(api_url=url):
            field, scenarios = "api_url", sorted(CLOUDFLARE_SCENARIOS)
        case SentrySourceInput(api_url=url):
            field, scenarios = "api_url", sorted(SENTRY_SCENARIOS)
        case WazuhSourceInput(api_url=url):
            field, scenarios = "api_url", sorted(WAZUH_SCENARIOS)
        case GrafanaSourceInput(api_url=url):
            field, scenarios = "api_url", sorted(GRAFANA_SCENARIOS)
    scenario = synthetic_url(url)
    if scenario is not None and scenario not in scenarios:
        raise _invalid(
            f"{field_prefix}.{field}",
            f"unknown synthetic scenario; expected one of {scenarios}",
        )


def _validate(data: ProjectInput) -> None:
    for i, source in enumerate(data.sources):
        _check_scenario(source, f"body.sources.{i}")


def _write_error(exc: Exception, field_prefix: str) -> ProblemError:
    match exc:
        case ProjectNameTaken(name=name):
            return ProblemError(
                409,
                ErrorCode.PROJECT_NAME_TAKEN,
                "Project name taken",
                f"A project named {name!r} already exists.",
            )
        case CredentialInvalid(field=field, message=message):
            return _invalid(f"{field_prefix}.{field}", message)
        case SecretsUnreadable():
            return _unreadable()
    raise exc


async def _summaries(services: Services, found: list[Project]) -> list[ProjectSummary]:
    activity = await services.repo.project_activity([p.project_id for p in found])
    return [
        ProjectSummary(
            **p.model_dump(),
            latest_analysis=activity[p.project_id].latest,
            active_analysis=activity[p.project_id].active,
            report_count=activity[p.project_id].report_count,
        )
        for p in found
    ]


@router.get("", response_model=ProjectList)
async def list_projects(services: ServicesDep) -> ProjectList:
    return ProjectList(items=await _summaries(services, await services.projects.list()))


@router.post(
    "", status_code=201, response_model=Project, responses=problem_responses(404, 409, 422)
)
async def create_project(data: ProjectInput, services: ServicesDep) -> Project:
    """A clone is a create with the original's settings; credentials are referenced, not copied."""
    _validate(data)
    try:
        return await services.projects.create(data)
    except (ProjectNameTaken, CredentialInvalid) as exc:
        raise _write_error(exc, "body.sources") from None


@router.get("/{project_id}", response_model=ProjectSummary, responses=problem_responses(404))
async def get_project(project_id: str, services: ServicesDep) -> ProjectSummary:
    project = await services.projects.get(project_id)
    if project is None:
        raise _not_found(project_id)
    return (await _summaries(services, [project]))[0]


@router.put("/{project_id}", response_model=Project, responses=problem_responses(404, 409, 422))
async def update_project(project_id: str, data: ProjectInput, services: ServicesDep) -> Project:
    _validate(data)
    try:
        project = await services.projects.update(project_id, data)
    except (ProjectNameTaken, CredentialInvalid) as exc:
        raise _write_error(exc, "body.sources") from None
    if project is None:
        raise _not_found(project_id)
    services.health_cache.pop(project_id, None)
    await services.repo.prune_reports(project_id)
    return project


@router.delete(
    "/{project_id}",
    status_code=204,
    response_class=Response,
    responses=problem_responses(404, 409),
)
async def delete_project(project_id: str, services: ServicesDep) -> Response:
    """Hard delete, including the project's analyses and reports."""
    if await services.repo.has_active(project_id):
        raise ProblemError(
            409,
            ErrorCode.PROJECT_BUSY,
            "Project has an active analysis",
            "Cancel the queued or running analysis before deleting the project.",
        )
    if not await services.projects.delete(project_id):
        raise _not_found(project_id)
    services.health_cache.pop(project_id, None)
    return Response(status_code=204)


async def _probe(conn: SourceConnection, scope: Scope) -> ConnectionTest:
    match conn:
        case PrometheusConnection():
            async with connect(conn) as (source, client):
                return await probe(source, client, scope)
        case CloudflareConnection():
            async with connect_cloudflare(conn) as cloudflare:
                return await probe_cloudflare(cloudflare, scope)
        case SentryConnection():
            async with connect_sentry(conn) as sentry:
                return await probe_sentry(sentry, scope)
        case WazuhConnection():
            async with connect_wazuh(conn) as wazuh:
                return await probe_wazuh(wazuh, scope)
        case GrafanaConnection():
            async with connect_grafana(conn) as grafana:
                return await probe_grafana(grafana, scope)


def _draft_scope(matchers: list[LabelMatcher]) -> Scope:
    return Scope(project_id=DRAFT_PROJECT_ID, project_name=DRAFT_PROJECT_NAME, matchers=matchers)


@router.post(
    "/test-connection", response_model=ConnectionTest, responses=problem_responses(404, 409, 422)
)
async def test_connection(body: ConnectionTestRequest, services: ServicesDep) -> ConnectionTest:
    _check_scenario(body.source, "body.source")
    try:
        conn = await services.projects.resolve_connection(body.source)
    except (CredentialInvalid, SecretsUnreadable) as exc:
        raise _write_error(exc, "body.source") from None
    return await _probe(conn, _draft_scope(body.matchers))


@router.post(
    "/custom-rules/preview",
    response_model=CustomRulePreview,
    responses=problem_responses(404, 409, 422),
)
async def preview_custom_rule(
    body: CustomRulePreviewRequest, services: ServicesDep
) -> CustomRulePreview:
    """Run one custom rule's query over the last 24 h; query errors are reported in the body."""
    _check_scenario(body.source, "body.source")
    try:
        conn = await services.projects.resolve_connection(body.source)
    except (CredentialInvalid, SecretsUnreadable) as exc:
        raise _write_error(exc, "body.source") from None
    if not isinstance(conn, PrometheusConnection):  # pragma: no cover - the body is Prometheus
        raise _invalid("body.source.kind", "custom rules need a Prometheus source")
    end_time = floor_to_step(datetime.now(UTC))
    async with connect(conn) as (source, _):
        return await source.preview_rule(
            body.rule, _draft_scope(body.matchers), end_time, STEP_SECONDS
        )


async def project_health(services: Services, project: Project) -> list[ConnectionTest]:
    """Cached connection test of each stored source; empty when none is configured."""
    if not project.sources:
        return []
    version = project.updated_at.isoformat()
    cached = services.health_cache.get(project.project_id)
    if cached and cached[1] == version and time.monotonic() - cached[0] < HEALTH_CACHE_SECONDS:
        return cached[2]

    try:
        connections = await services.projects.connections(project.project_id)
    except SecretsUnreadable:
        raise _unreadable() from None
    scope = Scope(
        project_id=project.project_id, project_name=project.name, matchers=project.matchers
    )
    result = [await _probe(conn, scope) for conn in connections]
    services.health_cache[project.project_id] = (time.monotonic(), version, result)
    return result


@router.get(
    "/{project_id}/health",
    response_model=list[ConnectionTest],
    responses=problem_responses(404, 409),
)
async def get_project_health(project_id: str, services: ServicesDep) -> list[ConnectionTest]:
    """One connection test per configured source; empty when the project has none."""
    project = await services.projects.get(project_id)
    if project is None:
        raise _not_found(project_id)
    return await project_health(services, project)
