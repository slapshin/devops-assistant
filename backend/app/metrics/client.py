"""Bounded, read-only client for the Prometheus-compatible HTTP API.

Only GET endpoints are used. Every call has a timeout, at most one retry for transient
failures, a shared concurrency limit, and a response-size cap. Range queries are split into
chunks so a single request stays inside the source's per-query duration limit.
"""

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from app.domain.projects import PrometheusConnection
from app.settings import Settings

log = logging.getLogger("app.metrics")

MAX_RESPONSE_BYTES = 64 * 1024 * 1024
CHUNK_SECONDS = 7 * 86400
RETRY_BACKOFF_SECONDS = 0.5
MAX_ERROR_MESSAGE_CHARS = 500
LABEL_CACHE_BUCKET_SECONDS = 300
"""Discovery calls within the same 5-minute bucket share a cache entry."""

AUTH_FAILURE_STATUSES = (401, 403)
UNAVAILABLE_STATUSES = (502, 503, 504)
MIN_SERVER_ERROR_STATUS = 500
MIN_CLIENT_ERROR_STATUS = 400


class SourceErrorKind(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    BAD_QUERY = "bad_query"
    TOO_LARGE = "too_large"
    SERVER_ERROR = "server_error"
    AUTH = "auth"


TRANSIENT_ERROR_KINDS = (SourceErrorKind.UNAVAILABLE, SourceErrorKind.SERVER_ERROR)


class SourceError(Exception):
    def __init__(self, kind: SourceErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class RangeResult:
    """One result series: labels and (timestamp, value) samples; non-finite values are None."""

    labels: Mapping[str, str]
    samples: Sequence[tuple[int, float | None]]


@dataclass
class ClientLimits:
    timeout_seconds: float = 30.0
    concurrency: int = 4
    retries: int = 1
    max_response_bytes: int = MAX_RESPONSE_BYTES
    chunk_seconds: int = CHUNK_SECONDS
    cache_entries: int = 256
    cache_ttl_seconds: float = 600.0


@dataclass
class _Cache:
    entries: int
    ttl: float
    data: OrderedDict[tuple[Any, ...], tuple[float, Any]] = field(default_factory=OrderedDict)

    def get(self, key: tuple[Any, ...]) -> Any | None:
        hit = self.data.get(key)
        if hit is None or time.monotonic() - hit[0] > self.ttl:
            self.data.pop(key, None)
            return None
        self.data.move_to_end(key)
        return hit[1]

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        self.data[key] = (time.monotonic(), value)
        self.data.move_to_end(key)
        while len(self.data) > self.entries:
            self.data.popitem(last=False)


def _value(raw: str) -> float | None:
    v = float(raw)
    return v if math.isfinite(v) else None


class PrometheusClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
        verify: bool = True,
        limits: ClientLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.limits = limits or ClientLimits()
        self._http = httpx.AsyncClient(
            headers={"Accept": "application/json", **(headers or {})},
            auth=auth,
            verify=verify,
            timeout=httpx.Timeout(self.limits.timeout_seconds),
            transport=transport,
            follow_redirects=False,
        )
        self._sem = asyncio.Semaphore(self.limits.concurrency)
        self._cache = _Cache(self.limits.cache_entries, self.limits.cache_ttl_seconds)
        self.request_count = 0

    @classmethod
    def from_connection(
        cls, conn: PrometheusConnection, transport: httpx.AsyncBaseTransport | None = None
    ) -> PrometheusClient:
        headers: dict[str, str] = {}
        auth: httpx.Auth | None = None
        if conn.bearer_token is not None:
            headers["Authorization"] = f"Bearer {conn.bearer_token.get_secret_value()}"
        if conn.basic_auth_user and conn.basic_auth_password:
            auth = httpx.BasicAuth(
                conn.basic_auth_user, conn.basic_auth_password.get_secret_value()
            )
        return cls(
            conn.url, headers=headers, auth=auth, verify=conn.tls_verify, transport=transport
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> PrometheusClient:
        return cls.from_connection(settings.metrics_connection, transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: Sequence[tuple[str, str]]) -> Any:
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    self.request_count += 1
                    return await self._get_once(url, params)
            except SourceError as exc:
                if exc.kind not in TRANSIENT_ERROR_KINDS or attempt > self.limits.retries:
                    raise
                log.warning("retrying %s after %s: %s", path, exc.kind, exc.message)
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    async def _get_once(self, url: str, params: Sequence[tuple[str, str]]) -> Any:
        started = time.monotonic()
        status, body = await self._read_bounded(url, params)
        log.debug("GET %s %s %.2fs %dB", url, status, time.monotonic() - started, len(body))

        if status in AUTH_FAILURE_STATUSES:
            raise SourceError(SourceErrorKind.AUTH, f"source rejected credentials (HTTP {status})")

        payload: Any
        try:
            payload = httpx.Response(status, content=body).json()
        except ValueError:
            payload = None
        is_success = isinstance(payload, dict) and payload.get("status") == "success"
        if is_success and status < MIN_CLIENT_ERROR_STATUS:
            return payload["data"]

        error = payload.get("error") if isinstance(payload, dict) else None
        message = str(error or f"HTTP {status}")[:MAX_ERROR_MESSAGE_CHARS]
        raise SourceError(_error_kind(status, message), message)

    async def _read_bounded(self, url: str, params: Sequence[tuple[str, str]]) -> tuple[int, bytes]:
        """Stream the body, aborting as soon as it exceeds the response-size cap."""
        try:
            async with self._http.stream("GET", url, params=list(params)) as res:
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
                return res.status_code, b"".join(chunks)
        except httpx.TimeoutException:
            raise SourceError(
                SourceErrorKind.TIMEOUT, f"no response within {self.limits.timeout_seconds:.0f} s"
            ) from None
        except httpx.TransportError as exc:
            raise SourceError(SourceErrorKind.UNAVAILABLE, f"cannot reach source: {exc}") from None

    async def buildinfo(self) -> dict[str, Any]:
        data = await self._get("/api/v1/status/buildinfo", [])
        return data if isinstance(data, dict) else {}

    async def label_values(
        self, label: str, match: Sequence[str], start: int, end: int, limit: int
    ) -> list[str]:
        params = [("start", str(start)), ("end", str(end)), ("limit", str(limit))]
        params += [("match[]", m) for m in match]
        bucket = LABEL_CACHE_BUCKET_SECONDS
        key = ("labels", self.base_url, label, tuple(match), start // bucket, end // bucket, limit)
        if (hit := self._cache.get(key)) is not None:
            return list(hit)
        data = await self._get(f"/api/v1/label/{label}/values", params)
        values = sorted(str(v) for v in data)
        self._cache.put(key, values)
        return values

    async def query(self, query: str, at: int) -> list[RangeResult]:
        key = ("query", self.base_url, query, at)
        if (hit := self._cache.get(key)) is not None:
            return list(hit)
        data = await self._get("/api/v1/query", [("query", query), ("time", str(at))])
        if data.get("resultType") not in ("vector", "scalar", None):
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"unexpected result type {data.get('resultType')} for query {query}",
            )
        if data.get("resultType") == "scalar":
            ts, raw = data["result"]
            data = {"result": [{"metric": {}, "value": [ts, raw]}]}
        results = [
            RangeResult(
                labels=dict(r.get("metric", {})),
                samples=[(int(float(r["value"][0])), _value(r["value"][1]))],
            )
            for r in data.get("result", [])
        ]
        self._cache.put(key, results)
        return results

    async def query_range(self, query: str, start: int, end: int, step: int) -> list[RangeResult]:
        """Evaluate at start, start+step, ..., end (inclusive), merging per-chunk results."""
        key = ("range", self.base_url, query, start, end, step)
        if (hit := self._cache.get(key)) is not None:
            return list(hit)
        merged: dict[tuple[tuple[str, str], ...], dict[int, float | None]] = {}
        labels_by_key: dict[tuple[tuple[str, str], ...], dict[str, str]] = {}
        chunk = max(step, self.limits.chunk_seconds // step * step)
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + chunk - step)
            data = await self._get(
                "/api/v1/query_range",
                [
                    ("query", query),
                    ("start", str(cursor)),
                    ("end", str(chunk_end)),
                    ("step", str(step)),
                ],
            )
            for r in data.get("result", []):
                labels = {k: str(v) for k, v in r.get("metric", {}).items()}
                label_key = tuple(sorted(labels.items()))
                labels_by_key[label_key] = labels
                points = merged.setdefault(label_key, {})
                for ts, raw in r.get("values", []):
                    points[int(float(ts))] = _value(raw)
            cursor = chunk_end + step
        results = [
            RangeResult(labels=labels_by_key[k], samples=sorted(v.items()))
            for k, v in sorted(merged.items())
        ]
        self._cache.put(key, results)
        return results


def _error_kind(status: int, message: str) -> SourceErrorKind:
    lowered = message.lower()
    if "timeout" in lowered or "deadline" in lowered:
        return SourceErrorKind.TIMEOUT
    if status in UNAVAILABLE_STATUSES:
        return SourceErrorKind.UNAVAILABLE
    if status >= MIN_SERVER_ERROR_STATUS:
        return SourceErrorKind.SERVER_ERROR
    return SourceErrorKind.BAD_QUERY
