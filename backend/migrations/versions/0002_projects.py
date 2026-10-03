"""Projects and their per-kind source configurations (T011).

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100, collation="NOCASE"), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("matchers", sa.Text, nullable=False),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.String(20), nullable=False),
    )
    op.create_table(
        "project_sources",
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
