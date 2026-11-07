"""Fusion の式(Lua)から参照を抽出し、参照を書き換える。

ツール参照は次の2形を取る。

    ドット形    ToolName.PortName(.属性...)   他ツールの入力または出力
    裸の識別子  PortName                        同じツールの入力
"""

from __future__ import annotations

import re
from typing import Final, Iterable, Iterator

__all__ = ["references", "qualify_same_tool_references"]

# 予約語と標準ライブラリ名(comp を含む)はツールでも入力でもないため、参照から除外する。
_LUA_RESERVED: Final[frozenset[str]] = frozenset({"and", "or", "not", "true", "false", "nil"})
_LUA_STDLIB: Final[frozenset[str]] = frozenset(
    {"comp", "string", "math", "table", "os", "io", "coroutine", "debug"}
)
# 数字の直後と 16 進数リテラルの "0x" の直後("0xE4" の "E4")から始まる識別子は参照ではない。
_IDENT_CHAIN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?<!0x)(?<!0X)[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
# 関数呼び出しの戻り値への属性アクセス(comp:GetPrefs().Comp...)の属性名はツール名ではない。
_CALL_RESULT_CHAIN_RE: Final[re.Pattern[str]] = re.compile(r"\)\s*\.\s*$")
# 直後に "(" が続く識別子は関数名(iif、Point など)である。
_CALL_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\(")
_STRING_LITERAL_RE: Final[re.Pattern[str]] = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')


def _reference_matches(expression: str) -> Iterator[tuple[re.Match[str], list[str]]]:
    """参照にあたる識別子連鎖を、一致と連鎖の要素の組として列挙する。"""
    literals = [m.span() for m in _STRING_LITERAL_RE.finditer(expression)]
    for match in _IDENT_CHAIN_RE.finditer(expression):
        if any(start <= match.start() < end for start, end in literals):
            continue
        chain = match.group().split(".")
        if len(chain) >= 2:
            if chain[0] in _LUA_STDLIB or _CALL_RESULT_CHAIN_RE.search(expression[: match.start()]):
                continue
        else:
            if chain[0] in _LUA_RESERVED or chain[0] in _LUA_STDLIB:
                continue
            if _CALL_SUFFIX_RE.match(expression[match.end() :]):
                continue
        yield match, chain


def references(expression: str, owner_tool: str) -> list[tuple[str, str]]:
    """参照先の ``(ツール名, ポート名)`` を抽出する。ドット形は先頭の2要素のみを使う。"""
    return [
        (chain[0], chain[1]) if len(chain) >= 2 else (owner_tool, chain[0])
        for _, chain in _reference_matches(expression)
    ]


def qualify_same_tool_references(expression: str, owner_tool: str, names: Iterable[str]) -> str:
    """``names`` に含まれる裸の識別子を ``<owner_tool>.<識別子>`` へ置換した式を返す。

    Fusion は裸の識別子を、ツールが内部に保持し ``GetInputList`` に現れない名前へ解決する
    場合がある(``TextPlus`` の ``Gap`` は 157 と評価される)。修飾した参照は入力へ解決される。
    ``time`` などの組み込みの名前は修飾すると式が壊れるため、置換の対象を ``names`` に限定する。
    """
    targets = frozenset(names)
    parts: list[str] = []
    last = 0
    for match, chain in _reference_matches(expression):
        if len(chain) != 1 or chain[0] not in targets:
            continue
        parts.append(expression[last : match.start()])
        parts.append(f"{owner_tool}.{chain[0]}")
        last = match.end()
    parts.append(expression[last:])
    return "".join(parts)
