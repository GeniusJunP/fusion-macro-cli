"""検査規則。

``check()`` は違反を、``fix()`` は修復手続きを持つ規則について直した文書を返す。
各規則は適用する文書の種類(:mod:`fusion_macro_cli.form` の ``.comp``、macro 形、tool 形)を定義として持つ。
ツールそのものに対する規則は3種類すべてに適用する。macro 形の内側のツールと tool 形のツールは ``.comp`` から持ち越したものであるためである。
公開する入力と出力に対する規則は、それらを持つ macro 形だけに適用する。
"""

from __future__ import annotations

import copy
import dataclasses
import re
from collections import defaultdict
from typing import Any, Callable, Final, Iterator

from fusion_macro_cli import document
from fusion_macro_cli import fonts
from fusion_macro_cli import form as form_mod
from fusion_macro_cli import graph as graph_mod
from fusion_macro_cli import macro as macro_mod
from fusion_macro_cli import schema as schema_mod

__all__ = [
    "Rule",
    "Violation",
    "CheckOutcome",
    "CheckResult",
    "RULES",
    "RULES_HELP",
    "check",
    "fix",
]


# --------------------------------------------------------------------------
# 規則の一覧
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Rule:
    """検査規則1つ分のメタデータ。``applies_to`` は規則を適用する文書の種類である。"""

    id: str
    applies_to: frozenset[form_mod.Kind]
    needs_schema: bool
    needs_graph: bool
    fixable: bool

    def __post_init__(self) -> None:
        unknown = set(self.applies_to) - set(form_mod.KINDS)
        if not self.applies_to or unknown:
            raise ValueError(f"rule {self.id!r} has invalid applies_to: {sorted(self.applies_to)!r}")


#: ツールそのものに対する規則の適用先。
_TOOLS: Final[frozenset[form_mod.Kind]] = frozenset({"comp", "macro", "tool"})
#: ``no-macro`` の適用先。tool 形の唯一のツールは定義から ``MacroOperator`` でなく、他のツールを含まないため、適用しない。
_COMP_AND_MACRO: Final[frozenset[form_mod.Kind]] = frozenset({"comp", "macro"})
#: 公開する入力と出力に対する規則の適用先。
_MACRO: Final[frozenset[form_mod.Kind]] = frozenset({"macro"})


#: 規則を適用する順序であり、違反もこの順に並ぶ。
RULES: Final[tuple[Rule, ...]] = (
    Rule("ascii-name", _TOOLS, needs_schema=False, needs_graph=False, fixable=True),
    Rule("unique-name", _TOOLS, needs_schema=False, needs_graph=False, fixable=True),
    Rule("no-dangling", _TOOLS, needs_schema=False, needs_graph=True, fixable=True),
    Rule("keyframe-origin", _TOOLS, needs_schema=False, needs_graph=True, fixable=True),
    Rule("no-absolute-path", _TOOLS, needs_schema=False, needs_graph=False, fixable=False),
    Rule("font-available", _TOOLS, needs_schema=False, needs_graph=False, fixable=False),
    Rule("generator-follows-format", _TOOLS, needs_schema=True, needs_graph=False, fixable=True),
    Rule("loader-scaled", _TOOLS, needs_schema=True, needs_graph=True, fixable=False),
    Rule("no-macro", _COMP_AND_MACRO, needs_schema=False, needs_graph=False, fixable=True),
    Rule("no-media-io", _TOOLS, needs_schema=False, needs_graph=False, fixable=True),
    Rule("page-controls", _MACRO, needs_schema=False, needs_graph=False, fixable=True),
    Rule("input-exists", _MACRO, needs_schema=True, needs_graph=False, fixable=True),
    Rule("label-unique", _MACRO, needs_schema=False, needs_graph=False, fixable=True),
    Rule("label-control-passive", _MACRO, needs_schema=True, needs_graph=False, fixable=True),
    Rule("no-driven-publish", _MACRO, needs_schema=False, needs_graph=True, fixable=True),
    Rule("no-connected-publish", _MACRO, needs_schema=False, needs_graph=True, fixable=False),
    Rule("no-nest-publish", _MACRO, needs_schema=True, needs_graph=True, fixable=False),
    Rule("publish-element-selected", _MACRO, needs_schema=True, needs_graph=True, fixable=False),
    Rule("color-control-rgb", _MACRO, needs_schema=True, needs_graph=True, fixable=False),
    Rule("output-reachable", _MACRO, needs_schema=True, needs_graph=True, fixable=False),
    Rule("no-self-image", _MACRO, needs_schema=False, needs_graph=True, fixable=False),
)

_RULES_BY_ID: Final[dict[str, Rule]] = {rule.id: rule for rule in RULES}


@dataclasses.dataclass(frozen=True)
class Violation:
    """違反1件。``location`` と ``message`` は表示専用で、機械的な突き合わせには使わない。"""

    rule: str
    location: str
    message: str


@dataclasses.dataclass(frozen=True)
class CheckOutcome:
    """1つの規則の判定結果。``skipped_inputs`` は公開入力単位で判定できなかった記録である。"""

    violations: list[Violation]
    skipped_inputs: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """``check()`` の結果。

    ``skipped`` はスキーマが無い、またはツール名の重複でグラフを作れないために実行しなかった規則である。
    ``skipped_inputs`` は ``"<規則ID> <マクロ名>:<公開入力のキー>: <理由>"`` の形の文字列である。
    """

    violations: tuple[Violation, ...]
    skipped: tuple[str, ...]
    skipped_inputs: tuple[str, ...]


def _rules_help() -> str:
    """``check --help`` と ``fix --help`` の epilog に載せる、規則と適用先の一覧を ``RULES`` から作る。"""
    lines = [
        "Rules and the documents they apply to:",
        "",
        "  comp   .comp",
        "  macro  macro-form .setting (a MacroOperator wrapping the tools)",
        "  tool   tool-form .setting (one tool saved as it is)",
        "",
        f"  {'RULE':<26}{'comp':<6}{'macro':<7}{'tool':<6}FIX",
    ]
    for rule in RULES:
        marks = ["yes" if kind in rule.applies_to else "-" for kind in form_mod.KINDS]
        lines.append(
            f"  {rule.id:<26}{marks[0]:<6}{marks[1]:<7}{marks[2]:<6}{'yes' if rule.fixable else '-'}"
        )
    lines.append("")
    lines.append("A rule that does not apply to a document is not run on it and prints nothing.")
    return "\n".join(lines) + "\n"


RULES_HELP: Final[str] = _rules_help()


# --------------------------------------------------------------------------
# 文書の走査(規則の実装が共有する補助関数)
# --------------------------------------------------------------------------


def _walk(table: document.Table, path: list[str]) -> Iterator[tuple[list[str], document.Table]]:
    """文書内の全テーブルを、マクロの境界で止めずに辿る。"""
    yield path, table
    for element in table:
        if isinstance(element.value, document.Table):
            key = element.key.name if element.key is not None else "?"
            yield from _walk(element.value, path + [key])


def _find_keyframe_tables(
    table: document.Table, path: list[str]
) -> Iterator[tuple[list[str], document.Table]]:
    """``KeyFrames`` を持つテーブルを探す。内側のマクロは呼び出し側がツールごとに巡るため、踏み込まない。"""
    for element in table:
        if (
            element.key is not None
            and element.key.name == "KeyFrames"
            and isinstance(element.value, document.Table)
        ):
            yield path + ["KeyFrames"], element.value
            continue
        if not isinstance(element.value, document.Table):
            continue
        if table.type_name == "MacroOperator" and element.key is not None and element.key.name == "Tools":
            continue
        sub = path + [element.key.name if element.key is not None else "?"]
        yield from _find_keyframe_tables(element.value, sub)


def _set_element(table: document.Table, key_name: str, value_node: document.Node) -> None:
    """``table`` の直下に ``key_name`` があれば値を差し替え、無ければ末尾に追加する。"""
    for element in table.elements:
        if element.key is not None and element.key.name == key_name:
            element.value = value_node
            return
    table.elements.append(document.Element(document.Key("ident", key_name), value_node))


def _remove_element(table: document.Table, key_name: str) -> None:
    table.elements = [
        el for el in table.elements if not (el.key is not None and el.key.name == key_name)
    ]


def _resolve_input_value(
    tool_table: document.Table,
    input_name: str,
    schema_obj: schema_mod.Schema | None,
    type_name: str | None,
) -> Any:
    """ツールの入力の現在値を返す。``.comp`` に ``Value`` が無ければスキーマの既定値を使う。"""
    inputs_table = tool_table.get("Inputs")
    if inputs_table is not None:
        entry = inputs_table.get(input_name)
        if isinstance(entry, document.Table):
            value_node = entry.get("Value")
            if value_node is not None:
                return document.as_native(value_node)
    if schema_obj is not None and type_name is not None and schema_obj.has_inputs(type_name):
        try:
            attrs = schema_obj.input_attributes(type_name, input_name)
        except KeyError:
            return None
        return attrs.default
    return None


# --------------------------------------------------------------------------
# ツールに対する規則: ascii-name
# --------------------------------------------------------------------------


def _check_ascii_name(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """内側のツール名が ASCII であるかを検査する。式は ``Tool.Port`` の形でツールを参照するため Lua の識別子である必要があり、マクロ名は対象外とする。"""
    violations = []
    for name, table in graph_mod.iter_tools(tools_table):
        if table.type_name == "MacroOperator":
            continue
        if not name.isascii():
            violations.append(Violation("ascii-name", name, f"tool name is not ASCII: {name!r}"))
    return CheckOutcome(violations)


_INVALID_IDENT_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_]")
_REPEATED_UNDERSCORE_RE: Final[re.Pattern[str]] = re.compile(r"_+")


def _asciiify(name: str) -> str:
    """仕様補完: 識別子に使えない文字を、位置を保つため ``_`` へ置換する。"""
    result = _INVALID_IDENT_CHAR_RE.sub("_", name)
    result = _REPEATED_UNDERSCORE_RE.sub("_", result).strip("_")
    if not result:
        result = "tool"
    if result[0].isdigit():
        result = "_" + result
    return result


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}_{i}"
        i += 1
    return candidate


def _rewrite_tool_references(node: document.Node, old: str, new: str) -> None:
    """``old`` を指す ``SourceOp``、``ActiveTool``、式のドット形参照を ``new`` へ書き換える。"""
    if not isinstance(node, document.Table):
        return
    expr_re = re.compile(rf"\b{re.escape(old)}\.")
    for element in node:
        if isinstance(element.value, document.String) and element.key is not None:
            if element.key.name in ("SourceOp", "ActiveTool") and element.value.raw == old:
                element.value.raw = new
            elif element.key.name == "Expression":
                element.value.raw = expr_re.sub(new + ".", element.value.raw)
        elif isinstance(element.value, document.Table):
            _rewrite_tool_references(element.value, old, new)


def _fix_ascii_name(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    fixed: list[Violation] = []
    used = {name for name, _ in graph_mod.iter_tools(tools_table)}
    for _, element in list(graph_mod.iter_tool_elements(tools_table)):
        if element.value.type_name == "MacroOperator":  # type: ignore[union-attr]
            continue  # マクロ名は対象外。
        old = element.key.name  # type: ignore[union-attr]
        if old.isascii():
            continue
        new = _unique_name(_asciiify(old), used)
        used.add(new)
        _rewrite_tool_references(doc.root, old, new)
        element.key = document.Key("ident", new)
        fixed.append(Violation("ascii-name", old, f"renamed to {new!r}"))
    return fixed


# --------------------------------------------------------------------------
# ツールに対する規則: unique-name
# --------------------------------------------------------------------------


def _check_unique_name(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    violations = []
    seen: set[str] = set()
    for name, _ in graph_mod.iter_tools(tools_table):
        if name in seen:
            violations.append(Violation("unique-name", name, f"duplicate tool name: {name!r}"))
        seen.add(name)
    return CheckOutcome(violations)


def _fix_unique_name(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    """2回目以降に現れた同名ツールへ接尾辞を付けて一意にする。

    どちらの出現を指す参照かは区別できないため、既存の参照は書き換えない(仕様補完)。
    """
    fixed: list[Violation] = []
    seen: set[str] = set()
    used = {name for name, _ in graph_mod.iter_tools(tools_table)}
    for _, element in list(graph_mod.iter_tool_elements(tools_table)):
        name = element.key.name  # type: ignore[union-attr]
        if name in seen:
            new = _unique_name(f"{name}_2", used) if f"{name}_2" in used else f"{name}_2"
            used.add(new)
            element.key = document.Key(element.key.kind, new)  # type: ignore[union-attr]
            fixed.append(
                Violation(
                    "unique-name",
                    name,
                    f"renamed duplicate to {new!r}; existing references to {name!r} "
                    "now resolve to the first occurrence",
                )
            )
        else:
            seen.add(name)
    return fixed


# --------------------------------------------------------------------------
# ツールに対する規則: no-dangling
# --------------------------------------------------------------------------


def _check_no_dangling(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    assert g is not None
    violations = []
    for edge in g.dangling_edges():
        violations.append(
            Violation(
                "no-dangling",
                f"{edge.tool}.{edge.input}",
                f"dangling {edge.kind} reference to {edge.target.tool}.{edge.target.port}",
            )
        )
    return CheckOutcome(violations)


def _fix_no_dangling(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    g = graph_mod.try_build_graph(tools_table, schema_obj)
    if g is None:
        return []
    fixed: list[Violation] = []
    handled: set[tuple[str, str, str]] = set()
    for edge in g.dangling_edges():
        key = (edge.tool, edge.input, edge.kind)
        if key in handled:
            continue
        handled.add(key)
        tool_table = g.tool_table(edge.tool)
        if tool_table is None:
            continue
        inputs_table = tool_table.get("Inputs")
        if inputs_table is None:
            continue
        input_table = inputs_table.get(edge.input)
        if not isinstance(input_table, document.Table):
            continue
        if edge.kind == "connection":
            _remove_element(input_table, "SourceOp")
            _remove_element(input_table, "Source")
        else:
            _remove_element(input_table, "Expression")
        fixed.append(
            Violation(
                "no-dangling",
                f"{edge.tool}.{edge.input}",
                f"removed dangling {edge.kind} reference to {edge.target.tool}.{edge.target.port}",
            )
        )
    return fixed


# --------------------------------------------------------------------------
# ツールに対する規則: keyframe-origin
# --------------------------------------------------------------------------


def _check_keyframe_origin(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """コンポジション全体でキーフレーム時刻の最小値が 0 であるかを検査する。

    タイムリマップの開始点のように 0 以外から始まることが正しいモディファイアがあるため、モディファイアごとには判定しない。
    """
    rng = graph_mod.keyframe_range(tools_table)
    if rng is None:
        return CheckOutcome([])
    origin, _max = rng
    if origin == 0:
        return CheckOutcome([])
    return CheckOutcome(
        [
            Violation(
                "keyframe-origin",
                "(composition)",
                f"keyframe origin is {origin!r}, expected 0",
            )
        ]
    )


def _shift_key_raw(raw: str, shift: float) -> str:
    value = float(raw) - shift
    if "." not in raw and "e" not in raw.lower() and value == int(value):
        return str(int(value))
    return repr(value)


def _fix_keyframe_origin(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    """全ての ``KeyFrames`` のキーを、全体の最小値だけ平行移動して 0 に揃える。

    モディファイア間の相対関係を保つため、同じ量だけ移動する。ハンドル(``RH``、``LH``)は相対値であるため移動しない(仕様補完)。
    """
    rng = graph_mod.keyframe_range(tools_table)
    if rng is None:
        return []
    origin, _max = rng
    if origin == 0:
        return []
    for tool_name, tool_table in graph_mod.iter_tools(tools_table):
        for _path, kf_table in _find_keyframe_tables(tool_table, [tool_name]):
            for el in kf_table.elements:
                if el.key is not None and el.key.kind == "number":
                    el.key = document.Key("number", _shift_key_raw(el.key.raw, origin))
    return [
        Violation(
            "keyframe-origin",
            "(composition)",
            f"shifted all keyframes by {-origin!r}",
        )
    ]


# --------------------------------------------------------------------------
# ツールに対する規則: no-absolute-path (修復不可)
# --------------------------------------------------------------------------

#: ファイル参照とみなすキー名。``.comp`` の ``Prefs`` は ``Tools`` の外にあり、走査の対象にならない。
_PATH_KEY_NAMES: Final[frozenset[str]] = frozenset({"Filename", "LastFile", "MEDIA_PATH"})

_DRIVE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:[\\/]")


def _looks_absolute_path(text: str) -> bool:
    return text.startswith("/") or text.startswith("\\\\") or bool(_DRIVE_RE.match(text))


def _check_no_absolute_path(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    violations = []
    for path, table in _walk(tools_table, ["Tools"]):
        in_map = bool(path) and path[-1] == "Map"
        for element in table:
            if element.key is None or not isinstance(element.value, document.String):
                continue
            key_name = element.key.name
            if key_name not in _PATH_KEY_NAMES and not in_map:
                continue
            text = element.value.value
            if _looks_absolute_path(text):
                loc = ".".join(path + [key_name])
                violations.append(Violation("no-absolute-path", loc, f"absolute path: {text}"))
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# ツールに対する規則: font-available (修復不可)
# --------------------------------------------------------------------------

def _check_font_available(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    violations = []
    families = fonts.installed_font_families()
    for path, table in _walk(tools_table, []):
        for element in table:
            if element.key is None or element.key.name != "Font":
                continue
            if not isinstance(element.value, document.Table):
                continue
            value = element.value.get("Value")
            if not isinstance(value, document.String):
                continue
            font_name = value.value
            if font_name and font_name.strip().lower() not in families:
                loc = ".".join(path) if path else font_name
                violations.append(Violation("font-available", loc, f"font not found: {font_name!r}"))
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# ツールに対する規則: generator-follows-format
# --------------------------------------------------------------------------


def _is_generator(schema_obj: schema_mod.Schema, type_name: str) -> bool:
    """種別が ``UseFrameFormatSettings`` と ``GlobalOut`` を両方持つ(ジェネレータである)かを返す。

    ``registry`` の ``category`` は表示言語で翻訳されるため、判定に使わない。
    """
    if not schema_obj.has_inputs(type_name):
        return False
    names = schema_obj.input_names(type_name)
    return "UseFrameFormatSettings" in names and "GlobalOut" in names


def _check_generator_follows_format(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    assert schema_obj is not None
    violations = []
    for name, table in graph_mod.iter_tools(tools_table):
        type_name = table.type_name
        if type_name is None or not _is_generator(schema_obj, type_name):
            continue
        value = _resolve_input_value(table, "UseFrameFormatSettings", schema_obj, type_name)
        if value == 1 or value is True:
            continue
        violations.append(
            Violation(
                "generator-follows-format",
                name,
                f"UseFrameFormatSettings is {value!r}, expected 1",
            )
        )
    return CheckOutcome(violations)


def _fix_generator_follows_format(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    if schema_obj is None:
        return []
    fixed: list[Violation] = []
    for name, table in graph_mod.iter_tools(tools_table):
        type_name = table.type_name
        if type_name is None or not _is_generator(schema_obj, type_name):
            continue
        value = _resolve_input_value(table, "UseFrameFormatSettings", schema_obj, type_name)
        if value == 1 or value is True:
            continue
        inputs_table = table.get("Inputs")
        if inputs_table is None:
            inputs_table = document.Table(None, [])
            table.elements.append(document.Element(document.Key("ident", "Inputs"), inputs_table))
        entry = inputs_table.get("UseFrameFormatSettings")
        if isinstance(entry, document.Table):
            _set_element(entry, "Value", document.Number("1"))
        else:
            new_input = document.Table(
                "Input", [document.Element(document.Key("ident", "Value"), document.Number("1"))]
            )
            inputs_table.elements.append(
                document.Element(document.Key("ident", "UseFrameFormatSettings"), new_input)
            )
        fixed.append(Violation("generator-follows-format", name, "set UseFrameFormatSettings to 1"))
    return fixed


# --------------------------------------------------------------------------
# ツールに対する規則: loader-scaled (修復不可)
# --------------------------------------------------------------------------


def _forward_reachable(forward: dict[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        for nxt in forward.get(current, ()):
            if nxt not in seen and nxt != start:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _check_loader_scaled(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """``Loader`` の下流のいずれかのツールが、``comp.RenderHeight`` を参照する式を持つかを検査する。"""
    assert g is not None
    violations = []
    forward: dict[str, list[str]] = defaultdict(list)
    for edge in g.edges():
        if edge.kind == "connection":
            forward[edge.target.tool].append(edge.tool)
    for name in g.tools():
        if g.tool_type(name) != "Loader":
            continue
        downstream = _forward_reachable(forward, name)
        driven = g.driven_inputs(downstream)
        if not any("RenderHeight" in d.expression for d in driven):
            violations.append(
                Violation(
                    "loader-scaled",
                    name,
                    "no downstream tool references comp.RenderHeight",
                )
            )
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# .comp と macro 形に対する規則: no-macro (修復: 平坦化)
# --------------------------------------------------------------------------


def _no_macro_scope(doc: document.Document, tools_table: document.Table) -> document.Table:
    """``no-macro`` の対象の ``Tools`` を返す。

    macro 形では最上位の ``MacroOperator`` 自体は必須であるため、その内側を対象とする。
    """
    if form_mod.classify(doc) != "macro":
        return tools_table
    inner = form_mod.top_tool(doc).table.get("Tools")
    return inner if isinstance(inner, document.Table) else document.Table(None, [])


def _check_no_macro(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """対象の ``Tools`` に含まれる ``MacroOperator`` を、入れ子を含めて1件ずつ報告する。"""
    violations = []
    for name, table in graph_mod.iter_tools(_no_macro_scope(doc, tools_table)):
        if table.type_name == "MacroOperator":
            violations.append(Violation("no-macro", name, f"macro operator found: {name!r}"))
    return CheckOutcome(violations)


def _flatten_macro_output_references(
    node: document.Node, macro_name: str, key_to_target: dict[str, tuple[str, str]]
) -> None:
    """マクロの ``Outputs`` を指す接続と ``<マクロ名>.<キー>`` 形の式を、内側のツールとポートへ書き換える。"""
    if not isinstance(node, document.Table):
        return
    source_op_el: document.String | None = None
    source_el: document.String | None = None
    for element in node:
        if element.key is None:
            continue
        if element.key.name == "SourceOp" and isinstance(element.value, document.String):
            source_op_el = element.value
        elif element.key.name == "Source" and isinstance(element.value, document.String):
            source_el = element.value
        elif element.key.name == "Expression" and isinstance(element.value, document.String):
            text = element.value.raw
            for key, (target_op, target_port) in key_to_target.items():
                pattern = re.compile(rf"\b{re.escape(macro_name)}\.{re.escape(key)}\b")
                text = pattern.sub(f"{target_op}.{target_port}", text)
            element.value.raw = text
        elif isinstance(element.value, document.Table):
            _flatten_macro_output_references(element.value, macro_name, key_to_target)
    if (
        source_op_el is not None
        and source_el is not None
        and source_op_el.value == macro_name
        and source_el.value in key_to_target
    ):
        target_op, target_port = key_to_target[source_el.value]
        source_op_el.raw = target_op
        source_el.raw = target_port


def _fix_no_macro(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    """``MacroOperator`` を内側の ``Tools`` の要素で置き換える。平坦化で入れ子のマクロが直下に現れるため、無くなるまで繰り返す。"""
    scope = _no_macro_scope(doc, tools_table)
    fixed: list[Violation] = []
    while True:
        macros = macro_mod.read_macros(scope)
        if not macros:
            break
        macro = macros[0]
        index = next(
            i
            for i, el in enumerate(scope.elements)
            if el.key is not None
            and el.key.name == macro.name
            and isinstance(el.value, document.Table)
            and el.value.type_name == "MacroOperator"
        )
        macro_table = scope.elements[index].value
        assert isinstance(macro_table, document.Table)
        inner_tools_table = macro_table.get("Tools")
        inner_elements = (
            list(inner_tools_table.elements) if isinstance(inner_tools_table, document.Table) else []
        )

        key_to_target = {
            output.key: (output.source_op, output.source)
            for output in macro.outputs
            if output.source_op is not None and output.source is not None
        }
        _flatten_macro_output_references(doc.root, macro.name, key_to_target)

        scope.elements[index : index + 1] = inner_elements
        fixed.append(Violation("no-macro", macro.name, f"flattened macro operator {macro.name!r}"))
    return fixed


# --------------------------------------------------------------------------
# ツールに対する規則: no-media-io (修復: 削除)
# --------------------------------------------------------------------------


def _check_no_media_io(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    violations = []
    for name, table in graph_mod.iter_tools(tools_table):
        if table.type_name in ("MediaIn", "MediaOut"):
            violations.append(Violation("no-media-io", name, f"{table.type_name} found: {name!r}"))
    return CheckOutcome(violations)


def _fix_no_media_io(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    """``MediaIn`` と ``MediaOut`` を削除する。生じた参照切れは ``no-dangling`` が扱う。"""
    fixed: list[Violation] = []
    for container, element in list(graph_mod.iter_tool_elements(tools_table)):
        if not isinstance(element.value, document.Table):
            continue
        type_name = element.value.type_name
        if type_name not in ("MediaIn", "MediaOut"):
            continue
        name = element.key.name  # type: ignore[union-attr]
        _remove_element(container, name)
        fixed.append(Violation("no-media-io", name, f"removed {type_name} tool {name!r}"))
    return fixed


# --------------------------------------------------------------------------
# macro 形に対する規則: page-controls
# --------------------------------------------------------------------------


def _macro_child(tools_table: document.Table, macro: macro_mod.Macro, key: str) -> document.Table | None:
    """マクロの ``Inputs`` や ``Tools`` など、``MacroOperator`` が直接保持する表を返す。"""
    macro_table = tools_table.get(macro.name)
    child = macro_table.get(key) if isinstance(macro_table, document.Table) else None
    return child if isinstance(child, document.Table) else None


def _iter_sourced_publish(
    tools_table: document.Table,
) -> Iterator[tuple[macro_mod.Macro, macro_mod.PublishedInput, str, str]]:
    """参照先(``SourceOp`` と ``Source``)を持つ公開入力を、マクロと参照先とともに列挙する。"""
    for macro in macro_mod.read_macros(tools_table):
        for p in macro.publish:
            if p.source_op and p.source:
                yield macro, p, p.source_op, p.source


def _check_page_controls(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    violations = []
    for macro in macro_mod.read_macros(tools_table):
        for p in macro.publish:
            # Page を省略した公開入力も、Page の削除を検出できるよう違反とする。
            if p.page != "Controls":
                violations.append(
                    Violation(
                        "page-controls",
                        f"{macro.name}:{p.key}",
                        f"Page is {p.page!r}, expected 'Controls'",
                    )
                )
    return CheckOutcome(violations)


def _fix_page_controls(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    fixed: list[Violation] = []
    for macro in macro_mod.read_macros(tools_table):
        inputs_table = _macro_child(tools_table, macro, "Inputs")
        if inputs_table is None:
            continue
        for p in macro.publish:
            if p.page != "Controls":
                entry = inputs_table.get(p.key)
                if isinstance(entry, document.Table):
                    _set_element(entry, "Page", document.String("Controls"))
                    fixed.append(Violation("page-controls", f"{macro.name}:{p.key}", "set Page to 'Controls'"))
    return fixed


# --------------------------------------------------------------------------
# macro 形に対する規則: input-exists
# --------------------------------------------------------------------------


def _check_input_exists(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    assert schema_obj is not None
    if g is None:
        return CheckOutcome([])
    violations: list[Violation] = []
    skipped_inputs: list[str] = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        # 入力の実在を判定できない種別は、違反とせず判定不能として記録する。
        known = g.known_inputs(source_op)
        if known is None:
            tool_type = g.tool_type(source_op)
            if tool_type is None:
                reason = f"tool {source_op!r} not found"
            else:
                reason = f"schema has no input definition for {tool_type!r} and it has no UserControls"
            skipped_inputs.append(f"input-exists {macro.name}:{p.key}: {reason}")
            continue
        if source not in known:
            violations.append(
                Violation("input-exists", f"{macro.name}:{p.key}", f"{source_op}.{source} does not exist")
            )
    return CheckOutcome(violations, skipped_inputs)


def _fix_input_exists(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    if schema_obj is None:
        return []
    g = graph_mod.try_build_graph(tools_table, schema_obj)
    if g is None:
        return []
    fixed: list[Violation] = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        known = g.known_inputs(source_op)
        inputs_table = _macro_child(tools_table, macro, "Inputs")
        if known is None or source in known or inputs_table is None:
            continue
        _remove_element(inputs_table, p.key)
        fixed.append(
            Violation("input-exists", f"{macro.name}:{p.key}", f"removed publish of nonexistent {source_op}.{source}")
        )
    return fixed


# --------------------------------------------------------------------------
# macro 形に対する規則: label-unique
# --------------------------------------------------------------------------


def _check_label_unique(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    violations = []
    for macro in macro_mod.read_macros(tools_table):
        seen: dict[str, str] = {}
        for p in macro.publish:
            if p.name is None:
                continue
            if p.name in seen:
                violations.append(
                    Violation(
                        "label-unique",
                        f"{macro.name}:{p.key}",
                        f"label {p.name!r} duplicates {seen[p.name]!r}",
                    )
                )
            else:
                seen[p.name] = p.key
    return CheckOutcome(violations)


def _fix_label_unique(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    fixed: list[Violation] = []
    for macro in macro_mod.read_macros(tools_table):
        inputs_table = _macro_child(tools_table, macro, "Inputs")
        if inputs_table is None:
            continue
        seen: dict[str, int] = {}
        for p in macro.publish:
            if p.name is None:
                continue
            count = seen.get(p.name, 0) + 1
            seen[p.name] = count
            if count > 1:
                entry = inputs_table.get(p.key)
                new_label = f"{p.name} ({count})"
                if isinstance(entry, document.Table):
                    _set_element(entry, "Name", document.String(document.encode_string(new_label)))
                    fixed.append(
                        Violation("label-unique", f"{macro.name}:{p.key}", f"renamed label to {new_label!r}")
                    )
    return fixed


# --------------------------------------------------------------------------
# macro 形に対する規則: label-control-passive
# --------------------------------------------------------------------------


def _label_control_state(
    g: graph_mod.Graph, schema_obj: schema_mod.Schema, source_op: str, source: str
) -> tuple[str | None, bool | None, bool]:
    """``(制御の種類, INP_External, スキーマ由来か)`` を返す。スキーマ由来の定義は文書側で直せない。"""
    tool_type = g.tool_type(source_op)
    if tool_type is not None and schema_obj.has_inputs(tool_type) and source in schema_obj.input_names(tool_type):
        attrs = schema_obj.input_attributes(tool_type, source)
        return attrs.control_type, attrs.external, True
    uc = g.user_controls(source_op).get(source)
    if uc is not None:
        return uc.control_type, uc.external, False
    return None, None, False


def _check_label_control_passive(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    assert schema_obj is not None
    if g is None:
        return CheckOutcome([])
    violations = []
    for macro, _p, source_op, source in _iter_sourced_publish(tools_table):
        control_type, external, _from_schema = _label_control_state(g, schema_obj, source_op, source)
        if control_type == "LabelControl" and external is not False:
            violations.append(
                Violation(
                    "label-control-passive",
                    f"{macro.name}:{source_op}.{source}",
                    "LabelControl does not have INP_External = false",
                )
            )
    return CheckOutcome(violations)


def _fix_label_control_passive(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    """``UserControls`` 由来の ``LabelControl`` に ``INP_External = false`` を書く。"""
    if schema_obj is None:
        return []
    g = graph_mod.try_build_graph(tools_table, schema_obj)
    if g is None:
        return []
    fixed: list[Violation] = []
    for macro, _p, source_op, source in _iter_sourced_publish(tools_table):
        control_type, external, from_schema = _label_control_state(g, schema_obj, source_op, source)
        if control_type != "LabelControl" or external is False or from_schema:
            continue
        uc_table = g.tool_table(source_op).get("UserControls")
        entry = uc_table.get(source) if isinstance(uc_table, document.Table) else None
        if isinstance(entry, document.Table):
            _set_element(entry, "INP_External", document.Boolean(False))
            fixed.append(
                Violation("label-control-passive", f"{macro.name}:{source_op}.{source}", "set INP_External to false")
            )
    return fixed


# --------------------------------------------------------------------------
# macro 形に対する規則: no-driven-publish
# --------------------------------------------------------------------------


def _driven_pairs(g: graph_mod.Graph, macro: macro_mod.Macro) -> set[tuple[str, str]]:
    return {(d.tool, d.input) for d in g.driven_inputs(macro.tools)}


def _check_no_driven_publish(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    if g is None:
        return CheckOutcome([])
    violations = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        if (source_op, source) in _driven_pairs(g, macro):
            violations.append(
                Violation(
                    "no-driven-publish",
                    f"{macro.name}:{p.key}",
                    f"published input {source_op}.{source} is driven by an expression",
                )
            )
    return CheckOutcome(violations)


def _fix_no_driven_publish(
    doc: document.Document, tools_table: document.Table, schema_obj: schema_mod.Schema | None
) -> list[Violation]:
    g = graph_mod.try_build_graph(tools_table, schema_obj)
    if g is None:
        return []
    fixed: list[Violation] = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        inputs_table = _macro_child(tools_table, macro, "Inputs")
        if inputs_table is None or (source_op, source) not in _driven_pairs(g, macro):
            continue
        _remove_element(inputs_table, p.key)
        fixed.append(
            Violation(
                "no-driven-publish",
                f"{macro.name}:{p.key}",
                f"removed publish of driven input {source_op}.{source}",
            )
        )
    return fixed


# --------------------------------------------------------------------------
# macro 形に対する規則: no-connected-publish (修復不可)
# --------------------------------------------------------------------------


def _check_no_connected_publish(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """接続された入力の公開を検出する。接続と式を併せ持つ入力は式が値を決めるため、``no-driven-publish`` が扱う。"""
    if g is None:
        return CheckOutcome([])
    violations = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        if (source_op, source) in _driven_pairs(g, macro):
            continue
        for edge in g.edges_within([source_op]):
            if edge.kind == "connection" and edge.input == source:
                violations.append(
                    Violation(
                        "no-connected-publish",
                        f"{macro.name}:{p.key}",
                        f"published input {source_op}.{source} is connected to "
                        f"{edge.target.tool}.{edge.target.port}; the inspector draws it as animated",
                    )
                )
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# macro 形に対する規則: no-nest-publish (修復不可)
# --------------------------------------------------------------------------


def _check_no_nest_publish(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    if g is None:
        return CheckOutcome([])
    violations = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        if g.control_type(source_op, source) == "NestControl":
            violations.append(
                Violation(
                    "no-nest-publish",
                    f"{macro.name}:{p.key}",
                    f"published input {source_op}.{source} is a NestControl; "
                    "it shows as an empty fold that swallows the next control. "
                    "Publish its child inputs instead",
                )
            )
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# macro 形に対する規則: publish-element-selected (修復不可)
# --------------------------------------------------------------------------


def _selected_element(g: graph_mod.Graph, tool: str) -> int | None:
    """内側のツールで選択中の要素の番号を返す。``SelectElement`` は 0 起点である。値が式や接続で決まる場合は ``None`` を返す。"""
    table = g.tool_table(tool)
    inputs = table.get("Inputs") if table is not None else None
    entry = inputs.get("SelectElement") if isinstance(inputs, document.Table) else None
    if entry is None:
        return 1
    if not isinstance(entry, document.Table) or entry.get("SourceOp") is not None or entry.get("Expression") is not None:
        return None
    value = entry.get("Value")
    if value is None:
        return 1
    if not isinstance(value, document.Number):
        return None
    return int(float(value.raw)) + 1


def _check_publish_element_selected(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    if g is None or schema_obj is None:
        return CheckOutcome([])
    violations = []
    skipped: list[str] = []
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        tool_type = g.tool_type(source_op)
        if tool_type is None or not schema_obj.has_inputs(tool_type) or source not in schema_obj.input_names(tool_type):
            continue
        element = schema_obj.element_of(tool_type, source)
        if element is None:
            continue
        selected = _selected_element(g, source_op)
        if selected is None:
            skipped.append(
                f"publish-element-selected {macro.name}:{p.key}: {source_op}.SelectElement is not a plain value"
            )
            continue
        if element == selected:
            continue
        message = (
            f"published input {source_op}.{source} belongs to element {element}, "
            f"but {source_op} selects element {selected}; the inspector hides it"
        )
        substitute = schema_obj.visible_substitute(tool_type, source)
        if substitute is not None:
            message += f". Publish {source_op}.{substitute} instead"
        violations.append(Violation("publish-element-selected", f"{macro.name}:{p.key}", message))
    return CheckOutcome(violations, skipped)


# --------------------------------------------------------------------------
# macro 形に対する規則: color-control-rgb (修復不可)
# --------------------------------------------------------------------------

#: ColorControl が色として描画されるために公開が必要な、制御の先頭からの入力数(赤・緑・青)。
_COLOR_COMPONENTS: Final = 3


def _check_color_control_rgb(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """``ColorControl`` の公開が、制御の先頭から赤・緑・青を含むかを検査する。

    アルファのみ、または赤のみの公開は操作できないスウォッチとして描画される。
    """
    if g is None:
        return CheckOutcome([])
    published: dict[tuple[str, str, tuple[str, ...]], list[macro_mod.PublishedInput]] = {}
    for macro, p, source_op, source in _iter_sourced_publish(tools_table):
        control = g.control_of(source_op, source)
        if control is None or g.control_type(source_op, source) != "ColorControl":
            continue
        published.setdefault((macro.name, source_op, tuple(control)), []).append(p)
    violations = []
    for (macro_name, tool, control), entries in published.items():
        names = {p.source for p in entries}
        missing = [name for name in control[:_COLOR_COMPONENTS] if name not in names]
        if missing:
            violations.append(
                Violation(
                    "color-control-rgb",
                    f"{macro_name}:{entries[0].key}",
                    f"ColorControl {tool}.{control[0]} is published without {', '.join(missing)}; "
                    "the inspector draws a swatch that cannot be edited. "
                    "Publish the red, green and blue inputs, or publish a slider instead",
                )
            )
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# macro 形に対する規則: output-reachable (修復不可)
# --------------------------------------------------------------------------


def _check_output_reachable(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    """全ての ``InstanceOutput`` の参照先が存在し、少なくとも1つの出力が画像型であるかを検査する。

    出力ポートの定義が無い出力は判定から外し、判定できた出力が無い場合は違反としない。
    """
    assert schema_obj is not None
    if g is None:
        return CheckOutcome([])
    violations = []
    for macro in macro_mod.read_macros(tools_table):
        determined_any = False
        has_image = False
        for output in macro.outputs:
            if not output.source_op or output.source_op not in macro.tools:
                violations.append(
                    Violation(
                        "output-reachable",
                        f"{macro.name}:{output.key}",
                        f"output tool {output.source_op!r} is not present inside the macro",
                    )
                )
                continue
            if not output.source:
                violations.append(
                    Violation("output-reachable", f"{macro.name}:{output.key}", "output has no Source")
                )
                continue
            ports = g.output_ports(output.source_op)
            if ports is None:
                continue  # この種別の出力ポート定義が無く、判定できない。
            if output.source not in ports:
                tool_type = g.tool_type(output.source_op)
                violations.append(
                    Violation(
                        "output-reachable",
                        f"{macro.name}:{output.key}",
                        f"{output.source_op}.{output.source} is not a port of {tool_type!r}",
                    )
                )
                continue
            determined_any = True
            if ports[output.source] == "Image":
                has_image = True
        if determined_any and not has_image:
            violations.append(
                Violation(
                    "output-reachable",
                    f"{macro.name}",
                    "no output is typed Image",
                )
            )
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# macro 形に対する規則: no-self-image (修復不可)
# --------------------------------------------------------------------------


def _check_no_self_image(
    doc: document.Document,
    tools_table: document.Table,
    schema_obj: schema_mod.Schema | None,
    g: graph_mod.Graph | None,
) -> CheckOutcome:
    if g is None:
        return CheckOutcome([])
    violations = []
    for macro in macro_mod.read_macros(tools_table):
        for edge in g.edges_within(macro.tools):
            if edge.data_type == "Image" and edge.target.tool == macro.name:
                violations.append(
                    Violation(
                        "no-self-image",
                        f"{macro.name}:{edge.tool}.{edge.input}",
                        f"references the macro's own name {macro.name!r} as an Image source",
                    )
                )
    return CheckOutcome(violations)


# --------------------------------------------------------------------------
# 規則テーブルへの登録
# --------------------------------------------------------------------------

_CheckFn = Callable[
    [document.Document, document.Table, "schema_mod.Schema | None", "graph_mod.Graph | None"],
    "CheckOutcome",
]
_FixFn = Callable[[document.Document, document.Table, "schema_mod.Schema | None"], "list[Violation]"]

_CHECKERS: Final[dict[str, _CheckFn]] = {
    "ascii-name": _check_ascii_name,
    "unique-name": _check_unique_name,
    "no-dangling": _check_no_dangling,
    "keyframe-origin": _check_keyframe_origin,
    "no-absolute-path": _check_no_absolute_path,
    "font-available": _check_font_available,
    "generator-follows-format": _check_generator_follows_format,
    "loader-scaled": _check_loader_scaled,
    "no-macro": _check_no_macro,
    "no-media-io": _check_no_media_io,
    "page-controls": _check_page_controls,
    "input-exists": _check_input_exists,
    "label-unique": _check_label_unique,
    "label-control-passive": _check_label_control_passive,
    "no-driven-publish": _check_no_driven_publish,
    "no-connected-publish": _check_no_connected_publish,
    "no-nest-publish": _check_no_nest_publish,
    "publish-element-selected": _check_publish_element_selected,
    "color-control-rgb": _check_color_control_rgb,
    "output-reachable": _check_output_reachable,
    "no-self-image": _check_no_self_image,
}

#: 修復の適用順。平坦化(no-macro)と削除(no-media-io)が生む参照切れを取りこぼさないよう、no-dangling をその後に実行する。
_FIXERS_IN_ORDER: Final[tuple[tuple[str, _FixFn], ...]] = (
    ("ascii-name", _fix_ascii_name),
    ("unique-name", _fix_unique_name),
    ("no-macro", _fix_no_macro),
    ("no-media-io", _fix_no_media_io),
    ("no-dangling", _fix_no_dangling),
    ("keyframe-origin", _fix_keyframe_origin),
    ("generator-follows-format", _fix_generator_follows_format),
    ("page-controls", _fix_page_controls),
    ("input-exists", _fix_input_exists),
    ("label-unique", _fix_label_unique),
    ("label-control-passive", _fix_label_control_passive),
    ("no-driven-publish", _fix_no_driven_publish),
)

assert set(_CHECKERS) == {r.id for r in RULES}
assert {rid for rid, _ in _FIXERS_IN_ORDER} == {r.id for r in RULES if r.fixable}


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def check(doc: document.Document, schema_obj: schema_mod.Schema | None = None) -> CheckResult:
    """文書を、その種類に適用する規則で検査する。種類を判別できなければ ``ValueError`` を送出する。"""
    kind = form_mod.classify(doc)
    tools_table = doc.root.get("Tools")
    if tools_table is None:
        tools_table = document.Table(None, [])

    needs_graph = any(kind in r.applies_to and r.needs_graph for r in RULES)
    g = graph_mod.try_build_graph(tools_table, schema_obj) if needs_graph else None

    violations: list[Violation] = []
    skipped: list[str] = []
    skipped_inputs: list[str] = []
    for rule in RULES:
        if kind not in rule.applies_to:
            continue
        if rule.needs_schema and schema_obj is None:
            skipped.append(rule.id)
            continue
        if rule.needs_graph and g is None:
            skipped.append(rule.id)
            continue
        outcome = _CHECKERS[rule.id](doc, tools_table, schema_obj, g)
        violations.extend(outcome.violations)
        skipped_inputs.extend(outcome.skipped_inputs)
    return CheckResult(
        violations=tuple(violations), skipped=tuple(skipped), skipped_inputs=tuple(skipped_inputs)
    )


def fix(
    doc: document.Document, schema_obj: schema_mod.Schema | None = None
) -> tuple[document.Document, tuple[Violation, ...], CheckResult]:
    """修復できる違反を直し、``(修復した文書, 直した違反, 再検査の結果)`` を返す。元の文書は変更しない。"""
    new_doc = copy.deepcopy(doc)
    kind = form_mod.classify(new_doc)
    tools_table = new_doc.root.get("Tools")
    if tools_table is None:
        return new_doc, (), check(new_doc, schema_obj)

    applied: list[Violation] = []
    for rule_id, fixer in _FIXERS_IN_ORDER:
        rule = _RULES_BY_ID[rule_id]
        if kind not in rule.applies_to:
            continue
        if rule.needs_schema and schema_obj is None:
            continue
        applied.extend(fixer(new_doc, tools_table, schema_obj))

    result = check(new_doc, schema_obj)
    return new_doc, tuple(applied), result
