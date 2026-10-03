"""ASGI application factory. Run: `uv run uvicorn app.main:create_app --factory`."""

import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.ai.providers import provider_from_settings
from app.analysis.engine import RobustDetector
from app.api.problems import install_problem_handlers
from app.api.projects import router as projects_router
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
from app.static import mount_ui
from app.storage.projects import SqliteProjectRepository
from app.storage.repository import SqliteReportRepository
from app.storage.secrets import load_secret_box

log = logging.getLogger("app")

CONFIG_ERROR_EXIT_CODE = 2


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


def load_detector_config(settings: Settings) -> DetectorConfig:
    """Defaults from DECISIONS §5, optionally overridden by a DETECTOR_CONFIG_FILE JSON file."""
    defaults = DetectorConfig()
    config_file = settings.detector_config_file
    if config_file is None:
        return defaults

    try:
        override = json.loads(config_file.read_text())
        merged = defaults.model_dump()
        signals = {**merged["signals"], **override.pop("signals", {})}
        merged.update(override)
        merged["signals"] = signals
        return DetectorConfig.model_validate(merged)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"Invalid configuration:\n  DETECTOR_CONFIG_FILE ({config_file}): {exc}"
        ) from None


def build_services(
    settings: Settings,
    source: MetricsSource | None = None,
    provider: ExplanationProvider | None = None,
    limits: RunnerLimits | None = None,
) -> Services:
    config = load_detector_config(settings)

    client = None
    if source is None:
        source, client = build_source(settings)

    unavailable, reason = settings.explanation_status, None
    if provider is None:
        provider, unavailable, reason = provider_from_settings(settings)

    pipeline = AnalysisPipeline(source, RobustDetector(), config, provider, unavailable, reason)
    repo = SqliteReportRepository(settings.database_path)
    projects = SqliteProjectRepository(repo.engine, load_secret_box(settings))
    runner = JobRunner(repo, pipeline, config, settings.explanation_status, limits)
    return Services(settings, config, source, repo, runner, client, projects)


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
            raise SystemExit(CONFIG_ERROR_EXIT_CODE) from None

    logging.basicConfig(level=settings.log_level)
    try:
        services = build_services(settings, source, provider, limits)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(CONFIG_ERROR_EXIT_CODE) from None

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
    app.include_router(projects_router)
    if settings.ui_dir is not None:
        mount_ui(app, settings.ui_dir)
    return app
