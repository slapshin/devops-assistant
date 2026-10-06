"""HTTP routes (docs/DECISIONS.md §3). Thin adapters over the job runner and repository."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app import __version__
from app.ai.providers import FAKE_MODEL
from app.api.problems import ProblemError, problem_responses
from app.container import Services
from app.domain.common import Scope, floor_to_step
from app.domain.detector_config import DetectorConfig
from app.domain.jobs import (
    AnalysisJob,
    AnalysisList,
    AnalysisSubmission,
    AnalysisSubmitted,
    ErrorCode,
    HealthResponse,
    Limits,
    RuntimeConfig,
)
from app.domain.report import AnalysisReport
from app.jobs import NotActive, QueueFull
from app.metrics.client import ClientLimits
from app.metrics.source import CollectionBudget
from app.settings import AIProvider, Settings
from app.storage.repository import SchemaUnsupported

router = APIRouter(prefix="/api")

MAX_END_TIME_AGE_DAYS = 90
MAX_END_TIME_AGE = timedelta(days=MAX_END_TIME_AGE_DAYS)
END_TIME_FUTURE_TOLERANCE = timedelta(minutes=1)
QUEUE_FULL_RETRY_AFTER_SECONDS = 30
# Model names reported by /api/config for providers that do not take OPENAI_MODEL.
FIXED_PROVIDER_MODELS = {AIProvider.FAKE: FAKE_MODEL}


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


SettingsDep = Annotated[Settings, Depends(get_settings)]
ServicesDep = Annotated[Services, Depends(get_services)]


def _ai_model(settings: Settings) -> str | None:
    if settings.ai_provider is AIProvider.OPENAI:
        return settings.openai_model
    return FIXED_PROVIDER_MODELS.get(settings.ai_provider)


@router.get("/health", response_model=HealthResponse)
async def health(services: ServicesDep) -> HealthResponse:
    try:
        await services.repo.stats()
        database = "ok"
    except Exception:
        database = "unavailable"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
    )


@router.get("/config", response_model=RuntimeConfig)
async def runtime_config(settings: SettingsDep, services: ServicesDep) -> RuntimeConfig:
    detector: DetectorConfig = services.config
    count, size = await services.repo.stats()
    limits = services.runner.limits
    # The live client/budget are not exposed on Services; report the defaults they run with.
    client_limits = ClientLimits()
    budget = CollectionBudget()
    return RuntimeConfig(
        version=__version__,
        ai_provider=settings.ai_provider.value,
        ai_model=_ai_model(settings),
        explanation_status=settings.explanation_status,
        detector_version=detector.version,
        config_hash=detector.config_hash,
        limits=Limits(
            max_running_jobs=limits.max_running,
            max_queued_jobs=limits.max_queued,
            query_timeout_seconds=int(client_limits.timeout_seconds),
            max_series_per_query=budget.max_series_per_query,
            max_series_per_job=budget.max_series_per_job,
            report_max_bytes=services.repo.max_report_bytes,
        ),
        report_count=count,
        database_bytes=size,
    )


async def project_scope(services: Services, project_id: str) -> Scope:
    """Scope of a project that can be analysed now; problem responses otherwise."""
    project = await services.projects.get(project_id)
    if project is None:
        raise ProblemError(
            404, ErrorCode.PROJECT_NOT_FOUND, "Project not found", f"No project {project_id}."
        )
    if not project.sources:
        raise ProblemError(
            409,
            ErrorCode.SOURCE_NOT_CONFIGURED,
            "No data source",
            f"Project {project.name!r} has no data source configured.",
        )
    if not project.credentials_readable:
        raise ProblemError(
            409,
            ErrorCode.CREDENTIALS_UNREADABLE,
            "Stored credentials are unreadable",
            "The secret key changed or was lost; re-enter the source credentials.",
        )
    return Scope(
        project_id=project.project_id, project_name=project.name, matchers=project.matchers
    )


@router.post(
    "/analyses",
    status_code=202,
    response_model=AnalysisSubmitted,
    responses={200: {"model": AnalysisSubmitted}, **problem_responses(404, 409, 422, 429)},
)
async def submit_analysis(
    submission: AnalysisSubmission, response: Response, services: ServicesDep
) -> AnalysisSubmitted:
    now = datetime.now(UTC)
    requested = submission.end_time or now
    if requested > now + END_TIME_FUTURE_TOLERANCE:
        raise ProblemError(
            422,
            ErrorCode.END_TIME_INVALID,
            "End time is in the future",
            f"End time {requested.isoformat()} is after the current time.",
        )
    if requested < now - MAX_END_TIME_AGE:
        raise ProblemError(
            422,
            ErrorCode.END_TIME_INVALID,
            "End time is too old",
            f"End time must be within the last {MAX_END_TIME_AGE_DAYS} days.",
        )

    scope = await project_scope(services, submission.project_id)
    request = services.runner.request_for(scope, floor_to_step(min(requested, now)))

    try:
        job, duplicate = await services.runner.submit(request)
    except QueueFull:
        raise ProblemError(
            429,
            ErrorCode.QUEUE_FULL,
            "Analysis queue is full",
            f"{services.runner.limits.max_running} running and "
            f"{services.runner.limits.max_queued} queued; retry later.",
            headers={"Retry-After": str(QUEUE_FULL_RETRY_AFTER_SECONDS)},
        ) from None

    if duplicate:
        response.status_code = 200
    return AnalysisSubmitted(analysis=job, duplicate_of_active=duplicate)


@router.get("/analyses", response_model=AnalysisList, responses=problem_responses(422))
async def list_analyses(
    services: ServicesDep,
    project_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> AnalysisList:
    jobs, next_cursor = await services.repo.list_jobs(project_id, limit, cursor)
    return AnalysisList(items=jobs, next_cursor=next_cursor)


async def _job(services: Services, analysis_id: str) -> AnalysisJob:
    job = await services.repo.get_job(analysis_id)
    if job is None:
        raise ProblemError(
            404, ErrorCode.ANALYSIS_NOT_FOUND, "Analysis not found", f"No analysis {analysis_id}."
        )
    return job


@router.get("/analyses/{analysis_id}", response_model=AnalysisJob, responses=problem_responses(404))
async def get_analysis(analysis_id: str, services: ServicesDep) -> AnalysisJob:
    return await _job(services, analysis_id)


@router.get(
    "/analyses/{analysis_id}/report",
    response_model=AnalysisReport,
    responses=problem_responses(404, 409),
)
async def get_report(analysis_id: str, services: ServicesDep) -> AnalysisReport:
    job = await _job(services, analysis_id)
    if job.state.active:
        raise ProblemError(
            409, ErrorCode.REPORT_NOT_READY, "Report not ready", f"Analysis is {job.state.value}."
        )
    try:
        report = await services.repo.get_report(analysis_id)
    except SchemaUnsupported as exc:
        raise ProblemError(
            404,
            ErrorCode.SCHEMA_UNSUPPORTED,
            "Report schema unsupported",
            f"Saved with schema {exc.version}.",
        ) from None

    if report is None:
        raise ProblemError(
            404,
            ErrorCode.REPORT_UNAVAILABLE,
            "No report for this analysis",
            f"Analysis {job.state.value}" + (f": {job.error.message}" if job.error else "."),
        )
    return report


@router.delete(
    "/analyses/{analysis_id}",
    status_code=202,
    response_model=AnalysisJob,
    responses=problem_responses(404, 409),
)
async def cancel_analysis(analysis_id: str, services: ServicesDep) -> AnalysisJob:
    await _job(services, analysis_id)
    try:
        return await services.runner.cancel(analysis_id)
    except NotActive:
        raise ProblemError(
            409,
            ErrorCode.ANALYSIS_NOT_ACTIVE,
            "Analysis is not active",
            f"Analysis {analysis_id} has already finished.",
        ) from None
