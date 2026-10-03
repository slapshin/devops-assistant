"""Attach analyses to projects (T012).

One project is created per distinct (project, env) pair of existing jobs, named
"<project> / <env>" with matchers project=<project>, env=<env>. Job and report snapshots are
rewritten to the 2.0 scope ``{project_id, project_name, matchers}``. Tables are rebuilt with
cascading foreign keys (projects -> analysis_jobs -> reports). The source credentials cannot be
encrypted here, so ``app_meta.legacy_source_import`` is left ``pending`` for the startup import
of the deprecated METRICS_* settings.

Revision ID: 0003
Revises: 0002
"""

import json
import uuid
import zlib
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"

SCHEMA_VERSION = "2.0"
COMPRESSION_LEVEL = 6
LEGACY_IMPORT_KEY = "legacy_source_import"


def _scope(project_id: str, name: str, project: str, env: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "project_name": name,
        "matchers": [{"name": "env", "value": env}, {"name": "project", "value": project}],
    }


def _unique_name(base: str, taken: set[str]) -> str:
    name, n = base, 1
    while name.lower() in taken:
        n += 1
        name = f"{base} ({n})"
    taken.add(name.lower())
    return name


def _create_projects(conn: sa.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    taken = {r[0].lower() for r in conn.execute(sa.text("SELECT name FROM projects"))}
    pairs = conn.execute(
        sa.text("SELECT DISTINCT project, env FROM analysis_jobs ORDER BY project, env")
    ).all()

    scopes: dict[tuple[str, str], dict[str, Any]] = {}
    for project, env in pairs:
        project_id = str(uuid.uuid7())
        name = _unique_name(f"{project} / {env}"[:100], taken)
        scope = _scope(project_id, name, project, env)
        conn.execute(
            sa.text(
                "INSERT INTO projects (project_id, name, description, matchers, created_at,"
                " updated_at) VALUES (:id, :name, :description, :matchers, :now, :now)"
            ),
            {
                "id": project_id,
                "name": name,
                "description": "Created from existing reports during the upgrade to projects.",
                "matchers": json.dumps(scope["matchers"]),
                "now": now,
            },
        )
        scopes[(project, env)] = scope
    return scopes


def upgrade() -> None:
    conn = op.get_bind()
    op.create_table(
        "app_meta",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )
    scopes = _create_projects(conn)
    if scopes:
        conn.execute(
            sa.text("INSERT INTO app_meta (key, value) VALUES (:key, 'pending')"),
            {"key": LEGACY_IMPORT_KEY},
        )

    op.create_table(
        "analysis_jobs_new",
        sa.Column("analysis_id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.project_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("end_time", sa.String(20), nullable=False),
        sa.Column("config_hash", sa.String(12), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("job", sa.Text, nullable=False),
    )
    rows = conn.execute(
        sa.text(
            "SELECT analysis_id, project, env, end_time, config_hash, state, created_at, job"
            " FROM analysis_jobs"
        )
    ).all()
    for analysis_id, project, env, end_time, config_hash, state, created_at, job in rows:
        scope = scopes[(project, env)]
        data = json.loads(job)
        data["scope"] = scope
        conn.execute(
            sa.text(
                "INSERT INTO analysis_jobs_new (analysis_id, project_id, end_time, config_hash,"
                " state, created_at, job) VALUES (:a, :p, :e, :c, :s, :t, :j)"
            ),
            {
                "a": analysis_id,
                "p": scope["project_id"],
                "e": end_time,
                "c": config_hash,
                "s": state,
                "t": created_at,
                "j": json.dumps(data),
            },
        )

    # References "analysis_jobs" by name: it resolves to the rebuilt table after the rename.
    op.create_table(
        "reports_new",
        sa.Column(
            "analysis_id",
            sa.String(36),
            sa.ForeignKey("analysis_jobs.analysis_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("schema_version", sa.String(8), nullable=False),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("body", sa.LargeBinary, nullable=False),
    )
    project_of = {r[0]: (r[1], r[2]) for r in rows}
    report_ids = [r[0] for r in conn.execute(sa.text("SELECT analysis_id FROM reports"))]
    for analysis_id in report_ids:  # one at a time: bodies can be tens of MB uncompressed
        created_at, body = conn.execute(
            sa.text("SELECT created_at, body FROM reports WHERE analysis_id = :a"),
            {"a": analysis_id},
        ).one()
        data = json.loads(zlib.decompress(body))
        data["scope"] = scopes[project_of[analysis_id]]
        data["schema_version"] = SCHEMA_VERSION
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        conn.execute(
            sa.text(
                "INSERT INTO reports_new (analysis_id, schema_version, created_at, size_bytes,"
                " body) VALUES (:a, :v, :t, :n, :b)"
            ),
            {
                "a": analysis_id,
                "v": SCHEMA_VERSION,
                "t": created_at,
                "n": len(raw),
                "b": zlib.compress(raw, COMPRESSION_LEVEL),
            },
        )

    op.drop_table("reports")
    op.drop_index("ix_jobs_scope", "analysis_jobs")
    op.drop_index("ix_jobs_state", "analysis_jobs")
    op.drop_table("analysis_jobs")
    op.rename_table("analysis_jobs_new", "analysis_jobs")
    op.rename_table("reports_new", "reports")
    op.create_index("ix_jobs_project", "analysis_jobs", ["project_id", "analysis_id"])
    op.create_index("ix_jobs_state", "analysis_jobs", ["state"])
