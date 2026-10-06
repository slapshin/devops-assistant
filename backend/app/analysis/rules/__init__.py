"""Analysis rules: which collected signals are analysed, how, and under which thresholds.

Rules are defined per category in the sibling modules and indexed by name in `RULES`.
"""

from collections.abc import Iterable

from app.analysis.rules.application import APPLICATION_RULES
from app.analysis.rules.base import SEVERITY_ORDER, Dir, Rule, RuleKind, format_value
from app.analysis.rules.container import CONTAINER_RULES
from app.analysis.rules.database import DATABASE_RULES
from app.analysis.rules.edge import EDGE_RULES
from app.analysis.rules.host import HOST_RULES
from app.analysis.rules.host_security import HOST_SECURITY_RULES
from app.analysis.rules.proxy import PROXY_RULES
from app.analysis.rules.requests import REQUEST_RULES
from app.analysis.rules.security import SECURITY_RULES

__all__ = ["RULES", "SEVERITY_ORDER", "Dir", "Rule", "RuleKind", "format_value"]


def _index(rules: Iterable[Rule]) -> dict[str, Rule]:
    indexed: dict[str, Rule] = {}
    for rule in rules:
        if rule.name in indexed:
            raise ValueError(f"duplicate analysis rule: {rule.name}")
        indexed[rule.name] = rule
    return indexed


RULES = _index(
    (
        *HOST_RULES,
        *CONTAINER_RULES,
        *REQUEST_RULES,
        *PROXY_RULES,
        *DATABASE_RULES,
        *EDGE_RULES,
        *SECURITY_RULES,
        *APPLICATION_RULES,
        *HOST_SECURITY_RULES,
    ),
)
