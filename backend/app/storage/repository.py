"""SQLite ReportRepository: job status and immutable, compressed report snapshots."""

import asyncio
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from app.domain.common import REPORT_SCHEMA_VERSION, Scope, Severity, format_utc
from app.domain.explanation import ExplanationStatus
from app.domain.jobs import AnalysisJob, ErrorCode, JobError, JobState, StageProgress, StageStatus
from app.domain.report import AnalysisReport, AnalysisRequest
from app.storage.db import analysis_jobs, make_engine, migrate, reports

ACTIVE = (JobState.QUEUED.value, JobState.RUNNING.value)


class SchemaUnsupported(Exception):
    def __init__(self, version: str) -> None:
        super().__init__(f"report schema {version} is not supported")
        self.version = version


class ReportTooLarge(Exception):
    pass


class SqliteReportRepository:
    def __init__(self, path: Path, max_report_bytes: int = 20 * 1024 * 1024) -> None:
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
            "project": job.scope.project,
            "env": job.scope.env,
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
                .where(analysis_jobs.c.project == request.scope.project)
                .where(analysis_jobs.c.env == request.scope.env)
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
        self, scope: Scope | None, limit: int, cursor: str | None
    ) -> tuple[list[AnalysisJob], str | None]:
        def q(conn: sa.Connection) -> tuple[list[AnalysisJob], str | None]:
            stmt = sa.select(analysis_jobs.c.job).order_by(analysis_jobs.c.analysis_id.desc())
            if scope is not None:
                stmt = stmt.where(analysis_jobs.c.project == scope.project).where(
                    analysis_jobs.c.env == scope.env
                )
            if cursor:
                stmt = stmt.where(analysis_jobs.c.analysis_id < cursor)
            rows = conn.execute(stmt.limit(limit + 1)).all()
            jobs = [AnalysisJob.model_validate_json(r[0]) for r in rows[:limit]]
            nxt = jobs[-1].analysis_id if len(rows) > limit and jobs else None
            return jobs, nxt

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
        report_available: bool = False,
    ) -> AnalysisJob:
        now = datetime.now(UTC)

        def q(conn: sa.Connection) -> AnalysisJob:
            job = self._get(conn, analysis_id)
            if job is None:
                raise KeyError(analysis_id)
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
                    "explanation_status": explanation_status or job.explanation_status,
                }
            )
            self._put(conn, updated)
            return updated

        return await self._run(q)

    async def save_report(self, report: AnalysisReport) -> None:
        raw = report.model_dump_json().encode()
        if len(raw) > self.max_report_bytes:
            raise ReportTooLarge(f"{len(raw)} bytes > {self.max_report_bytes}")
        body = zlib.compress(raw, 6)
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
