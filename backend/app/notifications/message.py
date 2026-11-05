"""The channel-neutral content of a scheduled report notification (T026).

Built once per finished job from the job and its report summary (never chart data); every
channel formats the same ``ReportMessage``. The content rules live here and only here: a report
without findings never reads as healthy when signals could not be evaluated, partial and failed
analyses say so, the AI summary is marked unverified, and at most ``TOP_FINDINGS`` findings are
listed. Values are plain text; each channel escapes them for its format.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.common import Severity
from app.domain.explanation import ExplanationStatus
from app.domain.findings import Finding, SignalStatus
from app.domain.jobs import AnalysisJob, JobState
from app.domain.notifications import SEVERITY_ORDER
from app.domain.report import ReportSummary

APP_NAME = "DevOps Assistant"
TOP_FINDINGS = 10
MAX_EXCLUSIONS = 10
UNEVALUATED = (SignalStatus.INSUFFICIENT_DATA, SignalStatus.SOURCE_ERROR)
NO_ANOMALIES = "No anomalies found in the evaluated signals."


@dataclass(frozen=True)
class MessageFinding:
    severity: Severity
    title: str
    details: str
    """Resource, local time span, and confidence."""


@dataclass(frozen=True)
class ReportMessage:
    """Everything a notification shows, as plain strings."""

    project_name: str
    """Single-line (whitespace collapsed)."""
    outcome: str
    """'2 critical, 1 high (partial)', 'no anomalies found' or 'analysis failed'."""
    heading: str
    window: str
    state: str
    failed: bool
    partial: bool
    max_severity: Severity | None
    """Most severe finding; None without findings."""
    counts: str | None
    findings: list[MessageFinding]
    """The most severe findings, at most TOP_FINDINGS."""
    more_findings: int
    unevaluated: str | None
    summary: str | None
    """The AI summary when the explanation succeeded; always shown as unverified."""
    exclusions: list[str]
    more_exclusions: int
    link: str | None


def report_link(public_url: str | None, analysis_id: str) -> str | None:
    return f"{public_url}/reports/{analysis_id}" if public_url else None


def ranked(findings: list[Finding]) -> list[Finding]:
    """Most severe first, then by severity points."""
    return sorted(
        findings, key=lambda f: (SEVERITY_ORDER.index(f.severity), f.severity_points), reverse=True
    )


def severity_counts(findings: list[Finding]) -> str | None:
    counts = Counter(f.severity for f in findings)
    parts = [f"{counts[s]} {s.value}" for s in reversed(SEVERITY_ORDER) if counts[s]]
    return ", ".join(parts) or None


def _local(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime("%H:%M")


def _state(job: AnalysisJob, summary: ReportSummary | None) -> str:
    if job.state is JobState.FAILED or summary is None:
        reason = job.error.message if job.error else "unknown error"
        return f"Analysis failed: {reason}"
    if job.state is JobState.PARTIAL:
        return "Partial: some data could not be collected (listed below)."
    return "Completed."


def _finding_details(finding: Finding, zone: ZoneInfo) -> str:
    span = f"{_clock(finding.start, zone)}-{_clock(finding.end, zone)}"
    return f"{finding.entity.display_name} · {span} · confidence {finding.confidence.value}"


def _unevaluated(summary: ReportSummary) -> str | None:
    families = sorted({c.family.value for c in summary.coverage if c.status in UNEVALUATED})
    if not families:
        return None
    return (
        f"Not evaluated (insufficient data or source errors): {', '.join(families)}. "
        "No anomaly there does not mean healthy."
    )


def build_message(
    project_name: str,
    job: AnalysisJob,
    summary: ReportSummary | None,
    *,
    timezone: str = "UTC",
    link: str | None = None,
) -> ReportMessage:
    zone = ZoneInfo(timezone)
    name = " ".join(project_name.split())
    heading = f"{project_name}: scheduled analysis"
    if job.state is JobState.FAILED or summary is None:
        return ReportMessage(
            project_name=name,
            outcome="analysis failed",
            heading=heading,
            window=f"24 hours up to {_local(job.end_time, zone)}",
            state=_state(job, None),
            failed=True,
            partial=False,
            max_severity=None,
            counts=None,
            findings=[],
            more_findings=0,
            unevaluated=None,
            summary=None,
            exclusions=[],
            more_exclusions=0,
            link=link,
        )

    latest = summary.windows.latest_day
    top = ranked(summary.findings)[:TOP_FINDINGS]
    partial = job.state is JobState.PARTIAL
    counts = severity_counts(summary.findings)
    outcome = (counts or "no anomalies found") + (" (partial)" if partial else "")
    explanation = summary.explanation
    ai_summary = (
        explanation.explanation.summary
        if explanation.status is ExplanationStatus.SUCCEEDED and explanation.explanation
        else None
    )
    exclusions = [e.message for e in summary.exclusions]
    return ReportMessage(
        project_name=name,
        outcome=outcome,
        heading=heading,
        window=f"{_local(latest.start, zone)} to {_local(latest.end, zone)}",
        state=_state(job, summary),
        failed=False,
        partial=partial,
        max_severity=top[0].severity if top else None,
        counts=counts,
        findings=[MessageFinding(f.severity, f.title, _finding_details(f, zone)) for f in top],
        more_findings=len(summary.findings) - len(top),
        unevaluated=_unevaluated(summary),
        summary=ai_summary,
        exclusions=exclusions[:MAX_EXCLUSIONS],
        more_exclusions=max(0, len(exclusions) - MAX_EXCLUSIONS),
        link=link,
    )
