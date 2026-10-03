"""Analysis job lifecycle and HTTP-facing contracts (framework-independent)."""

from enum import StrEnum

from pydantic import Field

from app.domain.common import Contract, LabelValue, Scope, Severity, UtcDatetime
from app.domain.explanation import ExplanationStatus


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def active(self) -> bool:
        return self in (JobState.QUEUED, JobState.RUNNING)


class StageName(StrEnum):
    DISCOVERY = "discovery"
    COLLECTION = "collection"
    DETECTION = "detection"
    TRENDS = "trends"
    EXPLANATION = "explanation"
    SAVING = "saving"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageProgress(Contract):
    stage: StageName
    status: StageStatus
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    done: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    message: str | None = None


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_NAME_TAKEN = "project_name_taken"
    CREDENTIALS_UNREADABLE = "credentials_unreadable"
    ENV_NOT_FOUND = "env_not_found"
    ANALYSIS_NOT_FOUND = "analysis_not_found"
    REPORT_NOT_READY = "report_not_ready"
    REPORT_UNAVAILABLE = "report_unavailable"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    ANALYSIS_NOT_ACTIVE = "analysis_not_active"
    QUEUE_FULL = "queue_full"
    METRICS_SOURCE_UNAVAILABLE = "metrics_source_unavailable"
    END_TIME_INVALID = "end_time_invalid"
    INTERRUPTED_BY_RESTART = "interrupted_by_restart"
    JOB_TIMEOUT = "job_timeout"
    INTERNAL_ERROR = "internal_error"
    NOT_IMPLEMENTED = "not_implemented"
    """Foundation-only: route exists but its task (T007) is not done."""


class JobError(Contract):
    code: ErrorCode
    message: str


class AnalysisJob(Contract):
    analysis_id: str
    scope: Scope
    end_time: UtcDatetime
    detector_version: str
    config_hash: str
    state: JobState
    stages: list[StageProgress]
    explanation_status: ExplanationStatus
    error: JobError | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    report_available: bool
    finding_counts: dict[Severity, int] | None = Field(
        default=None, description="Present once a report is saved."
    )


class AnalysisSubmission(Contract):
    """Body of POST /api/analyses. end_time is floored to the step server-side."""

    project: LabelValue
    env: LabelValue
    end_time: UtcDatetime | None = None


class AnalysisSubmitted(Contract):
    analysis: AnalysisJob
    duplicate_of_active: bool = False


class AnalysisList(Contract):
    items: list[AnalysisJob]
    next_cursor: str | None = None


class SourceStatus(Contract):
    reachable: bool | None = Field(description="Null when not checked.")
    checked_at: UtcDatetime | None = None
    message: str | None = None


class ProjectItem(Contract):
    project: str


class DiscoveredProjectList(Contract):
    """``project`` label values found in the global metrics source (legacy, until T012)."""

    items: list[ProjectItem]
    source_status: SourceStatus
    truncated: bool = False


class EnvItem(Contract):
    env: str


class EnvList(Contract):
    project: str
    items: list[EnvItem]
    truncated: bool = False


class Problem(Contract):
    """RFC 9457 problem details (application/problem+json)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: ErrorCode
    errors: list[dict[str, str]] | None = None


class HealthResponse(Contract):
    status: str
    version: str
    database: str = Field(description="'ok', 'unavailable', or 'not_initialized' before T007.")
    metrics_source: SourceStatus


class Limits(Contract):
    max_running_jobs: int
    max_queued_jobs: int
    query_timeout_seconds: int
    max_series_per_query: int
    max_series_per_job: int
    report_max_bytes: int


class RuntimeConfig(Contract):
    """GET /api/config — public runtime facts, never secrets."""

    version: str
    ai_provider: str
    ai_model: str | None
    explanation_status: ExplanationStatus = Field(
        description="disabled, not_configured, or pending (configured, per-report status varies)."
    )
    detector_version: str
    config_hash: str
    metrics_source: str
    limits: Limits
    report_count: int | None = None
    database_bytes: int | None = None
