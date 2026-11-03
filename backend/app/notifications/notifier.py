"""Emails a finished scheduled analysis to its project's recipients (T025).

Reads the project's current schedule at delivery time, so recipients removed while a run was
queued receive nothing. Every failure becomes a ``NotificationOutcome``; nothing raises into the
job runner.
"""

import logging
from datetime import UTC, datetime

from app.domain.jobs import AnalysisJob, JobState
from app.domain.notifications import (
    MAX_OUTCOME_REASON_CHARS,
    SEVERITY_ORDER,
    EmailNotification,
    NotificationOutcome,
    NotificationStatus,
    NotifyWhen,
)
from app.domain.report import ReportSummary
from app.notifications.render import render_email, report_link
from app.notifications.transport import EmailTransport, build_message
from app.storage.projects import SqliteProjectRepository
from app.storage.repository import SqliteReportRepository

log = logging.getLogger("app.notifications")


def skip_reason(
    notify: EmailNotification, job: AnalysisJob, summary: ReportSummary | None
) -> str | None:
    """Why the policy does not send this job's report; None when it does."""
    if notify.when is NotifyWhen.ALWAYS:
        return None
    if job.state in (JobState.FAILED, JobState.PARTIAL) or summary is None:
        return None
    floor = SEVERITY_ORDER.index(notify.min_severity)
    if any(SEVERITY_ORDER.index(f.severity) >= floor for f in summary.findings):
        return None
    return f"no finding at or above {notify.min_severity.value} severity"


def _error_reason(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
    return message[:MAX_OUTCOME_REASON_CHARS]


class ReportNotifier:
    def __init__(
        self,
        projects: SqliteProjectRepository,
        reports: SqliteReportRepository,
        transport: EmailTransport | None,
        sender: str,
        public_url: str | None,
    ) -> None:
        self.projects = projects
        self.reports = reports
        self.transport = transport
        self.sender = sender
        self.public_url = public_url

    async def job_finished(self, job: AnalysisJob) -> NotificationOutcome | None:
        """The delivery outcome of a finished scheduled job; None when none is requested."""
        project = await self.projects.get(job.scope.project_id)
        schedule = project.schedule if project else None
        if project is None or schedule is None or schedule.notify is None:
            return None
        notify = schedule.notify

        def outcome(status: NotificationStatus, reason: str | None = None) -> NotificationOutcome:
            recipients = len(notify.recipients) if status is not NotificationStatus.SKIPPED else 0
            return NotificationOutcome(
                status=status, recipients=recipients, reason=reason, at=datetime.now(UTC)
            )

        if self.transport is None:
            return outcome(
                NotificationStatus.NOT_CONFIGURED, "Email delivery is not configured (SMTP_HOST)."
            )

        try:
            summary = None
            if job.report_available and (report := await self.reports.get_report(job.analysis_id)):
                summary = report.summary()
            if (reason := skip_reason(notify, job, summary)) is not None:
                return outcome(NotificationStatus.SKIPPED, reason)

            email = render_email(
                project.name,
                job,
                summary,
                timezone=schedule.timezone,
                link=report_link(self.public_url, job.analysis_id),
            )
            await self.transport.send(build_message(self.sender, notify.recipients, email))
        except Exception as exc:
            log.warning("emailing analysis %s failed: %s", job.analysis_id, _error_reason(exc))
            return outcome(NotificationStatus.FAILED, _error_reason(exc))

        log.info("emailed analysis %s to %d recipient(s)", job.analysis_id, len(notify.recipients))
        return outcome(NotificationStatus.SENT)
