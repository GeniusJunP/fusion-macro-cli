"""参照グラフの構築と問い合わせ。

ツールを頂点とし、接続(``SourceOp`` と ``Source``)と式(``Expression``)を辺とする。
辺の型はスキーマの ``INPS_DataType`` から決め、スキーマに無い入力では ``UserControls`` の ``LINKID_DataType`` を使う。
``InstanceInput`` 自体が持つ式は内側の入力を駆動しないため、辺に含めない。
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any, Iterable, Iterator

from fusion_macro_cli import document
from fusion_macro_cli import expression as expression_mod
from fusion_macro_cli import schema as schema_mod

__all__ = [
    "Target",
    "Edge",
    "DrivenInput",
    "UserControlAttributes",
    "Graph",
    "DuplicateToolNameError",
    "iter_tool_elements",
    "iter_tools",
    "build_graph",
    "try_build_graph",
    "keyframe_range",
]


class DuplicateToolNameError(ValueError):
    """同じツール名が文書内に複数回現れたことを表す。後勝ちで上書きするとグラフが文書の構造からずれるため送出する。"""


@dataclasses.dataclass(frozen=True)
class Target:
    """入力が指す先の ``(ツール名, ポート名)``。

    ``port`` は接続では出力名、式では入力名(``Tool.Output`` の形では出力名)である。
    """

    tool: str
    port: str


@dataclasses.dataclass(frozen=True)
class Edge:
    """参照グラフの1つのエッジ。``kind`` は ``"connection"`` または ``"expression"`` であり、式のエッジは参照ごとに1つとなる。"""

    kind: str
    tool: str
    input: str
    target: Target
    data_type: str | None
    expression: str | None


@dataclasses.dataclass(frozen=True)
class DrivenInput:
    """``Expression`` を持つ入力。参照を解決できたかに関係なく該当する。"""

    tool: str
    input: str
    expression: str


# --------------------------------------------------------------------------
# UserControls
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class UserControlAttributes:
    """``UserControls`` に定義された入力1つ分の属性。

    名前付きの属性に含めない属性(``INP_Integer``、``INP_MinScale`` など)も、``raw`` に全件を保持する。
    ``choices`` はコンボの選択肢(位置引数の要素の ``CCS_AddString``)を宣言順に並べたものである。
    """

    data_type: str | None
    control_type: str | None
    external: bool | None
    passive: bool | None
    name: str | None
    page: str | None
    default: Any
    control_group: int | None
    has_control_id: bool
    choices: list[str]
    raw: dict[str, Any]


# --------------------------------------------------------------------------
# UserControls の読み出し
# --------------------------------------------------------------------------


def _read_user_control(entry: document.Table) -> UserControlAttributes:
    choices: list[str] = []
    raw: dict[str, Any] = {}
    for element in entry:
        if element.key is None:
            if isinstance(element.value, document.Table):
                label = element.value.get("CCS_AddString")
                if isinstance(label, document.String):
                    choices.append(label.value)
            continue
        raw[element.key.name] = document.as_native(element.value)

    return UserControlAttributes(
        data_type=document.as_str(entry.get("LINKID_DataType")),
        control_type=document.as_str(entry.get("INPID_InputControl")),
        external=document.as_bool(entry.get("INP_External")),
        passive=document.as_bool(entry.get("INP_Passive")),
        name=document.as_str(entry.get("LINKS_Name")),
        page=document.as_str(entry.get("ICS_ControlPage")),
        default=document.as_native(entry.get("INP_Default")),
        control_group=document.as_int(entry.get("IC_ControlGroup")),
        has_control_id=entry.get("IC_ControlID") is not None,
        choices=choices,
        raw=raw,
    )


# --------------------------------------------------------------------------
# 辺の型の決定
# --------------------------------------------------------------------------


def _resolve_data_type(
    schema: schema_mod.Schema | None,
    owner_type: str | None,
    owner_table: document.Table,
    input_name: str,
) -> str | None:
    if schema is not None and owner_type is not None and schema.has_inputs(owner_type):
        try:
            attributes = schema.input_attributes(owner_type, input_name)
        except KeyError:
            attributes = None
        if attributes is not None and attributes.data_type is not None:
            return attributes.data_type

    user_controls = owner_table.get("UserControls")
    if user_controls is not None:
        entry = user_controls.get(input_name)
        if isinstance(entry, document.Table):
            data_type = entry.get("LINKID_DataType")
            if isinstance(data_type, document.String):
                return data_type.value

    return None


# --------------------------------------------------------------------------
# グラフ
# --------------------------------------------------------------------------


class Graph:
    """1つの平坦なツール名前空間上の参照グラフ。ノードはマクロの内側を含む全ツールである。"""

    def __init__(
        self,
        tool_types: dict[str, str | None],
        tool_tables: dict[str, document.Table],
        edges: list[Edge],
        driven: list[DrivenInput],
        schema: schema_mod.Schema | None = None,
    ) -> None:
        self._tool_types = tool_types
        self._tool_tables = tool_tables
        self._edges = edges
        self._driven = driven
        self._schema = schema
        self._connections_from: dict[str, list[Edge]] = defaultdict(list)
        for edge in edges:
            if edge.kind == "connection":
                self._connections_from[edge.tool].append(edge)

    def tools(self) -> list[str]:
        return list(self._tool_types)

    def tool_type(self, tool: str) -> str | None:
        return self._tool_types.get(tool)

    def edges(self) -> list[Edge]:
        return list(self._edges)

    def edges_within(self, tools: Iterable[str]) -> list[Edge]:
        """参照元が ``tools`` に属する辺に絞る。"""
        names = frozenset(tools)
        return [edge for edge in self._edges if edge.tool in names]

    def dangling_edges(self) -> list[Edge]:
        """参照先のツールがこのグラフに存在しない辺を返す。"""
        return [edge for edge in self._edges if edge.target.tool not in self._tool_types]

    def reachable_from(self, tool: str, data_type: str | None = None) -> set[str]:
        """``tool`` から接続の辺を遡って到達できるツールの集合を返す。``tool`` 自身は含めない。

        式による値の参照は画像の接続ではないため、接続のエッジだけを辿る。``data_type`` を指定するとその型のエッジだけを辿る。
        """
        seen: set[str] = set()
        stack = [tool]
        while stack:
            current = stack.pop()
            for edge in self._connections_from.get(current, ()):
                if data_type is not None and edge.data_type != data_type:
                    continue
                target = edge.target.tool
                if target in seen or target == tool:
                    continue
                seen.add(target)
                stack.append(target)
        return seen

    def driven_inputs(self, tools: Iterable[str] | None = None) -> list[DrivenInput]:
        """式で駆動される入力を返す。``tools`` を指定するとそのツールのものに絞る。"""
        if tools is None:
            return list(self._driven)
        names = frozenset(tools)
        return [driven for driven in self._driven if driven.tool in names]

    def tool_table(self, tool: str) -> document.Table | None:
        """ツール名(マクロ名を含む)から、そのツールの生のテーブルを返す。"""
        return self._tool_tables.get(tool)

    def user_controls(self, tool: str) -> dict[str, UserControlAttributes]:
        """ツールが ``UserControls`` として持つ入力を宣言順に読み出す。"""
        table = self.tool_table(tool)
        if table is None:
            return {}
        user_controls = table.get("UserControls")
        if user_controls is None:
            return {}
        result: dict[str, UserControlAttributes] = {}
        for element in user_controls:
            if element.key is None or not isinstance(element.value, document.Table):
                continue
            result[element.key.name] = _read_user_control(element.value)
        return result

    def output_ports(self, tool: str) -> dict[str, str | None] | None:
        """出力名から値の型への対応を返す。ツールまたはスキーマの出力定義が無い場合は ``None`` を返す。"""
        table = self.tool_table(tool)
        if table is None:
            return None
        if self._schema is None or table.type_name is None:
            return None
        names = self._schema.output_names(table.type_name)
        if names is None:
            return None
        return {name: self._schema.output_type(table.type_name, name) for name in names}

    def known_inputs(self, tool: str) -> set[str] | None:
        """スキーマの入力と ``UserControls`` の和を返す。

        ``.comp`` の ``Inputs`` には既定値の入力が書かれないため、どちらの定義も無いツールでは実在を判定できず ``None`` を返す。
        """
        table = self.tool_table(tool)
        type_name = table.type_name if table is not None else None
        has_schema_definition = (
            self._schema is not None and type_name is not None and self._schema.has_inputs(type_name)
        )
        has_user_controls = table is not None and table.get("UserControls") is not None
        if not has_schema_definition and not has_user_controls:
            return None
        known: set[str] = set()
        if has_schema_definition:
            known |= set(self._schema.input_names(type_name))  # type: ignore[union-attr]
        known |= set(self.user_controls(tool).keys())
        return known

    def _schema_type_defining(self, tool: str, input_name: str) -> str | None:
        """スキーマが ``tool`` の種別の入力として ``input_name`` を定義していれば、その種別名を返す。"""
        type_name = self.tool_type(tool)
        if self._schema is None or type_name is None or not self._schema.has_inputs(type_name):
            return None
        return type_name if input_name in self._schema.input_names(type_name) else None

    def control_of(self, tool: str, input_name: str) -> list[str] | None:
        """入力が属する制御を、制御を構成する入力名の列として返す。どちらの定義にも無い入力では ``None`` を返す。

        ``UserControls`` の ``IC_ControlGroup`` はツールが自身で付けた番号であり、スキーマと異なり値で区切ってよい。
        """
        type_name = self._schema_type_defining(tool, input_name)
        if type_name is not None:
            return self._schema.control_of(type_name, input_name)  # type: ignore[union-attr]

        user_controls = self.user_controls(tool)
        attrs = user_controls.get(input_name)
        if attrs is None:
            return None
        if attrs.control_group is None:
            return [input_name]
        members = [
            name for name, other in user_controls.items() if other.control_group == attrs.control_group
        ]
        members.sort(key=lambda name: _user_control_id(user_controls[name]))
        return members

    def control_type(self, tool: str, input_name: str) -> str | None:
        """入力の制御の種類を返す。情報源の優先順位は :meth:`control_of` と同一である。"""
        type_name = self._schema_type_defining(tool, input_name)
        if type_name is not None:
            return self._schema.input_attributes(type_name, input_name).control_type  # type: ignore[union-attr]
        attrs = self.user_controls(tool).get(input_name)
        return attrs.control_type if attrs is not None else None


def _user_control_id(attrs: UserControlAttributes) -> float:
    """``IC_ControlID`` の値を返す。仕様補完: 値が無い入力は末尾へ並べる。"""
    value = attrs.raw.get("IC_ControlID")
    return float(value) if isinstance(value, (int, float)) else float("inf")


def iter_tool_elements(
    tools_table: document.Table,
) -> Iterator[tuple[document.Table, document.Element]]:
    """``Tools`` のツール要素を、``MacroOperator`` の内側へ再帰しながら ``(直接保持する表, 要素)`` の組で列挙する。

    ツール名を付け替える修復手続きが、差し替え対象の表を特定するために使う。
    """
    for element in tools_table:
        if element.key is None or not isinstance(element.value, document.Table):
            continue
        yield tools_table, element
        if element.value.type_name == "MacroOperator":
            inner = element.value.get("Tools")
            if inner is not None:
                yield from iter_tool_elements(inner)


def iter_tools(tools_table: document.Table) -> Iterator[tuple[str, document.Table]]:
    """``Tools`` のツールを、``MacroOperator`` の内側へ再帰しながら ``(名前, テーブル)`` で列挙する。"""
    for _, element in iter_tool_elements(tools_table):
        yield element.key.name, element.value  # type: ignore[union-attr]


def build_graph(
    tools_table: document.Table, schema: schema_mod.Schema | None = None
) -> Graph:
    """``Tools`` テーブルから参照グラフを作る。

    マクロの外のツール(``MediaOut1``)がマクロの内側のツールを直接指す接続があるため、
    内側の ``Tools`` も再帰的に読み、1つの平坦なグラフとする。
    """
    tool_types: dict[str, str | None] = {}
    tool_tables: dict[str, document.Table] = {}
    edges: list[Edge] = []
    driven: list[DrivenInput] = []

    for _, element in iter_tool_elements(tools_table):
        name = element.key.name  # type: ignore[union-attr]
        tool_table = element.value
        if not isinstance(tool_table, document.Table):
            continue
        if name in tool_types:
            raise DuplicateToolNameError(f"duplicate tool name: {name!r}")
        tool_types[name] = tool_table.type_name
        tool_tables[name] = tool_table

        inputs = tool_table.get("Inputs")
        if inputs is not None:
            for input_element in inputs:
                if input_element.key is None:
                    continue
                input_value = input_element.value
                if (
                    not isinstance(input_value, document.Table)
                    or input_value.type_name != "Input"
                ):
                    continue
                _collect_input_edges(
                    name,
                    tool_table,
                    input_element.key.name,
                    input_value,
                    schema,
                    edges,
                    driven,
                )

    return Graph(tool_types, tool_tables, edges, driven, schema)


def try_build_graph(
    tools_table: document.Table, schema: schema_mod.Schema | None = None
) -> Graph | None:
    """グラフを作る。ツール名が重複する場合は ``None`` を返す(重複は ``unique-name`` が報告する)。"""
    try:
        return build_graph(tools_table, schema=schema)
    except DuplicateToolNameError:
        return None


def _collect_input_edges(
    owner: str,
    owner_table: document.Table,
    input_name: str,
    input_table: document.Table,
    schema: schema_mod.Schema | None,
    edges: list[Edge],
    driven: list[DrivenInput],
) -> None:
    owner_type = owner_table.type_name

    source_op = input_table.get("SourceOp")
    source = input_table.get("Source")
    if isinstance(source_op, document.String) and isinstance(source, document.String):
        data_type = _resolve_data_type(schema, owner_type, owner_table, input_name)
        edges.append(
            Edge(
                kind="connection",
                tool=owner,
                input=input_name,
                target=Target(source_op.value, source.value),
                data_type=data_type,
                expression=None,
            )
        )

    expression = input_table.get("Expression")
    if isinstance(expression, document.String):
        text = expression.value
        driven.append(DrivenInput(tool=owner, input=input_name, expression=text))
        data_type = _resolve_data_type(schema, owner_type, owner_table, input_name)
        for tool, port in expression_mod.references(text, owner):
            edges.append(
                Edge(
                    kind="expression",
                    tool=owner,
                    input=input_name,
                    target=Target(tool, port),
                    data_type=data_type,
                    expression=text,
                )
            )


# --------------------------------------------------------------------------
# キーフレーム時刻の範囲
# --------------------------------------------------------------------------


def keyframe_range(tools_table: document.Table) -> tuple[float, float] | None:
    """``KeyFrames`` のキー(時刻)の最小値と最大値を、内側のマクロを含めて返す。キーフレームが無ければ ``None`` を返す。"""
    times: list[float] = []

    def visit(table: document.Table) -> None:
        for element in table:
            if (
                element.key is not None
                and element.key.name == "KeyFrames"
                and isinstance(element.value, document.Table)
            ):
                for kf_element in element.value:
                    if kf_element.key is not None and kf_element.key.kind == "number":
                        times.append(float(kf_element.key.raw))
            if isinstance(element.value, document.Table):
                visit(element.value)

    visit(tools_table)
    if not times:
        return None
    return (min(times), max(times))
