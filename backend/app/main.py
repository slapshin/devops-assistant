"""ASGI application factory. Run: `uv run uvicorn app.main:create_app --factory`."""

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.ai.providers import provider_from_settings
from app.analysis.engine import RobustDetector
from app.api.problems import install_problem_handlers
from app.api.routes import router
from app.container import Services
from app.domain.detector_config import DetectorConfig
from app.domain.interfaces import ExplanationProvider, MetricsSource
from app.jobs import JobRunner, RunnerLimits
from app.metrics.client import PrometheusClient
from app.metrics.source import PrometheusMetricsSource
from app.metrics.synthetic import SCENARIOS, SyntheticMetricsSource
from app.service import AnalysisPipeline
from app.settings import ConfigError, Settings, load_settings
from app.storage.repository import SqliteReportRepository

log = logging.getLogger("app")


def build_source(settings: Settings) -> tuple[MetricsSource, PrometheusClient | None]:
    scenario = settings.synthetic_scenario
    if scenario is not None:
        if scenario not in SCENARIOS:
            raise ConfigError(
                f"Invalid configuration:\n  METRICS_URL: unknown synthetic scenario {scenario!r}"
                f" (expected one of {', '.join(sorted(SCENARIOS))})"
            )
        return SyntheticMetricsSource(scenario), None
    client = PrometheusClient.from_settings(settings)
    return PrometheusMetricsSource(client), client


def build_services(
    settings: Settings,
    source: MetricsSource | None = None,
    provider: ExplanationProvider | None = None,
    limits: RunnerLimits | None = None,
) -> Services:
    config = DetectorConfig()  # DETECTOR_CONFIG overrides: see docs/OPERATIONS.md
    client = None
    if source is None:
        source, client = build_source(settings)
    unavailable, reason = settings.explanation_status, None
    if provider is None:
        provider, unavailable, reason = provider_from_settings(settings)
    pipeline = AnalysisPipeline(source, RobustDetector(), config, provider, unavailable, reason)
    repo = SqliteReportRepository(settings.database_path)
    runner = JobRunner(repo, pipeline, config, settings.explanation_status, limits)
    return Services(settings, config, source, repo, runner, client)


def create_app(
    settings: Settings | None = None,
    source: MetricsSource | None = None,
    provider: ExplanationProvider | None = None,
    limits: RunnerLimits | None = None,
) -> FastAPI:
    if settings is None:
        try:
            settings = load_settings()
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(2) from None
    logging.basicConfig(level=settings.log_level)
    try:
        services = build_services(settings, source, provider, limits)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        recovered = await services.runner.start()
        log.info(
            "started version=%s metrics_source=%s ai=%s db=%s interrupted_jobs_failed=%d",
            __version__,
            settings.metrics_source_display,
            settings.explanation_status.value,
            settings.database_path,
            recovered,
        )
        try:
            yield
        finally:
            await services.runner.stop()
            if services.client is not None:
                await services.client.aclose()
            services.repo.close()

    app = FastAPI(
        title="DevOps AI Assistant",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.detector_config = services.config
    app.state.services = services
    install_problem_handlers(app)
    app.include_router(router)
    return app
