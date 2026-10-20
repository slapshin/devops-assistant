"""Scope diagnostics of a Grafana alerts source (T022), from the rules already listed."""

from collections.abc import Sequence

from app.domain.common import LabelMatcher, Reason, SourceKind
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
from app.sources.grafana.api import AlertRule
from app.sources.grafana.catalog import (
    ANNOTATIONS_PATH,
    RULES_PATH,
    firing_signal,
    instance_matches,
    selects,
)


def _listed_check(
    part: str, field: FilterField, matched: int, available: list[str], matching: list[str]
) -> FilterCheck:
    values, truncated = capped_available(available)
    return FilterCheck(
        part=part,
        field=field,
        matched=matched,
        matched_without=None,
        unit=CountUnit.RULES,
        available=values,
        available_truncated=truncated,
        matching=[v for v in values if v in matching],
    )


def _label_check(
    matcher: LabelMatcher,
    field: FilterField,
    unit: CountUnit,
    matched: int,
    rest: list[dict[str, str]],
) -> FilterCheck:
    """``rest``: label sets selected with this matcher dropped."""
    values, truncated = capped_available([v for labels in rest if (v := labels.get(matcher.name))])
    return FilterCheck(
        part=str(matcher),
        field=field,
        matched=matched,
        matched_without=len(rest),
        unit=unit,
        available=values,
        available_truncated=truncated,
        matching=[v for v in values if matcher.matches(v)],
    )


def grafana_diagnostics(
    rules: Sequence[AlertRule],
    selected: Sequence[AlertRule],
    folders: Sequence[str],
    rule_uids: Sequence[str],
    rule_labels: Sequence[LabelMatcher],
    instance_labels: Sequence[LabelMatcher],
) -> ScopeDiagnostics:
    """``rules``: every rule of the organization; ``selected``: the analysed ones."""
    labelled = [r for r in rules if selects(r, (), (), rule_labels)]
    chosen = [r for r in rules if selects(r, folders, rule_uids, rule_labels)]
    checks: list[FilterCheck] = []
    for folder in folders:
        in_folder = [r for r in labelled if folder in (r.folder, r.folder_uid)]
        checks.append(
            _listed_check(
                folder,
                FilterField.FOLDER,
                len(in_folder),
                [r.folder for r in labelled],
                [r.folder for r in in_folder],
            )
        )
    for uid in rule_uids:
        checks.append(
            _listed_check(uid, FilterField.RULE_UID, sum(r.uid == uid for r in labelled), [], [])
        )
    for m in rule_labels:
        others = [o for o in rule_labels if o != m]
        rest = [r.labels for r in rules if selects(r, folders, rule_uids, others)]
        checks.append(_label_check(m, FilterField.RULE_LABEL, CountUnit.RULES, len(chosen), rest))

    instances = [i.labels for r in selected for i in r.instances]
    kept = [labels for labels in instances if instance_matches(labels, instance_labels)]
    for m in instance_labels:
        others = [o for o in instance_labels if o != m]
        rest = [labels for labels in instances if instance_matches(labels, others)]
        checks.append(
            _label_check(m, FilterField.INSTANCE_LABEL, CountUnit.INSTANCES, len(kept), rest)
        )

    selection = [
        SelectedEntity(
            display_name=r.title,
            labels={"folder": r.folder, "group": r.group, "rule_uid": r.uid, **r.labels},
            count=sum(instance_matches(i.labels, instance_labels) for i in r.instances),
            unit=CountUnit.INSTANCES,
            families=[firing_signal(r).family],
        )
        for r in selected
    ]
    samples = kept[:MAX_SAMPLES] or [r.labels for r in selected[:MAX_SAMPLES]]
    notes = []
    if not chosen:
        notes.append(
            Reason(
                code="nothing_selected",
                message=f"No alert rule of the {len(rules)} Grafana-managed rules matches the "
                "folders, rule UIDs and rule labels.",
            )
        )
    elif instance_labels and not kept:
        notes.append(
            Reason(
                code="nothing_selected",
                message="No current alert instance of the selected rules matches the instance "
                "labels; past instances may still match.",
            )
        )
    return ScopeDiagnostics(
        kind=SourceKind.GRAFANA,
        summary=f"{len(chosen):,} of {counted(len(rules), 'rule')}, "
        f"{counted(len(kept), 'current instance')}",
        selection=capped_selection(selection),
        selection_total=len(selection),
        samples=[dict(s) for s in samples],
        checks=checks,
        requests=capped_requests(
            [
                f"GET {RULES_PATH}",
                *(
                    f"GET {ANNOTATIONS_PATH}?type=alert&alertUID={r.uid}&from=$start&to=$end"
                    for r in selected
                ),
            ]
        ),
        notes=notes,
    )
