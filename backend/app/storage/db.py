"""SQLite engine, schema, and migrations (Alembic, upgrade-only at startup)."""

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

metadata = sa.MetaData()

analysis_jobs = sa.Table(
    "analysis_jobs",
    metadata,
    sa.Column("analysis_id", sa.String(36), primary_key=True),
    sa.Column("project", sa.String(256), nullable=False),
    sa.Column("env", sa.String(256), nullable=False),
    sa.Column("end_time", sa.String(20), nullable=False),
    sa.Column("config_hash", sa.String(12), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("created_at", sa.String(20), nullable=False),
    sa.Column("job", sa.Text, nullable=False),
    sa.Index("ix_jobs_scope", "project", "env", "analysis_id"),
    sa.Index("ix_jobs_state", "state"),
)

reports = sa.Table(
    "reports",
    metadata,
    sa.Column(
        "analysis_id",
        sa.String(36),
        sa.ForeignKey("analysis_jobs.analysis_id"),
        primary_key=True,
    ),
    sa.Column("schema_version", sa.String(8), nullable=False),
    sa.Column("created_at", sa.String(20), nullable=False),
    sa.Column("size_bytes", sa.Integer, nullable=False),
    sa.Column("body", sa.LargeBinary, nullable=False),
)


def make_engine(path: Path) -> sa.Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @sa.event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def migrate(engine: sa.Engine) -> None:
    """Apply pending migrations. Never drops data; existing reports are preserved."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
