"""Delivers a finished scheduled analysis to its schedule's notification channels (T025, T026).

Reads the project's current schedule and the channels' current destinations at delivery time,
so a channel edited or removed while a run was queued is honoured. The message is built once;
every enabled notification is then delivered concurrently, each bounded by
``DELIVERY_TIMEOUT_SECONDS``, so one slow or broken channel never affects another. Every failure
becomes a ``DeliveryOutcome``; nothing raises into the runner.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.domain.jobs import AnalysisJob
from app.domain.notifications import (
    DeliveryOutcome,
    NotificationChannel,
    NotificationStatus,
    ScheduleNotification,
    destination_count,
)
from app.domain.report import ReportSummary
from app.notifications.drivers import Drivers
from app.notifications.drivers.base import (
    DELIVERY_TIMEOUT_SECONDS,
    ChannelNotConfigured,
    DeliveryError,
)
from app.notifications.message import ReportMessage, build_message, report_link
from app.notifications.policy import has_problem, skip_reason
from app.storage.channels import ChannelNotFound, SqliteChannelRepository
from app.storage.projects import SqliteProjectRepository
from app.storage.repository import SqliteReportRepository
from app.storage.secrets import SecretsUnreadable

log = logging.getLogger("app.notifications")


class ReportNotifier:
    def __init__(
        self,
        projects: SqliteProjectRepository,
        reports: SqliteReportRepository,
        channels: SqliteChannelRepository,
        drivers: Drivers,
        public_url: str | None,
    ) -> None:
        self.projects = projects
        self.reports = reports
        self.channels = channels
        self.drivers = drivers
        self.public_url = public_url

    async def job_finished(self, job: AnalysisJob) -> list[DeliveryOutcome]:
        """One outcome per enabled notification of the project's schedule; empty without any."""
        project = await self.projects.get(job.scope.project_id)
        schedule = project.schedule if project else None
        if project is None or schedule is None:
            return []
        wanted = [n for n in schedule.notifications if n.enabled]
        if not wanted:
            return []

        message: ReportMessage | None = None
        build_error = ""
        try:
            summary: ReportSummary | None = None
            if job.report_available and (report := await self.reports.get_report(job.analysis_id)):
                summary = report.summary()
            message = build_message(
                project.name,
                job,
                summary,
                timezone=schedule.timezone,
                link=report_link(self.public_url, job.analysis_id),
            )
        except Exception as exc:
            log.exception("building the notification of analysis %s failed", job.analysis_id)
            build_error = f"Could not read the report: {type(exc).__name__}"

        outcomes = await asyncio.gather(
            *(
                self._deliver(n, message, job.analysis_id)
                if message is not None
                else self._failed(n, build_error)
                for n in wanted
            )
        )
        return [o for o in outcomes if o is not None]

    async def _failed(
        self, notification: ScheduleNotification, reason: str
    ) -> DeliveryOutcome | None:
        try:
            channel, _ = await self.channels.resolve(notification.channel_id)
        except ChannelNotFound, SecretsUnreadable:
            return None
        return _outcome(
            channel, NotificationStatus.FAILED, destination_count(channel.destination), reason
        )

    async def _deliver(
        self, notification: ScheduleNotification, message: ReportMessage, analysis_id: str
    ) -> DeliveryOutcome | None:
        try:
            channel, secrets = await self.channels.resolve(notification.channel_id)
        except ChannelNotFound:  # removed meanwhile; the schedule no longer names it
            return None
        except SecretsUnreadable:
            unreadable = await self.channels.get(notification.channel_id)
            if unreadable is None:
                return None
            return _outcome(
                unreadable,
                NotificationStatus.FAILED,
                destination_count(unreadable.destination),
                "The channel's secret cannot be decrypted (secret key changed); re-enter it.",
            )

        destination = channel.destination
        addressed = destination_count(destination)
        if (skipped := skip_reason(notification.policy, message)) is not None:
            return _outcome(channel, NotificationStatus.SKIPPED, 0, skipped)
        driver = self.drivers.get(destination.kind)
        if driver is None:
            missing = f"No {destination.kind} driver on this server."
            return _outcome(channel, NotificationStatus.NOT_CONFIGURED, addressed, missing)
        if (unavailable := driver.unavailable()) is not None:
            return _outcome(channel, NotificationStatus.NOT_CONFIGURED, addressed, unavailable)

        silent = not has_problem(notification.policy, message)
        try:
            reached = await asyncio.wait_for(
                driver.deliver(destination, secrets, message, silent=silent),
                DELIVERY_TIMEOUT_SECONDS,
            )
        except ChannelNotConfigured as exc:
            return _outcome(channel, NotificationStatus.NOT_CONFIGURED, addressed, exc.reason)
        except DeliveryError as exc:
            log.warning("delivering analysis %s to %r failed: %s", analysis_id, channel.name, exc)
            return _outcome(channel, NotificationStatus.FAILED, addressed, exc.reason)
        except TimeoutError:
            reason = f"Delivery exceeded {DELIVERY_TIMEOUT_SECONDS:.0f} s."
            log.warning("delivering analysis %s to %r timed out", analysis_id, channel.name)
            return _outcome(channel, NotificationStatus.FAILED, addressed, reason)
        except Exception as exc:
            # Library messages may embed secrets (URLs with tokens): record the type only.
            log.warning(
                "delivering analysis %s to %r failed: %s",
                analysis_id,
                channel.name,
                type(exc).__name__,
            )
            reason = f"Unexpected error: {type(exc).__name__}"
            return _outcome(channel, NotificationStatus.FAILED, addressed, reason)

        log.info("delivered analysis %s to %r (%d)", analysis_id, channel.name, reached)
        return _outcome(channel, NotificationStatus.SENT, reached)


def _outcome(
    channel: NotificationChannel,
    status: NotificationStatus,
    destinations: int,
    reason: str | None = None,
) -> DeliveryOutcome:
    return DeliveryOutcome(
        channel_id=channel.channel_id,
        name=channel.name,
        kind=channel.destination.kind,
        status=status,
        destinations=destinations,
        reason=reason,
        at=datetime.now(UTC),
    )
