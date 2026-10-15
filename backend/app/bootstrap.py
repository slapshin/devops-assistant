"""One-off project setup at startup: legacy source import and demo seeding (T012)."""

import logging

from pydantic import SecretStr

from app.domain.auth import CredentialRef, TokenAuthInput
from app.domain.common import LabelMatcher, SourceKind
from app.domain.credentials import CredentialInput
from app.domain.projects import (
    CloudflareSourceInput,
    GrafanaSourceInput,
    ProjectInput,
    PrometheusSourceInput,
    SentrySourceInput,
    WazuhSourceInput,
)
from app.settings import Settings
from app.sources.cloudflare.synthetic import SYNTHETIC_ZONE_ID
from app.sources.prometheus.synthetic import DEMO_CUSTOM_RULES, SCENARIOS
from app.sources.sentry.synthetic import SYNTHETIC_ORGANIZATION
from app.storage.credentials import SqliteCredentialRepository
from app.storage.projects import SqliteProjectRepository

log = logging.getLogger("app")

DEMO_MATCHERS = [
    LabelMatcher(name="env", value="production"),
    LabelMatcher(name="project", value="shop"),
]


DEMO_CREDENTIAL = CredentialInput(
    name="Demo Grafana token",
    description="Shared by the demo projects' Grafana sources; synthetic sources ignore it.",
    kind=SourceKind.GRAFANA,
    auth=TokenAuthInput(token=SecretStr("glsa_demo")),
)


def demo_projects(grafana_credential_id: str) -> list[ProjectInput]:
    return [
        ProjectInput(
            name=f"Demo: {scenario}",
            description=f"Synthetic data, scenario {scenario!r}. No real metrics, Cloudflare "
            "analytics, Sentry events, Wazuh alerts or Grafana alerts are queried.",
            matchers=DEMO_MATCHERS,
            sources=[
                PrometheusSourceInput(
                    url=f"synthetic://{scenario}", custom_rules=DEMO_CUSTOM_RULES
                ),
                CloudflareSourceInput(
                    zone_id=SYNTHETIC_ZONE_ID,
                    hostnames=["shop.example.com"],
                    api_url=f"synthetic://{scenario}",
                ),
                SentrySourceInput(
                    organization=SYNTHETIC_ORGANIZATION,
                    projects=["shop-api", "shop-web"],
                    environment="production",
                    api_url=f"synthetic://{scenario}",
                ),
                WazuhSourceInput(api_url=f"synthetic://{scenario}", groups=["shop-db", "shop-web"]),
                GrafanaSourceInput(
                    api_url=f"synthetic://{scenario}",
                    folders=["Shop"],
                    instance_labels=[LabelMatcher(name="project", value="shop")],
                    auth=CredentialRef(credential_id=grafana_credential_id),
                ),
            ],
        )
        for scenario in SCENARIOS
    ]


async def bootstrap_projects(
    settings: Settings,
    projects: SqliteProjectRepository,
    credentials: SqliteCredentialRepository,
) -> None:
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

    moved = await credentials.move_inline_secrets()
    if moved:
        log.warning("moved source secrets into %d credential(s); see Credentials", moved)

    if settings.demo_projects and await projects.count() == 0:
        credential = next(
            (c for c in await credentials.list() if c.name == DEMO_CREDENTIAL.name), None
        ) or await credentials.create(DEMO_CREDENTIAL)
        for data in demo_projects(credential.credential_id):
            await projects.create(data)
        log.info("seeded %d demo projects", len(SCENARIOS))
