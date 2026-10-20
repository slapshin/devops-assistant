"""Scope diagnostics of a Sentry source (T022): error events of the latest 24 h per project,
with the environment and each tag dropped in turn."""

from collections.abc import Sequence

from app.domain.common import Reason, SignalFamily, SourceKind
from app.domain.diagnostics import (
    MAX_SAMPLES,
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
from app.sources.sentry.api import SECONDS_PER_DAY, IssueInfo, ProjectInfo, SentryApi
from app.sources.sentry.catalog import environments_path, error_count_text


async def sentry_diagnostics(
    api: SentryApi,
    organization: str,
    infos: Sequence[ProjectInfo],
    environment: str | None,
    tags: Sequence[tuple[str, str]],
    kinds: dict[str, list[IssueInfo]],
    end: int,
    requests: Sequence[str],
) -> ScopeDiagnostics:
    """``kinds``: top issues per project ID (samples); ``requests``: the series requests."""
    start = end - SECONDS_PER_DAY
    ids = [i.id for i in infos]
    notes: list[Reason] = []

    async def count(
        project_ids: Sequence[str],
        env: str | None,
        tag_filters: Sequence[tuple[str, str]],
        what: str,
    ) -> int | None:
        try:
            return await api.error_count(project_ids, start, end, env, tag_filters)
        except SourceError as exc:
            notes.append(diagnostic_failed(what, exc))
            return None

    per_project = {
        i.id: await count([i.id], environment, tags, f"errors of {i.slug}") for i in infos
    }
    known = [n for n in per_project.values() if n is not None]
    total = sum(known) if len(known) == len(per_project) else None
    checks = [
        FilterCheck(
            part=i.slug,
            field=FilterField.PROJECT,
            matched=per_project[i.id],
            matched_without=None,
            unit=CountUnit.EVENTS,
        )
        for i in infos
    ]
    extra = [error_count_text(organization, ids, environment, tags)]
    if environment:
        names: list[str] = []
        for i in infos:
            try:
                names += await api.environments(i.slug)
            except SourceError as exc:
                notes.append(diagnostic_failed(f"environments of {i.slug}", exc))
        values, truncated = capped_available(names)
        checks.append(
            FilterCheck(
                part=environment,
                field=FilterField.ENVIRONMENT,
                matched=total,
                matched_without=await count(ids, None, tags, "errors without the environment"),
                unit=CountUnit.EVENTS,
                available=values,
                available_truncated=truncated,
                matching=[v for v in values if v == environment],
            )
        )
        extra += [f"GET {environments_path(organization, i.slug)}" for i in infos]
        extra.append(error_count_text(organization, ids, None, tags))
    for n, (key, value) in enumerate(tags):
        rest = [*tags[:n], *tags[n + 1 :]]
        checks.append(
            FilterCheck(
                part=f"{key}:{value}",
                field=FilterField.TAG,
                matched=total,
                matched_without=await count(ids, environment, rest, f"errors without tag {key}"),
                unit=CountUnit.EVENTS,
            )
        )
        extra.append(error_count_text(organization, ids, environment, rest))

    filters = {
        **({"environment": environment} if environment else {}),
        **{f"tag:{k}": v for k, v in tags},
    }
    selection = [
        SelectedEntity(
            display_name=i.name,
            labels={"project": i.slug, "project_id": i.id, **filters},
            count=per_project[i.id] or 0,
            unit=CountUnit.EVENTS,
            families=[SignalFamily.APP_ERRORS],
        )
        for i in infos
    ]
    slugs = {i.id: i.slug for i in infos}
    samples = [
        {"project": slugs[pid], "issue": k.issue, "title": k.title}
        for pid, issues in kinds.items()
        for k in issues
    ][:MAX_SAMPLES]
    if total == 0:
        notes.insert(
            0,
            Reason(
                code="nothing_selected",
                message="No error events in the latest 24 h with these filters.",
            ),
        )
    return ScopeDiagnostics(
        kind=SourceKind.SENTRY,
        summary=f"{counted(len(infos), 'project')}, "
        + (
            f"{counted(total, 'error event')} in 24 h"
            if total is not None
            else "error events unknown"
        ),
        selection=capped_selection(selection),
        selection_total=len(selection),
        samples=samples,
        checks=checks,
        requests=capped_requests([*requests, *extra]),
        notes=notes,
    )
