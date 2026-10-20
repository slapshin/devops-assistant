"""Scope diagnostics of a Wazuh source (T022): selected agents and per-filter-part checks."""

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
)
from app.sources.wazuh.api import AgentList, GroupMembers
from app.sources.wazuh.catalog import SIGNALS

FAMILIES = sorted({s.family for s in SIGNALS}, key=list(SignalFamily).index)


def wazuh_diagnostics(
    found: AgentList,
    members: GroupMembers | None,
    agents: Sequence[str],
    labels: Sequence[tuple[str, str]],
    without_label: Sequence[int | None],
    any_name: AgentList | None,
    requests: Sequence[str],
    notes: Sequence[Reason],
) -> ScopeDiagnostics:
    """``without_label[i]``: agents found with label i dropped; ``any_name``: agents with
    alerts under the labels whatever their name (only listed for named agents)."""
    alerts = {a.name: a.alerts for a in found.agents}
    groups_of: dict[str, list[str]] = {}
    for group, names in (members.members if members else {}).items():
        for name in names:
            groups_of.setdefault(name, []).append(group)
    expected = sorted({*agents, *groups_of, *alerts})
    selection = [
        SelectedEntity(
            display_name=name,
            labels={
                "agent": name,
                **({"groups": ", ".join(groups_of[name])} if name in groups_of else {}),
            },
            count=alerts.get(name, 0),
            unit=CountUnit.ALERTS,
            families=FAMILIES if name in alerts else [],
        )
        for name in expected
    ]

    checks: list[FilterCheck] = []
    named, truncated = capped_available([a.name for a in any_name.agents] if any_name else [])
    for name in agents:
        checks.append(
            FilterCheck(
                part=name,
                field=FilterField.AGENT,
                matched=alerts.get(name, 0),
                matched_without=None,
                unit=CountUnit.ALERTS,
                available=named,
                available_truncated=truncated or bool(any_name and any_name.truncated),
                matching=[n for n in named if n == name],
            )
        )
    for group, names in (members.members if members else {}).items():
        checks.append(
            FilterCheck(
                part=group,
                field=FilterField.GROUP,
                matched=len(names),
                matched_without=None,
                unit=CountUnit.AGENTS,
            )
        )
    for (key, value), without in zip(labels, without_label, strict=True):
        checks.append(
            FilterCheck(
                part=f"{key}={value}",
                field=FilterField.LABEL,
                matched=len(found.agents),
                matched_without=without,
                unit=CountUnit.AGENTS,
            )
        )

    all_notes = list(notes)
    if not found.agents:
        all_notes.insert(
            0,
            Reason(
                code="nothing_selected",
                message="No selected agent had alerts in the window; check the agent names, "
                "groups, labels and index pattern.",
            ),
        )
    with_alerts = [e for e in selection if e.count]
    return ScopeDiagnostics(
        kind=SourceKind.WAZUH,
        summary=f"{counted(len(with_alerts), 'agent')} with alerts, "
        f"{counted(sum(alerts.values()), 'alert')}"
        + (
            f", {len(expected) - len(with_alerts)} silent"
            if len(expected) > len(with_alerts)
            else ""
        ),
        selection=capped_selection(selection),
        selection_total=len(selection),
        samples=[dict(e.labels) for e in capped_selection(with_alerts)[:MAX_SAMPLES]],
        checks=checks,
        requests=capped_requests(list(requests)),
        notes=all_notes,
    )
