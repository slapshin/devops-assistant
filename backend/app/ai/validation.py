"""Validate provider output against the report: references must resolve, text is bounded."""

from collections.abc import Iterable

from app.ai.prompt import clean
from app.domain.explanation import Explanation, ExplanationResult, ExplanationStatus

MAX_TEXT = 1200
MAX_SUMMARY = 2000


def validate(explanation: Explanation, known_ids: Iterable[str]) -> ExplanationResult:
    known = set(known_ids)
    notes: list[str] = []
    hypotheses = []
    for h in explanation.hypotheses:
        unknown = sorted(set(h.finding_ids) - known)
        if unknown:
            notes.append(f"Dropped a hypothesis citing unknown finding IDs {unknown}.")
            continue
        hypotheses.append(h.model_copy(update={"text": clean(h.text, MAX_TEXT)}))
    steps = []
    for s in explanation.investigation_steps:
        unknown = sorted(set(s.finding_ids) - known)
        if unknown:
            notes.append(f"Dropped an investigation step citing unknown finding IDs {unknown}.")
            continue
        steps.append(s.model_copy(update={"text": clean(s.text, MAX_TEXT)}))
    summary = clean(explanation.summary, MAX_SUMMARY).strip()
    offered = len(explanation.hypotheses) + len(explanation.investigation_steps)
    if not summary or (offered and not hypotheses and not steps):
        return ExplanationResult(
            status=ExplanationStatus.FAILED,
            reason="invalid_output",
            validation_notes=notes,
        )
    return ExplanationResult(
        status=ExplanationStatus.SUCCEEDED,
        explanation=explanation.model_copy(
            update={
                "summary": summary,
                "hypotheses": hypotheses,
                "investigation_steps": steps,
                "uncertainty": clean(explanation.uncertainty, MAX_TEXT),
            }
        ),
        validation_notes=notes,
    )
