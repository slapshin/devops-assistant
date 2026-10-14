"""Scope injection for user-written PromQL (custom rules).

A custom rule's query is plain PromQL such as ``sum by (job) (rate(orders_failed_total[5m]))``.
``scope_template`` lexes it and turns it into a ``str.format`` template in the catalog's
convention (see app/sources/prometheus/promql.py): ``{s}`` is inserted into every vector
selector, ``$__window`` becomes ``{w}``, and literal braces are doubled. The rendered query is
then verified again by ``assert_scoped`` with the metric names found here, so a selector the
lexer missed is refused rather than run unscoped.

Constructs that could leave the scope or the analysed time are rejected:

- ``@`` modifiers and negative ``offset`` (they read data after the evaluated step, so a
  baseline could see the future);
- ``or`` inside a selector (MetricsQL filter alternatives would bypass the scope matchers);
- comparisons at the top level of the query without ``bool``: they filter series out, and
  filtered-out series would look healthy. Use the rule's threshold instead.
"""

import re
from dataclasses import dataclass

WINDOW_PLACEHOLDER = "$__window"
MAX_QUERY_CHARS = 2000

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>\#[^\n]*)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)
  | (?P<placeholder>\$[A-Za-z0-9_]*)
  | (?P<duration>(?:\d+(?:ms|[smhdwy]))+(?![A-Za-z0-9_]))
  | (?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_:]*|:[A-Za-z_][A-Za-z0-9_:]*)
  | (?P<op>==|!=|>=|<=|=~|!~|[-+*/%^<>=@:,(){}\[\]])
  | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)

_GROUPING = frozenset({"by", "without", "on", "ignoring", "group_left", "group_right"})
_BINARY_WORDS = frozenset({"and", "or", "unless", "atan2"})
_LITERAL_WORDS = frozenset({"inf", "nan"})
_COMPARISONS = frozenset({"==", "!=", ">", "<", ">=", "<="})


class UserQueryError(ValueError):
    """The query cannot be scoped safely (or is not well-formed enough to tell)."""


@dataclass(frozen=True)
class Token:
    kind: str
    text: str


@dataclass(frozen=True)
class ScopedTemplate:
    template: str
    """``str.format`` template: ``{s}`` scope matchers, ``{w}`` rate window."""
    metrics: tuple[str, ...]
    """Metric names selected by the query, sorted."""


def _tokens(query: str) -> list[Token]:
    out: list[Token] = []
    for m in _TOKEN.finditer(query):
        kind = m.lastgroup or "other"
        out.append(Token(kind, m.group(0)))
    return out


def _escape(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


class _Lexer:
    def __init__(self, query: str) -> None:
        self.tokens = _tokens(query)
        self.i = 0
        self.out: list[str] = []
        self.metrics: set[str] = set()
        self.selectors = 0
        self.depth = 0

    # --- cursor ------------------------------------------------------------------------

    def _peek(self, j: int | None = None) -> Token | None:
        """Next significant token at or after index ``j`` (default: the cursor)."""
        k = self.i if j is None else j
        while k < len(self.tokens) and self.tokens[k].kind in ("ws", "comment"):
            k += 1
        return self.tokens[k] if k < len(self.tokens) else None

    def _skip_insignificant(self) -> None:
        while self.i < len(self.tokens) and self.tokens[self.i].kind in ("ws", "comment"):
            self.i += 1

    # --- scanning ----------------------------------------------------------------------

    def run(self) -> ScopedTemplate:
        while self.i < len(self.tokens):
            self._step()
        if self.depth != 0:
            raise UserQueryError("unbalanced parentheses")
        if not self.selectors:
            raise UserQueryError("the query selects no series (it needs at least one metric)")
        return ScopedTemplate("".join(self.out).strip(), tuple(sorted(self.metrics)))

    def _step(self) -> None:
        tok = self.tokens[self.i]
        self.i += 1
        match tok.kind:
            case "ws":
                self.out.append(tok.text)
            case "comment":
                pass
            case "string" | "number" | "duration":
                self.out.append(_escape(tok.text))
            case "placeholder":
                self.out.append(self._placeholder(tok.text))
            case "ident":
                self._ident(tok.text)
            case "op":
                self._op(tok.text)
            case _:
                raise UserQueryError(f"unexpected character {tok.text!r}")

    def _placeholder(self, text: str) -> str:
        if text != WINDOW_PLACEHOLDER:
            raise UserQueryError(
                f"unknown placeholder {text}; only {WINDOW_PLACEHOLDER} is supported"
            )
        return "{w}"

    def _ident(self, name: str) -> None:
        lower = name.lower()
        nxt = self._peek()
        if lower in _GROUPING:
            self.out.append(name)
            if nxt is not None and nxt.text == "(":
                self._skip_insignificant()
                self.i += 1
                self.out.append(" (" + self._raw_until(")", "label list") + ")")
            return
        if lower in _BINARY_WORDS or lower == "bool" or lower in _LITERAL_WORDS:
            self.out.append(name)
            return
        if lower == "offset":
            if nxt is not None and nxt.text == "-":
                raise UserQueryError("negative offset is not allowed (it reads future data)")
            self.out.append(name)
            return
        if nxt is not None and (nxt.text == "(" or nxt.text.lower() in ("by", "without")):
            self.out.append(name)  # function call or aggregation
            return

        # A metric name: always followed by a scoped matcher block.
        self.metrics.add(name)
        self.out.append(name)
        if nxt is not None and nxt.text == "{":
            self._skip_insignificant()
            self.i += 1
            self._selector_block()
        else:
            self.out.append("{{{s}}}")
            self.selectors += 1

    def _op(self, op: str) -> None:
        match op:
            case "{":
                self._selector_block()
            case "}":
                raise UserQueryError("unbalanced '}'")
            case "[":
                self.out.append("[" + self._raw_until("]", "range") + "]")
            case "]":
                raise UserQueryError("unbalanced ']'")
            case "(":
                self.depth += 1
                self.out.append("(")
            case ")":
                self.depth -= 1
                if self.depth < 0:
                    raise UserQueryError("unbalanced parentheses")
                self.out.append(")")
            case "@":
                raise UserQueryError("the @ modifier is not allowed (it pins the evaluation time)")
            case _ if op in _COMPARISONS and self.depth == 0:
                nxt = self._peek()
                if nxt is None or nxt.text.lower() != "bool":
                    raise UserQueryError(
                        f"top-level comparison '{op}' filters series out; use the rule's "
                        "threshold instead (or add 'bool')"
                    )
                self.out.append(op)
            case _:
                self.out.append(op)

    def _selector_block(self) -> None:
        """The matchers after ``{`` up to the matching ``}``; the scope goes first."""
        body: list[str] = []
        while True:
            if self.i >= len(self.tokens):
                raise UserQueryError("unterminated label matchers '{'")
            tok = self.tokens[self.i]
            self.i += 1
            if tok.text == "}":
                break
            if tok.text in ("{", "[", "(", "@"):
                raise UserQueryError(f"unexpected {tok.text!r} inside label matchers")
            if tok.kind == "ident" and tok.text.lower() == "or":
                raise UserQueryError("'or' inside label matchers is not allowed")
            if tok.kind == "placeholder":
                raise UserQueryError("placeholders are not allowed inside label matchers")
            if tok.kind != "comment":
                body.append(_escape(tok.text))
        text = "".join(body).strip()
        self.out.append("{{{s}" + (f", {text}" if text else "") + "}}")
        self.selectors += 1

    def _raw_until(self, close: str, what: str) -> str:
        """Copy tokens up to ``close`` (exclusive), replacing the window placeholder."""
        parts: list[str] = []
        while True:
            if self.i >= len(self.tokens):
                raise UserQueryError(f"unterminated {what}")
            tok = self.tokens[self.i]
            self.i += 1
            if tok.text == close:
                return "".join(parts)
            if tok.text in ("{", "}", "(", "[", "@"):
                raise UserQueryError(f"unexpected {tok.text!r} inside {what}")
            if tok.kind == "placeholder":
                parts.append(self._placeholder(tok.text))
            elif tok.kind == "string":
                raise UserQueryError(f"unexpected string inside {what}")
            elif tok.kind != "comment":
                parts.append(_escape(tok.text))


def scope_template(query: str) -> ScopedTemplate:
    """The query as a scope-injecting template; raises UserQueryError when it is not safe."""
    if not query.strip():
        raise UserQueryError("the query is empty")
    if len(query) > MAX_QUERY_CHARS:
        raise UserQueryError(f"the query is longer than {MAX_QUERY_CHARS} characters")
    return _Lexer(query).run()
