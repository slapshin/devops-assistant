"""Provider selection, the fake provider, and the fault-tolerant explanation step."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.ai.openai_adapter import OpenAIExplanationProvider
from app.ai.prompt import build_input
from app.ai.validation import validate
from app.domain.common import Scope, TimeRange
from app.domain.explanation import (
    Explanation,
    ExplanationInput,
    ExplanationResult,
    ExplanationStatus,
    Hypothesis,
    InvestigationStep,
    Likelihood,
)
from app.domain.findings import Finding, SignalCoverage
from app.domain.interfaces import ExplanationError, ExplanationProvider
from app.settings import AIProvider, Settings

log = logging.getLogger("app.ai")

OVERALL_TIMEOUT_SECONDS = 150.0
FAKE_MODEL = "deterministic-template"
FAKE_CITED_FINDINGS = 3
FAKE_INVENTED_FINDING_ID = "fnd_ffffffffffffffff"


class FakeExplanationProvider:
    """Deterministic provider for tests and offline demos; cites the most severe findings."""

    def __init__(self, fail_with: str | None = None, invent_ids: bool = False) -> None:
        self.fail_with = fail_with
        self.invent_ids = invent_ids
        self.calls: list[ExplanationInput] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return FAKE_MODEL

    async def explain(self, explanation_input: ExplanationInput) -> Explanation:
        self.calls.append(explanation_input)
        if self.fail_with:
            raise ExplanationError(self.fail_with)

        top = explanation_input.findings[:FAKE_CITED_FINDINGS]
        hypotheses = [
            Hypothesis(
                text=f"{f.title} on {f.entity.display_name} may reflect a workload or "
                "configuration change around its start time.",
                finding_ids=[f.finding_id],
                likelihood=Likelihood.POSSIBLE,
            )
            for f in top
        ]
        if self.invent_ids:
            hypotheses.append(
                Hypothesis(
                    text="Invented.",
                    finding_ids=[FAKE_INVENTED_FINDING_ID],
                    likelihood=Likelihood.PLAUSIBLE,
                )
            )

        return Explanation(
            summary=_fake_summary(explanation_input),
            hypotheses=hypotheses,
            investigation_steps=[
                InvestigationStep(
                    text=f"Open the evidence chart for {f.title} and compare "
                    "with deploy and log history.",
                    finding_ids=[f.finding_id],
                )
                for f in top
            ],
            uncertainty="Template explanation; causes are not inferred from data.",
            provider=self.name,
            model=self.model,
            generated_at=datetime.now(UTC),
        )


def _fake_summary(explanation_input: ExplanationInput) -> str:
    if not explanation_input.findings:
        return "No findings."
    most_severe = explanation_input.findings[0]
    return (
        f"{len(explanation_input.findings)} finding(s) in the latest day; "
        f"most severe: {most_severe.title} on {most_severe.entity.display_name}."
    )


def provider_from_settings(
    settings: Settings,
) -> tuple[ExplanationProvider | None, ExplanationStatus, str | None]:
    """Return (provider, status when no provider, reason)."""
    if settings.ai_provider is AIProvider.NONE:
        return None, ExplanationStatus.DISABLED, "AI_PROVIDER=none"
    if settings.ai_provider is AIProvider.FAKE:
        return FakeExplanationProvider(), ExplanationStatus.PENDING, None
    if settings.openai_api_key is None or not settings.openai_model:
        return None, ExplanationStatus.NOT_CONFIGURED, settings.explanation_hint

    return (
        OpenAIExplanationProvider(
            settings.openai_api_key.get_secret_value(),
            settings.openai_model,
            base_url=settings.openai_base_url,
        ),
        ExplanationStatus.PENDING,
        None,
    )


async def explain_findings(
    provider: ExplanationProvider | None,
    unavailable: ExplanationStatus,
    unavailable_reason: str | None,
    scope: Scope,
    latest_day: TimeRange,
    findings: Sequence[Finding],
    coverage: Sequence[SignalCoverage],
) -> ExplanationResult:
    """Never raises: every provider problem becomes an explanation status."""
    if provider is None:
        return ExplanationResult(status=unavailable, reason=unavailable_reason)
    if not findings:
        return ExplanationResult(status=ExplanationStatus.SKIPPED_NO_FINDINGS)

    payload, notes = build_input(scope, latest_day, findings, coverage)
    try:
        explanation = await asyncio.wait_for(provider.explain(payload), OVERALL_TIMEOUT_SECONDS)
    except TimeoutError:
        return ExplanationResult(
            status=ExplanationStatus.FAILED, reason="timeout", validation_notes=notes
        )
    except ExplanationError as exc:
        log.warning("explanation failed: %s", exc.reason)
        return ExplanationResult(
            status=ExplanationStatus.FAILED, reason=exc.reason, validation_notes=notes
        )
    except Exception as exc:  # provider bugs must not break numerical reporting
        log.exception("explanation provider error")
        return ExplanationResult(
            status=ExplanationStatus.FAILED,
            reason=f"provider_error: {type(exc).__name__}",
            validation_notes=notes,
        )

    result = validate(explanation, [d.finding_id for d in payload.findings])
    return result.model_copy(update={"validation_notes": notes + result.validation_notes})
