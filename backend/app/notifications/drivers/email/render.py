"""Email content of a scheduled report: a subject, a plain-text and an HTML body (T025).

Formats a ``ReportMessage``; every value taken from a report is escaped in the HTML part:
finding titles and resource names come from source labels.
"""

from dataclasses import dataclass
from html import escape

from app.domain.common import Severity
from app.notifications.message import APP_NAME, NO_ANOMALIES, ReportMessage

MAX_SUBJECT_CHARS = 150
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


def render_email(message: ReportMessage) -> RenderedEmail:
    return RenderedEmail(subject=_subject(message), text=_text(message), html=_html(message))


def _subject(c: ReportMessage) -> str:
    subject = f"[{APP_NAME}] {c.project_name}: {c.outcome}"
    return subject if len(subject) <= MAX_SUBJECT_CHARS else subject[: MAX_SUBJECT_CHARS - 1] + "…"


def _text(c: ReportMessage) -> str:
    lines = [c.heading, f"Window: {c.window}", c.state, ""]
    if c.counts is not None:
        lines.append(f"Findings: {c.counts}")
        lines.extend(f"- [{f.severity.value}] {f.title} ({f.details})" for f in c.findings)
        if c.more_findings:
            lines.append(f"- and {c.more_findings} more")
        lines.append("")
    elif not c.failed:
        lines.extend([NO_ANOMALIES, ""])
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


def _html(c: ReportMessage) -> str:
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
            f'color:{SEVERITY_COLORS[f.severity]}">{escape(f.severity.value)}</td>'
            f'<td style="padding:4px 0">{escape(f.title)}<br>'
            f'<span style="{_MUTED};font-size:12px">{escape(f.details)}</span></td>'
            "</tr>"
            for f in c.findings
        )
        parts.append(f'<table style="border-collapse:collapse;margin:0 0 12px">{rows}</table>')
        if c.more_findings:
            parts.append(f'<p style="margin:0 0 12px;{_MUTED}">and {c.more_findings} more</p>')
    elif not c.failed:
        parts.append(f"<p {_P}>{NO_ANOMALIES}</p>")
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
