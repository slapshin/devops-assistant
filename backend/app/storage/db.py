"""SQLite engine, schema, and migrations (Alembic, upgrade-only at startup)."""

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
# Wait this long for a competing writer (e.g. a maintenance command) before failing.
BUSY_TIMEOUT_MS = 5000

metadata = sa.MetaData()

projects = sa.Table(
    "projects",
    metadata,
    sa.Column("project_id", sa.String(36), primary_key=True),
    sa.Column("name", sa.String(100, collation="NOCASE"), nullable=False, unique=True),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("matchers", sa.Text, nullable=False),
    sa.Column("created_at", sa.String(20), nullable=False),
    sa.Column("updated_at", sa.String(20), nullable=False),
    sa.Column("schedule", sa.Text, nullable=True, comment="JSON ReportSchedule"),
    sa.Column(
        "schedule_anchor",
        sa.String(20),
        nullable=True,
        comment="Scheduled runs at or before this instant are handled",
    ),
    sa.Column("keep_reports", sa.Integer, nullable=True, comment="Newest reports kept"),
)

project_sources = sa.Table(
    "project_sources",
    metadata,
    sa.Column(
        "project_id",
        sa.String(36),
        sa.ForeignKey("projects.project_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("kind", sa.String(32), primary_key=True),
    sa.Column("config", sa.Text, nullable=False, comment="JSON without secrets"),
    sa.Column("secrets", sa.LargeBinary, nullable=True, comment="Fernet-encrypted JSON"),
    sa.Column("updated_at", sa.String(20), nullable=False),
)

analysis_jobs = sa.Table(
    "analysis_jobs",
    metadata,
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
    sa.Index("ix_jobs_project", "project_id", "analysis_id"),
    sa.Index("ix_jobs_state", "state"),
)

reports = sa.Table(
    "reports",
    metadata,
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

app_meta = sa.Table(
    "app_meta",
    metadata,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
)


def make_engine(path: Path) -> sa.Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @sa.event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.close()

    return engine


def migrate(engine: sa.Engine) -> None:
    """Apply pending migrations. Never drops data; existing reports are preserved.

    Foreign keys are off while migrating so tables can be rebuilt (SQLite cannot alter
    constraints in place) and are verified before they are switched back on.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()  # a pragma inside an open transaction would be ignored
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                if violations:
                    raise RuntimeError(f"migration left foreign key violations: {violations[:5]}")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
