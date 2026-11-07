"""``.comp`` / ``.setting`` の解析と書き出し。

文字列のエスケープは解析側と書き出し側で対称でなければならないため、両者を1つのモジュールで実装する。
数値は、書き戻したときに浮動小数の桁数が変わらないようテキストのまま保持する。
字下げ幅、末尾の改行、表ごとの1行表記と末尾のコンマは文書から導けないため、属性として保持する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Union


class ParseError(ValueError):
    """解析に失敗したことを表す。"""


# --------------------------------------------------------------------------
# ノード
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Key:
    """テーブルのキー。

    ``kind`` は ``Value``(``"ident"``)、``["文字列"]``(``"string"``)、``[数値]``(``"number"``)の形を表す。
    ``raw`` は角括弧と引用符を除いたテキストである。キーフレーム時刻の小数のキーがあるため、数値キーもテキストで保持する。
    """

    kind: str
    raw: str

    def __post_init__(self) -> None:
        if self.kind not in ("ident", "string", "number"):
            raise ValueError(f"unknown key kind: {self.kind!r}")

    @property
    def name(self) -> str:
        """キーが指す論理名。``string`` キーはエスケープを解いて返す。"""
        if self.kind == "string":
            return decode_string(self.raw)
        return self.raw


@dataclass
class Number:
    """数値。解析せずテキストのまま(``raw``)保持する。"""

    raw: str


@dataclass
class Boolean:
    """真偽値。"""

    value: bool


@dataclass
class String:
    """文字列。``raw`` は引用符を除いた、エスケープを解いていないテキスト。"""

    raw: str

    @property
    def value(self) -> str:
        """エスケープを解いた文字列。"""
        return decode_string(self.raw)


@dataclass
class Table:
    """``{ ... }`` の並び。``type_name`` は ``TextPlus``、``ordered()`` などの前置のテキストである。"""

    type_name: str | None
    elements: list["Element"] = field(default_factory=list)
    inline: bool = False
    trailing_comma: bool = False

    def __iter__(self):
        return iter(self.elements)

    def get(self, name: str) -> "Node | None":
        """キーの論理名が最初に一致した要素の値を返す。"""
        for element in self.elements:
            if element.key is not None and element.key.name == name:
                return element.value
        return None


@dataclass
class Element:
    """テーブルの要素。``key`` が ``None`` の場合は位置引数の要素。"""

    key: Key | None
    value: "Node"


Node = Union[Table, Number, Boolean, String]


def as_native(node: Node | None) -> Any:
    """スカラーのノードを Python の値へ変換する。``Table`` はスカラーに還元できないため、そのまま返す。"""
    if node is None:
        return None
    if isinstance(node, String):
        return node.value
    if isinstance(node, Boolean):
        return node.value
    if isinstance(node, Number):
        raw = node.raw
        if "." in raw or "e" in raw or "E" in raw:
            return float(raw)
        return int(raw)
    return node


def as_int(node: Node | None) -> int | None:
    """:func:`as_native` の結果が ``int`` であれば返し、それ以外は ``None`` を返す。"""
    value = as_native(node)
    return value if isinstance(value, int) else None


def as_str(node: Node | None) -> str | None:
    """:func:`as_native` の結果が ``str`` であれば返し、それ以外は ``None`` を返す。"""
    value = as_native(node)
    return value if isinstance(value, str) else None


def as_bool(node: Node | None) -> bool | None:
    """:func:`as_native` の結果が ``bool`` であれば返し、それ以外は ``None`` を返す。"""
    value = as_native(node)
    return value if isinstance(value, bool) else None


@dataclass
class Document:
    """1つの ``.comp`` / ``.setting`` 文書。``indent`` は1段の字下げのテキストである。"""

    root: Table
    indent: str
    trailing_newline: bool


# --------------------------------------------------------------------------
# エスケープ
# --------------------------------------------------------------------------
# Fusion が書き出すエスケープ列は \n \" \\ の3種類に限られる。タブなどの制御文字はそのまま通す。

_ESCAPE_TARGETS = {"\\": "\\\\", '"': '\\"', "\n": "\\n"}
_ESCAPE_RE = re.compile(r'[\\"\n]')
_UNESCAPE_RE = re.compile(r"\\(.)", re.S)
_UNESCAPE_TARGETS = {"n": "\n", '"': '"', "\\": "\\"}


def encode_string(value: str) -> str:
    """Python の文字列を、引用符を除いた文字列リテラルの中身へ変換する。"""
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_TARGETS[m.group()], value)


def decode_string(raw: str) -> str:
    """文字列リテラルの中身を Python の文字列へ変換する。未知のエスケープ列は :class:`ParseError` とする。"""

    def _replace(match: re.Match[str]) -> str:
        char = match.group(1)
        if char not in _UNESCAPE_TARGETS:
            raise ParseError(f"unknown escape sequence: \\{char}")
        return _UNESCAPE_TARGETS[char]

    return _UNESCAPE_RE.sub(_replace, raw)


# --------------------------------------------------------------------------
# 字句解析
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      (?P<WS>[ \t\r\n]+)
    | (?P<COMMENT>--[^\n]*)
    | (?P<STRING>"(?:[^"\\]|\\.)*")
    | (?P<NUMBER>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<LBRACE>\{)
    | (?P<RBRACE>\})
    | (?P<LBRACKET>\[)
    | (?P<RBRACKET>\])
    | (?P<EQUALS>=)
    | (?P<COMMA>,)
    | (?P<LPAREN>\()
    | (?P<RPAREN>\))
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class _Token:
    kind: str
    raw: str
    start: int
    end: int


def _tokenize(text: str) -> list[_Token]:
    """空白とコメントを除いたトークン列を作る。末尾に ``EOF`` トークンを追加する。"""
    tokens: list[_Token] = []
    pos = 0
    n = len(text)
    while pos < n:
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            line = text.count("\n", 0, pos) + 1
            raise ParseError(f"unexpected character {text[pos]!r} at line {line}")
        kind = match.lastgroup
        if kind not in ("WS", "COMMENT"):
            tokens.append(_Token(kind, match.group(), match.start(), match.end()))
        pos = match.end()
    tokens.append(_Token("EOF", "", n, n))
    return tokens


# --------------------------------------------------------------------------
# 構文解析
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token], text: str) -> None:
        self._tokens = tokens
        self._text = text
        self._i = 0

    def _peek(self) -> _Token:
        return self._tokens[self._i]

    def _advance(self) -> _Token:
        token = self._tokens[self._i]
        self._i += 1
        return token

    def _line_of(self, pos: int) -> int:
        return self._text.count("\n", 0, pos) + 1

    def _expect(self, kind: str) -> _Token:
        token = self._peek()
        if token.kind != kind:
            raise ParseError(
                f"expected {kind} but found {token.kind} at line {self._line_of(token.start)}"
            )
        return self._advance()

    def expect_eof(self) -> None:
        self._expect("EOF")

    def parse_value(self) -> Node:
        token = self._peek()
        if token.kind == "STRING":
            self._advance()
            return String(token.raw[1:-1])
        if token.kind == "NUMBER":
            self._advance()
            return Number(token.raw)
        if token.kind == "IDENT":
            if token.raw == "true":
                self._advance()
                return Boolean(True)
            if token.raw == "false":
                self._advance()
                return Boolean(False)
            self._advance()
            type_name = token.raw
            if self._peek().kind == "LPAREN":
                self._advance()
                self._expect("RPAREN")
                type_name += "()"
            return self._parse_table(type_name)
        if token.kind == "LBRACE":
            return self._parse_table(None)
        raise ParseError(f"unexpected token {token.kind} at line {self._line_of(token.start)}")

    def _parse_table(self, type_name: str | None) -> Table:
        lbrace = self._expect("LBRACE")
        elements: list[Element] = []
        trailing_comma = False
        while self._peek().kind != "RBRACE":
            elements.append(self._parse_element())
            if self._peek().kind == "COMMA":
                self._advance()
                trailing_comma = True
            else:
                trailing_comma = False
                break
        rbrace = self._expect("RBRACE")
        inline = "\n" not in self._text[lbrace.end : rbrace.start]
        return Table(type_name, elements, inline, trailing_comma)

    def _parse_element(self) -> Element:
        token = self._peek()
        if token.kind == "LBRACKET":
            self._advance()
            key_token = self._advance()
            if key_token.kind == "STRING":
                key = Key("string", key_token.raw[1:-1])
            elif key_token.kind == "NUMBER":
                key = Key("number", key_token.raw)
            else:
                raise ParseError(
                    f"expected string or number key at line {self._line_of(key_token.start)}"
                )
            self._expect("RBRACKET")
            self._expect("EQUALS")
            value = self.parse_value()
            return Element(key, value)
        if token.kind == "IDENT" and self._tokens[self._i + 1].kind == "EQUALS":
            self._advance()
            self._expect("EQUALS")
            value = self.parse_value()
            return Element(Key("ident", token.raw), value)
        value = self.parse_value()
        return Element(None, value)


_INDENT_RE = re.compile(r"\n([ \t]+)\S")


def _detect_indent(text: str) -> str:
    """最初の字下げされた行の先頭空白を1段分とする。字下げが無ければ Fusion の既定のタブとする。"""
    match = _INDENT_RE.search(text)
    if match is not None:
        return match.group(1)
    return "\t"


def parse(text: str) -> Document:
    """``.comp`` / ``.setting`` のテキストを解析する。"""
    indent = _detect_indent(text)
    trailing_newline = text.endswith("\n")
    tokens = _tokenize(text)
    parser = _Parser(tokens, text)
    root = parser.parse_value()
    parser.expect_eof()
    if not isinstance(root, Table):
        raise ParseError("document root is not a table")
    return Document(root=root, indent=indent, trailing_newline=trailing_newline)


# --------------------------------------------------------------------------
# 書き出し
# --------------------------------------------------------------------------


def _render_key(key: Key) -> str:
    if key.kind == "ident":
        return key.raw
    if key.kind == "string":
        return f'["{key.raw}"]'
    return f"[{key.raw}]"


def _render_value(node: Node, level: int, indent: str) -> str:
    if isinstance(node, Table):
        return _render_table(node, level, indent)
    if isinstance(node, String):
        return f'"{node.raw}"'
    if isinstance(node, Number):
        return node.raw
    if isinstance(node, Boolean):
        return "true" if node.value else "false"
    raise TypeError(f"unknown node type: {type(node)!r}")


def _render_element(element: Element, level: int, indent: str) -> str:
    value_text = _render_value(element.value, level, indent)
    if element.key is None:
        return value_text
    return f"{_render_key(element.key)} = {value_text}"


def _render_table(table: Table, level: int, indent: str) -> str:
    prefix = f"{table.type_name} " if table.type_name else ""
    if table.inline:
        parts = [_render_element(el, level, indent) for el in table.elements]
        if not parts:
            return prefix + "{ }"
        content = ", ".join(parts)
        if table.trailing_comma:
            content += ","
        return prefix + "{ " + content + " }"

    inner_level = level + 1
    if not table.elements:
        return prefix + "{\n" + indent * level + "}"
    lines = []
    last_index = len(table.elements) - 1
    for i, element in enumerate(table.elements):
        line = indent * inner_level + _render_element(element, inner_level, indent)
        if i < last_index or table.trailing_comma:
            line += ","
        lines.append(line)
    return prefix + "{\n" + "\n".join(lines) + "\n" + indent * level + "}"


def dump(document: Document) -> str:
    """文書をテキストへ書き出す。解析時に記録した書式を保つ。"""
    text = _render_table(document.root, 0, document.indent)
    if document.trailing_newline:
        text += "\n"
    return text


# --------------------------------------------------------------------------
# 整形(78文字規則)
# --------------------------------------------------------------------------


def _reformat_value(node: Node) -> Node:
    if isinstance(node, Table):
        return _reformat_table(node)
    return node


def _reformat_table(table: Table) -> Table:
    """入れ子がすべて1行であり、型名を含む自身の1行表現が78文字以内であれば1行とする。"""
    new_elements = [Element(el.key, _reformat_value(el.value)) for el in table.elements]
    all_children_inline = all(
        not isinstance(el.value, Table) or el.value.inline for el in new_elements
    )
    if all_children_inline:
        candidate = Table(table.type_name, new_elements, True, table.trailing_comma)
        if len(_render_table(candidate, 0, "")) <= 78:
            return candidate
    return Table(table.type_name, new_elements, False, table.trailing_comma)


def format_compact(document: Document) -> Document:
    """表ごとの1行表記を整形の規則で決め直した文書を返す。

    ``.drt`` から取り出した文書は全体が1行に詰まっており、解析時の記録が書式として使えないため、書き出しの前に適用する。
    """
    return Document(
        root=_reformat_table(document.root),
        indent=document.indent,
        trailing_newline=document.trailing_newline,
    )
