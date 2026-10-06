"""Bounded, read-only client for the Sentry REST API.

Every call has a timeout, a shared concurrency limit, a response-size cap, and at most one
retry for rate limiting or transient failures. Error messages never contain the auth token.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain.common import STEP_SECONDS, format_utc
from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.projects import SentryConnection
from app.sources.base import ClientLimits
from app.sources.sentry.api import Chunk, ProjectInfo
from app.sources.sentry.catalog import (
    BY_SIGNAL,
    SPANS_DATASET,
    Query,
    project_path,
    query_spec,
    request_text,
    timeseries_params,
    timeseries_path,
)

log = logging.getLogger("app.sources.sentry")

MAX_ERROR_MESSAGE_CHARS = 500
MAX_RETRY_AFTER_SECONDS = 30.0
RETRY_BACKOFF_SECONDS = 1.0
MS_PER_SECOND = 1000.0
EPOCH_MS_THRESHOLD = 10**11
"""Timestamps above this are milliseconds (documented), below it seconds (older versions)."""
ACCEPTED_INTERVALS = (STEP_SECONDS, STEP_SECONDS * 1000)
UNAUTHORIZED_STATUS = 401
FORBIDDEN_STATUS = 403
NOT_FOUND_STATUS = 404
RATE_LIMITED_STATUS = 429
MIN_REDIRECT_STATUS = 300
MIN_CLIENT_ERROR_STATUS = 400
MIN_SERVER_ERROR_STATUS = 500


def _utc(ts: int) -> str:
    return format_utc(datetime.fromtimestamp(ts, UTC))


def _bucket(raw: Any) -> int:
    ts = float(raw)
    if ts > EPOCH_MS_THRESHOLD:
        ts /= MS_PER_SECOND
    return int(ts) // STEP_SECONDS * STEP_SECONDS


def _detail(body: bytes) -> str:
    try:
        payload = httpx.Response(200, content=body).json()
    except ValueError:
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail)[:MAX_ERROR_MESSAGE_CHARS] if detail else ""


class SentryClient:
    """``SentryApi`` over the REST API for one project."""

    def __init__(
        self,
        conn: SentryConnection,
        *,
        limits: ClientLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.conn = conn
        self.limits = limits or ClientLimits()
        headers = {"Accept": "application/json"}
        if conn.auth_token is not None:
            headers["Authorization"] = f"Bearer {conn.auth_token.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=conn.api_url,
            headers=headers,
            timeout=httpx.Timeout(self.limits.timeout_seconds),
            transport=transport,
            follow_redirects=False,
            verify=conn.tls_verify,
        )
        self._sem = asyncio.Semaphore(self.limits.concurrency)
        self.request_count = 0
        self.transactions_dataset = SPANS_DATASET

    @property
    def base_url(self) -> str:
        return self.conn.api_url

    @property
    def backend(self) -> str | None:
        return "sentry"

    def begin(self, end_time: datetime) -> None:
        pass

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- transport ------------------------------------------------------------------------

    async def get(self, path: str, params: list[tuple[str, str]] | None = None) -> Any:
        """The decoded JSON of a GET; raises SourceError on any error."""
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    self.request_count += 1
                    return await self._get_once(path, params or [])
            except _Retry as retry:
                if attempt > self.limits.retries:
                    raise retry.error from None
                delay = retry.after if retry.after is not None else RETRY_BACKOFF_SECONDS * attempt
                log.warning("retrying Sentry request in %.1fs: %s", delay, retry.error.message)
                await asyncio.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))

    async def _get_once(self, path: str, params: list[tuple[str, str]]) -> Any:
        started = time.monotonic()
        status, headers, body = await self._get_bounded(path, params)
        log.debug("GET %s %s %.2fs %dB", path, status, time.monotonic() - started, len(body))

        detail = _detail(body) if status >= MIN_CLIENT_ERROR_STATUS else ""
        suffix = f": {detail}" if detail else ""
        if status == UNAUTHORIZED_STATUS:
            raise SourceError(
                SourceErrorKind.AUTH, f"Sentry rejected the auth token (HTTP 401){suffix}"
            )
        if status == FORBIDDEN_STATUS:
            raise SourceError(
                SourceErrorKind.AUTH,
                f"the auth token lacks a scope (needs org:read and project:read; HTTP 403){suffix}",
            )
        if status == RATE_LIMITED_STATUS:
            raise _Retry(
                SourceError(SourceErrorKind.UNAVAILABLE, "Sentry rate limit reached (HTTP 429)"),
                _retry_after(headers.get("retry-after")),
            )
        if status >= MIN_SERVER_ERROR_STATUS:
            raise _Retry(SourceError(SourceErrorKind.SERVER_ERROR, f"HTTP {status}"), None)
        if status == NOT_FOUND_STATUS:
            raise SourceError(SourceErrorKind.BAD_QUERY, f"not found (HTTP 404){suffix}")
        if MIN_REDIRECT_STATUS <= status < MIN_CLIENT_ERROR_STATUS:
            location = headers.get("location", "")
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"unexpected redirect (HTTP {status}) to {location[:200]}; "
                "check the Sentry URL (EU organizations use https://de.sentry.io)",
            )
        if status >= MIN_CLIENT_ERROR_STATUS:
            raise SourceError(SourceErrorKind.BAD_QUERY, f"HTTP {status}{suffix}")
        try:
            return httpx.Response(status, content=body).json()
        except ValueError:
            raise SourceError(
                SourceErrorKind.BAD_QUERY, f"unexpected response (HTTP {status})"
            ) from None

    async def _get_bounded(
        self, path: str, params: list[tuple[str, str]]
    ) -> tuple[int, httpx.Headers, bytes]:
        try:
            async with self._http.stream("GET", path, params=tuple(params)) as res:
                chunks: list[bytes] = []
                size = 0
                async for chunk in res.aiter_bytes():
                    size += len(chunk)
                    if size > self.limits.max_response_bytes:
                        raise SourceError(
                            SourceErrorKind.TOO_LARGE,
                            f"response exceeded {self.limits.max_response_bytes} bytes",
                        )
                    chunks.append(chunk)
                return res.status_code, res.headers, b"".join(chunks)
        except httpx.TimeoutException:
            raise SourceError(
                SourceErrorKind.TIMEOUT, f"no response within {self.limits.timeout_seconds:.0f} s"
            ) from None
        except httpx.TransportError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise SourceError(
                    SourceErrorKind.UNAVAILABLE,
                    "the TLS certificate of Sentry is not trusted (self-signed or internal CA); "
                    "turn off TLS verification for this source if that is expected",
                ) from None
            raise SourceError(SourceErrorKind.UNAVAILABLE, f"cannot reach Sentry: {exc}") from None

    # --- SentryApi ------------------------------------------------------------------------

    async def _project(self, slug: str) -> ProjectInfo:
        try:
            raw = await self.get(project_path(self.conn.organization, slug))
        except SourceError as exc:
            if exc.kind is SourceErrorKind.BAD_QUERY and "404" in exc.message:
                raise SourceError(
                    SourceErrorKind.AUTH,
                    f"project {self.conn.organization}/{slug} not found, "
                    "or the token cannot read it",
                ) from None
            raise
        if not isinstance(raw, dict) or not str(raw.get("id") or "").isdigit():
            raise SourceError(SourceErrorKind.BAD_QUERY, "unexpected project response")
        created = raw.get("dateCreated")
        return ProjectInfo(
            id=str(raw["id"]),
            slug=str(raw.get("slug") or slug),
            name=str(raw.get("name") or slug),
            created=(
                datetime.fromisoformat(str(created).replace("Z", "+00:00")) if created else None
            ),
        )

    async def projects(self) -> list[ProjectInfo]:
        return list(await asyncio.gather(*(self._project(s) for s in self.conn.projects)))

    async def series(self, query: Query, project_ids: Sequence[str], start: int, end: int) -> Chunk:
        spec = query_spec(query, self.transactions_dataset)
        tags = [(t.key, t.value) for t in self.conn.tags]
        args = (spec, project_ids, self.conn.environment, tags, _utc(start), _utc(end))
        raw = await self.get(timeseries_path(self.conn.organization), timeseries_params(*args))
        axes = _axes(raw)
        chunk = Chunk(query=request_text(spec, self.conn.organization, *args[1:]))
        signals = [s for s in BY_SIGNAL.values() if s.query is query]
        if query is not Query.TRANSACTIONS:
            for defn in signals:
                values = axes.get(spec.y_axes[defn.axis], {})
                chunk.values[defn.signal] = {b: v for b, v in values.items() if v is not None}
            return chunk
        _transactions(chunk, [axes.get(axis, {}) for axis in spec.y_axes])
        return chunk


def _axes(raw: Any) -> dict[str, dict[int, float | None]]:
    """yAxis -> bucket -> value of an ``events-timeseries`` response (ungrouped series).

    Sentry 25.x names the list ``timeseries``; current documentation shows ``timeSeries``.
    """
    found = raw.get("timeSeries", raw.get("timeseries")) if isinstance(raw, dict) else None
    if not isinstance(found, list):
        raise SourceError(SourceErrorKind.BAD_QUERY, "unexpected events-timeseries response")
    out: dict[str, dict[int, float | None]] = {}
    for series in found:
        if not isinstance(series, dict) or series.get("groupBy"):
            continue
        interval = (series.get("meta") or {}).get("interval")
        if interval is not None and int(interval) not in ACCEPTED_INTERVALS:
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"Sentry answered with {interval} buckets instead of 5 minutes",
            )
        values = out.setdefault(str(series.get("yAxis")), {})
        for point in series.get("values") or []:
            value = point.get("value")
            values[_bucket(point["timestamp"])] = None if value is None else float(value)
    return out


def _transactions(chunk: Chunk, axes: list[dict[int, float | None]]) -> None:
    """Counts as they are; failures from the failure rate; durations (ms) only with traffic."""
    count, failure_rate, p95, p99 = axes
    counts = {b: v for b, v in count.items() if v is not None}
    chunk.values["sentry_transactions"] = counts
    chunk.values["sentry_transaction_failures"] = {
        b: round(n * (failure_rate.get(b) or 0.0), 3) for b, n in counts.items()
    }
    for values, signal in ((p95, "sentry_duration_p95"), (p99, "sentry_duration_p99")):
        chunk.values[signal] = {
            b: ms / MS_PER_SECOND for b, ms in values.items() if ms is not None and counts.get(b)
        }


class _Retry(Exception):
    def __init__(self, error: SourceError, after: float | None) -> None:
        super().__init__(error.message)
        self.error = error
        self.after = after


def _retry_after(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None
