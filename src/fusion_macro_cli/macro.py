"""マクロ(``MacroOperator``)の公開入力、出力、内側のツール名の読み出し。"""

from __future__ import annotations

import dataclasses
from typing import Any

from fusion_macro_cli import document

__all__ = ["PublishedInput", "MacroOutput", "Macro", "read_macro", "read_macros"]


@dataclasses.dataclass(frozen=True)
class PublishedInput:
    """マクロが公開する入力(``InstanceInput``)1つ分。

    ``expression`` は ``InstanceInput`` 自体の式であり、内側のツールの入力が持つ式とは別である。
    """

    key: str
    source_op: str | None
    source: str | None
    name: str | None
    page: str | None
    control_group: int | None
    expression: str | None
    default: Any


@dataclasses.dataclass(frozen=True)
class MacroOutput:
    """マクロの出力(``InstanceOutput``)1つ分。"""

    key: str
    source_op: str | None
    source: str | None


@dataclasses.dataclass(frozen=True)
class Macro:
    """マクロ1つ分の読み出し結果。``tools`` は内側の直下にあるツール名の集合である。"""

    name: str
    publish: list[PublishedInput]
    outputs: list[MacroOutput]
    tools: frozenset[str]


def read_macros(tools_table: document.Table) -> list[Macro]:
    """``tools_table`` の直下にある ``MacroOperator`` を出現順に読み出す。入れ子のマクロは探さない。"""
    macros: list[Macro] = []
    for element in tools_table:
        if element.key is None or not isinstance(element.value, document.Table):
            continue
        macro_table = element.value
        if macro_table.type_name != "MacroOperator":
            continue
        macros.append(read_macro(element.key.name, macro_table))
    return macros


def read_macro(name: str, macro_table: document.Table) -> Macro:
    """名前が ``name`` の ``MacroOperator`` の表を読み出す。"""
    publish: list[PublishedInput] = []
    inputs = macro_table.get("Inputs")
    if inputs is not None:
        for element in inputs:
            if element.key is None or not isinstance(element.value, document.Table):
                continue
            entry = element.value
            publish.append(
                PublishedInput(
                    key=element.key.name,
                    source_op=document.as_str(entry.get("SourceOp")),
                    source=document.as_str(entry.get("Source")),
                    name=document.as_str(entry.get("Name")),
                    page=document.as_str(entry.get("Page")),
                    control_group=document.as_int(entry.get("ControlGroup")),
                    expression=document.as_str(entry.get("Expression")),
                    default=document.as_native(entry.get("Default")),
                )
            )

    outputs: list[MacroOutput] = []
    outputs_table = macro_table.get("Outputs")
    if outputs_table is not None:
        for element in outputs_table:
            if element.key is None or not isinstance(element.value, document.Table):
                continue
            entry = element.value
            outputs.append(
                MacroOutput(
                    key=element.key.name,
                    source_op=document.as_str(entry.get("SourceOp")),
                    source=document.as_str(entry.get("Source")),
                )
            )

    tool_names: frozenset[str] = frozenset()
    inner_tools = macro_table.get("Tools")
    if inner_tools is not None:
        tool_names = frozenset(
            e.key.name for e in inner_tools if e.key is not None and isinstance(e.value, document.Table)
        )

    return Macro(name=name, publish=publish, outputs=outputs, tools=tool_names)
