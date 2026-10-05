"""Per-project report schedules.

``schedule`` holds the ReportSchedule JSON (null: on demand only). ``schedule_anchor`` is the
instant up to which scheduled runs are handled; it is reset to the save time whenever the
schedule changes, so a new schedule never fires for times before it was saved.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("schedule", sa.Text, nullable=True, comment="JSON ReportSchedule")
    )
    op.add_column(
        "projects",
        sa.Column(
            "schedule_anchor",
            sa.String(20),
            nullable=True,
            comment="Scheduled runs at or before this instant are handled",
        ),
    )
