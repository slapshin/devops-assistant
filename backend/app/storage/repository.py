"""SQLite ReportRepository: job status and immutable, compressed report snapshots."""

import asyncio
import zlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from app.domain.common import REPORT_SCHEMA_VERSION, Severity, format_utc
from app.domain.explanation import ExplanationStatus
from app.domain.interfaces import ProjectActivity
from app.domain.jobs import AnalysisJob, ErrorCode, JobError, JobState, StageProgress, StageStatus
from app.domain.report import AnalysisReport, AnalysisRequest
from app.storage.db import analysis_jobs, make_engine, migrate, reports

ACTIVE = (JobState.QUEUED.value, JobState.RUNNING.value)
MAX_REPORT_BYTES = 20 * 1024 * 1024
REPORT_COMPRESSION_LEVEL = 6


class SchemaUnsupported(Exception):
    def __init__(self, version: str) -> None:
        super().__init__(f"report schema {version} is not supported")
        self.version = version


class ReportTooLarge(Exception):
    pass


class SqliteReportRepository:
    def __init__(self, path: Path, max_report_bytes: int = MAX_REPORT_BYTES) -> None:
        self.path = path
        self.max_report_bytes = max_report_bytes
        self.engine = make_engine(path)
        migrate(self.engine)
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self.engine.dispose()

    async def _run[T](self, fn: Callable[[sa.Connection], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(self._tx, fn)

    def _tx[T](self, fn: Callable[[sa.Connection], T]) -> T:
        with self.engine.begin() as conn:
            return fn(conn)

    @staticmethod
    def _row(job: AnalysisJob) -> dict[str, str]:
        return {
            "analysis_id": job.analysis_id,
            "project_id": job.scope.project_id,
            "end_time": format_utc(job.end_time),
            "config_hash": job.config_hash,
            "state": job.state.value,
            "created_at": format_utc(job.created_at),
            "job": job.model_dump_json(),
        }

    async def create_job(self, job: AnalysisJob) -> None:
        await self._run(lambda c: c.execute(analysis_jobs.insert().values(**self._row(job))))

    def _get(self, conn: sa.Connection, analysis_id: str) -> AnalysisJob | None:
        row = conn.execute(
            sa.select(analysis_jobs.c.job).where(analysis_jobs.c.analysis_id == analysis_id)
        ).first()
        return AnalysisJob.model_validate_json(row[0]) if row else None

    def _put(self, conn: sa.Connection, job: AnalysisJob) -> None:
        conn.execute(
            analysis_jobs.update()
            .where(analysis_jobs.c.analysis_id == job.analysis_id)
            .values(state=job.state.value, job=job.model_dump_json())
        )

    async def get_job(self, analysis_id: str) -> AnalysisJob | None:
        return await self._run(lambda c: self._get(c, analysis_id))

    async def find_active(self, request: AnalysisRequest) -> AnalysisJob | None:
        def q(conn: sa.Connection) -> AnalysisJob | None:
            row = conn.execute(
                sa.select(analysis_jobs.c.job)
                .where(analysis_jobs.c.project_id == request.scope.project_id)
                .where(analysis_jobs.c.end_time == format_utc(request.end_time))
                .where(analysis_jobs.c.config_hash == request.config_hash)
                .where(analysis_jobs.c.state.in_(ACTIVE))
                .order_by(analysis_jobs.c.analysis_id.desc())
            ).first()
            return AnalysisJob.model_validate_json(row[0]) if row else None

        return await self._run(q)

    async def count_active(self) -> dict[JobState, int]:
        def q(conn: sa.Connection) -> dict[JobState, int]:
            rows = conn.execute(
                sa.select(analysis_jobs.c.state, sa.func.count())
                .where(analysis_jobs.c.state.in_(ACTIVE))
                .group_by(analysis_jobs.c.state)
            ).all()
            return {JobState(s): n for s, n in rows}

        return await self._run(q)

    async def list_jobs(
        self, project_id: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AnalysisJob], str | None]:
        def q(conn: sa.Connection) -> tuple[list[AnalysisJob], str | None]:
            stmt = sa.select(analysis_jobs.c.job).order_by(analysis_jobs.c.analysis_id.desc())
            if project_id is not None:
                stmt = stmt.where(analysis_jobs.c.project_id == project_id)
            if cursor:
                stmt = stmt.where(analysis_jobs.c.analysis_id < cursor)

            rows = conn.execute(stmt.limit(limit + 1)).all()
            jobs = [AnalysisJob.model_validate_json(r[0]) for r in rows[:limit]]
            # One extra row was fetched only to learn whether another page exists.
            has_more = len(rows) > limit
            next_cursor = jobs[-1].analysis_id if has_more and jobs else None
            return jobs, next_cursor

        return await self._run(q)

    async def project_activity(self, project_ids: Sequence[str]) -> dict[str, ProjectActivity]:

        def newest(conn: sa.Connection, project_id: str, active: bool) -> AnalysisJob | None:
            state = analysis_jobs.c.state
            row = conn.execute(
                sa.select(analysis_jobs.c.job)
                .where(analysis_jobs.c.project_id == project_id)
                .where(state.in_(ACTIVE) if active else state.not_in(ACTIVE))
                .order_by(analysis_jobs.c.analysis_id.desc())
                .limit(1)
            ).first()
            return AnalysisJob.model_validate_json(row[0]) if row else None

        def report_count(conn: sa.Connection, project_id: str) -> int:
            return int(
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(reports.join(analysis_jobs))
                    .where(analysis_jobs.c.project_id == project_id)
                ).scalar_one()
            )

        def q(conn: sa.Connection) -> dict[str, ProjectActivity]:
            return {
                pid: ProjectActivity(
                    latest=newest(conn, pid, active=False),
                    active=newest(conn, pid, active=True),
                    report_count=report_count(conn, pid),
                )
                for pid in project_ids
            }

        return await self._run(q)

    async def has_active(self, project_id: str) -> bool:
        def q(conn: sa.Connection) -> bool:
            row = conn.execute(
                sa.select(analysis_jobs.c.analysis_id)
                .where(analysis_jobs.c.project_id == project_id)
                .where(analysis_jobs.c.state.in_(ACTIVE))
                .limit(1)
            ).first()
            return row is not None

        return await self._run(q)

    async def update_stage(self, analysis_id: str, progress: StageProgress) -> None:
        def q(conn: sa.Connection) -> None:
            job = self._get(conn, analysis_id)
            if job is None or not job.state.active:
                return

            stages = [_merge(s, progress) if s.stage is progress.stage else s for s in job.stages]
            self._put(conn, job.model_copy(update={"stages": stages}))

        await self._run(q)

    async def mark_running(self, analysis_id: str, at: datetime) -> AnalysisJob | None:
        def q(conn: sa.Connection) -> AnalysisJob | None:
            job = self._get(conn, analysis_id)
            if job is None or job.state is not JobState.QUEUED:
                return None

            job = job.model_copy(update={"state": JobState.RUNNING, "started_at": at})
            self._put(conn, job)
            return job

        return await self._run(q)

    async def finish_job(
        self,
        analysis_id: str,
        state: JobState,
        error: JobError | None,
        *,
        explanation_status: ExplanationStatus | None = None,
        finding_counts: dict[Severity, int] | None = None,
        daily_episodes: list[int | None] | None = None,
        report_available: bool = False,
    ) -> AnalysisJob:
        now = datetime.now(UTC)

        def q(conn: sa.Connection) -> AnalysisJob:
            job = self._get(conn, analysis_id)
            if job is None:
                raise KeyError(analysis_id)

            # A running stage cannot finish once the job has ended without completing.
            stages = [
                s.model_copy(update={"status": StageStatus.FAILED, "finished_at": now})
                if s.status is StageStatus.RUNNING and state is not JobState.COMPLETED
                else s
                for s in job.stages
            ]
            updated = job.model_copy(
                update={
                    "state": state,
                    "error": error,
                    "finished_at": now,
                    "stages": stages,
                    "report_available": report_available,
                    "finding_counts": finding_counts,
                    "daily_episodes": daily_episodes,
                    "explanation_status": explanation_status or job.explanation_status,
                }
            )
            self._put(conn, updated)
            return updated

        return await self._run(q)

    async def save_report(self, report: AnalysisReport) -> None:
        raw = report.model_dump_json().encode()
        if len(raw) > self.max_report_bytes:
            raise ReportTooLarge(
                f"report {report.analysis_id} is {len(raw)} bytes > {self.max_report_bytes}"
            )

        body = zlib.compress(raw, REPORT_COMPRESSION_LEVEL)
        await self._run(
            lambda c: c.execute(
                reports.insert().values(
                    analysis_id=report.analysis_id,
                    schema_version=report.schema_version,
                    created_at=format_utc(report.generated_at),
                    size_bytes=len(raw),
                    body=body,
                )
            )
        )

    async def get_report(self, analysis_id: str) -> AnalysisReport | None:
        def q(conn: sa.Connection) -> tuple[str, bytes] | None:
            row = conn.execute(
                sa.select(reports.c.schema_version, reports.c.body).where(
                    reports.c.analysis_id == analysis_id
                )
            ).first()
            return (row[0], row[1]) if row else None

        row = await self._run(q)
        if row is None:
            return None

        version, body = row
        # Only a major schema change breaks reading; minor versions stay compatible.
        if version.split(".")[0] != REPORT_SCHEMA_VERSION.split(".")[0]:
            raise SchemaUnsupported(version)
        return AnalysisReport.model_validate_json(zlib.decompress(body))

    async def fail_interrupted(self) -> int:
        """Mark queued/running jobs failed with interrupted_by_restart; return the count."""
        error = JobError(
            code=ErrorCode.INTERRUPTED_BY_RESTART,
            message="The service restarted while this analysis was running. No report was saved.",
        )

        def q(conn: sa.Connection) -> list[str]:
            rows = conn.execute(
                sa.select(analysis_jobs.c.analysis_id).where(analysis_jobs.c.state.in_(ACTIVE))
            ).all()
            return [r[0] for r in rows]

        ids = await self._run(q)
        for analysis_id in ids:
            await self.finish_job(analysis_id, JobState.FAILED, error)
        return len(ids)

    async def stats(self) -> tuple[int, int]:
        def q(conn: sa.Connection) -> int:
            return int(conn.execute(sa.select(sa.func.count()).select_from(reports)).scalar_one())

        count = await self._run(q)
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*"))
        return count, size


def _merge(current: StageProgress, update: StageProgress) -> StageProgress:
    data = current.model_dump()
    data.update({k: v for k, v in update.model_dump().items() if v is not None})
    return StageProgress.model_validate(data)
