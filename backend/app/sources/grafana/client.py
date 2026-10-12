"""Bounded, read-only client for Grafana's alerting and annotations HTTP API.

Every call has a timeout, a shared concurrency limit, a response-size cap, and at most one
retry for rate limiting or transient failures. Only GET requests are sent, authenticated with a
service account token. Error messages never contain the token.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.projects import GrafanaConnection
from app.sources.base import ClientLimits
from app.sources.grafana.api import AlertInstance, AlertRule, History, Transition
from app.sources.grafana.catalog import (
    HEALTH_PATH,
    MAX_TRANSITIONS_PER_RULE,
    PAGE_SIZE,
    RULES_PATH,
    history_path,
    parse_text,
    tag_labels,
)

log = logging.getLogger("app.sources.grafana")

MAX_ERROR_MESSAGE_CHARS = 300
MAX_RETRY_AFTER_SECONDS = 30.0
RETRY_BACKOFF_SECONDS = 1.0
MS_PER_SECOND = 1000
MIN_ACTIVE_AT_YEAR = 2000
"""Grafana reports ``0001-01-01T00:00:00Z`` for instances that were never active."""
UNAUTHORIZED_STATUS = 401
FORBIDDEN_STATUS = 403
NOT_FOUND_STATUS = 404
RATE_LIMITED_STATUS = 429
MIN_REDIRECT_STATUS = 300
MIN_CLIENT_ERROR_STATUS = 400
MIN_SERVER_ERROR_STATUS = 500


def _message(body: bytes) -> str:
    """The ``message`` of a Grafana error body."""
    try:
        payload = httpx.Response(200, content=body).json()
    except ValueError:
        return ""
    found = payload.get("message") if isinstance(payload, dict) else None
    return str(found or "")[:MAX_ERROR_MESSAGE_CHARS]


def _epoch(raw: Any) -> int | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return int(moment.timestamp()) if moment.year >= MIN_ACTIVE_AT_YEAR else None


def _labels(raw: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


class GrafanaClient:
    """``GrafanaApi`` over the Grafana HTTP API for one source."""

    def __init__(
        self,
        conn: GrafanaConnection,
        *,
        limits: ClientLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.conn = conn
        self.limits = limits or ClientLimits()
        headers = {"Accept": "application/json", "X-Grafana-Org-Id": str(conn.org_id)}
        if conn.token is not None:
            headers["Authorization"] = f"Bearer {conn.token.get_secret_value()}"
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

    @property
    def base_url(self) -> str:
        return self.conn.api_url

    @property
    def backend(self) -> str | None:
        return "grafana"

    def begin(self, end_time: datetime) -> None:
        pass

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- transport ------------------------------------------------------------------------

    async def request(self, path: str, what: str) -> Any:
        """The decoded JSON of a GET request; raises SourceError on any error.

        ``what`` names the requested data in error messages.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    self.request_count += 1
                    return await self._once(path, what)
            except _Retry as retry:
                if attempt > self.limits.retries:
                    raise retry.error from None
                delay = retry.after if retry.after is not None else RETRY_BACKOFF_SECONDS * attempt
                log.warning("retrying Grafana request in %.1fs: %s", delay, retry.error.message)
                await asyncio.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))

    async def _once(self, path: str, what: str) -> Any:
        started = time.monotonic()
        status, headers, raw = await self._bounded(path)
        log.debug("GET %s %s %.2fs %dB", path, status, time.monotonic() - started, len(raw))

        reason = _message(raw) if status >= MIN_CLIENT_ERROR_STATUS else ""
        suffix = f": {reason}" if reason else ""
        if status == UNAUTHORIZED_STATUS:
            raise SourceError(
                SourceErrorKind.AUTH,
                "Grafana rejected the service account token (HTTP 401); check that it exists "
                "and has not expired",
            )
        if status == FORBIDDEN_STATUS:
            raise SourceError(
                SourceErrorKind.AUTH,
                f"the service account may not read {what} in organization {self.conn.org_id} "
                f"(needs the Viewer role; HTTP 403){suffix}",
            )
        if status == RATE_LIMITED_STATUS:
            raise _Retry(
                SourceError(SourceErrorKind.UNAVAILABLE, "Grafana is rate limiting (HTTP 429)"),
                _retry_after(headers.get("retry-after")),
            )
        if status >= MIN_SERVER_ERROR_STATUS:
            raise _Retry(SourceError(SourceErrorKind.SERVER_ERROR, f"HTTP {status}{suffix}"), None)
        if status == NOT_FOUND_STATUS:
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"Grafana has no {what} endpoint (HTTP 404); check the URL and that Grafana "
                "alerting is enabled",
            )
        if MIN_REDIRECT_STATUS <= status < MIN_CLIENT_ERROR_STATUS:
            location = headers.get("location", "")
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"unexpected redirect (HTTP {status}) to {location[:200]}; check the Grafana URL",
            )
        if status >= MIN_CLIENT_ERROR_STATUS:
            raise SourceError(SourceErrorKind.BAD_QUERY, f"HTTP {status}{suffix}")
        try:
            return httpx.Response(status, content=raw).json()
        except ValueError:
            raise SourceError(
                SourceErrorKind.BAD_QUERY,
                f"unexpected response (HTTP {status}); is this the Grafana URL?",
            ) from None

    async def _bounded(self, path: str) -> tuple[int, httpx.Headers, bytes]:
        try:
            async with self._http.stream("GET", path) as res:
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
                    "the TLS certificate of Grafana is not trusted (self-signed or internal CA); "
                    "turn off TLS verification for this source if that is expected",
                ) from None
            raise SourceError(SourceErrorKind.UNAVAILABLE, f"cannot reach Grafana: {exc}") from None

    # --- GrafanaApi -----------------------------------------------------------------------

    async def version(self) -> str | None:
        try:
            raw = await self.request(HEALTH_PATH, "health")
        except SourceError:
            return None
        found = raw.get("version") if isinstance(raw, dict) else None
        return str(found) if found else None

    async def rules(self) -> list[AlertRule]:
        rules: list[AlertRule] = []
        token: str | None = None
        while True:
            path = RULES_PATH + (f"?{urlencode({'group_next_token': token})}" if token else "")
            raw = await self.request(path, "alert rules")
            data = raw.get("data") if isinstance(raw, dict) else None
            groups = data.get("groups") if isinstance(data, dict) else None
            if not isinstance(groups, list):
                raise SourceError(SourceErrorKind.BAD_QUERY, "rules response lacks data.groups")
            rules += [rule for group in groups for rule in _group_rules(group)]
            token = data.get("groupNextToken") if isinstance(data, dict) else None
            if not token:
                return rules

    async def history(self, rule: AlertRule, start: int, end: int) -> History:
        """Pages backwards from ``end``: Grafana returns the newest ``PAGE_SIZE`` first."""
        start_ms, to_ms = start * MS_PER_SECOND, end * MS_PER_SECOND - 1
        found: dict[int, Transition] = {}
        truncated = False
        while True:
            raw = await self.request(history_path(rule.uid, start_ms, to_ms), "alert state history")
            if not isinstance(raw, list):
                raise SourceError(SourceErrorKind.BAD_QUERY, "annotations response is not a list")
            times = []
            for item in raw:
                if isinstance(item, dict) and (t := _transition(item, rule.title)) is not None:
                    times.append(item["time"])
                    if start <= t.time < end:
                        found.setdefault(t.id, t)
            if len(raw) < PAGE_SIZE:
                break
            oldest = min(times, default=to_ms)
            if oldest >= to_ms or len(found) >= MAX_TRANSITIONS_PER_RULE:
                truncated = True  # one millisecond holds a full page, or the budget is spent
                break
            to_ms = oldest  # inclusive: changes at the boundary are deduplicated by ID
        transitions = sorted(found.values(), key=lambda t: (t.time, t.id))
        kept = transitions[-MAX_TRANSITIONS_PER_RULE:]
        known_from = kept[0].time if truncated and kept else None
        return History(kept, truncated, known_from)


def _group_rules(group: Any) -> list[AlertRule]:
    if not isinstance(group, dict):
        return []
    rules = []
    for raw in group.get("rules") or []:
        if not isinstance(raw, dict) or raw.get("type") != "alerting" or not raw.get("uid"):
            continue
        instances = [
            AlertInstance(
                _labels(a.get("labels")),
                str(a.get("state") or ""),
                _epoch(a.get("activeAt")),
                _labels(a.get("annotations")),
            )
            for a in raw.get("alerts") or []
            if isinstance(a, dict)
        ]
        rules.append(
            AlertRule(
                uid=str(raw["uid"]),
                title=str(raw.get("name") or raw["uid"]),
                folder_uid=str(raw.get("folderUid") or group.get("folderUid") or ""),
                folder=str(group.get("file") or ""),
                group=str(group.get("name") or ""),
                labels=_labels(raw.get("labels")),
                annotations=_labels(raw.get("annotations")),
                paused=bool(raw.get("isPaused")),
                instances=instances,
            )
        )
    return rules


def _transition(item: dict[str, Any], title: str) -> Transition | None:
    try:
        ident, at = int(item["id"]), int(item["time"]) // MS_PER_SECOND
    except KeyError, TypeError, ValueError:
        return None
    tags = [str(t) for t in item.get("tags") or [] if isinstance(t, str)]
    parsed = parse_text(str(item.get("text") or ""), str(item.get("alertName") or title))
    labels = tag_labels(tags)
    if parsed is not None:
        labels |= parsed[1]
    return Transition(
        id=ident,
        time=at,
        prev=str(item.get("prevState") or ""),
        next=str(item.get("newState") or ""),
        instance=parsed[0] if parsed is not None else None,
        labels=labels,
    )


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
