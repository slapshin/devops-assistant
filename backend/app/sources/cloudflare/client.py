"""Bounded, read-only client for the Cloudflare GraphQL Analytics API.

Every call has a timeout, a shared concurrency limit, a response-size cap, and at most one
retry for rate limiting or transient failures. Error messages never contain the API token.
"""

import asyncio
import logging
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.domain.common import format_utc
from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.projects import CloudflareConnection
from app.metrics.client import ClientLimits
from app.sources.cloudflare.api import (
    DATASETS,
    DEFAULT_MAX_DURATION,
    DEFAULT_NOT_OLDER_THAN,
    DEFAULT_PAGE_SIZE,
    Chunk,
    DatasetSettings,
)
from app.sources.cloudflare.catalog import (
    TIMING_FIELDS,
    TRAFFIC_ALIASES,
    action_signal,
    daily_document,
    security_document,
    settings_document,
    timing_document,
    traffic_document,
)

log = logging.getLogger("app.sources.cloudflare")

MAX_ERROR_MESSAGE_CHARS = 500
MAX_RETRY_AFTER_SECONDS = 30.0
RETRY_BACKOFF_SECONDS = 1.0
MS_PER_SECOND = 1000.0
AUTH_FAILURE_STATUSES = (401, 403)
RATE_LIMITED_STATUS = 429
MIN_SERVER_ERROR_STATUS = 500
MIN_CLIENT_ERROR_STATUS = 400
_AUTH_HINTS = ("authentication", "authorization", "not authorized", "unauthorized", "permission")
_RATE_HINTS = ("rate limit", "too many requests")


def _epoch(raw: str) -> int:
    return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())


def _utc(ts: int) -> str:
    return format_utc(datetime.fromtimestamp(ts, UTC))


def _graphql_error(errors: list[Any]) -> SourceError:
    messages = [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors]
    message = "; ".join(messages)[:MAX_ERROR_MESSAGE_CHARS] or "GraphQL error"
    lowered = message.lower()
    if any(h in lowered for h in _AUTH_HINTS):
        return SourceError(SourceErrorKind.AUTH, message)
    if any(h in lowered for h in _RATE_HINTS):
        return SourceError(SourceErrorKind.UNAVAILABLE, message)
    return SourceError(SourceErrorKind.BAD_QUERY, message)


class CloudflareClient:
    """``CloudflareApi`` over GraphQL for one zone."""

    def __init__(
        self,
        conn: CloudflareConnection,
        *,
        limits: ClientLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.conn = conn
        self.limits = limits or ClientLimits()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if conn.api_token is not None:
            headers["Authorization"] = f"Bearer {conn.api_token.get_secret_value()}"
        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.limits.timeout_seconds),
            transport=transport,
            follow_redirects=False,
        )
        self._sem = asyncio.Semaphore(self.limits.concurrency)
        self.request_count = 0

    @property
    def base_url(self) -> str:
        return self.conn.api_url

    @property
    def backend(self) -> str | None:
        return "cloudflare"

    def begin(self, end_time: datetime) -> None:
        pass

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- transport ------------------------------------------------------------------------

    async def query(self, document: str) -> dict[str, Any]:
        """The ``data`` of a GraphQL response; raises SourceError on any error."""
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    self.request_count += 1
                    return await self._query_once(document)
            except _Retry as retry:
                if attempt > self.limits.retries:
                    raise retry.error from None
                delay = retry.after if retry.after is not None else RETRY_BACKOFF_SECONDS * attempt
                log.warning("retrying Cloudflare query in %.1fs: %s", delay, retry.error.message)
                await asyncio.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))

    async def _query_once(self, document: str) -> dict[str, Any]:
        started = time.monotonic()
        status, headers, body = await self._post_bounded(document)
        log.debug(
            "POST %s %s %.2fs %dB", self.base_url, status, time.monotonic() - started, len(body)
        )

        if status in AUTH_FAILURE_STATUSES:
            raise SourceError(
                SourceErrorKind.AUTH, f"Cloudflare rejected the API token (HTTP {status})"
            )
        if status == RATE_LIMITED_STATUS:
            raise _Retry(
                SourceError(
                    SourceErrorKind.UNAVAILABLE, "Cloudflare rate limit reached (HTTP 429)"
                ),
                _retry_after(headers.get("retry-after")),
            )
        if status >= MIN_SERVER_ERROR_STATUS:
            raise _Retry(SourceError(SourceErrorKind.SERVER_ERROR, f"HTTP {status}"), None)

        try:
            payload = httpx.Response(status, content=body).json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise SourceError(SourceErrorKind.BAD_QUERY, f"unexpected response (HTTP {status})")
        if errors := payload.get("errors"):
            error = _graphql_error(errors if isinstance(errors, list) else [errors])
            if error.kind is SourceErrorKind.UNAVAILABLE:
                raise _Retry(error, None)
            raise error
        if status >= MIN_CLIENT_ERROR_STATUS or not isinstance(payload.get("data"), dict):
            raise SourceError(SourceErrorKind.BAD_QUERY, f"unexpected response (HTTP {status})")
        data: dict[str, Any] = payload["data"]
        return data

    async def _post_bounded(self, document: str) -> tuple[int, httpx.Headers, bytes]:
        try:
            async with self._http.stream("POST", self.base_url, json={"query": document}) as res:
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
            raise SourceError(
                SourceErrorKind.UNAVAILABLE, f"cannot reach Cloudflare: {exc}"
            ) from None

    async def _zone(self, document: str) -> dict[str, Any]:
        zones = (await self.query(document)).get("viewer", {}).get("zones") or []
        if not zones or not isinstance(zones[0], dict):
            raise SourceError(
                SourceErrorKind.AUTH,
                f"zone {self.conn.zone_id} not found, or the token cannot read its analytics",
            )
        zone: dict[str, Any] = zones[0]
        return zone

    # --- CloudflareApi --------------------------------------------------------------------

    async def settings(self) -> dict[str, DatasetSettings]:
        zone = await self._zone(settings_document(self.conn.zone_id))
        found = zone.get("settings") or {}
        out = {}
        for dataset in DATASETS:
            raw = found.get(dataset)
            if not isinstance(raw, dict):
                out[dataset] = DatasetSettings(enabled=False)
                continue
            out[dataset] = DatasetSettings(
                enabled=bool(raw.get("enabled", True)),
                max_duration=int(raw.get("maxDuration") or DEFAULT_MAX_DURATION),
                not_older_than=int(raw.get("notOlderThan") or DEFAULT_NOT_OLDER_THAN),
                max_page_size=int(raw.get("maxPageSize") or DEFAULT_PAGE_SIZE),
            )
        return out

    def _document_args(self, start: int, end: int, page_size: int) -> tuple[Any, ...]:
        return (self.conn.zone_id, self.conn.hostnames, _utc(start), _utc(end), page_size)

    async def traffic(self, start: int, end: int, page_size: int) -> Chunk:
        document = traffic_document(*self._document_args(start, end, page_size))
        zone = await self._zone(document)
        chunk = Chunk(query=document)
        for alias, (signal, _) in TRAFFIC_ALIASES.items():
            rows = zone.get(alias) or []
            chunk.truncated |= len(rows) >= page_size
            values = chunk.values.setdefault(signal, {})
            for row in rows:
                bucket = _epoch(row["dimensions"]["datetimeFiveMinutes"])
                values[bucket] = values.get(bucket, 0.0) + float(row.get("count") or 0)
                interval = (row.get("avg") or {}).get("sampleInterval")
                if signal == "cf_requests" and interval is not None:
                    chunk.sample_intervals[bucket] = float(interval)
        return chunk

    async def timing(self, start: int, end: int, page_size: int) -> Chunk:
        document = timing_document(*self._document_args(start, end, page_size))
        zone = await self._zone(document)
        rows = zone.get("timing") or []
        chunk = Chunk(query=document, truncated=len(rows) >= page_size)
        for signal in TIMING_FIELDS.values():
            chunk.values[signal] = {}
        for row in rows:
            bucket = _epoch(row["dimensions"]["datetimeFiveMinutes"])
            quantiles = row.get("quantiles") or {}
            for field, signal in TIMING_FIELDS.items():
                if (ms := quantiles.get(field)) is not None:
                    chunk.values[signal][bucket] = float(ms) / MS_PER_SECOND
        return chunk

    async def security(self, start: int, end: int, page_size: int) -> Chunk:
        document = security_document(*self._document_args(start, end, page_size))
        zone = await self._zone(document)
        rows = zone.get("events") or []
        chunk = Chunk(
            query=document,
            values={"cf_blocked": {}, "cf_challenged": {}},
            truncated=len(rows) >= page_size,
        )
        for row in rows:
            signal = action_signal(str(row["dimensions"].get("action", "")))
            if signal is None:
                continue
            bucket = _epoch(row["dimensions"]["datetimeFiveMinutes"])
            values = chunk.values[signal]
            values[bucket] = values.get(bucket, 0.0) + float(row.get("count") or 0)
        return chunk

    async def daily_requests(self, first: date, last: date) -> dict[date, float]:
        zone = await self._zone(
            daily_document(self.conn.zone_id, first.isoformat(), last.isoformat())
        )
        return {
            date.fromisoformat(row["dimensions"]["date"]): float(
                (row.get("sum") or {}).get("requests") or 0
            )
            for row in zone.get("httpRequests1dGroups") or []
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
