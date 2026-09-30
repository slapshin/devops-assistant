"""Bounded in-process job runner: 1 running, up to N queued, cancellation, and timeouts."""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.common import Scope, Severity
from app.domain.detector_config import DetectorConfig
from app.domain.explanation import ExplanationStatus
from app.domain.ids import new_analysis_id
from app.domain.interfaces import AnalysisService, CancellationToken, Cancelled, ReportRepository
from app.domain.jobs import (
    AnalysisJob,
    ErrorCode,
    JobError,
    JobState,
    StageName,
    StageProgress,
    StageStatus,
)
from app.domain.report import AnalysisRequest

log = logging.getLogger("app.jobs")


class QueueFull(Exception):
    pass


class NotActive(Exception):
    pass


@dataclass(frozen=True)
class RunnerLimits:
    max_running: int = 1
    max_queued: int = 4
    job_timeout_seconds: float = 600.0


class _Progress:
    def __init__(self, repo: ReportRepository, analysis_id: str) -> None:
        self.repo = repo
        self.analysis_id = analysis_id

    async def update(self, progress: StageProgress) -> None:
        await self.repo.update_stage(self.analysis_id, progress)


class JobRunner:
    def __init__(
        self,
        repo: ReportRepository,
        service: AnalysisService,
        config: DetectorConfig,
        explanation_status: ExplanationStatus,
        limits: RunnerLimits | None = None,
    ) -> None:
        self.repo = repo
        self.service = service
        self.config = config
        self.explanation_status = explanation_status
        self.limits = limits or RunnerLimits()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._requests: dict[str, AnalysisRequest] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._user_cancelled: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._submit_lock = asyncio.Lock()

    async def start(self) -> int:
        recovered = await self.repo.fail_interrupted()
        if recovered:
            log.warning("marked %d interrupted job(s) failed after restart", recovered)
        self._workers = [
            asyncio.create_task(self._worker(), name=f"analysis-worker-{i}")
            for i in range(self.limits.max_running)
        ]
        return recovered

    async def stop(self) -> None:
        for task in [*self._tasks.values(), *self._workers]:
            task.cancel()
        await asyncio.gather(*self._tasks.values(), *self._workers, return_exceptions=True)
        self._workers = []

    def request_for(self, scope: Scope, end_time: datetime) -> AnalysisRequest:
        return AnalysisRequest(
            scope=scope,
            end_time=end_time,
            detector_version=self.config.version,
            config_hash=self.config.config_hash,
        )

    async def submit(self, request: AnalysisRequest) -> tuple[AnalysisJob, bool]:
        """Return (job, duplicate). Raises QueueFull when the queue limit is reached."""
        async with self._submit_lock:
            if (existing := await self.repo.find_active(request)) is not None:
                return existing, True
            active = await self.repo.count_active()
            if active.get(JobState.QUEUED, 0) >= self.limits.max_queued:
                raise QueueFull
            job = AnalysisJob(
                analysis_id=new_analysis_id(),
                scope=request.scope,
                end_time=request.end_time,
                detector_version=request.detector_version,
                config_hash=request.config_hash,
                state=JobState.QUEUED,
                stages=[StageProgress(stage=s, status=StageStatus.PENDING) for s in StageName],
                explanation_status=self.explanation_status,
                created_at=datetime.now(UTC),
                report_available=False,
            )
            await self.repo.create_job(job)
            self._requests[job.analysis_id] = request
            self._tokens[job.analysis_id] = CancellationToken()
            await self._queue.put(job.analysis_id)
            return job, False

    async def cancel(self, analysis_id: str) -> AnalysisJob:
        job = await self.repo.get_job(analysis_id)
        if job is None:
            raise KeyError(analysis_id)
        if not job.state.active:
            raise NotActive
        self._user_cancelled.add(analysis_id)
        if (token := self._tokens.get(analysis_id)) is not None:
            token.cancel()
        if (task := self._tasks.get(analysis_id)) is not None:
            task.cancel()  # abort in-flight source/provider requests immediately
            await asyncio.gather(task, return_exceptions=True)
        elif job.state is JobState.QUEUED:
            await self._finish_cancelled(analysis_id)
        refreshed = await self.repo.get_job(analysis_id)
        assert refreshed is not None
        return refreshed

    async def _finish_cancelled(self, analysis_id: str) -> None:
        await self.repo.finish_job(
            analysis_id,
            JobState.CANCELLED,
            JobError(code=ErrorCode.INTERNAL_ERROR, message="Cancelled by user."),
        )

    async def _worker(self) -> None:
        while True:
            analysis_id = await self._queue.get()
            try:
                if analysis_id in self._user_cancelled:
                    continue
                task = asyncio.create_task(self._run(analysis_id), name=f"analysis-{analysis_id}")
                self._tasks[analysis_id] = task
                await asyncio.gather(task, return_exceptions=True)
            finally:
                self._tasks.pop(analysis_id, None)
                self._tokens.pop(analysis_id, None)
                self._requests.pop(analysis_id, None)
                self._queue.task_done()

    async def _run(self, analysis_id: str) -> None:
        request = self._requests[analysis_id]
        token = self._tokens[analysis_id]
        if await self.repo.mark_running(analysis_id, datetime.now(UTC)) is None:
            return
        try:
            report = await asyncio.wait_for(
                self.service.run(analysis_id, request, _Progress(self.repo, analysis_id), token),
                self.limits.job_timeout_seconds,
            )
            await self.repo.update_stage(
                analysis_id,
                StageProgress(
                    stage=StageName.SAVING,
                    status=StageStatus.RUNNING,
                    started_at=datetime.now(UTC),
                ),
            )
            await self.repo.save_report(report)
            await self.repo.update_stage(
                analysis_id,
                StageProgress(
                    stage=StageName.SAVING,
                    status=StageStatus.DONE,
                    finished_at=datetime.now(UTC),
                ),
            )
            counts = Counter(f.severity for f in report.findings)
            await self.repo.finish_job(
                analysis_id,
                JobState.PARTIAL if report.state.value == "partial" else JobState.COMPLETED,
                None,
                explanation_status=report.explanation.status,
                finding_counts={s: counts.get(s, 0) for s in Severity},
                report_available=True,
            )
        except Cancelled, asyncio.CancelledError:
            if analysis_id in self._user_cancelled:
                await self._finish_cancelled(analysis_id)
            else:  # shutdown: next start marks it interrupted
                raise
        except TimeoutError:
            await self.repo.finish_job(
                analysis_id,
                JobState.FAILED,
                JobError(
                    code=ErrorCode.JOB_TIMEOUT,
                    message=f"Analysis exceeded {self.limits.job_timeout_seconds:.0f} s.",
                ),
            )
        except Exception as exc:
            log.exception("analysis %s failed", analysis_id)
            from app.metrics.client import SourceError

            code = (
                ErrorCode.METRICS_SOURCE_UNAVAILABLE
                if isinstance(exc, SourceError)
                else ErrorCode.INTERNAL_ERROR
            )
            message = exc.message if isinstance(exc, SourceError) else type(exc).__name__
            await self.repo.finish_job(
                analysis_id, JobState.FAILED, JobError(code=code, message=message)
            )
        finally:
            self._user_cancelled.discard(analysis_id)
