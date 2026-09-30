"""Initial schema: analysis jobs and report snapshots.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "analysis_jobs",
        sa.Column("analysis_id", sa.String(36), primary_key=True),
        sa.Column("project", sa.String(256), nullable=False),
        sa.Column("env", sa.String(256), nullable=False),
        sa.Column("end_time", sa.String(20), nullable=False),
        sa.Column("config_hash", sa.String(12), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("job", sa.Text, nullable=False),
    )
    op.create_index("ix_jobs_scope", "analysis_jobs", ["project", "env", "analysis_id"])
    op.create_index("ix_jobs_state", "analysis_jobs", ["state"])
    op.create_table(
        "reports",
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
