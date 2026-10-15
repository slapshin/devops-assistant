"""Shared credentials and per-kind source auth (T021).

``credentials`` holds secrets shared by the sources of many projects; a source references one
with ``project_sources.credential_id`` (deletion is restricted while in use). Every stored
source config gets the ``auth`` object its kind's contract uses: Cloudflare, Sentry and Grafana
sources become ``token`` (or ``none`` without a stored secret), Wazuh's ``username`` moves into
``basic`` auth. Sentry configs saved before multi-project support get ``projects``. Secrets are
not re-encrypted here (the key is not available to migrations); the repository reads the
pre-T021 secret keys until a source is saved again.

Revision ID: 0006
Revises: 0005
"""

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"

TOKEN_KINDS = ("cloudflare", "sentry", "grafana")


def _config(kind: str, config: dict[str, Any], has_secrets: bool) -> dict[str, Any]:
    if kind in TOKEN_KINDS and "auth" not in config:
        config["auth"] = {"type": "token" if has_secrets else "none"}
    if kind == "wazuh" and "auth" not in config:
        username = config.pop("username", None)
        config["auth"] = (
            {"type": "basic", "username": username}
            if username and has_secrets
            else {"type": "none"}
        )
    if kind == "sentry" and "project" in config:
        legacy = config.pop("project")
        config.setdefault("projects", [legacy])
    return config


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("credential_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100, collation="NOCASE"), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("auth", sa.Text, nullable=False, comment="JSON mechanism without secrets"),
        sa.Column("secrets", sa.LargeBinary, nullable=False, comment="Fernet-encrypted JSON"),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.String(20), nullable=False),
    )
    with op.batch_alter_table("project_sources") as batch:
        batch.add_column(
            sa.Column(
                "credential_id",
                sa.String(36),
                nullable=True,
                comment="Shared credential used instead of inline secrets",
            )
        )
        batch.create_foreign_key(
            "fk_project_sources_credential",
            "credentials",
            ["credential_id"],
            ["credential_id"],
            ondelete="RESTRICT",
        )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT project_id, kind, config, secrets IS NOT NULL FROM project_sources")
    ).all()
    for project_id, kind, raw, has_secrets in rows:
        config = _config(kind, json.loads(raw), bool(has_secrets))
        conn.execute(
            sa.text(
                "UPDATE project_sources SET config = :config"
                " WHERE project_id = :project_id AND kind = :kind"
            ),
            {"config": json.dumps(config, sort_keys=True), "project_id": project_id, "kind": kind},
        )
