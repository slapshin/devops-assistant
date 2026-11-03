"""Email notifications for scheduled reports (T025).

A project's ``ReportSchedule`` can carry an ``EmailNotification``: when a scheduled analysis
finishes, the report is emailed to its recipients if the policy matches. Delivery is a side
effect: its outcome is recorded on the job (``AnalysisJob.notification``) and never changes the
job's state. Manual analyses are never emailed.
"""

import re
from enum import StrEnum

from pydantic import Field, field_validator

from app.domain.common import Contract, Severity, UtcDatetime

MAX_RECIPIENTS = 20
MAX_EMAIL_CHARS = 254
MAX_OUTCOME_REASON_CHARS = 300
# Deliberately strict: one plain address per entry, nothing a mail header would interpret
# (display names, comments, lists, quoting, line breaks).
_EMAIL = re.compile(
    r"^[^@\s,;:<>()\[\]\"\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)

SEVERITY_ORDER = list(Severity)
"""Lowest first."""


def validate_recipients(value: list[str]) -> list[str]:
    """Strip, validate, and deduplicate (case-insensitively) email addresses, keeping order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        address = raw.strip()
        if len(address) > MAX_EMAIL_CHARS or not _EMAIL.match(address):
            raise ValueError(f"not a valid email address: {address!r}")
        if address.lower() not in seen:
            seen.add(address.lower())
            result.append(address)
    return result


class NotifyWhen(StrEnum):
    ALWAYS = "always"
    """Every finished scheduled analysis, including ones without findings."""
    PROBLEMS = "problems"
    """A failed or partial analysis, or one with a finding at or above ``min_severity``."""


class EmailNotification(Contract):
    """Email each scheduled report to ``recipients`` when ``when`` matches."""

    recipients: list[str] = Field(
        min_length=1,
        max_length=MAX_RECIPIENTS,
        description="Plain email addresses (no display names); duplicates are removed.",
        examples=[["oncall@example.com"]],
    )
    when: NotifyWhen = Field(
        default=NotifyWhen.PROBLEMS,
        description="always, or problems: failed or partial analyses and findings at or "
        "above min_severity.",
    )
    min_severity: Severity = Field(
        default=Severity.HIGH, description="Lowest finding severity that sends (problems only)."
    )

    @field_validator("recipients")
    @classmethod
    def _valid_recipients(cls, value: list[str]) -> list[str]:
        return validate_recipients(value)


class NotificationStatus(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"
    """The policy did not match (e.g. no finding at or above the minimum severity)."""
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    """The project asks for email but the server has no email delivery configured."""


class NotificationOutcome(Contract):
    status: NotificationStatus
    recipients: int = Field(ge=0, description="Number of recipients addressed.")
    reason: str | None = Field(
        default=None,
        max_length=MAX_OUTCOME_REASON_CHARS,
        description="Why skipped, failed, or not configured.",
    )
    at: UtcDatetime


class EmailTestRequest(Contract):
    """Body of POST /api/notifications/test-email."""

    recipients: list[str] = Field(min_length=1, max_length=MAX_RECIPIENTS)

    @field_validator("recipients")
    @classmethod
    def _valid_recipients(cls, value: list[str]) -> list[str]:
        return validate_recipients(value)


class EmailTestResult(Contract):
    recipients: list[str]
    sent_at: UtcDatetime
