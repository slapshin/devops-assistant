"""HTTP routes. Contracts are frozen here; bodies marked not_implemented belong to later tasks."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app import __version__
from app.api.problems import not_implemented, problem_responses
from app.domain.detector_config import DetectorConfig
from app.domain.jobs import (
    AnalysisJob,
    AnalysisList,
    AnalysisSubmission,
    AnalysisSubmitted,
    EnvList,
    HealthResponse,
    Limits,
    ProjectList,
    RuntimeConfig,
    SourceStatus,
)
from app.domain.report import AnalysisReport
from app.settings import Settings

router = APIRouter(prefix="/api")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_detector_config(request: Request) -> DetectorConfig:
    config: DetectorConfig = request.app.state.detector_config
    return config


SettingsDep = Annotated[Settings, Depends(get_settings)]
DetectorDep = Annotated[DetectorConfig, Depends(get_detector_config)]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # T004 adds the metrics reachability probe; T007 adds the database check.
    return HealthResponse(
        status="ok",
        version=__version__,
        database="not_initialized",
        metrics_source=SourceStatus(reachable=None, message="Not checked (T004)"),
    )


@router.get("/config", response_model=RuntimeConfig)
async def runtime_config(settings: SettingsDep, detector: DetectorDep) -> RuntimeConfig:
    return RuntimeConfig(
        version=__version__,
        ai_provider=settings.ai_provider.value,
        ai_model=settings.openai_model if settings.ai_provider == "openai" else None,
        explanation_status=settings.explanation_status,
        detector_version=detector.version,
        config_hash=detector.config_hash,
        metrics_source=settings.metrics_source_display,
        limits=Limits(
            max_running_jobs=1,
            max_queued_jobs=4,
            query_timeout_seconds=30,
            max_series_per_query=500,
            max_series_per_job=5000,
            report_max_bytes=20 * 1024 * 1024,
        ),
    )


@router.get("/projects", response_model=ProjectList, responses=problem_responses(501, 503))
async def list_projects() -> ProjectList:
    raise not_implemented("T004/T007")


@router.get(
    "/projects/{project}/envs", response_model=EnvList, responses=problem_responses(404, 501, 503)
)
async def list_envs(project: str) -> EnvList:
    raise not_implemented("T004/T007")


@router.post(
    "/analyses",
    status_code=202,
    response_model=AnalysisSubmitted,
    responses={200: {"model": AnalysisSubmitted}, **problem_responses(404, 422, 429, 501)},
)
async def submit_analysis(submission: AnalysisSubmission) -> AnalysisSubmitted:
    raise not_implemented("T007")


@router.get("/analyses", response_model=AnalysisList, responses=problem_responses(501))
async def list_analyses(
    project: str | None = None,
    env: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> AnalysisList:
    raise not_implemented("T007")


@router.get(
    "/analyses/{analysis_id}", response_model=AnalysisJob, responses=problem_responses(404, 501)
)
async def get_analysis(analysis_id: str) -> AnalysisJob:
    raise not_implemented("T007")


@router.get(
    "/analyses/{analysis_id}/report",
    response_model=AnalysisReport,
    responses=problem_responses(404, 409, 501),
)
async def get_report(analysis_id: str) -> AnalysisReport:
    raise not_implemented("T007")


@router.delete(
    "/analyses/{analysis_id}",
    status_code=202,
    response_model=AnalysisJob,
    responses=problem_responses(404, 409, 501),
)
async def cancel_analysis(analysis_id: str) -> AnalysisJob:
    raise not_implemented("T007")
