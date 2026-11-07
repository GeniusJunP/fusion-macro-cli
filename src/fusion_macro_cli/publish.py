"""公開の宣言の読み込みと、``.setting`` の組み立て。

宣言は ``.setting`` の形(:mod:`fusion_macro_cli.form`)を ``form`` に持ち、形ごとに ``.comp`` が持たない情報だけを持つ。

- macro 形: マクロ名、公開する入力とその並びと表示名、マクロの出力。``.comp`` の接続、値、式、
  ``UserControls`` はそのまま内側に入れる。
- tool 形: 保存するツールの名前とファイル名。ツールは ``.comp`` の名前と内容のまま保存する。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import tomllib
from collections import Counter
from typing import Any, ClassVar, Final

from fusion_macro_cli import document
from fusion_macro_cli import form as form_mod
from fusion_macro_cli import graph as graph_mod
from fusion_macro_cli import macro as macro_mod
from fusion_macro_cli import schema as schema_mod

__all__ = [
    "DeclarationError",
    "PublishEntry",
    "OutputEntry",
    "MacroDeclaration",
    "ToolDeclaration",
    "Declaration",
    "load_declaration",
    "load_comp",
    "build_setting",
    "declare",
    "declaration_to_toml",
    "DECLARATION_FORMAT",
]

#: ``apply --help`` の epilog に載せる、宣言(TOML)の書式。
DECLARATION_FORMAT: str = """\
Publish declaration (TOML) format:

  name              (required) output file name without the extension;
                     in the macro form, also the MacroOperator's name
  source            (required) path to the source .comp, relative to
                     this declaration file
  form              (optional, default "macro") form of the .setting:
                     "macro"  wrap the .comp's tools in a MacroOperator
                              that publishes the inputs listed below
                     "tool"   save one tool of the .comp as it is; the
                              inspector shows all of its inputs

Macro form ("macro"):

  [[output]]         (required, at least one) a macro output
    tool              (required) tool name
    port              (optional, default "Output") output port name

  [[publish]]         published input, in inspector order
    tool              (required) tool name
    input             (required) input name
    label             (optional) display name; omit to leave the
                       InstanceInput without a Name, so Fusion shows the
                       schema's own control name instead
    page              (optional, default "Controls")
    expression        (optional) expression carried by the InstanceInput
                       itself, evaluated by the MacroOperator; distinct
                       from an expression on the inner tool's input

Tool form ("tool"):

  tool              (required) name of the .comp's top-level tool to
                     save; the tool keeps this name
  [[output]] and [[publish]] are errors in the tool form.

Example (macro form):

  name = "title_blue"
  source = "0003_title.comp"
  form = "macro"

  [[output]]
  tool = "Merge1"

  [[publish]]
  tool = "Template"
  input = "StyledText"
  label = "Text"

  [[publish]]
  tool = "Template"
  input = "Red1Clone"
  label = "Color"
  expression = "iif(ColorSelect == 2, CustomColorR, iif(ColorSelect == 0, 0xE4, 0x41) / 255)"

Example (tool form):

  name = "01 Blue Subtitle"
  source = "0001_01 Blue Subtitle.comp"
  form = "tool"
  tool = "Template"
"""


class DeclarationError(ValueError):
    """宣言の検証に失敗したことを表す。メッセージは何番目の要素かを含む。"""


# --------------------------------------------------------------------------
# 宣言のデータモデル
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PublishEntry:
    """``[[publish]]`` の1要素。

    ``label`` が ``None`` の場合は ``Name`` を書かず、Fusion がスキーマの制御名を表示する(制御の続きの入力は名前を持たない)。
    """

    tool: str
    input: str
    label: str | None = None
    page: str = "Controls"
    expression: str | None = None


@dataclasses.dataclass(frozen=True)
class OutputEntry:
    """``[[output]]`` の1要素。``InstanceOutput`` は表示名を持たない。"""

    tool: str
    port: str = "Output"


@dataclasses.dataclass(frozen=True)
class MacroDeclaration:
    """macro 形の宣言1つ分。``source`` は宣言ファイルからの相対パスである。

    ``output`` の並び順に ``MainOutput1``、``MainOutput2`` のキーを付ける。
    """

    form: ClassVar[form_mod.SettingForm] = "macro"

    name: str
    source: str
    output: tuple[OutputEntry, ...]
    publish: tuple[PublishEntry, ...]


@dataclasses.dataclass(frozen=True)
class ToolDeclaration:
    """tool 形の宣言1つ分。``tool`` は ``.comp`` の最上位のツールの名前であり、そのまま保存する。"""

    form: ClassVar[form_mod.SettingForm] = "tool"

    name: str
    source: str
    tool: str


#: 宣言。形ごとに持つ情報が異なるため、形ごとの型の和とする。
Declaration = MacroDeclaration | ToolDeclaration


# --------------------------------------------------------------------------
# TOML の読み込み
# --------------------------------------------------------------------------


def _require_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise DeclarationError(f"{where} is missing required string field {key!r}")
    return value


def load_declaration(path: pathlib.Path | str) -> Declaration:
    """宣言(TOML)を読み、キーの構造を検証する。ツールと入力の実在は :func:`build_setting` が検証する。"""
    path = pathlib.Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)

    name = _require_str(data, "name", "declaration")
    source = _require_str(data, "source", "declaration")
    # 仕様補完: form を省略した宣言は macro 形とする。
    form = data.get("form", "macro")
    if form not in form_mod.SETTING_FORMS:
        raise DeclarationError(
            f"declaration field 'form' must be one of {', '.join(map(repr, form_mod.SETTING_FORMS))} (got {form!r})"
        )
    if form == "tool":
        return _load_tool_declaration(data, name, source)
    return _load_macro_declaration(data, name, source)


def _load_tool_declaration(data: dict[str, Any], name: str, source: str) -> ToolDeclaration:
    # tool 形は公開する入力と出力を持たないため、書かれていれば無視せずにエラーとする。
    for key in ("output", "publish"):
        if key in data:
            raise DeclarationError(
                f"declaration field {key!r} is not allowed with form = \"tool\"; "
                "a tool-form .setting has no MacroOperator to publish inputs or outputs"
            )
    tool = _require_str(data, "tool", "declaration")
    return ToolDeclaration(name=name, source=source, tool=tool)


def _load_macro_declaration(data: dict[str, Any], name: str, source: str) -> MacroDeclaration:
    if "tool" in data:
        raise DeclarationError(
            "declaration field 'tool' is only allowed with form = \"tool\"; "
            "the macro form names tools in [[output]] and [[publish]]"
        )
    raw_outputs = data.get("output", [])
    if not isinstance(raw_outputs, list):
        raise DeclarationError("declaration field 'output' must be an array of tables")
    if not raw_outputs:
        raise DeclarationError("declaration must have at least one [[output]] entry")

    outputs: list[OutputEntry] = []
    for index, raw in enumerate(raw_outputs, start=1):
        where = f"output[{index}]"
        if not isinstance(raw, dict):
            raise DeclarationError(f"{where} must be a table")
        tool = _require_str(raw, "tool", where)
        port = raw.get("port", "Output")
        if not isinstance(port, str) or not port:
            raise DeclarationError(f"{where} field 'port' must be a non-empty string")
        outputs.append(OutputEntry(tool=tool, port=port))

    raw_entries = data.get("publish", [])
    if not isinstance(raw_entries, list):
        raise DeclarationError("declaration field 'publish' must be an array of tables")

    entries: list[PublishEntry] = []
    for index, raw in enumerate(raw_entries, start=1):
        where = f"publish[{index}]"
        if not isinstance(raw, dict):
            raise DeclarationError(f"{where} must be a table")
        tool = _require_str(raw, "tool", where)
        input_name = _require_str(raw, "input", where)
        label = raw.get("label")
        if label is not None and not isinstance(label, str):
            raise DeclarationError(f"{where} field 'label' must be a string")
        page = raw.get("page", "Controls")
        if not isinstance(page, str) or not page:
            raise DeclarationError(f"{where} field 'page' must be a non-empty string")
        expression = raw.get("expression")
        if expression is not None and not isinstance(expression, str):
            raise DeclarationError(f"{where} field 'expression' must be a string")
        entries.append(
            PublishEntry(tool=tool, input=input_name, label=label, page=page, expression=expression)
        )

    return MacroDeclaration(name=name, source=source, output=tuple(outputs), publish=tuple(entries))


def load_comp(declaration: Declaration, declaration_path: pathlib.Path | str) -> document.Document:
    """宣言の ``source`` が指す ``.comp`` を、宣言ファイルからの相対パスとして読む。"""
    declaration_path = pathlib.Path(declaration_path)
    comp_path = declaration_path.parent / declaration.source
    text = comp_path.read_text(encoding="utf-8")
    return document.parse(text)


# --------------------------------------------------------------------------
# .comp 側の探索
# --------------------------------------------------------------------------


_IDENT_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _key_for(name: str) -> document.Key:
    """名前をキーへ変換する。Lua の識別子でない名前(日本語を含むマクロ名)は ``["..."]`` 形にする。"""
    if _IDENT_RE.match(name):
        return document.Key("ident", name)
    return document.Key("string", document.encode_string(name))


def _string_node(value: str) -> document.String:
    return document.String(document.encode_string(value))


def _current_value_node(tool_table: document.Table, input_name: str) -> document.Node | None:
    """``.comp`` の ``Inputs`` に明示された ``Value`` を ``Default`` 用に返す。

    値が無ければ Fusion が既定値を補うため、スキーマや ``UserControls`` の既定値へは代替しない。
    仕様補完: 複合値(テーブル)は単一の ``Default`` で表せないため ``None`` を返す。
    """
    inputs_table = tool_table.get("Inputs")
    if inputs_table is None:
        return None
    entry = inputs_table.get(input_name)
    if not isinstance(entry, document.Table):
        return None
    value_node = entry.get("Value")
    if value_node is None or isinstance(value_node, document.Table):
        return None
    return value_node


# --------------------------------------------------------------------------
# ControlGroup の割り当て
# --------------------------------------------------------------------------


def _control_key(
    g: graph_mod.Graph | None,
    entry: PublishEntry,
    index: int,
) -> tuple:
    """入力が属する制御の比較用のキーを返す。制御を判定できない入力は単独の制御として扱う。"""
    control = g.control_of(entry.tool, entry.input) if g is not None else None
    if control is None:
        return ("standalone", index)
    return (entry.tool, tuple(control))


def _assign_control_groups(
    g: graph_mod.Graph | None, entries: tuple[PublishEntry, ...]
) -> list[int | None]:
    """同じ制御の入力を複数公開している場合に限り、その制御の出現順に 1 から ``ControlGroup`` を付ける。"""
    keys = [_control_key(g, entry, i) for i, entry in enumerate(entries)]
    counts = Counter(keys)
    assigned: dict[tuple, int] = {}
    next_number = 1
    numbers: list[int | None] = []
    for key in keys:
        if counts[key] < 2:
            numbers.append(None)
            continue
        if key not in assigned:
            assigned[key] = next_number
            next_number += 1
        numbers.append(assigned[key])
    return numbers


# --------------------------------------------------------------------------
# 組み立て(apply)
# --------------------------------------------------------------------------


def build_setting(
    declaration: Declaration,
    comp: document.Document,
    schema: schema_mod.Schema | None = None,
) -> document.Document:
    """宣言と ``.comp`` から、宣言の形の ``.setting`` を組み立てる。

    宣言が指すツール、入力、出力が ``.comp`` に無い場合は :class:`DeclarationError` を送出する。
    """
    comp_tools = comp.root.get("Tools")
    if comp_tools is None or not isinstance(comp_tools, document.Table):
        raise DeclarationError("source .comp has no Tools table")
    if isinstance(declaration, ToolDeclaration):
        root = _build_tool_root(declaration, comp_tools)
    else:
        root = _build_macro_root(declaration, comp_tools, schema)
    # 仕様補完: 字下げは .comp から引き継ぎ、末尾の改行は常に付ける。
    built = document.Document(root=root, indent=comp.indent, trailing_newline=True)
    return document.format_compact(built)


def _build_tool_root(declaration: ToolDeclaration, comp_tools: document.Table) -> document.Table:
    """``.comp`` の最上位のツールのうち ``tool`` を1つだけ、名前と内容を変えずに保存する。

    仕様補完: ``.comp`` が複数のツールを持つ場合もエラーにせず、指すツールだけを保存する。
    他のツールへの接続は参照切れになるが、組み立ての後の検査(``no-dangling``)が報告するため、ここでは判定しない。
    Resolve が保存する tool 形は ``ActiveTool`` を持たないため、書かない。
    """
    element = next(
        (
            el
            for el in comp_tools.elements
            if el.key is not None and el.key.name == declaration.tool and isinstance(el.value, document.Table)
        ),
        None,
    )
    if element is None:
        raise DeclarationError(f"tool {declaration.tool!r} does not exist in the source .comp")
    assert element.key is not None and isinstance(element.value, document.Table)
    if element.value.type_name == "MacroOperator":
        raise DeclarationError(
            f"tool {declaration.tool!r} is a MacroOperator; saving it as it is gives a macro-form "
            "file whose inputs and outputs no declaration describes. Declare the macro form instead"
        )
    # 末尾のカンマは、Resolve が保存する tool 形の書式に合わせる。
    tools_table = document.Table(
        "ordered()", [document.Element(element.key, element.value)], trailing_comma=True
    )
    return document.Table(
        None, [document.Element(document.Key("ident", "Tools"), tools_table)], trailing_comma=True
    )


def _build_macro_root(
    declaration: MacroDeclaration,
    comp_tools: document.Table,
    schema: schema_mod.Schema | None,
) -> document.Table:
    """``.comp`` のツールを ``MacroOperator`` で包み、``[[publish]]`` に書いた入力だけを公開する。"""
    g = graph_mod.try_build_graph(comp_tools, schema)

    for index, entry in enumerate(declaration.publish, start=1):
        where = f"publish[{index}]"
        tool_table = g.tool_table(entry.tool) if g is not None else None
        if tool_table is None:
            raise DeclarationError(f"{where}: tool {entry.tool!r} does not exist in the source .comp")
        known = g.known_inputs(entry.tool) if g is not None else None
        # known が None の種別は入力の実在を判定できないため、エラーにしない。
        if known is not None and entry.input not in known:
            raise DeclarationError(
                f"{where}: input {entry.input!r} of tool {entry.tool!r} does not exist"
            )

    if not declaration.output:
        raise DeclarationError("declaration must have at least one [[output]] entry")
    for index, output_entry in enumerate(declaration.output, start=1):
        output_tool = g.tool_table(output_entry.tool) if g is not None else None
        if output_tool is None:
            raise DeclarationError(
                f"output[{index}]: tool {output_entry.tool!r} does not exist in the source .comp"
            )
        # ports が None の種別は出力ポートを判定できないため、エラーにしない。
        ports = g.output_ports(output_entry.tool) if g is not None else None
        if ports is not None and output_entry.port not in ports:
            raise DeclarationError(
                f"output[{index}]: port {output_entry.port!r} does not exist on tool "
                f"{output_entry.tool!r}"
            )

    control_groups = _assign_control_groups(g, declaration.publish)

    input_elements: list[document.Element] = []
    for entry, control_group in zip(declaration.publish, control_groups):
        tool_table = g.tool_table(entry.tool) if g is not None else None
        assert tool_table is not None

        fields: list[document.Element] = [
            document.Element(document.Key("ident", "SourceOp"), _string_node(entry.tool)),
            document.Element(document.Key("ident", "Source"), _string_node(entry.input)),
        ]
        if entry.expression is not None:
            fields.append(
                document.Element(document.Key("ident", "Expression"), _string_node(entry.expression))
            )
        fields.append(document.Element(document.Key("ident", "Page"), _string_node(entry.page)))
        if entry.label is not None:
            fields.append(document.Element(document.Key("ident", "Name"), _string_node(entry.label)))
        if control_group is not None:
            fields.append(
                document.Element(document.Key("ident", "ControlGroup"), document.Number(str(control_group)))
            )
        # 式が値を決め Default は使われないため、expression を持つ公開入力には Default を書かない。
        if entry.expression is None:
            default_node = _current_value_node(tool_table, entry.input)
            if default_node is not None:
                fields.append(document.Element(document.Key("ident", "Default"), default_node))

        key_name = f"{entry.tool}_{entry.input}"
        input_elements.append(
            document.Element(_key_for(key_name), document.Table("InstanceInput", fields))
        )

    inputs_table = document.Table("ordered()", input_elements)
    output_elements = [
        document.Element(
            document.Key("ident", f"MainOutput{i}"),
            document.Table(
                "InstanceOutput",
                [
                    document.Element(document.Key("ident", "SourceOp"), _string_node(output_entry.tool)),
                    document.Element(document.Key("ident", "Source"), _string_node(output_entry.port)),
                ],
            ),
        )
        for i, output_entry in enumerate(declaration.output, start=1)
    ]
    outputs_table = document.Table(None, output_elements)
    # MacroOperator の内側の Tools は ordered() を伴い、.comp の最上位の Tools は伴わない。
    inner_tools_table = document.Table("ordered()", list(comp_tools.elements))

    macro_table = document.Table(
        "MacroOperator",
        [
            document.Element(document.Key("ident", "Inputs"), inputs_table),
            document.Element(document.Key("ident", "Outputs"), outputs_table),
            document.Element(document.Key("ident", "Tools"), inner_tools_table),
        ],
    )

    root = document.Table(
        None,
        [
            document.Element(
                document.Key("ident", "Tools"),
                document.Table("ordered()", [document.Element(_key_for(declaration.name), macro_table)]),
            ),
            document.Element(document.Key("ident", "ActiveTool"), _string_node(declaration.name)),
        ],
    )
    return root


# --------------------------------------------------------------------------
# apply の逆写像
# --------------------------------------------------------------------------


def declare(setting: document.Document, *, file_stem: str) -> tuple[Declaration, document.Table]:
    """既存の ``.setting`` から、その形の宣言を起こす。:func:`build_setting` の逆写像である。

    戻り値は ``(宣言, .comp の Tools にあたる表)`` であり、後者は ``build_setting`` の ``comp`` の ``Tools`` の代わりに使える。
    ``name`` は、macro 形ではマクロ名、tool 形では ``file_stem`` とする。tool 形の文書はツール名しか持たず、
    ツール名(多くは ``Template``)はファイルごとに一意でないためである。
    ``.setting`` は元の ``.comp`` のパスを持たないため、``source`` は空文字列とする。
    """
    try:
        kind = form_mod.classify(setting)
    except ValueError as exc:
        raise DeclarationError(str(exc)) from exc
    if kind == "comp":
        raise DeclarationError("not a .setting document (root type is 'Composition')")
    top = form_mod.top_tool(setting)
    if kind == "tool":
        # 仕様補完: tool 形の宣言の name はファイル名から取る。
        tools = document.Table(None, [document.Element(_key_for(top.name), top.table)])
        return ToolDeclaration(name=file_stem, source="", tool=top.name), tools
    return _declare_macro(top)


def _declare_macro(top: form_mod.TopTool) -> tuple[MacroDeclaration, document.Table]:
    """``Outputs`` は同じ出力を指す複数の ``InstanceOutput`` もまとめずに起こす。"""
    macro = macro_mod.read_macro(top.name, top.table)

    inner_tools = top.table.get("Tools")
    if inner_tools is None or not isinstance(inner_tools, document.Table):
        raise DeclarationError(f"macro {macro.name!r} has no inner Tools table")

    if not macro.outputs:
        raise DeclarationError(f"macro {macro.name!r} has no Outputs")
    outputs: list[OutputEntry] = []
    for output in macro.outputs:
        if not output.source_op or not output.source:
            raise DeclarationError(
                f"macro {macro.name!r} output {output.key!r} has no SourceOp/Source"
            )
        outputs.append(OutputEntry(tool=output.source_op, port=output.source))

    entries: list[PublishEntry] = []
    for published in macro.publish:
        if not published.source_op or not published.source:
            raise DeclarationError(
                f"macro {macro.name!r} published input {published.key!r} has no SourceOp/Source"
            )
        page = published.page or "Controls"
        entries.append(
            PublishEntry(
                tool=published.source_op,
                input=published.source,
                label=published.name,
                page=page,
                expression=published.expression,
            )
        )

    declaration = MacroDeclaration(name=macro.name, source="", output=tuple(outputs), publish=tuple(entries))
    return declaration, inner_tools


def _toml_string(value: str) -> str:
    """TOML の基本文字列として書く。宣言の文字列は改行を含まないため、``"`` と ``\\`` だけを逃がす。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def declaration_to_toml(declaration: Declaration, source: str) -> str:
    """宣言を TOML へ書き出す。``form`` は省略できるが、形を読み取れるよう常に書く。既定値と同じ ``port``、``page``、``label`` は書かない。"""
    lines = [
        f"name = {_toml_string(declaration.name)}",
        f"source = {_toml_string(source)}",
        f"form = {_toml_string(declaration.form)}",
    ]
    if isinstance(declaration, ToolDeclaration):
        lines.append(f"tool = {_toml_string(declaration.tool)}")
        return "\n".join(lines) + "\n"
    lines.append("")
    for output in declaration.output:
        lines.append("[[output]]")
        lines.append(f"tool = {_toml_string(output.tool)}")
        if output.port != "Output":
            lines.append(f"port = {_toml_string(output.port)}")
        lines.append("")
    for entry in declaration.publish:
        lines.append("[[publish]]")
        lines.append(f"tool = {_toml_string(entry.tool)}")
        lines.append(f"input = {_toml_string(entry.input)}")
        if entry.label is not None:
            lines.append(f"label = {_toml_string(entry.label)}")
        if entry.page is not None and entry.page != "Controls":
            lines.append(f"page = {_toml_string(entry.page)}")
        if entry.expression is not None:
            lines.append(f"expression = {_toml_string(entry.expression)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
