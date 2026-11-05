"""Notification channels and schedule notifications (T026).

``notification_channels`` holds application-level channels (a destination per row, secrets
encrypted separately); ``schedule_notifications`` lists the channels a project's schedule sends
to, each with its policy (the channel is a restricting foreign key). A T025 schedule's
``notify`` becomes an email channel (projects with the same recipients share one, named after
the first recipient) plus a schedule notification with the T025 policy; the schedule JSON loses
``notify`` and ``schedule_anchor`` is left alone, so no scheduled run fires again or is skipped.
Stored jobs replace ``notification`` (one email outcome or null) with ``notifications`` (a list).

Revision ID: 0009
Revises: 0008
"""

import json
import uuid
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"

LEGACY_OUTCOME_NAME = "Email"


def _create_tables() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("channel_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100, collation="NOCASE"), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "destination", sa.Text, nullable=False, comment="JSON destination without secrets"
        ),
        sa.Column("secrets", sa.LargeBinary, nullable=True, comment="Fernet-encrypted JSON"),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.String(20), nullable=False),
    )
    op.create_table(
        "schedule_notifications",
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.project_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "channel_id",
            sa.String(36),
            sa.ForeignKey("notification_channels.channel_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("policy", sa.Text, nullable=False, comment="JSON NotificationPolicy"),
    )


def _channel_name(recipients: list[str], taken: set[str]) -> str:
    """ "Email oncall@example.com" or "Email oncall@example.com +2", unique."""
    more = f" +{len(recipients) - 1}" if len(recipients) > 1 else ""
    base = f"Email {recipients[0]}{more}"[:95]
    name, n = base, 1
    while name.lower() in taken:
        n += 1
        name = f"{base} {n}"
    taken.add(name.lower())
    return name


def _move_schedule_notify(conn: sa.Connection) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT project_id, schedule, updated_at FROM projects"
            " WHERE schedule IS NOT NULL ORDER BY name"
        )
    ).all()
    channels: dict[tuple[str, ...], str] = {}
    taken: set[str] = set()
    for project_id, raw, updated_at in rows:
        schedule: dict[str, Any] = json.loads(raw)
        if "notify" not in schedule:
            continue
        notify = schedule.pop("notify")
        if notify is not None:
            recipients: list[str] = notify["recipients"]
            key = tuple(sorted(r.lower() for r in recipients))
            if key not in channels:
                channels[key] = str(uuid.uuid7())
                conn.execute(
                    sa.text(
                        "INSERT INTO notification_channels (channel_id, name, description, kind,"
                        " destination, secrets, created_at, updated_at) VALUES (:channel_id,"
                        " :name, :description, 'email', :destination, NULL, :at, :at)"
                    ),
                    {
                        "channel_id": channels[key],
                        "name": _channel_name(recipients, taken),
                        "description": "Created on upgrade from a schedule's email setting.",
                        "destination": json.dumps(
                            {"kind": "email", "recipients": recipients}, sort_keys=True
                        ),
                        "at": updated_at,
                    },
                )
            policy = {
                "when": notify.get("when", "problems"),
                "min_severity": notify.get("min_severity", "high"),
            }
            conn.execute(
                sa.text(
                    "INSERT INTO schedule_notifications (project_id, channel_id, position,"
                    " enabled, policy) VALUES (:project_id, :channel_id, 0, 1, :policy)"
                ),
                {
                    "project_id": project_id,
                    "channel_id": channels[key],
                    "policy": json.dumps(policy),
                },
            )
        conn.execute(
            sa.text("UPDATE projects SET schedule = :schedule WHERE project_id = :project_id"),
            {"schedule": json.dumps(schedule), "project_id": project_id},
        )


def _outcomes(legacy: dict[str, Any] | None) -> list[dict[str, Any]]:
    if legacy is None:
        return []
    return [
        {
            "channel_id": None,
            "name": LEGACY_OUTCOME_NAME,
            "kind": "email",
            "status": legacy["status"],
            "destinations": legacy.get("recipients", 0),
            "reason": legacy.get("reason"),
            "at": legacy["at"],
        }
    ]


def _rewrite_jobs(conn: sa.Connection) -> None:
    rows = conn.execute(
        sa.text("SELECT analysis_id, job FROM analysis_jobs WHERE job LIKE '%\"notification\"%'")
    ).all()
    for analysis_id, raw in rows:
        job: dict[str, Any] = json.loads(raw)
        if "notification" not in job:
            continue
        job["notifications"] = _outcomes(job.pop("notification"))
        conn.execute(
            sa.text("UPDATE analysis_jobs SET job = :job WHERE analysis_id = :analysis_id"),
            {"job": json.dumps(job), "analysis_id": analysis_id},
        )


def upgrade() -> None:
    _create_tables()
    conn = op.get_bind()
    _move_schedule_notify(conn)
    _rewrite_jobs(conn)
