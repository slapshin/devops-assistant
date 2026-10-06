"""One-off project setup at startup: legacy source import and demo seeding (T012)."""

import logging

from app.domain.common import LabelMatcher
from app.domain.projects import CloudflareSourceInput, ProjectInput, PrometheusSourceInput
from app.settings import Settings
from app.sources.cloudflare.synthetic import SYNTHETIC_ZONE_ID
from app.sources.prometheus.synthetic import SCENARIOS
from app.storage.projects import SqliteProjectRepository

log = logging.getLogger("app")

DEMO_MATCHERS = [
    LabelMatcher(name="env", value="production"),
    LabelMatcher(name="project", value="shop"),
]


def demo_projects() -> list[ProjectInput]:
    return [
        ProjectInput(
            name=f"Demo: {scenario}",
            description=f"Synthetic data, scenario {scenario!r}. No real metrics or Cloudflare "
            "analytics are queried.",
            matchers=DEMO_MATCHERS,
            sources=[
                PrometheusSourceInput(url=f"synthetic://{scenario}"),
                CloudflareSourceInput(
                    zone_id=SYNTHETIC_ZONE_ID,
                    hostnames=["shop.example.com"],
                    api_url=f"synthetic://{scenario}",
                ),
            ],
        )
        for scenario in SCENARIOS
    ]


async def bootstrap_projects(settings: Settings, projects: SqliteProjectRepository) -> None:
    imported = await projects.import_legacy_source(settings.metrics_connection)
    if imported is not None:
        if settings.metrics_url is None:
            log.warning(
                "projects created from existing reports have no metrics source; "
                "configure one per project in the UI"
            )
        else:
            log.warning(
                "attached METRICS_URL to %d migrated project(s); METRICS_* settings are now "
                "deprecated and can be removed from config.env",
                imported,
            )
    elif settings.metrics_url is not None:
        log.info("METRICS_URL is deprecated and ignored; sources are configured per project")

    if settings.demo_projects and await projects.count() == 0:
        for data in demo_projects():
            await projects.create(data)
        log.info("seeded %d demo projects", len(SCENARIOS))
