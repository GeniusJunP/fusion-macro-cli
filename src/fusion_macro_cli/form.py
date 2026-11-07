"""文書の種類の判別。

文書は ``.comp`` と ``.setting`` の2種類であり、``.setting`` は最上位のツールにより2つの形を持つ。

- macro 形: 最上位の唯一のツールが ``MacroOperator`` であり、内側の ``Tools`` に ``.comp`` のツールを持つ。
  公開する入力(``InstanceInput``)と出力(``InstanceOutput``)をマクロ自体が持つ。
- tool 形: 最上位の唯一のツールが ``MacroOperator`` 以外のツールであり、そのツールをそのまま保存する。
  インスペクタにはツールの入力がそのまま並ぶ。

拡張子ではなく構造で判別する。各コマンドと検査規則はこの判別の結果を参照する。
"""

from __future__ import annotations

import dataclasses
from typing import Final, Literal

from fusion_macro_cli import document

__all__ = ["Kind", "KINDS", "SettingForm", "SETTING_FORMS", "TopTool", "classify", "top_tool"]

#: ``.setting`` の形。
SettingForm = Literal["macro", "tool"]

#: 文書の種類。``.comp`` は ``"comp"``、``.setting`` は形の名前で表す。
Kind = Literal["comp", "macro", "tool"]

KINDS: Final[tuple[Kind, ...]] = ("comp", "macro", "tool")
SETTING_FORMS: Final[tuple[SettingForm, ...]] = ("macro", "tool")


@dataclasses.dataclass(frozen=True)
class TopTool:
    """``.setting`` の最上位の唯一のツール。``table`` は文書の中の表そのものであり、複製ではない。"""

    name: str
    table: document.Table


def top_tool(doc: document.Document) -> TopTool:
    """``.setting`` の最上位の ``Tools`` にある唯一のツールを返す。

    ``.setting`` でない文書、またはツールが1つでない文書には ``ValueError`` を送出する。
    """
    if doc.root.type_name is not None:
        raise ValueError(f"not a .setting document (root type is {doc.root.type_name!r})")
    tools = doc.root.get("Tools")
    if not isinstance(tools, document.Table):
        raise ValueError("not a .setting document (no Tools table)")
    elements = [el for el in tools.elements if el.key is not None]
    if len(elements) != 1:
        raise ValueError(
            f"expected exactly one top-level tool in Tools (a MacroOperator for the macro form, "
            f"or any other tool for the tool form), found {len(elements)}"
        )
    element = elements[0]
    assert element.key is not None
    if not isinstance(element.value, document.Table):
        raise ValueError(f"top-level tool {element.key.name!r} is not a table")
    return TopTool(element.key.name, element.value)


def classify(doc: document.Document) -> Kind:
    """文書の種類を判別する。``.comp`` でも2つの形の ``.setting`` でもなければ ``ValueError`` を送出する。"""
    if doc.root.type_name == "Composition":
        return "comp"
    if doc.root.type_name is not None:
        raise ValueError(f"cannot classify document (root type_name={doc.root.type_name!r})")
    tool = top_tool(doc)
    if tool.table.type_name == "MacroOperator":
        return "macro"
    return "tool"
