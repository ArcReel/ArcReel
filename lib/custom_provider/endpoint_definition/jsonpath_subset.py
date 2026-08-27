"""RFC 9535 JSONPath 受限子集的语法闸门。

定义里的每条取值路径都必须落在这个子集内：``$`` 起头、只用 child segment（``.name`` /
``['name']`` / ``[n]`` 含负下标 / ``[*]`` / ``[a:b]``）、过滤器只做 ``@`` 单值查询与字面量
比较并以 ``&&`` ``||`` 组合，``!`` 只作用于括号组或存在性判定。递归下降、联合选择器、切片
step、函数扩展一律拒绝：它们在各实现之间行为分叉，写定义的人无从预期前端预览与后端执行是否
一致。字面量与转义按 RFC 文法收严（禁前导零、引号只在同种串内转义），免得闸门放行的路径被
运行时的严格实现拒绝。

本模块只解析与拒绝，不求值——求值由运行时的 JSONPath 实现负责。解析结果里保留段序列，供
校验器判断路径是否含通配（对象通配只取首个，键序在两端可能不同）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import DefinitionErrorCode

#: 与引号种类无关的转义字符；``'`` 与 ``"`` 只能在同种引号的串里转义，逐串另加。
_ESCAPES = frozenset({"n", "t", "r", "b", "f", "/", "\\"})
_COMPARISON_OPERATORS = ("==", "!=", "<=", ">=", "<", ">")
_LITERAL_KEYWORDS = ("true", "false", "null")


class SegmentKind(StrEnum):
    NAME = "name"
    INDEX = "index"
    WILDCARD = "wildcard"
    SLICE = "slice"
    FILTER = "filter"


@dataclass(frozen=True)
class PathSegment:
    """一个 child segment。闸门只关心它是哪一类选择器，选中的具体键或下标归求值方。"""

    kind: SegmentKind


@dataclass(frozen=True)
class ParsedJsonPath:
    source: str
    segments: tuple[PathSegment, ...]

    @property
    def has_wildcard(self) -> bool:
        return any(segment.kind is SegmentKind.WILDCARD for segment in self.segments)


class JsonPathSubsetError(ValueError):
    """路径落在子集之外。``code`` 直接进诊断，``position`` 是出问题的字符序号，从 1 起数。"""

    def __init__(self, code: DefinitionErrorCode, position: int, source: str) -> None:
        super().__init__(f"{source!r}[{position}]: {code.value}")
        self.code = code
        self.position = position
        self.source = source


def parse_json_path(source: object) -> ParsedJsonPath:
    """解析一条路径，越出子集即抛 :class:`JsonPathSubsetError`。"""
    if not isinstance(source, str):
        raise JsonPathSubsetError(DefinitionErrorCode.JSONPATH_NOT_A_STRING, 1, str(source))
    if source != source.strip():
        raise JsonPathSubsetError(DefinitionErrorCode.JSONPATH_SURROUNDING_WHITESPACE, 1, source)
    return _Parser(source).parse()


def _is_name_start(char: str) -> bool:
    return char.isalpha() or char == "_" or ord(char) > 127


def _is_name_char(char: str) -> bool:
    return char.isalnum() or char == "_" or ord(char) > 127


class _Parser:
    """手写的下降解析器：每条禁用构造都对应一个显式诊断码，而不是笼统的语法错。"""

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0

    # ---- 游标 ----

    def _eof(self) -> bool:
        return self._pos >= len(self._source)

    def _peek(self, offset: int = 0) -> str:
        index = self._pos + offset
        return self._source[index] if index < len(self._source) else ""

    def _skip_whitespace(self) -> None:
        while not self._eof() and self._peek().isspace():
            self._pos += 1

    def _fail(self, code: DefinitionErrorCode) -> JsonPathSubsetError:
        return JsonPathSubsetError(code, self._pos + 1, self._source)

    # ---- 顶层 ----

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
        elif char == ":" or char == "-" or char.isdigit():
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
        if self._peek() == "-" or self._peek().isdigit():
            self._read_int()
            self._skip_whitespace()
        if self._peek() == ":":
            self._pos += 1
            self._skip_whitespace()
            if self._peek() == "-" or self._peek().isdigit():
                raise self._fail(DefinitionErrorCode.JSONPATH_SLICE_STEP)
        return PathSegment(SegmentKind.SLICE)

    # ---- 词法 ----

    def _read_shorthand(self) -> None:
        if self._eof() or not _is_name_start(self._peek()):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        while not self._eof() and _is_name_char(self._peek()):
            self._pos += 1

    def _read_quoted(self) -> None:
        """引号名选择器。转义集按引号种类分开：单引号串只能转义 ``'``，双引号串只能转义 ``"``。"""
        quote = self._peek()
        allowed = _ESCAPES | {quote}
        self._pos += 1
        while not self._eof() and self._peek() != quote:
            if self._peek() != "\\":
                if self._peek() < " ":
                    raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
                self._pos += 1
                continue
            self._pos += 1
            escape = self._peek()
            self._pos += 1
            if escape == "u":
                digits = self._source[self._pos : self._pos + 4]
                if len(digits) < 4 or any(char not in "0123456789abcdefABCDEF" for char in digits):
                    raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
                self._pos += 4
            elif escape not in allowed:
                raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        if self._eof():
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        self._pos += 1

    def _read_int(self) -> None:
        """下标与切片端点：``-`` 可选，除单个 ``0`` 外不得有前导零，``-0`` 不是合法下标。"""
        start = self._pos
        if self._peek() == "-":
            self._pos += 1
        if self._eof() or not self._peek().isdigit():
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        while not self._eof() and self._peek().isdigit():
            self._pos += 1
        digits = self._source[start : self._pos].lstrip("-")
        if self._source[start] == "-" and digits == "0" or len(digits) > 1 and digits.startswith("0"):
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)

    def _read_number(self) -> None:
        """过滤器里的数字字面量：整数部分同样禁前导零，小数点与指数后必须跟数字。"""
        if self._peek() == "-":
            self._pos += 1
        if self._eof() or not self._peek().isdigit():
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
        if self._eof() or not self._peek().isdigit():
            raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
        while not self._eof() and self._peek().isdigit():
            self._pos += 1

    # ---- 过滤器 ----

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
        """取反只能作用于括号组或存在性判定，比较式要取反须自己加括号。"""
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
                elif self._peek() == "-" or self._peek().isdigit():
                    self._read_int()
                else:
                    raise self._fail(DefinitionErrorCode.JSONPATH_FILTER_NON_SINGULAR)
                self._skip_whitespace()
                if self._peek() != "]":
                    raise self._fail(DefinitionErrorCode.JSONPATH_SYNTAX)
                self._pos += 1
                continue
            return

    def _parse_literal(self) -> None:
        char = self._peek()
        if char in {"'", '"'}:
            self._read_quoted()
            return
        if char == "-" or char.isdigit():
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
