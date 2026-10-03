"""Manual OpenAI configuration check (never run by automated tests).

Usage:
  uv run python -m scripts.check_openai               # key + model availability (no tokens)
  uv run python -m scripts.check_openai --structured  # one tiny paid structured-output call
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from app.ai.openai_adapter import OpenAIExplanationProvider
from app.domain.common import LabelMatcher, Scope, TimeRange
from app.domain.explanation import ExplanationInput
from app.domain.interfaces import ExplanationError
from app.settings import ConfigError, load_settings

CONFIG_ERROR_EXIT_CODE = 2
CHECK_FAILED_EXIT_CODE = 1
SUMMARY_PREVIEW_CHARS = 120


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structured", action="store_true", help="make one paid test call")
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE
    if settings.openai_api_key is None or not settings.openai_model:
        print("OPENAI_API_KEY and OPENAI_MODEL must be set.", file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE

    provider = OpenAIExplanationProvider(
        settings.openai_api_key.get_secret_value(),
        settings.openai_model,
        base_url=settings.openai_base_url,
    )
    try:
        model = await provider._client.models.retrieve(settings.openai_model)
    except Exception as exc:
        print(
            f"Model check failed for {settings.openai_model}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return CHECK_FAILED_EXIT_CODE
    print(f"Model available: {model.id}")
    if not args.structured:
        return 0

    now = datetime.now(UTC)
    payload = ExplanationInput(
        scope=Scope(
            project_id="check",
            project_name="check",
            matchers=[LabelMatcher(name="project", value="check")],
        ),
        latest_day=TimeRange(start=now - timedelta(days=1), end=now),
        coverage_summary="synthetic connectivity check",
        findings=[],
    )
    try:
        result = await provider.explain(payload)
    except ExplanationError as exc:
        print(f"Structured call to {settings.openai_model} failed: {exc.reason}", file=sys.stderr)
        return CHECK_FAILED_EXIT_CODE
    print(f"Structured output OK from {result.model}: {result.summary[:SUMMARY_PREVIEW_CHARS]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
