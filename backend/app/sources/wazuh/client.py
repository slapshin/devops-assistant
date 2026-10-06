"""Bounded, read-only client for the Wazuh indexer's search API.

Every call has a timeout, a shared concurrency limit, a response-size cap, and at most one
retry for rate limiting or transient failures. Only ``_search`` requests (and a best-effort
``GET /`` for the version) are sent. Error messages never contain the password.
A search that timed out or failed on some shards raises instead of returning partial counts,
so missing alerts are never reported as a quiet period.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from app.domain.common import STEP_SECONDS
from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.projects import WazuhConnection
from app.sources.base import ClientLimits
from app.sources.wazuh.api import AgentInfo, AgentList, Chunk, GroupMembers
from app.sources.wazuh.catalog import (
    FILTERED,
    MAX_AGENTS,
    MAX_GROUP_MEMBERS,
    discovery_body,
    members_body,
    request_text,
    search_path,
    series_body,
)

log = logging.getLogger("app.sources.wazuh")

MAX_ERROR_MESSAGE_CHARS = 300
MAX_RETRY_AFTER_SECONDS = 30.0
RETRY_BACKOFF_SECONDS = 1.0
MS_PER_SECOND = 1000
UNAUTHORIZED_STATUS = 401
FORBIDDEN_STATUS = 403
NOT_FOUND_STATUS = 404
RATE_LIMITED_STATUS = 429
MIN_REDIRECT_STATUS = 300
MIN_CLIENT_ERROR_STATUS = 400
MIN_SERVER_ERROR_STATUS = 500
TOTAL_SIGNAL = "wazuh_alerts"


def _reason(body: bytes) -> tuple[str, str]:
    """(error type, reason) of an OpenSearch error body, preferring the root cause."""
    try:
        payload = httpx.Response(200, content=body).json()
    except ValueError:
        return "", ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "", str(error or "")[:MAX_ERROR_MESSAGE_CHARS]
    causes = error.get("root_cause") or []
    if isinstance(causes, list) and causes and isinstance(causes[0], dict):
        error = causes[0]
    elif isinstance(error.get("caused_by"), dict):
        error = error["caused_by"]
    return str(error.get("type") or ""), str(error.get("reason") or "")[:MAX_ERROR_MESSAGE_CHARS]


class WazuhClient:
    """``WazuhApi`` over the indexer REST API for one source."""

    def __init__(
        self,
        conn: WazuhConnection,
        *,
        limits: ClientLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.conn = conn
        self.limits = limits or ClientLimits()
        auth = None
        if conn.username is not None:
            password = conn.password.get_secret_value() if conn.password is not None else ""
            auth = httpx.BasicAuth(conn.username, password)
        self._http = httpx.AsyncClient(
            base_url=conn.api_url,
            headers={"Accept": "application/json"},
            auth=auth,
            timeout=httpx.Timeout(self.limits.timeout_seconds),
            transport=transport,
            follow_redirects=False,
            verify=conn.tls_verify,
        )
        self._sem = asyncio.Semaphore(self.limits.concurrency)
        self._labels = [(label.key, label.value) for label in conn.labels]
        self.request_count = 0

    @property
    def base_url(self) -> str:
        return self.conn.api_url

    @property
    def backend(self) -> str | None:
        return "wazuh-indexer"

    def begin(self, end_time: datetime) -> None:
        pass

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- transport ------------------------------------------------------------------------

    async def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, index: str = ""
    ) -> Any:
        """The decoded JSON of a request; raises SourceError on any error.

        ``index`` names the searched index pattern in error messages.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    self.request_count += 1
                    return await self._once(method, path, body, index or self.conn.index_pattern)
            except _Retry as retry:
                if attempt > self.limits.retries:
                    raise retry.error from None
                delay = retry.after if retry.after is not None else RETRY_BACKOFF_SECONDS * attempt
                log.warning("retrying Wazuh request in %.1fs: %s", delay, retry.error.message)
                await asyncio.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))

    async def _once(self, method: str, path: str, body: dict[str, Any] | None, index: str) -> Any:
        started = time.monotonic()
        status, headers, raw = await self._bounded(method, path, body)
        log.debug("%s %s %s %.2fs %dB", method, path, status, time.monotonic() - started, len(raw))

        kind, reason = _reason(raw) if status >= MIN_CLIENT_ERROR_STATUS else ("", "")
        suffix = f": {reason}" if reason else ""
        if status == UNAUTHORIZED_STATUS:
            raise SourceError(
                SourceErrorKind.AUTH, "the Wazuh indexer rejected the user or password (HTTP 401)"
            )
        if status == FORBIDDEN_STATUS:
            raise SourceError(
                SourceErrorKind.AUTH,
                f"the user may not search {index} (needs read on those indices; HTTP 403){suffix}",
            )
        if status == RATE_LIMITED_STATUS:
            raise _Retry(
                SourceError(SourceErrorKind.UNAVAILABLE, "Wazuh indexer is overloaded (HTTP 429)"),
                _retry_after(headers.get("retry-after")),
            )
        if kind == "too_many_buckets_exception":
            raise SourceError(SourceErrorKind.TOO_LARGE, f"too many aggregation buckets{suffix}")
        if status >= MIN_SERVER_ERROR_STATUS:
            raise _Retry(SourceError(SourceErrorKind.SERVER_ERROR, f"HTTP {status}{suffix}"), None)
        if status == NOT_FOUND_STATUS or kind == "index_not_found_exception":
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"index {index} not found (HTTP {status}); check the index pattern",
            )
        if MIN_REDIRECT_STATUS <= status < MIN_CLIENT_ERROR_STATUS:
            location = headers.get("location", "")
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"unexpected redirect (HTTP {status}) to {location[:200]}; "
                "check the Wazuh indexer URL",
            )
        if status >= MIN_CLIENT_ERROR_STATUS:
            raise SourceError(SourceErrorKind.BAD_QUERY, f"HTTP {status}{suffix}")
        try:
            return httpx.Response(status, content=raw).json()
        except ValueError:
            raise SourceError(
                SourceErrorKind.BAD_QUERY, f"unexpected response (HTTP {status})"
            ) from None

    async def _bounded(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> tuple[int, httpx.Headers, bytes]:
        try:
            async with self._http.stream(method, path, json=body) as res:
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
                    "the TLS certificate of the Wazuh indexer is not trusted (self-signed or "
                    "internal CA, the Wazuh default); turn off TLS verification for this "
                    "source if that is expected",
                ) from None
            raise SourceError(
                SourceErrorKind.UNAVAILABLE, f"cannot reach the Wazuh indexer: {exc}"
            ) from None

    async def search(
        self, body: dict[str, Any], index: str | None = None, missing_hint: str = ""
    ) -> dict[str, Any]:
        index = index or self.conn.index_pattern
        raw = await self.request("POST", search_path(index), body, index)
        if not isinstance(raw, dict):
            raise SourceError(SourceErrorKind.BAD_QUERY, "unexpected search response")
        shards = raw.get("_shards") or {}
        if shards.get("total") == 0:
            raise SourceError(
                SourceErrorKind.BAD_QUERY, f"index pattern {index} matches no indices{missing_hint}"
            )
        if raw.get("timed_out"):
            raise SourceError(SourceErrorKind.TIMEOUT, "the search timed out on the indexer")
        if failed := int(shards.get("failed") or 0):
            raise SourceError(
                SourceErrorKind.SERVER_ERROR,
                f"{failed} of {shards.get('total')} shards failed; counts would be incomplete",
            )
        return raw

    # --- WazuhApi -------------------------------------------------------------------------

    async def version(self) -> str | None:
        try:
            raw = await self.request("GET", "/")
        except SourceError:
            return None  # a role limited to the alerts indices may not read cluster info
        found = raw.get("version") if isinstance(raw, dict) else None
        return str(found["number"]) if isinstance(found, dict) and "number" in found else None

    async def group_members(self, groups: Sequence[str], start: int, end: int) -> GroupMembers:
        raw = await self.search(
            members_body(groups, start, end),
            self.conn.monitoring_index_pattern,
            "; groups are read from the agent snapshots the Wazuh dashboard writes when "
            "wazuh.monitoring.enabled is on",
        )
        members: dict[str, list[str]] = {group: [] for group in groups}
        truncated = False
        for bucket in _buckets(raw, "groups"):
            names = [str(b["key"]) for b in (bucket.get("agents") or {}).get("buckets") or []]
            truncated |= len(names) > MAX_GROUP_MEMBERS
            if (group := str(bucket["key"])) in members:
                members[group] = names[:MAX_GROUP_MEMBERS]
        return GroupMembers(members, truncated)

    async def agents(self, names: Sequence[str] | None, start: int, end: int) -> AgentList:
        if names is not None and not names:
            return AgentList([])  # an empty selection is no agents, never every agent
        raw = await self.search(discovery_body(names or [], self._labels, start, end))
        found = []
        for bucket in _buckets(raw, "agents"):
            first = (bucket.get("first") or {}).get("value")
            seen = int(float(first) // MS_PER_SECOND) if first is not None else start
            found.append(AgentInfo(str(bucket["key"]), int(bucket["doc_count"]), seen))
        found.sort(key=lambda a: a.name)
        return AgentList(found[:MAX_AGENTS], truncated=len(found) > MAX_AGENTS)

    async def series(self, agents: Sequence[str], start: int, end: int) -> Chunk:
        body = series_body(agents, self._labels, start, end)
        raw = await self.search(body)
        chunk = Chunk(query=request_text(search_path(self.conn.index_pattern), body))
        values = chunk.values
        for agent_bucket in _buckets(raw, "agents"):
            agent = str(agent_bucket["key"])
            for bucket in (agent_bucket.get("time") or {}).get("buckets") or []:
                ts = int(bucket["key"]) // MS_PER_SECOND // STEP_SECONDS * STEP_SECONDS
                total = float(bucket.get("doc_count") or 0)
                values.setdefault(TOTAL_SIGNAL, {}).setdefault(agent, {})[ts] = total
                signals = (bucket.get("signals") or {}).get("buckets") or {}
                for defn in FILTERED:
                    count = float((signals.get(defn.signal) or {}).get("doc_count") or 0)
                    values.setdefault(defn.signal, {}).setdefault(agent, {})[ts] = count
        return chunk


def _buckets(raw: dict[str, Any], name: str) -> list[dict[str, Any]]:
    found = ((raw.get("aggregations") or {}).get(name) or {}).get("buckets")
    if not isinstance(found, list):
        raise SourceError(SourceErrorKind.BAD_QUERY, f"search response lacks the {name} buckets")
    return found


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
