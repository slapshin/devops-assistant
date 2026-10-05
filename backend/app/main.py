"""ASGI application factory. Run: `uv run uvicorn app.main:create_app --factory`."""

import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from app import __version__
from app.ai.providers import provider_from_settings
from app.analysis.engine import RobustDetector
from app.api.problems import install_problem_handlers
from app.api.projects import router as projects_router
from app.api.routes import router
from app.bootstrap import bootstrap_projects
from app.container import Services
from app.domain.detector_config import DetectorConfig
from app.domain.interfaces import ExplanationProvider, MetricsSource
from app.jobs import JobRunner, RunnerLimits
from app.metrics.factory import ProjectSources
from app.service import AnalysisPipeline
from app.settings import ConfigError, Settings, load_settings
from app.static import mount_ui
from app.storage.projects import SqliteProjectRepository
from app.storage.repository import SqliteReportRepository
from app.storage.secrets import load_secret_box

log = logging.getLogger("app")

CONFIG_ERROR_EXIT_CODE = 2
GZIP_MINIMUM_BYTES = 1024
# Level 6 gets nearly all of level 9's ratio on JSON at a fraction of the CPU.
GZIP_COMPRESSION_LEVEL = 6


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
    """``source``, when given, replaces every project's metrics source (tests, fixtures)."""
    config = load_detector_config(settings)

    unavailable, reason = settings.explanation_status, None
    if provider is None:
        provider, unavailable, reason = provider_from_settings(settings)

    repo = SqliteReportRepository(settings.database_path)
    projects = SqliteProjectRepository(repo.engine, load_secret_box(settings))
    sources = ProjectSources(projects, override=source)
    pipeline = AnalysisPipeline(sources, RobustDetector(), config, provider, unavailable, reason)
    runner = JobRunner(repo, pipeline, config, settings.explanation_status, limits)
    return Services(settings, config, sources, repo, runner, projects)


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
        await bootstrap_projects(settings, services.projects)
        recovered = await services.runner.start()
        log.info(
            "started version=%s ai=%s db=%s interrupted_jobs_failed=%d",
            __version__,
            settings.explanation_status.value,
            settings.database_path,
            recovered,
        )
        try:
            yield
        finally:
            await services.runner.stop()
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

    # Reports are multi-MB JSON that compresses ~4x; uncompressed they are slow on remote links.
    app.add_middleware(
        GZipMiddleware, minimum_size=GZIP_MINIMUM_BYTES, compresslevel=GZIP_COMPRESSION_LEVEL
    )
    install_problem_handlers(app)
    app.include_router(router)
    app.include_router(projects_router)
    if settings.ui_dir is not None:
        mount_ui(app, settings.ui_dir)
    return app
