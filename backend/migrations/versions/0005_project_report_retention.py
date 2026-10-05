"""Per-project report retention.

``keep_reports`` is the number of newest reports a project keeps (null: all). Older finished
analyses are deleted with their reports after each analysis and when the setting is saved.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("keep_reports", sa.Integer, nullable=True, comment="Newest reports kept"),
    )
