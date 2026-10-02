"""OpenAI Responses API adapter. The only module that imports the OpenAI SDK."""

import logging
from datetime import UTC, datetime
from enum import StrEnum

import httpx2
import openai
from openai.types.responses import ParsedResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from app.ai.prompt import INSTRUCTIONS, render
from app.domain.explanation import (
    Explanation,
    ExplanationInput,
    Hypothesis,
    InvestigationStep,
    Likelihood,
)
from app.domain.interfaces import ExplanationError

log = logging.getLogger("app.ai")

MAX_OUTPUT_TOKENS = 2000
DEFAULT_TIMEOUT_SECONDS = 60.0
PROVIDER_NAME = "openai"
# The domain models reject empty text and empty citations. Placeholders keep an otherwise
# well-formed answer parseable; validation then drops anything citing EMPTY_CITATION.
EMPTY_TEXT_PLACEHOLDER = " "
EMPTY_CITATION = "<none>"


class _Likelihood(StrEnum):
    PLAUSIBLE = "plausible"
    POSSIBLE = "possible"
    SPECULATIVE = "speculative"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Hypothesis(_Strict):
    text: str
    finding_ids: list[str]
    likelihood: _Likelihood


class _Step(_Strict):
    text: str
    finding_ids: list[str]


class _Output(_Strict):
    """Structured-output schema sent to the model (all fields required, no defaults)."""

    summary: str
    hypotheses: list[_Hypothesis]
    investigation_steps: list[_Step]
    uncertainty: str


class OpenAIExplanationProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 1,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
            http_client=http_client,
        )

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def model(self) -> str:
        return self._model

    async def explain(self, explanation_input: ExplanationInput) -> Explanation:
        payload = render(explanation_input)
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=INSTRUCTIONS,
                input=payload,
                text_format=_Output,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except openai.APITimeoutError:
            raise ExplanationError("timeout") from None
        except openai.RateLimitError:
            raise ExplanationError("rate_limited") from None
        except openai.AuthenticationError:
            raise ExplanationError("authentication_failed") from None
        except openai.PermissionDeniedError:
            raise ExplanationError("permission_denied") from None
        except openai.NotFoundError:
            raise ExplanationError("model_not_found") from None
        except openai.APIConnectionError:
            raise ExplanationError("unavailable") from None
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as exc:
            raise ExplanationError(f"incomplete: {type(exc).__name__}") from None
        except openai.APIStatusError as exc:
            raise ExplanationError(f"provider_error_{exc.status_code}") from None
        except (ValidationError, ValueError) as exc:
            log.info("invalid structured output: %s", exc)
            raise ExplanationError("invalid_output") from None

        _raise_if_unusable(response)
        parsed = response.output_parsed
        if parsed is None:
            raise ExplanationError("invalid_output")

        return self._to_explanation(parsed, response.model or self._model)

    def _to_explanation(self, parsed: _Output, model: str) -> Explanation:
        return Explanation(
            summary=parsed.summary or EMPTY_TEXT_PLACEHOLDER,
            hypotheses=[
                Hypothesis(
                    text=h.text or EMPTY_TEXT_PLACEHOLDER,
                    finding_ids=h.finding_ids or [EMPTY_CITATION],
                    likelihood=Likelihood(h.likelihood.value),
                )
                for h in parsed.hypotheses
            ],
            investigation_steps=[
                InvestigationStep(text=s.text or EMPTY_TEXT_PLACEHOLDER, finding_ids=s.finding_ids)
                for s in parsed.investigation_steps
            ],
            uncertainty=parsed.uncertainty,
            provider=self.name,
            model=model,
            generated_at=datetime.now(UTC),
        )


def _raise_if_unusable(response: ParsedResponse[_Output]) -> None:
    """Truncated answers and refusals are failures even when they parse."""
    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None) or "unknown"
        raise ExplanationError(f"incomplete: {reason}")
    for item in response.output:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                raise ExplanationError("refusal")
