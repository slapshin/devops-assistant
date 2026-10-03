"""Scope-enforcing PromQL/MetricsQL construction.

Templates write every metric selector as ``metric{{{s}}}`` or ``metric{{{s}, extra="x"}}``;
``{s}`` expands to the escaped exact project/env matchers and ``{w}`` to the rate window.
``render`` enforces two invariants, so a template can never silently leave its scope
(including both operands of a ratio):

1. every ``{...}`` matcher block contains the exact project and env matchers;
2. every declared metric name is immediately followed by a matcher block (no bare names).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.common import Scope
from app.domain.projects import LabelMatcher


class ScopeViolation(ValueError):
    """A rendered query contains a selector without exact scope matchers."""


def escape_label_value(value: str) -> str:
    """Escape for a double-quoted PromQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_matchers(matchers: Sequence[LabelMatcher]) -> str:
    """Exact equality matchers in the given order, e.g. ``project="a", env="b"``."""
    return ", ".join(f'{m.name}="{escape_label_value(m.value)}"' for m in matchers)


def scope_matchers(scope: Scope) -> str:
    return f'project="{escape_label_value(scope.project)}", env="{escape_label_value(scope.env)}"'


def selector(metric: str, scope: Scope, extra: str = "") -> str:
    """Single scoped selector, e.g. for discovery ``match[]`` parameters."""
    body = scope_matchers(scope) + (f", {extra}" if extra else "")
    return f"{metric}{{{body}}}" if metric else f"{{{body}}}"


_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _mask_strings(query: str) -> str:
    """Blank out string literal contents (keeping length) so they cannot fake structure."""
    return _STRING.sub(lambda m: '"' + "_" * (len(m.group(0)) - 2) + '"', query)


def assert_scoped(query: str, scope: Scope, metrics: Sequence[str]) -> None:
    project = f'project="{escape_label_value(scope.project)}"'
    env = f'env="{escape_label_value(scope.env)}"'
    masked = _mask_strings(query)
    blocks = [(m.start(), m.end()) for m in re.finditer(r"\{[^{}]*\}", masked)]
    if not blocks:
        raise ScopeViolation(f"query has no selector: {query}")
    for start, end in blocks:
        # Check against the original text: the masked text hides label values.
        block = query[start:end]
        matchers = [m.strip() for m in _split_matchers(block[1:-1])]
        if project not in matchers or env not in matchers:
            raise ScopeViolation(f"selector block {block} is not scoped to {scope}")
    for metric in metrics:
        for m in re.finditer(rf"(?<![A-Za-z0-9_:]){re.escape(metric)}(?![A-Za-z0-9_:])", masked):
            if not masked[m.end() :].startswith("{"):
                raise ScopeViolation(f"metric {metric!r} used without a selector: {query}")


def _split_matchers(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_str = esc = False
    for c in body:
        if in_str:
            current.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        if c == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(c)
    parts.append("".join(current))
    return parts


@dataclass(frozen=True)
class QueryTemplate:
    template: str
    metrics: tuple[str, ...]

    def render(self, scope: Scope, window: str = "5m") -> str:
        query = self.template.format(s=scope_matchers(scope), w=window)
        assert_scoped(query, scope, self.metrics)
        return query
