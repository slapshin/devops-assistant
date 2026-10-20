"""Scope diagnostics of a Cloudflare source (T022): requests of the latest 24 h per hostname."""

from app.domain.common import Reason, SignalFamily, SourceKind
from app.domain.diagnostics import (
    MAX_SAMPLES,
    MAX_SELECTED,
    CountUnit,
    FilterCheck,
    FilterField,
    ScopeDiagnostics,
    SelectedEntity,
    capped_available,
    capped_requests,
    capped_selection,
    counted,
    diagnostic_failed,
)
from app.domain.interfaces import SourceError
from app.sources.cloudflare.api import SECONDS_PER_DAY, CloudflareApi
from app.sources.cloudflare.catalog import CHUNK_PLACEHOLDER, hosts_document


async def cloudflare_diagnostics(
    api: CloudflareApi,
    zone_id: str,
    zone_name: str | None,
    hostnames: list[str],
    end: int,
    max_duration: int,
    requests: list[str],
) -> ScopeDiagnostics:
    """The zone's busiest hostnames and the configured ones, over the latest 24 h (or the
    widest range one query may cover, when that is shorter)."""
    start = end - min(SECONDS_PER_DAY, max_duration)
    notes: list[Reason] = []
    busiest: dict[str, float] | None = None
    selected: dict[str, float] | None = None
    try:
        busiest = await api.host_requests(start, end, [], MAX_SELECTED)
        selected = (
            await api.host_requests(start, end, hostnames, MAX_SELECTED) if hostnames else busiest
        )
    except SourceError as exc:
        notes.append(diagnostic_failed("requests per hostname", exc))
    hosts = sorted(busiest or {}, key=lambda h: -(busiest or {})[h])
    zone_total = int(sum((busiest or {}).values())) if busiest is not None else None
    total = int(sum(selected.values())) if selected is not None else None
    available, truncated = capped_available(hosts)
    checks = [
        FilterCheck(
            part=zone_name or zone_id,
            field=FilterField.ZONE,
            matched=zone_total,
            matched_without=None,
            unit=CountUnit.REQUESTS,
        ),
        *(
            FilterCheck(
                part=h,
                field=FilterField.HOSTNAME,
                matched=int(selected.get(h, 0)) if selected is not None else None,
                matched_without=None,
                unit=CountUnit.REQUESTS,
                available=available,
                available_truncated=truncated or len(hosts) >= MAX_SELECTED,
                matching=[v for v in available if v == h],
            )
            for h in hostnames
        ),
    ]
    shown = hostnames or hosts
    selection = [
        SelectedEntity(
            display_name=h,
            labels={"zone": zone_name or zone_id, "hostname": h},
            count=int((selected or {}).get(h, 0)),
            unit=CountUnit.REQUESTS,
            families=[SignalFamily.EDGE],
        )
        for h in shown
    ]
    if total == 0:
        notes.insert(
            0,
            Reason(
                code="nothing_selected",
                message="No eyeball requests in the latest 24 h"
                + (" for the configured hostnames." if hostnames else " for this zone."),
            ),
        )
    summary = (
        f"{counted(len(hostnames) or len(hosts), 'hostname')}, {counted(total, 'request')} in 24 h"
        if total is not None
        else "requests per hostname unknown"
    )
    if hostnames and zone_total is not None:
        summary += f" (zone: {zone_total:,})"
    doc = hosts_document(zone_id, hostnames, *CHUNK_PLACEHOLDER, MAX_SELECTED)
    return ScopeDiagnostics(
        kind=SourceKind.CLOUDFLARE,
        summary=summary,
        selection=capped_selection(selection),
        selection_total=len(selection),
        samples=[
            {"hostname": e.display_name, "requests": str(e.count)}
            for e in capped_selection(selection)[:MAX_SAMPLES]
        ],
        checks=checks,
        requests=capped_requests(
            [*requests, f"# Cloudflare GraphQL, requests per hostname\n{doc}"]
        ),
        notes=notes,
    )
