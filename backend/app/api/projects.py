"""Project management routes (T011): CRUD, connection test, and cached per-project health."""

import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Response

from app.api.problems import ProblemError, problem_responses
from app.api.routes import ServicesDep
from app.container import Services
from app.domain.jobs import ErrorCode
from app.domain.projects import (
    ConnectionTest,
    ConnectionTestRequest,
    LabelMatcher,
    Project,
    ProjectInput,
    ProjectList,
    PrometheusConnection,
    SourceKind,
)
from app.metrics.client import PrometheusClient
from app.metrics.probe import probe_prometheus, probe_synthetic
from app.metrics.synthetic import SCENARIOS
from app.storage.projects import ProjectNameTaken, SecretRequired
from app.storage.secrets import SecretsUnreadable

router = APIRouter(prefix="/api/projects", tags=["projects"])

HEALTH_CACHE_SECONDS = 60


def _not_found(project_id: str) -> ProblemError:
    return ProblemError(
        404, ErrorCode.PROJECT_NOT_FOUND, "Project not found", f"No project {project_id}."
    )


def _invalid(field: str, message: str) -> ProblemError:
    error = ProblemError(422, ErrorCode.VALIDATION_ERROR, "Request validation failed")
    error.problem = error.problem.model_copy(
        update={"errors": [{"field": field, "message": message}]}
    )
    return error


def _unreadable() -> ProblemError:
    return ProblemError(
        409,
        ErrorCode.CREDENTIALS_UNREADABLE,
        "Stored credentials are unreadable",
        "The secret key changed or was lost; re-enter the source credentials.",
    )


def _check_scenario(url: str, field: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "synthetic" and parts.netloc not in SCENARIOS:
        raise _invalid(field, f"unknown synthetic scenario; expected one of {sorted(SCENARIOS)}")


def _validate(data: ProjectInput) -> None:
    for i, source in enumerate(data.sources):
        _check_scenario(source.url, f"body.sources.{i}.url")


def _write_error(exc: Exception, field_prefix: str) -> ProblemError:
    match exc:
        case ProjectNameTaken(name=name):
            return ProblemError(
                409,
                ErrorCode.PROJECT_NAME_TAKEN,
                "Project name taken",
                f"A project named {name!r} already exists.",
            )
        case SecretRequired(field=field):
            return _invalid(f"{field_prefix}.{field}", "required when no secret is stored")
        case SecretsUnreadable():
            return _unreadable()
    raise exc


@router.get("", response_model=ProjectList)
async def list_projects(services: ServicesDep) -> ProjectList:
    return ProjectList(items=await services.projects.list())


@router.post("", status_code=201, response_model=Project, responses=problem_responses(409, 422))
async def create_project(data: ProjectInput, services: ServicesDep) -> Project:
    _validate(data)
    try:
        return await services.projects.create(data)
    except (ProjectNameTaken, SecretRequired, SecretsUnreadable) as exc:
        raise _write_error(exc, "body.sources.0") from None


@router.get("/{project_id}", response_model=Project, responses=problem_responses(404))
async def get_project(project_id: str, services: ServicesDep) -> Project:
    project = await services.projects.get(project_id)
    if project is None:
        raise _not_found(project_id)
    return project


@router.put("/{project_id}", response_model=Project, responses=problem_responses(404, 409, 422))
async def update_project(project_id: str, data: ProjectInput, services: ServicesDep) -> Project:
    _validate(data)
    try:
        project = await services.projects.update(project_id, data)
    except (ProjectNameTaken, SecretRequired, SecretsUnreadable) as exc:
        raise _write_error(exc, "body.sources.0") from None
    if project is None:
        raise _not_found(project_id)
    services.health_cache.pop(project_id, None)
    return project


@router.delete(
    "/{project_id}", status_code=204, response_class=Response, responses=problem_responses(404)
)
async def delete_project(project_id: str, services: ServicesDep) -> Response:
    if not await services.projects.delete(project_id):
        raise _not_found(project_id)
    services.health_cache.pop(project_id, None)
    return Response(status_code=204)


async def _probe(conn: PrometheusConnection, matchers: list[LabelMatcher]) -> ConnectionTest:
    parts = urlsplit(conn.url)
    if parts.scheme == "synthetic":
        return probe_synthetic(parts.netloc)
    client = PrometheusClient.from_connection(conn)
    try:
        return await probe_prometheus(client, matchers)
    finally:
        await client.aclose()


@router.post(
    "/test-connection", response_model=ConnectionTest, responses=problem_responses(404, 409, 422)
)
async def test_connection(body: ConnectionTestRequest, services: ServicesDep) -> ConnectionTest:
    _check_scenario(body.source.url, "body.source.url")
    if body.project_id is not None and await services.projects.get(body.project_id) is None:
        raise _not_found(body.project_id)
    try:
        conn = await services.projects.resolve_connection(body.source, body.project_id)
    except (SecretRequired, SecretsUnreadable) as exc:
        raise _write_error(exc, "body.source") from None
    return await _probe(conn, body.matchers)


async def project_health(services: Services, project: Project) -> ConnectionTest | None:
    """Cached connection test of a stored project; None when it has no metrics source."""
    if project.source(SourceKind.PROMETHEUS) is None:
        return None
    version = project.updated_at.isoformat()
    cached = services.health_cache.get(project.project_id)
    if cached and cached[1] == version and time.monotonic() - cached[0] < HEALTH_CACHE_SECONDS:
        return cached[2]

    try:
        conn = await services.projects.prometheus_connection(project.project_id)
    except SecretsUnreadable:
        raise _unreadable() from None
    if conn is None:
        return None
    result = await _probe(conn, project.matchers)
    services.health_cache[project.project_id] = (time.monotonic(), version, result)
    return result


@router.get(
    "/{project_id}/health",
    response_model=ConnectionTest | None,
    responses=problem_responses(404, 409),
)
async def get_project_health(project_id: str, services: ServicesDep) -> ConnectionTest | None:
    """Null when the project has no metrics source configured."""
    project = await services.projects.get(project_id)
    if project is None:
        raise _not_found(project_id)
    return await project_health(services, project)
