"""RFC 9457 problem responses."""

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.common import SourceKind
from app.domain.jobs import ErrorCode, Problem

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: ErrorCode,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.problem = Problem(status=status, code=code, title=title, detail=detail)
        self.headers = headers


def problem_response(problem: Problem, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=problem.status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI documentation for problem responses."""
    return {
        s: {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}, "description": "Problem"}
        for s in statuses
    }


_UNION_TAGS = {kind.value for kind in SourceKind}


def field_path(loc: Sequence[int | str]) -> str:
    """Dotted path of an error; source union tags (``sources.0.prometheus.url``) are dropped."""
    parts = [str(p) for i, p in enumerate(loc) if not (i and p in _UNION_TAGS)]
    return ".".join(parts)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(exc.problem, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": field_path(e["loc"]), "message": e["msg"]} for e in exc.errors()]
        problem = Problem(
            status=422,
            code=ErrorCode.VALIDATION_ERROR,
            title="Request validation failed",
            errors=errors,
        )
        return problem_response(problem)
