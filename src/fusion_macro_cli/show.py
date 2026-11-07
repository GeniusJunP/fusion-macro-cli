"""入力の一覧の表示。

``.comp`` では公開の候補を、macro 形の ``.setting`` では公開の結果を、tool 形の ``.setting`` では保存したツールの入力を、文字列の表として返す。
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

from fusion_macro_cli import document
from fusion_macro_cli import form as form_mod
from fusion_macro_cli import graph as graph_mod
from fusion_macro_cli import macro as macro_mod
from fusion_macro_cli import schema as schema_mod

__all__ = ["show", "show_comp", "show_setting", "COLUMN_HELP"]

#: ``show --help`` の epilog に載せる、両方の表の列の意味。
COLUMN_HELP: str = """\
Columns of "candidates for publishing (.comp)":

  HEAD        "head" if this input starts a control, "-" otherwise
  INPUT       input name
  IC_NAME     the schema's own control name (INPS_IC_Name); this is what
              Fusion shows in the inspector when a published input omits
              its "label". Use this column to tell apart similarly named
              inputs (Red / Red1 / Red1Clone) when deciding which one is
              the one shown in the inspector
  CONTROL     control type (INPID_InputControl)
  SIBLINGS    other inputs belonging to the same control
  TYPE        value type
  VALUE       the .comp's current value
  DEFAULT     the schema's default value
  DIFF        "same" if VALUE matches DEFAULT, "diff" if it differs,
              "unknown" if it cannot be compared (a composite value such
              as a coordinate or color table, or no schema loaded for
              this tool type)
  EXPRESSION  the input's Expression, if any
  CONNECTION  "Tool.Port" if this input is connected, else "-"
  SOURCE      "schema", "user-controls", or "no-schema"

Columns of "inputs of the saved tool (tool-form .setting)":

  The same columns as "candidates for publishing (.comp)". A tool-form
  .setting saves one tool as it is, so the inspector shows every input
  of that tool; the table lists what differs from the schema's defaults.

Columns of "published inputs (.setting)" (macro form):

  KEY         the InstanceInput's key (its field name inside Inputs)
  NAME        the InstanceInput's Name (display label), or "-" if omitted
  TOOL        the SourceOp this published input targets
  INPUT       the Source (input name) this published input targets
  GROUP       ControlGroup number, or "-" if not grouped
  PAGE        the InstanceInput's Page
  DEFAULT     the InstanceInput's Default
  EXPRESSION  the InstanceInput's own Expression (evaluated by the
              MacroOperator, distinct from an expression on the inner
              tool's input), or "-" if none

A "MacroOperator" tool inside a .comp (typically a title already wrapped
by Fusion at extraction time, e.g. one inserted from a template) gets its
own block instead of the columns above: it repeats the "published inputs
(.setting)" header and lists the macro's own Inputs (InstanceInput) with
those same columns, followed by a second block (KEY, SOURCE_OP, SOURCE)
listing each Outputs entry. Read this block before running "fix": the
no-macro rule flattens the macro and discards its Inputs and Outputs.
"""

#: 仕様補完: 表示のために文字列を切り詰める長さ。
_MAX_TEXT_LEN = 40

_COMP_HEADER = "\t".join(
    [
        "HEAD",
        "INPUT",
        "IC_NAME",
        "CONTROL",
        "SIBLINGS",
        "TYPE",
        "VALUE",
        "DEFAULT",
        "DIFF",
        "EXPRESSION",
        "CONNECTION",
        "SOURCE",
    ]
)

_SETTING_HEADER = "\t".join(
    ["KEY", "NAME", "TOOL", "INPUT", "GROUP", "PAGE", "DEFAULT", "EXPRESSION"]
)

#: ``.comp`` の ``MacroOperator`` ブロックで ``Outputs`` を列挙する際の見出し。
_MACRO_OUTPUTS_HEADER = "\t".join(["KEY", "SOURCE_OP", "SOURCE"])


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def show(
    path: pathlib.Path | str,
    *,
    schema: schema_mod.Schema | None = None,
    show_all: bool = False,
) -> str:
    """``path`` を読み、種類に応じた表を返す。``schema`` が ``None`` の場合はスキーマ無しとして扱う。

    種類を判別できなければ ``ValueError`` を送出する。
    """
    path = pathlib.Path(path)
    doc = document.parse(path.read_text(encoding="utf-8"))
    if form_mod.classify(doc) == "comp":
        return show_comp(doc, filename=str(path), schema=schema, show_all=show_all)
    return show_setting(doc, filename=str(path), schema=schema, show_all=show_all)


# --------------------------------------------------------------------------
# 値の整形
# --------------------------------------------------------------------------
# 表示専用の整形であり往復を保証しないため、長い値は切り詰める。


def _quote(text: str) -> str:
    text = text.replace("\n", "\\n")
    if len(text) > _MAX_TEXT_LEN:
        text = text[: _MAX_TEXT_LEN - 3] + "..."
    return f'"{text}"'


def _format_node(node: document.Node) -> str:
    if isinstance(node, document.String):
        return _quote(node.value)
    if isinstance(node, document.Number):
        return node.raw
    if isinstance(node, document.Boolean):
        return "true" if node.value else "false"
    if isinstance(node, document.Table):
        return _format_table_compact(node)
    return "-"


def _format_table_compact(table: document.Table) -> str:
    parts: list[str] = []
    for element in table:
        rendered = "{...}" if isinstance(element.value, document.Table) else _format_node(element.value)
        if element.key is not None:
            parts.append(f"{element.key.name}={rendered}")
        else:
            parts.append(rendered)
    text = "{" + ", ".join(parts) + "}"
    if len(text) > _MAX_TEXT_LEN:
        text = text[: _MAX_TEXT_LEN - 3] + "..."
    return text


def _format_value_node(node: document.Node | None) -> str:
    if node is None:
        return "-"
    return _format_node(node)


def _format_any(value: Any) -> str:
    """スキーマの既定値と、``document.Node`` のままの複合値の両方を表示用の文字列へ変換する。"""
    if value is None:
        return "-"
    if isinstance(value, (document.Table, document.Number, document.String, document.Boolean)):
        return _format_node(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _quote(value)
    return str(value)


# --------------------------------------------------------------------------
# .comp: 公開の候補
# --------------------------------------------------------------------------


@dataclasses.dataclass
class _CompRow:

    input: str
    head: bool
    ic_name: str | None
    control_type: str | None
    siblings: list[str]
    data_type: str | None
    value_text: str
    default_text: str
    diff: str  # "same"、"diff"、"unknown"
    expression: str | None
    connection: str | None
    source: str  # "schema" | "user-controls" | "no-schema"


def _is_notable(row: _CompRow) -> bool:
    """既定値との差、式、接続のいずれかを持つかを返す。比較できない値(``unknown``)は変更なしの根拠にならないため含める。"""
    return row.diff != "same" or row.expression is not None or row.connection is not None


def _group_notable_rows(rows: list[_CompRow]) -> list[_CompRow]:
    """自身、または同じ制御の他の入力が注目対象である行を返す。仕様補完: 判定には列挙済みの行だけを使う。"""
    notable_self = {row.input: _is_notable(row) for row in rows}
    return [
        row
        for row in rows
        if notable_self[row.input] or any(notable_self.get(sib, False) for sib in row.siblings)
    ]


def _input_entry(inputs_table: document.Table | None, input_name: str) -> document.Table | None:
    """入力名から、型名が ``Input`` のエントリを返す(``MacroOperator`` の ``Inputs`` は ``InstanceInput`` を持つため)。"""
    if inputs_table is None:
        return None
    entry = inputs_table.get(input_name)
    if isinstance(entry, document.Table) and entry.type_name == "Input":
        return entry
    return None


def _connection_text(entry: document.Table | None) -> str | None:
    if entry is None:
        return None
    source_op = entry.get("SourceOp")
    source = entry.get("Source")
    if isinstance(source_op, document.String) and isinstance(source, document.String):
        return f"{source_op.value}.{source.value}"
    return None


def _expression_text(entry: document.Table | None) -> str | None:
    if entry is None:
        return None
    expr = entry.get("Expression")
    return expr.value if isinstance(expr, document.String) else None


def _diff_marker(value_node: document.Node | None, default_raw: Any) -> str:
    """``.comp`` の現在値とスキーマの既定値を比べる。複合値は ``unknown`` とする。

    スキーマは偽の値(``0``、``False``、``''``)の既定値を持たないため、仕様補完: 既定値が無い場合は現在値が偽であれば一致とみなす。
    """
    if value_node is None:
        return "same"
    if isinstance(value_node, document.Table):
        return "unknown"
    native = document.as_native(value_node)
    if default_raw is None:
        return "same" if not native else "diff"
    return "same" if native == default_raw else "diff"


def _schema_rows(
    g: graph_mod.Graph,
    tool: str,
    type_name: str,
    inputs_table: document.Table | None,
    schema: schema_mod.Schema,
) -> list[_CompRow]:
    """スキーマが種別を知っている場合の候補行。外部から設定できる入力だけを含む。"""
    rows: list[_CompRow] = []
    for input_name in schema.input_names(type_name):
        attrs = schema.input_attributes(type_name, input_name)
        if attrs.external is not True:
            continue
        control = g.control_of(tool, input_name) or [input_name]
        entry = _input_entry(inputs_table, input_name)
        value_node = entry.get("Value") if entry is not None else None
        rows.append(
            _CompRow(
                input=input_name,
                head=control[0] == input_name,
                ic_name=attrs.ic_name,
                control_type=attrs.control_type,
                siblings=[n for n in control if n != input_name],
                data_type=attrs.data_type,
                value_text=_format_value_node(value_node),
                default_text=_format_any(attrs.default),
                diff=_diff_marker(value_node, attrs.default),
                expression=_expression_text(entry),
                connection=_connection_text(entry),
                source="schema",
            )
        )
    return rows


def _fallback_rows(inputs_table: document.Table | None) -> list[_CompRow]:
    """スキーマに種別の入力定義が無い場合の候補行。Fusion は既定値のままの入力を書き出さないため、``Inputs`` の入力をそのまま列挙する。"""
    rows: list[_CompRow] = []
    if inputs_table is None:
        return rows
    for element in inputs_table:
        if element.key is None or not isinstance(element.value, document.Table):
            continue
        entry = element.value
        if entry.type_name != "Input":
            continue
        input_name = element.key.name
        value_node = entry.get("Value")
        rows.append(
            _CompRow(
                input=input_name,
                head=True,
                ic_name=None,
                control_type=None,
                siblings=[],
                data_type=None,
                value_text=_format_value_node(value_node),
                default_text="-",
                diff="unknown",
                expression=_expression_text(entry),
                connection=_connection_text(entry),
                source="no-schema",
            )
        )
    return rows


def _user_control_row(
    g: graph_mod.Graph,
    tool: str,
    input_name: str,
    attrs: graph_mod.UserControlAttributes,
    inputs_table: document.Table | None,
) -> _CompRow:
    """``UserControls`` の入力1つ分の候補行。"""
    control = g.control_of(tool, input_name) or [input_name]
    entry = _input_entry(inputs_table, input_name)
    value_node = entry.get("Value") if entry is not None else None
    return _CompRow(
        input=input_name,
        head=control[0] == input_name,
        # UserControls の LINKS_Name は表示名そのものであり、Name 省略時の名前(IC_NAME)にあたる属性は無い。
        ic_name=None,
        control_type=attrs.control_type,
        siblings=[n for n in control if n != input_name],
        data_type=attrs.data_type,
        value_text=_format_value_node(value_node),
        default_text=_format_any(attrs.default),
        diff=_diff_marker(value_node, attrs.default),
        expression=_expression_text(entry),
        connection=_connection_text(entry),
        source="user-controls",
    )


def _format_comp_row(row: _CompRow) -> str:
    return "\t".join(
        [
            "head" if row.head else "-",
            row.input,
            row.ic_name or "-",
            row.control_type or "-",
            ",".join(row.siblings) if row.siblings else "-",
            row.data_type or "-",
            row.value_text,
            row.default_text,
            row.diff,
            row.expression or "-",
            row.connection or "-",
            row.source,
        ]
    )


def _render_comp_tool(
    name: str,
    type_name: str | None,
    table: document.Table,
    g: graph_mod.Graph,
    schema: schema_mod.Schema | None,
    show_all: bool,
) -> list[str]:
    inputs_table = table.get("Inputs")
    note: str | None = None

    if schema is not None and type_name is not None and schema.has_inputs(type_name):
        schema_rows = _schema_rows(g, name, type_name, inputs_table, schema)
    else:
        if schema is not None and type_name is not None:
            # この種別の入力定義が無い場合は、スキーマが無い場合と同じく値、式、接続を持つ入力だけを列挙する。
            note = f"no schema for type {type_name!r}: listing inputs with a value or expression only"
        schema_rows = _fallback_rows(inputs_table)

    uc_rows = [
        _user_control_row(g, name, input_name, attrs, inputs_table)
        for input_name, attrs in g.user_controls(name).items()
    ]

    out: list[str] = []
    if note:
        out.append(f"({note})")

    if not schema_rows and not uc_rows:
        out.append("(no candidate inputs)")
        return out

    notable = _group_notable_rows(schema_rows)
    printed = schema_rows if show_all else notable
    for row in printed:
        out.append(_format_comp_row(row))
    if not show_all:
        remainder = len(schema_rows) - len(notable)
        if remainder > 0:
            out.append(f"... {remainder} further input(s) unchanged (pass --all to list)")

    for row in uc_rows:
        out.append(_format_comp_row(row))

    return out


def _format_published_input_row(published: macro_mod.PublishedInput) -> str:
    """``PublishedInput`` 1件を ``_SETTING_HEADER`` の列順で1行にする。"""
    return "\t".join(
        [
            published.key,
            published.name or "-",
            published.source_op or "-",
            published.source or "-",
            str(published.control_group) if published.control_group is not None else "-",
            published.page or "-",
            _format_any(published.default),
            published.expression or "-",
        ]
    )


def _render_macro_operator_block(name: str, macro_table: document.Table) -> list[str]:
    """``.comp`` に現れる ``MacroOperator`` 1つ分のブロックを作る。

    ``no-macro`` の修復で平坦化すると ``Inputs`` と ``Outputs`` が失われるため、公開の内容を平坦化の前に読めるよう列挙する。
    """
    macro = macro_mod.read_macro(name, macro_table)

    out: list[str] = []
    if not macro.publish and not macro.outputs:
        out.append("(no Inputs or Outputs found)")
        return out

    if macro.publish:
        out.append(_SETTING_HEADER)
        for published in macro.publish:
            out.append(_format_published_input_row(published))

    if macro.outputs:
        out.append(_MACRO_OUTPUTS_HEADER)
        for output in macro.outputs:
            out.append("\t".join([output.key, output.source_op or "-", output.source or "-"]))

    return out


def show_comp(
    doc: document.Document,
    *,
    filename: str,
    schema: schema_mod.Schema | None,
    show_all: bool = False,
) -> str:
    """``.comp`` の公開候補の表を返す。

    既定値との差、式、接続を持つ入力と同じ制御の入力を先に並べ、それ以外は件数だけを示す(``show_all`` で全て列挙する)。
    """
    lines = [f"candidates for publishing (.comp): {filename}"]
    tools_table = doc.root.get("Tools")
    if tools_table is None:
        lines.append("(no Tools table found)")
        return "\n".join(lines) + "\n"

    if schema is None:
        lines.append("no schema loaded: listing inputs with a value or expression only")

    # schema を渡さないと、スキーマ由来の入力が制御を判定できず単独の制御となる。
    g = graph_mod.build_graph(tools_table, schema)

    lines.append(_COMP_HEADER)
    for name, table in graph_mod.iter_tools(tools_table):
        type_name = table.type_name
        lines.append(f"== {name} ({type_name or '?'}) ==")
        if type_name == "MacroOperator":
            lines.extend(_render_macro_operator_block(name, table))
        else:
            lines.extend(_render_comp_tool(name, type_name, table, g, schema, show_all))

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# .setting: 公開の結果
# --------------------------------------------------------------------------


def show_setting(
    doc: document.Document,
    *,
    filename: str,
    schema: schema_mod.Schema | None = None,
    show_all: bool = False,
) -> str:
    """``.setting`` の表を、形に応じて返す。種類を判別できなければ ``ValueError`` を送出する。"""
    kind = form_mod.classify(doc)
    if kind == "comp":
        raise ValueError("not a .setting document (root type is 'Composition')")
    if kind == "tool":
        return _show_tool_setting(doc, filename=filename, schema=schema, show_all=show_all)
    return _show_macro_setting(doc, filename=filename)


def _show_tool_setting(
    doc: document.Document,
    *,
    filename: str,
    schema: schema_mod.Schema | None,
    show_all: bool,
) -> str:
    """tool 形の ``.setting`` が保存するツールの入力を、``.comp`` の公開候補と同じ列で返す。"""
    lines = [f"inputs of the saved tool (tool-form .setting): {filename}"]
    if schema is None:
        lines.append("no schema loaded: listing inputs with a value or expression only")
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    top = form_mod.top_tool(doc)
    g = graph_mod.build_graph(tools_table, schema)
    lines.append(_COMP_HEADER)
    lines.append(f"== {top.name} ({top.table.type_name or '?'}) ==")
    lines.extend(_render_comp_tool(top.name, top.table.type_name, top.table, g, schema, show_all))
    return "\n".join(lines) + "\n"


def _show_macro_setting(doc: document.Document, *, filename: str) -> str:
    """macro 形の ``.setting`` の公開結果の表を返す。``InstanceInput`` 自体の式も値を決めるため列に含める。"""
    lines = [f"published inputs (.setting): {filename}"]
    top = form_mod.top_tool(doc)
    macro = macro_mod.read_macro(top.name, top.table)

    lines.append(_SETTING_HEADER)
    lines.append(f"== {macro.name} ==")
    for published in macro.publish:
        lines.append(_format_published_input_row(published))

    return "\n".join(lines) + "\n"
