"""ASGI application factory. Run: `uv run uvicorn app.main:create_app --factory`."""

import logging
import sys

from fastapi import FastAPI

from app import __version__
from app.api.problems import install_problem_handlers
from app.api.routes import router
from app.domain.detector_config import DetectorConfig
from app.settings import ConfigError, Settings, load_settings

log = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        try:
            settings = load_settings()
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(2) from None
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="DevOps AI Assistant",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    # T005 loads DETECTOR_CONFIG overrides; defaults until then.
    app.state.detector_config = DetectorConfig()
    install_problem_handlers(app)
    app.include_router(router)
    log.info(
        "starting version=%s metrics_source=%s ai=%s",
        __version__,
        settings.metrics_source_display,
        settings.explanation_status.value,
    )
    return app
