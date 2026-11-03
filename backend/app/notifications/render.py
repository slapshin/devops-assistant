"""Email content for a finished scheduled analysis: a subject, a plain-text and an HTML body.

Built from the job and the report summary only (never chart data). Every value taken from a
report is escaped in the HTML part: finding titles and resource names come from source labels.
A report without findings never reads as healthy when signals could not be evaluated.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
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
MAX_SUBJECT_CHARS = 150
UNEVALUATED = (SignalStatus.INSUFFICIENT_DATA, SignalStatus.SOURCE_ERROR)
SEVERITY_COLORS = {
    Severity.CRITICAL: "#b42318",
    Severity.HIGH: "#c4320a",
    Severity.MEDIUM: "#b54708",
    Severity.LOW: "#475467",
}


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class _Content:
    """Everything both bodies show, as plain strings."""

    heading: str
    window: str
    state: str
    failed: bool
    counts: str | None
    findings: list[tuple[Severity, str, str]]
    """(severity, title, details) of the top findings."""
    more_findings: int
    unevaluated: str | None
    summary: str | None
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


def render_email(
    project_name: str,
    job: AnalysisJob,
    summary: ReportSummary | None,
    *,
    timezone: str = "UTC",
    link: str | None = None,
) -> RenderedEmail:
    content = _content(project_name, job, summary, ZoneInfo(timezone), link)
    return RenderedEmail(
        subject=_subject(project_name, job, summary), text=_text(content), html=_html(content)
    )


def _subject(project_name: str, job: AnalysisJob, summary: ReportSummary | None) -> str:
    if job.state is JobState.FAILED or summary is None:
        outcome = "analysis failed"
    else:
        outcome = severity_counts(summary.findings) or "no anomalies found"
        if job.state is JobState.PARTIAL:
            outcome += " (partial)"
    name = " ".join(project_name.split())  # never a line break in a header
    subject = f"[{APP_NAME}] {name}: {outcome}"
    return subject if len(subject) <= MAX_SUBJECT_CHARS else subject[: MAX_SUBJECT_CHARS - 1] + "…"


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


def _content(
    project_name: str,
    job: AnalysisJob,
    summary: ReportSummary | None,
    zone: ZoneInfo,
    link: str | None,
) -> _Content:
    heading = f"{project_name}: scheduled analysis"
    if job.state is JobState.FAILED or summary is None:
        return _Content(
            heading=heading,
            window=f"24 hours up to {_local(job.end_time, zone)}",
            state=_state(job, None),
            failed=True,
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
    explanation = summary.explanation
    ai_summary = (
        explanation.explanation.summary
        if explanation.status is ExplanationStatus.SUCCEEDED and explanation.explanation
        else None
    )
    exclusions = [e.message for e in summary.exclusions]
    return _Content(
        heading=heading,
        window=f"{_local(latest.start, zone)} to {_local(latest.end, zone)}",
        state=_state(job, summary),
        failed=False,
        counts=severity_counts(summary.findings),
        findings=[(f.severity, f.title, _finding_details(f, zone)) for f in top],
        more_findings=len(summary.findings) - len(top),
        unevaluated=_unevaluated(summary),
        summary=ai_summary,
        exclusions=exclusions[:MAX_EXCLUSIONS],
        more_exclusions=max(0, len(exclusions) - MAX_EXCLUSIONS),
        link=link,
    )


def _text(c: _Content) -> str:
    lines = [c.heading, f"Window: {c.window}", c.state, ""]
    if c.counts is not None:
        lines.append(f"Findings: {c.counts}")
        lines.extend(f"- [{sev.value}] {title} ({details})" for sev, title, details in c.findings)
        if c.more_findings:
            lines.append(f"- and {c.more_findings} more")
        lines.append("")
    elif not c.failed:
        lines.extend(["No anomalies found in the evaluated signals.", ""])
    if c.unevaluated:
        lines.extend([c.unevaluated, ""])
    if c.summary:
        lines.extend(["AI summary (unverified):", c.summary, ""])
    if c.exclusions:
        lines.append("Not included:")
        lines.extend(f"- {message}" for message in c.exclusions)
        if c.more_exclusions:
            lines.append(f"- and {c.more_exclusions} more")
        lines.append("")
    if c.link:
        lines.append(f"Open the report: {c.link}")
    lines.extend(["", f"-- \nSent by {APP_NAME} for a scheduled analysis."])
    return "\n".join(lines).strip() + "\n"


_P = 'style="margin:0 0 12px"'
_MUTED = "color:#667085"


def _html(c: _Content) -> str:
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.45;'
        'color:#101828;max-width:640px">',
        f'<h2 style="font-size:18px;margin:0 0 4px">{escape(c.heading)}</h2>',
        f'<p style="margin:0 0 12px;{_MUTED}">{escape(c.window)}</p>',
        f"<p {_P}>{escape(c.state)}</p>",
    ]
    if c.counts is not None:
        parts.append(f"<p {_P}><strong>Findings:</strong> {escape(c.counts)}</p>")
        rows = "".join(
            "<tr>"
            f'<td style="padding:4px 8px 4px 0;vertical-align:top;font-weight:bold;'
            f'color:{SEVERITY_COLORS[sev]}">{escape(sev.value)}</td>'
            f'<td style="padding:4px 0">{escape(title)}<br>'
            f'<span style="{_MUTED};font-size:12px">{escape(details)}</span></td>'
            "</tr>"
            for sev, title, details in c.findings
        )
        parts.append(f'<table style="border-collapse:collapse;margin:0 0 12px">{rows}</table>')
        if c.more_findings:
            parts.append(f'<p style="margin:0 0 12px;{_MUTED}">and {c.more_findings} more</p>')
    elif not c.failed:
        parts.append(f"<p {_P}>No anomalies found in the evaluated signals.</p>")
    if c.unevaluated:
        parts.append(f"<p {_P}>{escape(c.unevaluated)}</p>")
    if c.summary:
        parts.append(
            f"<p {_P}><strong>AI summary (unverified):</strong><br>{escape(c.summary)}</p>"
        )
    if c.exclusions:
        items = "".join(f"<li>{escape(m)}</li>" for m in c.exclusions)
        if c.more_exclusions:
            items += f"<li>and {c.more_exclusions} more</li>"
        parts.append(f"<p style='margin:0'><strong>Not included:</strong></p><ul>{items}</ul>")
    if c.link:
        parts.append(f'<p {_P}><a href="{escape(c.link, quote=True)}">Open the report</a></p>')
    parts.append(
        f'<p style="margin:16px 0 0;font-size:12px;{_MUTED}">'
        f"Sent by {APP_NAME} for a scheduled analysis.</p></div>"
    )
    return "".join(parts)
