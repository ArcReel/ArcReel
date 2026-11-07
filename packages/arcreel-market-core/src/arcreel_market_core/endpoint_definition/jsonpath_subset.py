"""Syntax gate for a restricted RFC 9535 JSONPath subset.

Every extraction path in a definition must fit this subset: start with ``$``, use only child segments (``.name`` /
``['name']`` / ``[n]`` including negative indices / ``[*]`` / ``[a:b]``), and filters only comparing ``@`` singular queries with literals
and combining them with ``&&`` ``||``; ``!`` applies only to parenthesized groups or existence checks. Reject recursive descent, union selectors, slice
steps, and function extensions: implementations disagree on behavior, so definition authors cannot predict whether frontend previews and backend execution
agree. Enforce RFC grammar for literals and escapes (no leading zeros; quote escapes only inside matching quote types), preventing gate-accepted paths from being rejected by
strict runtime implementations.

This module only parses and rejects; the runtime JSONPath implementation evaluates. Retain segment sequences in parsed results so
validators can identify wildcards (object wildcards take only the first match, and key order may differ between sides).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from arcreel_market_core.definition_diagnostics import DefinitionErrorCode

#: Quote-independent escape characters; ``'`` and ``"`` may be escaped only within matching quote types, added per string.
_ESCAPES = frozenset({"n", "t", "r", "b", "f", "/", "\\"})
_COMPARISON_OPERATORS = ("==", "!=", "<=", ">=", "<", ">")
_LITERAL_KEYWORDS = ("true", "false", "null")

#: RFC 9535 ``S``: only space, tab, LF, CR. `str.isspace` also accepts ``\v`` / ``\f`` / ``\xa0``,
#: so using it as the gate would allow paths a conforming evaluator rejects.
_RFC_BLANK = frozenset({" ", "\t", "\n", "\r"})


class SegmentKind(StrEnum):
    NAME = "name"
    INDEX = "index"
    WILDCARD = "wildcard"
    SLICE = "slice"
    FILTER = "filter"


@dataclass(frozen=True)
class PathSegment:
    """One child segment. The gate needs only the selector kind; selected keys or indices belong to the evaluator."""

    kind: SegmentKind


@dataclass(frozen=True)
class ParsedJsonPath:
    source: str
    segments: tuple[PathSegment, ...]

    @property
    def has_wildcard(self) -> bool:
        return any(segment.kind is SegmentKind.WILDCARD for segment in self.segments)


class JsonPathSubsetError(ValueError):
    """Path outside the subset. ``code`` goes directly into diagnostics; ``position`` is the offending character index, starting at 1."""

    def __init__(self, code: DefinitionErrorCode, position: int, source: str) -> None:
        super().__init__(f"{source!r}[{position}]: {code.value}")
        self.code = code
        self.position = position
        self.source = source


def parse_json_path(source: object) -> ParsedJsonPath:
    """Parse a path; raise :class:`JsonPathSubsetError` outside the subset."""
    if not isinstance(source, str):
        raise JsonPathSubsetError(DefinitionErrorCode.JSONPATH_NOT_A_STRING, 1, str(source))
    # Judge surrounding whitespace using RFC S only: `str.strip` also removes U+00A0 / U+0085, which are valid member-name characters,
    # and would falsely classify a path ending in `$.foo ` as having surrounding whitespace.
    if source and (source[0] in _RFC_BLANK or source[-1] in _RFC_BLANK):
        raise JsonPathSubsetError(DefinitionErrorCode.JSONPATH_SURROUNDING_WHITESPACE, 1, source)
    return _Parser(source).parse()


def _is_ascii_digit(char: str) -> bool:
    """RFC 9535 indices and numeric literals accept ASCII digits only; `str.isdigit` also accepts Arabic-Indic digits and others."""
    return "0" <= char <= "9"


def _is_extended(char: str) -> bool:
    """RFC 9535 name range ``%x80-10FFFF`` excludes surrogate code points; unpaired surrogates are invalid code points."""
    return ord(char) > 127 and not 0xD800 <= ord(char) <= 0xDFFF


def _is_name_start(char: str) -> bool:
    return char.isalpha() or char == "_" or _is_extended(char)


def _is_name_char(char: str) -> bool:
    return char.isalnum() or char == "_" or _is_extended(char)


class _Parser:
    """Handwritten descent parser: each prohibited construct has an explicit diagnostic code instead of a generic syntax error."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0

    # ---- Cursor ----

    def _eof(self) -> bool:
        return self._pos >= len(self._source)

    def _peek(self, offset: int = 0) -> str:
        index = self._pos + offset
        return self._source[index] if index < len(self._source) else ""

    def _skip_whitespace(self) -> None:
        while not self._eof() and self._peek() in _RFC_BLANK:
            self._pos += 1

    def _fail(self, code: DefinitionErrorCode) -> JsonPathSubsetError:
        return JsonPathSubsetError(code, self._pos + 1, self._source)

    # ---- Top level ----

    def parse(self) -> ParsedJsonPath:
        if self._peek() != "$":
            raise self._fail(DefinitionErrorCode.JSONPATH_MISSING_ROOT)
        self._pos += 1
        segments: list[PathSegment] = []
        self._skip_whitespace()
        while not self._eof():
            if self._peek() == ".":
                segments.append(self._parse_dot_segment())
            elif self._peek() == "[":
                segments.append(self._parse_bracket_segment())
            else:
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
            self._skip_whitespace()
        return ParsedJsonPath(self._source, tuple(segments))

    def _parse_dot_segment(self) -> PathSegment:
        if self._peek(1) == ".":
            raise self._fail(DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT)
        self._pos += 1
        if self._peek() == "*":
            self._pos += 1
            return PathSegment(SegmentKind.WILDCARD)
        self._read_shorthand()
        return PathSegment(SegmentKind.NAME)

    def _parse_bracket_segment(self) -> PathSegment:
        self._pos += 1
        self._skip_whitespace()
        char = self._peek()
        if char in {"'", '"'}:
            self._read_quoted()
            segment = PathSegment(SegmentKind.NAME)
        elif char == "*":
            self._pos += 1
            segment = PathSegment(SegmentKind.WILDCARD)
        elif char == "?":
            self._pos += 1
            segment = self._parse_filter()
        elif char == ":" or char == "-" or _is_ascii_digit(char):
            segment = self._parse_index_or_slice(char)
        else:
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        self._skip_whitespace()
        if self._peek() == ",":
            raise self._fail(DefinitionErrorCode.JSONPATH_UNION)
        if self._peek() != "]":
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        self._pos += 1
        return segment

    def _parse_index_or_slice(self, first: str) -> PathSegment:
        if first != ":":
            self._read_int()
        self._skip_whitespace()
        if self._peek() != ":":
            return PathSegment(SegmentKind.INDEX)
        self._pos += 1
        self._skip_whitespace()
        if self._peek() == "-" or _is_ascii_digit(self._peek()):
            self._read_int()
            self._skip_whitespace()
        if self._peek() == ":":
            self._pos += 1
            self._skip_whitespace()
            if self._peek() == "-" or _is_ascii_digit(self._peek()):
                raise self._fail(DefinitionErrorCode.JSONPATH_SLICE_STEP)
        return PathSegment(SegmentKind.SLICE)

    # ---- Lexing ----

    def _read_shorthand(self) -> None:
        if self._eof() or not _is_name_start(self._peek()):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        while not self._eof() and _is_name_char(self._peek()):
            self._pos += 1

    def _read_quoted(self) -> None:
        """Quoted-name selector. Escape sets differ by quote type: single-quoted strings may escape only ``'``, double-quoted strings only ``"``."""
        quote = self._peek()
        allowed = _ESCAPES | {quote}
        self._pos += 1
        while not self._eof() and self._peek() != quote:
            if self._peek() != "\\":
                # Unpaired surrogates are invalid code points: JSON decoding delivers them as raw characters invisible to escape checks.
                if self._peek() < " " or "\ud800" <= self._peek() <= "\udfff":
                    raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
                self._pos += 1
                continue
            self._pos += 1
            escape = self._peek()
            self._pos += 1
            if escape == "u":
                self._read_unicode_escape()
            elif escape not in allowed:
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        if self._eof():
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        self._pos += 1

    def _read_unicode_escape(self) -> None:
        """``\\uXXXX``: high-surrogate escapes must be followed immediately by low-surrogate escapes; either unpaired half is invalid."""
        code = self._read_four_hex()
        if 0xDC00 <= code <= 0xDFFF:
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        if 0xD800 <= code <= 0xDBFF:
            if self._source[self._pos : self._pos + 2] != "\\u":
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
            self._pos += 2
            if not 0xDC00 <= self._read_four_hex() <= 0xDFFF:
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)

    def _read_four_hex(self) -> int:
        digits = self._source[self._pos : self._pos + 4]
        if len(digits) < 4 or any(char not in "0123456789abcdefABCDEF" for char in digits):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        self._pos += 4
        return int(digits, 16)

    def _read_int(self) -> None:
        """Indices and slice endpoints: optional ``-``, no leading zeros except a single ``0``, and ``-0`` is not a valid index."""
        start = self._pos
        if self._peek() == "-":
            self._pos += 1
        if self._eof() or not _is_ascii_digit(self._peek()):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        while not self._eof() and _is_ascii_digit(self._peek()):
            self._pos += 1
        digits = self._source[start : self._pos].lstrip("-")
        if (self._source[start] == "-" and digits == "0") or (len(digits) > 1 and digits.startswith("0")):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)

    def _read_number(self) -> None:
        """Numeric filter literals: integer parts likewise prohibit leading zeros; decimal points and exponents must be followed by digits."""
        if self._peek() == "-":
            self._pos += 1
        if self._eof() or not _is_ascii_digit(self._peek()):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        if self._peek() == "0":
            self._pos += 1
        else:
            self._read_digits()
        if self._peek() == ".":
            self._pos += 1
            self._read_digits()
        if self._peek() in {"e", "E"}:
            self._pos += 1
            if self._peek() in {"+", "-"}:
                self._pos += 1
            self._read_digits()

    def _read_digits(self) -> None:
        if self._eof() or not _is_ascii_digit(self._peek()):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        while not self._eof() and _is_ascii_digit(self._peek()):
            self._pos += 1

    # ---- Filters ----

    def _parse_filter(self) -> PathSegment:
        self._parse_or()
        self._skip_whitespace()
        return PathSegment(SegmentKind.FILTER)

    def _parse_or(self) -> None:
        self._parse_and()
        self._skip_whitespace()
        while self._source.startswith("||", self._pos):
            self._pos += 2
            self._parse_and()
            self._skip_whitespace()

    def _parse_and(self) -> None:
        self._parse_basic()
        self._skip_whitespace()
        while self._source.startswith("&&", self._pos):
            self._pos += 2
            self._parse_basic()
            self._skip_whitespace()

    def _parse_basic(self) -> None:
        """Negation applies only to parenthesized groups or existence tests; add parentheses explicitly to negate comparisons."""
        self._skip_whitespace()
        negated = self._peek() == "!"
        if negated:
            self._pos += 1
            self._skip_whitespace()
        if self._peek() == "(":
            self._pos += 1
            self._parse_or()
            self._skip_whitespace()
            if self._peek() != ")":
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
            self._pos += 1
            return
        is_query = self._peek() == "@"
        self._parse_operand()
        self._skip_whitespace()
        if self._source.startswith("=~", self._pos):
            raise self._fail(DefinitionErrorCode.JSONPATH_REGEX_OPERATOR)
        operator = next((op for op in _COMPARISON_OPERATORS if self._source.startswith(op, self._pos)), None)
        if operator is None:
            if not is_query:
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
            return
        if negated:
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        self._pos += len(operator)
        self._skip_whitespace()
        self._parse_operand()

    def _parse_operand(self) -> None:
        if self._peek() == "@":
            self._parse_singular_query()
            return
        self._parse_literal()

    def _parse_singular_query(self) -> None:
        self._pos += 1
        while True:
            # RFC `singular-query-segments = *(S (name-segment / index-segment))` permits whitespace before segments.
            # If not at a segment start, restore the cursor; the caller handles subsequent whitespace before comparison operators.
            before_blank = self._pos
            self._skip_whitespace()
            if self._peek() not in {".", "["}:
                self._pos = before_blank
                return
            if self._peek() == ".":
                if self._peek(1) == ".":
                    raise self._fail(DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT)
                self._pos += 1
                if self._peek() == "*":
                    raise self._fail(DefinitionErrorCode.JSONPATH_FILTER_NON_SINGULAR)
                self._read_shorthand()
                continue
            if self._peek() == "[":
                self._pos += 1
                self._skip_whitespace()
                if self._peek() in {"'", '"'}:
                    self._read_quoted()
                elif self._peek() == "-" or _is_ascii_digit(self._peek()):
                    self._read_int()
                else:
                    raise self._fail(DefinitionErrorCode.JSONPATH_FILTER_NON_SINGULAR)
                self._skip_whitespace()
                # After names/indices, `:` denotes slices and `,` unions; both yield multiple values and are nonsingular like `@.*`.
                if self._peek() in {":", ","}:
                    raise self._fail(DefinitionErrorCode.JSONPATH_FILTER_NON_SINGULAR)
                if self._peek() != "]":
                    raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
                self._pos += 1

    def _parse_literal(self) -> None:
        char = self._peek()
        if char in {"'", '"'}:
            self._read_quoted()
            return
        if char == "-" or _is_ascii_digit(char):
            self._read_number()
            return
        for keyword in _LITERAL_KEYWORDS:
            if self._source.startswith(keyword, self._pos):
                self._pos += len(keyword)
                return
        if char == "$":
            raise self._fail(DefinitionErrorCode.JSONPATH_FILTER_ROOT_REFERENCE)
        if _is_name_start(char or " "):
            raise self._fail(DefinitionErrorCode.JSONPATH_FUNCTION_EXTENSION)
        raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
