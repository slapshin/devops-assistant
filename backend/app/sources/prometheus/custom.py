"""Custom rules as catalog signals: one scope-injected query per rule (T020)."""

from collections.abc import Sequence

from app.domain.common import EntityKind, Scope, SignalFamily
from app.domain.custom_rules import ChangeDirection, CustomRule
from app.domain.user_promql import scope_template
from app.sources.prometheus.catalog import Direction, SignalDef
from app.sources.prometheus.promql import QueryTemplate

_DIRECTION = {
    ChangeDirection.UP: Direction.UP,
    ChangeDirection.DOWN: Direction.DOWN,
    ChangeDirection.BOTH: Direction.BOTH,
}
NAME_LABEL = "__name__"


def custom_signal(rule: CustomRule) -> SignalDef:
    scoped = scope_template(rule.query)
    return SignalDef(
        rule.signal,
        SignalFamily.CUSTOM,
        rule.unit,
        EntityKind.CUSTOM,
        tuple(rule.identity_labels),
        QueryTemplate(scoped.template, scoped.metrics),
        direction=_DIRECTION[rule.direction],
        description=rule.title,
    )


def custom_signals(rules: Sequence[CustomRule]) -> tuple[SignalDef, ...]:
    return tuple(custom_signal(r) for r in rules)


def custom_identity(defn: SignalDef, labels: dict[str, str], scope: Scope) -> tuple[str, ...]:
    """The rule's identity labels, or every returned label except the name and the scope's."""
    if defn.identity:
        return defn.identity
    skip = {NAME_LABEL, *scope.label_names}
    return tuple(sorted(k for k in labels if k not in skip))
