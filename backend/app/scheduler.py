"""Background scheduler: submits each project's scheduled analyses to the job runner.

Every tick it compares each schedule's latest due instant with the project's anchor (the
instant scheduled runs are handled up to). A due run analyses the 24 h ending at its scheduled
instant, so a slightly late tick still produces the 08:00 report. Runs missed by more than
``MISSED_RUN_GRACE`` (e.g. the app was down) are skipped rather than run late; a full queue
is retried on the next tick.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.domain.common import Scope, floor_to_step
from app.domain.jobs import JobTrigger
from app.jobs import JobRunner, QueueFull
from app.storage.projects import SqliteProjectRepository

log = logging.getLogger("app.scheduler")

TICK_SECONDS = 30
MISSED_RUN_GRACE = timedelta(hours=6)


class ReportScheduler:
    def __init__(
        self,
        projects: SqliteProjectRepository,
        runner: JobRunner,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.projects = projects
        self.runner = runner
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="report-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    async def tick(self, now: datetime) -> list[str]:
        """Submit every due scheduled run; returns the submitted analysis IDs."""
        submitted = []
        for project, anchor in await self.projects.scheduled():
            assert project.schedule is not None
            due = project.schedule.latest_at_or_before(now)
            if due <= anchor:
                continue
            if now - due > MISSED_RUN_GRACE:
                log.warning("project %s: skipped scheduled run at %s (missed)", project.name, due)
            elif not project.sources:
                log.warning("project %s: scheduled run skipped, no data source", project.name)
            else:
                scope = Scope(
                    project_id=project.project_id,
                    project_name=project.name,
                    matchers=project.matchers,
                )
                request = self.runner.request_for(scope, floor_to_step(due))
                try:
                    job, _ = await self.runner.submit(request, JobTrigger.SCHEDULED)
                except QueueFull:
                    log.warning("project %s: queue full, retrying scheduled run", project.name)
                    continue
                submitted.append(job.analysis_id)
                log.info("project %s: scheduled analysis %s", project.name, job.analysis_id)
            await self.projects.advance_schedule(project.project_id, due)
        return submitted
