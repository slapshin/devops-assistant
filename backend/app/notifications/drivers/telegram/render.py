"""Telegram content of a scheduled report: one message in HTML parse mode (T027).

Only tags Telegram supports are used (``b``, ``i``, ``a``, ``blockquote expandable``); every
value is escaped. A message may hold 4096 UTF-16 code units after entity parsing; the HTML
source is measured instead (always longer), and when it does not fit, findings are dropped
from the end ("and N more"), then the AI summary, then exclusions. The status line, the
"not evaluated" note and the link are never dropped.
"""

from dataclasses import dataclass, replace
from html import escape

from app.domain.common import Severity
from app.notifications.message import APP_NAME, NO_ANOMALIES, MessageFinding, ReportMessage

MAX_MESSAGE_UNITS = 4096
MAX_STATE_CHARS = 500
SEVERITY_MARKERS = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
}
FAILED_MARKER = "❗"
UNCLEAR_MARKER = "⚠️"
"""No findings, but partial or with unevaluated signals: never shown as healthy."""
HEALTHY_MARKER = "✅"
TEST_TEXT = (
    f"<b>{APP_NAME}</b>: test message.\n"
    "Scheduled reports of projects that use this chat will be posted the same way."
)


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _e(text: str) -> str:
    return escape(text, quote=False)


def marker(m: ReportMessage) -> str:
    if m.failed:
        return FAILED_MARKER
    if m.max_severity is not None:
        return SEVERITY_MARKERS[m.max_severity]
    if m.partial or m.unevaluated:
        return UNCLEAR_MARKER
    return HEALTHY_MARKER


@dataclass(frozen=True)
class _Parts:
    findings: list[MessageFinding]
    more_findings: int
    summary: str | None
    exclusions: list[str]
    more_exclusions: int


def _compose(m: ReportMessage, p: _Parts) -> str:
    blocks = [
        f"{marker(m)} <b>{_e(m.project_name)}</b>: {_e(m.outcome)}\n<i>{_e(m.window)}</i>",
    ]
    if m.failed or m.partial:
        state = m.state if len(m.state) <= MAX_STATE_CHARS else m.state[:MAX_STATE_CHARS] + "…"
        blocks.append(_e(state))
    if p.findings:
        lines = [
            f"{SEVERITY_MARKERS[f.severity]} <b>{_e(f.severity.value)}</b> {_e(f.title)}\n"
            f"      {_e(f.details)}"
            for f in p.findings
        ]
        if p.more_findings:
            lines.append(f"and {p.more_findings} more")
        blocks.append("\n".join(lines))
    elif not m.failed and m.counts is None:
        blocks.append(_e(NO_ANOMALIES))
    if m.unevaluated:
        blocks.append(f"{UNCLEAR_MARKER} {_e(m.unevaluated)}")
    if p.summary:
        blocks.append(
            f"<blockquote expandable><b>AI summary (unverified)</b>\n{_e(p.summary)}</blockquote>"
        )
    if p.exclusions or p.more_exclusions:
        lines = ["<b>Not included:</b>", *(f"• {_e(x)}" for x in p.exclusions)]
        if p.more_exclusions:
            lines.append(f"• and {p.more_exclusions} more")
        blocks.append("\n".join(lines))
    if m.link:
        blocks.append(f'<a href="{escape(m.link, quote=True)}">Open the report</a>')
    return "\n\n".join(blocks)


def _shrink(p: _Parts) -> _Parts | None:
    """The next smaller variant; None when nothing optional is left."""
    if p.findings:
        return replace(p, findings=p.findings[:-1], more_findings=p.more_findings + 1)
    if p.summary:
        return replace(p, summary=None)
    if p.exclusions:
        return replace(p, exclusions=p.exclusions[:-1], more_exclusions=p.more_exclusions + 1)
    return None


def render_telegram(m: ReportMessage) -> str:
    parts: _Parts | None = _Parts(
        findings=list(m.findings),
        more_findings=m.more_findings,
        summary=m.summary,
        exclusions=list(m.exclusions),
        more_exclusions=m.more_exclusions,
    )
    text = ""
    while parts is not None:
        text = _compose(m, parts)
        if utf16_units(text) <= MAX_MESSAGE_UNITS:
            return text
        parts = _shrink(parts)
    # Only mandatory parts left and still too long (a pathological project name or note):
    # a plain fallback that always fits.
    fallback = f"{marker(m)} {m.project_name}: {m.outcome}"[:200]
    link = f'\n\n<a href="{escape(m.link, quote=True)}">Open the report</a>' if m.link else ""
    return _e(fallback) + link
