"""``fusion_macro_cli.publish`` の検証。

単独実行できる(``python3 tests/test_publish.py``)。終了コードは、致命的な失敗が
1件でもあれば1、失敗は無いが実データに依存する検査(``FUSION_MACRO_CLI_PRESETS``
配下の既存 ``.setting`` の走査)が対象データの不在で行えなかった場合は2、
すべて実行できて失敗が無ければ0を返す(``tests/test_document.py``
``tests/test_graph.py`` ``tests/test_show.py`` と同じ規約)。
``tests/data/sample.comp`` を使う検査は常に実行する。

合成した小さな ``.comp``・``.setting`` に対する検証は、``document.parse`` /
``schema.Schema.from_dict`` の出力を ``publish.py`` の公開関数へそのまま渡す
(公開 API だけを経由する。``rules.py``・``show.py`` は import しない)。

往復(宣言)の比較で表示名(``label``/``Name``)を比較する際は、``Name`` の無い
公開入力を ``label=None`` としてそのまま比べる(スキーマ側の制御名への
フォールバックは ``declare`` 自身が行わない設計であるため、比較側も
フォールバックしない)。
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.document as document  # noqa: E402
import fusion_macro_cli.graph as graph  # noqa: E402
import fusion_macro_cli.macro as macro_mod  # noqa: E402
import fusion_macro_cli.publish as publish  # noqa: E402
import fusion_macro_cli.schema as schema  # noqa: E402

from fusion_macro_cli.publish import MacroDeclaration, OutputEntry, PublishEntry  # noqa: E402

SKIP = "skip"
PASS = "pass"

DATA = HERE / "data"


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------


def _comp_from_tools_text(tools_text: str) -> document.Document:
    """``Composition { Tools = <tools_text>, }`` を組み立てるヘルパー。"""
    return document.parse("Composition { Tools = " + tools_text + ", }")


def _load_schema_or_none() -> schema.Schema | None:
    try:
        return schema.load()
    except FileNotFoundError:
        return None


def _find_tool(tools_table: document.Table, name: str) -> document.Table | None:
    """``publish._find_tool`` と独立に書いた、テスト側の再帰探索。

    比較の妥当性を検証対象の実装(``publish.py``)へ依存させないため、
    ``publish._find_tool`` は呼ばずここへ書き下す。
    """
    for element in tools_table:
        if element.key is None or not isinstance(element.value, document.Table):
            continue
        if element.key.name == name:
            return element.value
        if element.value.type_name == "MacroOperator":
            inner = element.value.get("Tools")
            if inner is not None:
                found = _find_tool(inner, name)
                if found is not None:
                    return found
    return None


def _write_toml(directory: pathlib.Path, name: str, text: str) -> pathlib.Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 合成した文書に対する検証
# --------------------------------------------------------------------------


def test_declaration_to_toml_round_trips_through_load_declaration():
    """``declare`` が起こした宣言を TOML へ書き、読み戻すと同じ宣言になる。

    ``declare`` の結果を人間やエージェントが編集できる形にするのがこの
    書き出しの役目であり、書いたものを ``apply`` が読めなければ意味が無い。
    """
    setting = document.parse((DATA / "sample.setting").read_text(encoding="utf-8"))
    decl, _inner = publish.declare(setting, file_stem="unused")
    text = publish.declaration_to_toml(decl, source="sample.comp")
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "sample.toml"
        path.write_text(text, encoding="utf-8")
        (pathlib.Path(td) / "sample.comp").write_text(
            (DATA / "sample.comp").read_text(encoding="utf-8"), encoding="utf-8")
        back = publish.load_declaration(path)
    assert back.name == decl.name
    assert back.source == "sample.comp"
    assert [(o.tool, o.port) for o in back.output] == [(o.tool, o.port) for o in decl.output]
    assert [(e.tool, e.input, e.label, e.page, e.expression) for e in back.publish] == [
        (e.tool, e.input, e.label, e.page, e.expression) for e in decl.publish
    ]


def test_declaration_to_toml_keeps_every_output_including_duplicates():
    """同じ (SourceOp, Source) を指す ``InstanceOutput`` が複数あってもまとめない。

    ``MainOutput1``/``MainOutput2`` の両方が同じツールの同じ出力を指すマクロがある。
    まとめるとマクロの出力ポートが1つ減る。
    """
    decl = publish.MacroDeclaration(
        name="dup",
        source="dup.comp",
        output=(publish.OutputEntry(tool="Merge1"), publish.OutputEntry(tool="Merge1")),
        publish=(publish.PublishEntry(tool="Template", input="StyledText"),),
    )
    text = publish.declaration_to_toml(decl, source="dup.comp")
    assert text.count("[[output]]") == 2


def test_declaration_to_toml_omits_defaults():
    """``page`` が ``Controls``、``port`` が ``Output``、``label`` が無い要素は書かない。"""
    decl = publish.MacroDeclaration(
        name="d",
        source="d.comp",
        output=(publish.OutputEntry(tool="Merge1", port="Output"),),
        publish=(
            publish.PublishEntry(tool="T", input="A", label=None, page="Controls"),
            publish.PublishEntry(tool="T", input="B", label="名前", page="Controls"),
        ),
    )
    text = publish.declaration_to_toml(decl, source="d.comp")
    assert "port =" not in text
    assert "page =" not in text
    assert text.count("label =") == 1


def test_expression_declared_appears_as_instance_input_expression():
    """``[[publish]]`` に ``expression`` を書くと、``InstanceInput`` 自体に
    ``Expression`` が現れることを確認する(内側のツールの式とは別物)。
    ``.comp`` に明示的な ``Value`` があっても、``expression`` を持つ公開入力には
    ``Default`` を書かない(規約: 式が値を決めるので ``Default`` は無視される)。
    """
    comp = _comp_from_tools_text(
        """{
            Template = TextPlus { Inputs = { Red1Clone = Input { Value = 0.5 } } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="expr_test",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(
            PublishEntry(
                tool="Template",
                input="Red1Clone",
                label="カラー",
                expression="iif(ColorSelect == 0, 0xE4, 0x41) / 255",
            ),
        ),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=None)
    tools = setting_doc.root.get("Tools")
    macro = tools.get("expr_test")
    entry = macro.get("Inputs").get("Template_Red1Clone")
    assert isinstance(entry, document.Table)
    assert entry.type_name == "InstanceInput"
    expr = entry.get("Expression")
    assert isinstance(expr, document.String)
    assert expr.value == "iif(ColorSelect == 0, 0xE4, 0x41) / 255"
    assert entry.get("Default") is None
    # 内側のツールの Red1Clone 自身の値は変更されない(InstanceInput 自身の式は別物であるため)。
    inner_value = tools.get("expr_test").get("Tools").get("Template").get("Inputs").get("Red1Clone").get("Value")
    assert isinstance(inner_value, document.Number)
    return PASS


def test_default_written_from_explicit_comp_value():
    """``.comp`` の ``Inputs`` に明示的な ``Value`` があり ``expression`` を
    持たない公開入力は、その値をそのまま ``Default`` として書く。"""
    comp = _comp_from_tools_text(
        """{
            Template = TextPlus { Inputs = { Size = Input { Value = 0.055 } } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="default_from_value",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="Template", input="Size", label="サイズ"),),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=None)
    entry = setting_doc.root.get("Tools").get("default_from_value").get("Inputs").get("Template_Size")
    default = entry.get("Default")
    assert isinstance(default, document.Number)
    assert default.raw == "0.055"
    return PASS


def test_default_not_written_without_explicit_comp_value():
    """``.comp`` に明示的な ``Value`` が無い入力は、スキーマが既定値を持って
    いても ``Default`` を書かない(規約: スキーマ・``UserControls`` の既定値へ
    フォールバックしない。Fusion 自身が既定値を補う)。"""
    schema_obj = schema.Schema.from_dict(
        {
            "version": "test",
            "registry": [{"id": "TextPlus"}, {"id": "Merge"}],
            "tools": {"TextPlus": {"Size": {"INPN_Default": 0.05}}},
            "failed": [],
        }
    )
    comp = _comp_from_tools_text(
        """{
            Template = TextPlus { Inputs = { } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="no_default",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="Template", input="Size", label="サイズ"),),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=schema_obj)
    entry = setting_doc.root.get("Tools").get("no_default").get("Inputs").get("Template_Size")
    assert entry.get("Default") is None
    return PASS


def _color_tool_schema() -> schema.Schema:
    """``Red1``/``Green1``/``Blue1``/``Alpha1`` が1つの制御、``Solo`` が
    単独の制御になる、合成したスキーマ。"""
    return schema.Schema.from_dict(
        {
            "version": "test",
            "registry": [{"id": "ColorTool"}, {"id": "Merge"}],
            "tools": {
                "ColorTool": {
                    "Red1": {"INPI_IC_ControlGroup": 1},
                    "Green1": {"INPI_IC_ControlGroup": 1, "INPI_IC_ControlID": 1},
                    "Blue1": {"INPI_IC_ControlGroup": 1, "INPI_IC_ControlID": 2},
                    "Alpha1": {"INPI_IC_ControlGroup": 1, "INPI_IC_ControlID": 3},
                    "Solo": {},
                },
            },
            "failed": [],
        }
    )


def test_control_group_assigned_only_when_multiple_members_published():
    """同じ制御に属する入力を2つ公開すると、同じ ``ControlGroup`` 番号を持つ。"""
    schema_obj = _color_tool_schema()
    comp = _comp_from_tools_text(
        """{
            C = ColorTool { Inputs = { } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="cg_test",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(
            PublishEntry(tool="C", input="Red1", label="R"),
            PublishEntry(tool="C", input="Alpha1", label="A"),
            PublishEntry(tool="C", input="Solo", label="S"),
        ),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=schema_obj)
    inputs = setting_doc.root.get("Tools").get("cg_test").get("Inputs")
    red_group = inputs.get("C_Red1").get("ControlGroup")
    alpha_group = inputs.get("C_Alpha1").get("ControlGroup")
    solo_group = inputs.get("C_Solo").get("ControlGroup")
    assert isinstance(red_group, document.Number)
    assert isinstance(alpha_group, document.Number)
    assert red_group.raw == alpha_group.raw == "1"
    assert solo_group is None
    return PASS


def test_control_group_not_assigned_for_single_member_of_multi_member_control():
    """複数の入力からなる制御でも、1つだけ公開すれば ``ControlGroup`` を持たない
    (`Font`/`Style` の制御から `Font` だけを公開する例)。"""
    schema_obj = _color_tool_schema()
    comp = _comp_from_tools_text(
        """{
            C = ColorTool { Inputs = { } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="cg_test2",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="C", input="Red1", label="R"),),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=schema_obj)
    entry = setting_doc.root.get("Tools").get("cg_test2").get("Inputs").get("C_Red1")
    assert entry.get("ControlGroup") is None
    return PASS


def test_control_group_assigned_for_user_controls_sharing_ic_control_group():
    """``UserControls`` 由来の入力(``IC_ControlGroup`` を共有する3件)を公開すると、
    同じ ``ControlGroup`` 番号を持つ ``InstanceInput`` になる。"""
    comp = _comp_from_tools_text(
        """{
            Template = TextPlus {
                Inputs = { },
                UserControls = ordered() {
                    CustomColorR = {
                        INPID_InputControl = "ColorControl", LINKID_DataType = "Number",
                        IC_ControlGroup = 1, IC_ControlID = 0,
                    },
                    CustomColorG = {
                        INPID_InputControl = "ColorControl", LINKID_DataType = "Number",
                        IC_ControlGroup = 1, IC_ControlID = 1,
                    },
                    CustomColorB = {
                        INPID_InputControl = "ColorControl", LINKID_DataType = "Number",
                        IC_ControlGroup = 1, IC_ControlID = 2,
                    },
                },
            },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="uc_group_test",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(
            PublishEntry(tool="Template", input="CustomColorR", label="カスタムカラー"),
            PublishEntry(tool="Template", input="CustomColorG"),
            PublishEntry(tool="Template", input="CustomColorB"),
        ),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=None)
    inputs = setting_doc.root.get("Tools").get("uc_group_test").get("Inputs")
    r_group = inputs.get("Template_CustomColorR").get("ControlGroup")
    g_group = inputs.get("Template_CustomColorG").get("ControlGroup")
    b_group = inputs.get("Template_CustomColorB").get("ControlGroup")
    assert isinstance(r_group, document.Number)
    assert r_group.raw == g_group.raw == b_group.raw == "1"
    return PASS


def test_missing_tool_raises_declaration_error_with_index():
    """宣言が指すツールが ``.comp`` に無ければ、何番目かを含めてエラーになる。"""
    comp = _comp_from_tools_text("{ Merge1 = Merge { Inputs = { } } }")
    declaration = MacroDeclaration(
        name="n",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="NoSuchTool", input="Foo", label="L"),),
    )
    try:
        publish.build_setting(declaration, comp)
    except publish.DeclarationError as exc:
        assert "publish[1]" in str(exc)
        assert "NoSuchTool" in str(exc)
        return PASS
    raise AssertionError("expected DeclarationError")


def test_missing_input_raises_declaration_error_when_schema_knows_the_type():
    """宣言が指す入力がツールに無く、スキーマがその型を知っていればエラーになる。"""
    schema_obj = _color_tool_schema()
    comp = _comp_from_tools_text(
        """{
            C = ColorTool { Inputs = { } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="n",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="C", input="NoSuchInput", label="L"),),
    )
    try:
        publish.build_setting(declaration, comp, schema=schema_obj)
    except publish.DeclarationError as exc:
        assert "publish[1]" in str(exc)
        assert "NoSuchInput" in str(exc)
        return PASS
    raise AssertionError("expected DeclarationError")


def test_missing_output_tool_raises_declaration_error():
    """``[[output]]`` が指すツールが ``.comp`` に無ければ、何番目かを含めて
    エラーになる。"""
    comp = _comp_from_tools_text("{ Template = TextPlus { Inputs = { } } }")
    declaration = MacroDeclaration(
        name="n", source="x.comp", output=(OutputEntry(tool="NoSuchOutput"),), publish=()
    )
    try:
        publish.build_setting(declaration, comp)
    except publish.DeclarationError as exc:
        assert "output[1]" in str(exc)
        assert "NoSuchOutput" in str(exc)
        return PASS
    raise AssertionError("expected DeclarationError")


def test_build_setting_requires_at_least_one_output():
    """``[[output]]`` が1つも無い宣言はエラーになる(参照されていないツールから
    推測しない)。"""
    comp = _comp_from_tools_text("{ Template = TextPlus { Inputs = { } } }")
    declaration = MacroDeclaration(name="n", source="x.comp", output=(), publish=())
    try:
        publish.build_setting(declaration, comp)
    except publish.DeclarationError as exc:
        assert "output" in str(exc)
        return PASS
    raise AssertionError("expected DeclarationError")


def test_multiple_outputs_numbered_in_order():
    """``[[output]]`` を複数書くと、並び順に ``MainOutput1``・``MainOutput2`` …
    というキーの ``InstanceOutput`` になる。``InstanceOutput`` は ``SourceOp``・
    ``Source`` だけを持ち、表示名は持たない。"""
    comp = _comp_from_tools_text(
        """{
            Template = TextPlus { Inputs = { } },
            Path1 = PolyPath { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="multi_output",
        source="x.comp",
        output=(OutputEntry(tool="Template"), OutputEntry(tool="Path1", port="Heading")),
        publish=(),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=None)
    outputs = setting_doc.root.get("Tools").get("multi_output").get("Outputs")
    first = outputs.get("MainOutput1")
    second = outputs.get("MainOutput2")
    assert isinstance(first, document.Table) and first.type_name == "InstanceOutput"
    assert isinstance(second, document.Table) and second.type_name == "InstanceOutput"
    assert first.get("SourceOp").value == "Template"
    assert first.get("Source").value == "Output"
    assert second.get("SourceOp").value == "Path1"
    assert second.get("Source").value == "Heading"
    assert {el.key.name for el in first.elements if el.key is not None} == {"SourceOp", "Source"}
    return PASS


def test_duplicate_output_entries_not_deduplicated():
    """同じ (``SourceOp``, ``Source``) を指す ``[[output]]`` を2つ書くと、
    重複排除せず2件の ``InstanceOutput`` になる(1つの
    ``MultiMerge1.Output`` を ``MainOutput1``/``MainOutput2`` の両方で指すプリセットがある)。
    """
    comp = _comp_from_tools_text("{ MultiMerge1 = MultiMerge { Inputs = { } } }")
    declaration = MacroDeclaration(
        name="dup_output",
        source="x.comp",
        output=(OutputEntry(tool="MultiMerge1"), OutputEntry(tool="MultiMerge1")),
        publish=(),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=None)
    outputs = setting_doc.root.get("Tools").get("dup_output").get("Outputs")
    assert outputs.get("MainOutput1").get("SourceOp").value == "MultiMerge1"
    assert outputs.get("MainOutput2").get("SourceOp").value == "MultiMerge1"
    return PASS


def test_label_omitted_writes_no_name():
    """``label`` を省略した ``[[publish]]`` は ``InstanceInput`` に ``Name`` を
    書かない(仕様「公開の宣言」)。"""
    comp = _comp_from_tools_text(
        """{
            Template = TextPlus { Inputs = { Green1 = Input { Value = 0.5 } } },
            Merge1 = Merge { Inputs = { } },
        }"""
    )
    declaration = MacroDeclaration(
        name="no_label",
        source="x.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="Template", input="Green1"),),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=None)
    entry = setting_doc.root.get("Tools").get("no_label").get("Inputs").get("Template_Green1")
    assert entry.get("Name") is None
    return PASS


def test_declare_omits_label_for_name_less_instance_input():
    """``Name`` を持たない ``InstanceInput`` は ``label=None`` として起こされ、
    再度 ``build_setting`` しても ``Name`` を書かない(往復の対称性)。"""
    setting_text = """{
        Tools = ordered() {
            MyMacro = MacroOperator {
                Inputs = ordered() {
                    Template_Green1 = InstanceInput {
                        SourceOp = "Template",
                        Source = "Green1",
                        Page = "Controls",
                    },
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "Merge1", Source = "Output", }, },
                Tools = ordered() {
                    Template = TextPlus { Inputs = { Green1 = Input { Value = 0.5 } }, },
                    Merge1 = Merge { Inputs = { }, },
                },
            },
        },
        ActiveTool = "MyMacro",
    }"""
    original = document.parse(setting_text)
    declaration, inner_tools = publish.declare(original, file_stem="unused")
    assert declaration.publish[0].label is None

    comp_like = document.Document(
        root=document.Table(
            "Composition", [document.Element(document.Key("ident", "Tools"), inner_tools)]
        ),
        indent=original.indent,
        trailing_newline=original.trailing_newline,
    )
    rebuilt = publish.build_setting(declaration, comp_like, schema=None)
    entry = rebuilt.root.get("Tools").get("MyMacro").get("Inputs").get("Template_Green1")
    assert entry.get("Name") is None
    return PASS


def _macro_wrapping_schema() -> schema.Schema:
    return schema.Schema.from_dict(
        {
            "version": "test",
            "registry": [{"id": "TextPlus"}, {"id": "Merge"}],
            "tools": {"TextPlus": {}, "Merge": {}},
            "outputs": {
                "TextPlus": {"Output": {"OUTS_ID": "Output", "OUTS_DataType": "Image"}},
                "Merge": {"Output": {"OUTS_ID": "Output", "OUTS_DataType": "Image"}},
            },
            "failed": [],
        }
    )


def test_output_declares_port_of_tool_without_schema_output_definition():
    """``[[output]]`` が指すツールの種別にスキーマの出力定義が無い場合(この例の
    ``MacroOperator`` を含む)、``port`` の実在は判定できないため、宣言に
    書かれた値をそのまま ``SourceOp``/``Source`` へ書く(仕様補完: 検証は
    ``graph.Graph.output_ports()`` が値を返せる場合に限られる。``no-macro``
    により ``.comp`` の内側に ``MacroOperator`` が現れることは無いはずだが、
    ``build_setting`` 自身はそれを検査しない。検査は ``cli`` が ``rules.check``
    と組み合わせて行う)。
    """
    schema_obj = _macro_wrapping_schema()
    comp = _comp_from_tools_text(
        """{
            Inner = MacroOperator {
                Inputs = ordered() { },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "T1", Source = "Output", }, },
                Tools = ordered() { T1 = TextPlus { Inputs = { }, }, },
            },
        }"""
    )
    declaration = MacroDeclaration(
        name="outer",
        source="x.comp",
        output=(OutputEntry(tool="Inner", port="MainOutput1"),),
        publish=(),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=schema_obj)
    outputs = setting_doc.root.get("Tools").get("outer").get("Outputs")
    assert outputs.get("MainOutput1").get("SourceOp").value == "Inner"
    assert outputs.get("MainOutput1").get("Source").value == "MainOutput1"
    return PASS


def test_output_port_not_validated_for_tool_without_schema_output_definition():
    """``[[output]]`` が指すツールの種別にスキーマの出力定義が無い場合、存在しない
    ``port`` を指定してもエラーにならない(判定不能を違反の根拠にしない、
    ``known_inputs`` と対にした立場。``output_ports`` は ``MacroOperator`` を
    特別扱いしないため、以前は検出できた「内側マクロに無い出力名」はもはや
    検出できない。検出は ``no-macro``/``output-reachable`` を通じて ``cli`` の
    ``check`` 側が担う)。
    """
    schema_obj = _macro_wrapping_schema()
    comp = _comp_from_tools_text(
        """{
            Inner = MacroOperator {
                Inputs = ordered() { },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "T1", Source = "Output", }, },
                Tools = ordered() { T1 = TextPlus { Inputs = { }, }, },
            },
        }"""
    )
    declaration = MacroDeclaration(
        name="outer",
        source="x.comp",
        output=(OutputEntry(tool="Inner", port="NoSuchOutput"),),
        publish=(),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=schema_obj)
    outputs = setting_doc.root.get("Tools").get("outer").get("Outputs")
    assert outputs.get("MainOutput1").get("Source").value == "NoSuchOutput"
    return PASS


def test_load_declaration_requires_output():
    """``output`` を欠いた宣言はエラーになる。"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_toml(pathlib.Path(d), "decl.toml", 'name = "n"\nsource = "x.comp"\n')
        try:
            publish.load_declaration(path)
        except publish.DeclarationError as exc:
            assert "output" in str(exc)
            return PASS
    raise AssertionError("expected DeclarationError")


def test_load_declaration_parses_publish_entries():
    """TOML の ``[[publish]]`` から ``PublishEntry`` の並びを、``[[output]]``
    から ``OutputEntry`` の並びを正しく読む。"""
    toml_text = """
name = "test_macro"
source = "some.comp"

[[output]]
tool = "Merge1"

[[output]]
tool = "CharacterLevelStyling1"
port = "StyledText"

[[publish]]
tool = "Template"
input = "StyledText"
label = "テキスト"

[[publish]]
tool = "Template"
input = "Red1Clone"
label = "カラー"
expression = "iif(ColorSelect == 2, CustomColorR, iif(ColorSelect == 0, 0xE4, 0x41) / 255)"
page = "Controls"
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_toml(pathlib.Path(d), "decl.toml", toml_text)
        declaration = publish.load_declaration(path)
    assert declaration.name == "test_macro"
    assert declaration.source == "some.comp"
    assert declaration.output == (
        OutputEntry(tool="Merge1"),
        OutputEntry(tool="CharacterLevelStyling1", port="StyledText"),
    )
    assert len(declaration.publish) == 2
    assert declaration.publish[0] == PublishEntry(tool="Template", input="StyledText", label="テキスト")
    assert declaration.publish[1].expression is not None
    assert declaration.publish[1].page == "Controls"
    return PASS


def test_load_comp_resolves_relative_to_declaration():
    """``source`` は宣言ファイルからの相対パスとして解決される。"""
    with tempfile.TemporaryDirectory() as d:
        directory = pathlib.Path(d)
        (directory / "x.comp").write_text(
            "Composition { Tools = { Merge1 = Merge { Inputs = { } } } }", encoding="utf-8"
        )
        decl_path = _write_toml(
            directory, "decl.toml", 'name = "n"\nsource = "x.comp"\n\n[[output]]\ntool = "Merge1"\n'
        )
        declaration = publish.load_declaration(decl_path)
        comp = publish.load_comp(declaration, decl_path)
    assert comp.root.type_name == "Composition"
    assert comp.root.get("Tools").get("Merge1") is not None
    return PASS


def test_declare_and_rebuild_synthetic_setting_matches():
    """小さな ``.setting`` を手で書き、``declare`` → ``build_setting`` で
    組み立て直したものが元と(往復の構造項目のうち検証可能なもので)一致する
    ことを確認する。``Outputs`` は2件持たせ、うち2件目は1件目と同じ
    (``SourceOp``, ``Source``)を指すことで、
    ``declare`` が重複を排除せず2件として起こすことも併せて確認する。"""
    setting_text = """{
        Tools = ordered() {
            MyMacro = MacroOperator {
                Inputs = ordered() {
                    Template_StyledText = InstanceInput {
                        SourceOp = "Template",
                        Source = "StyledText",
                        Page = "Controls",
                        Name = "テキスト",
                    },
                },
                Outputs = {
                    MainOutput1 = InstanceOutput {
                        SourceOp = "Merge1",
                        Source = "Output",
                    },
                    MainOutput2 = InstanceOutput {
                        SourceOp = "Merge1",
                        Source = "Output",
                    },
                },
                Tools = ordered() {
                    Template = TextPlus {
                        Inputs = { StyledText = Input { Value = "hi" } },
                    },
                    Merge1 = Merge {
                        Inputs = { },
                    },
                },
            },
        },
        ActiveTool = "MyMacro",
    }"""
    original = document.parse(setting_text)
    declaration, inner_tools = publish.declare(original, file_stem="unused")
    assert declaration.name == "MyMacro"
    assert declaration.source == ""
    assert declaration.output == (OutputEntry(tool="Merge1"), OutputEntry(tool="Merge1"))
    assert len(declaration.publish) == 1
    assert declaration.publish[0] == PublishEntry(
        tool="Template", input="StyledText", label="テキスト", page="Controls"
    )

    comp_like = document.Document(
        root=document.Table(
            "Composition", [document.Element(document.Key("ident", "Tools"), inner_tools)]
        ),
        indent=original.indent,
        trailing_newline=original.trailing_newline,
    )
    rebuilt = publish.build_setting(declaration, comp_like, schema=None)

    original_macro = macro_mod.read_macros(original.root.get("Tools"))[0]
    rebuilt_macro = macro_mod.read_macros(rebuilt.root.get("Tools"))[0]

    orig_pairs = [(p.source_op, p.source) for p in original_macro.publish]
    rebuilt_pairs = [(p.source_op, p.source) for p in rebuilt_macro.publish]
    assert orig_pairs == rebuilt_pairs

    assert original_macro.publish[0].name == rebuilt_macro.publish[0].name
    assert original_macro.publish[0].page == rebuilt_macro.publish[0].page

    orig_outputs = [(o.source_op, o.source) for o in original_macro.outputs]
    rebuilt_outputs = [(o.source_op, o.source) for o in rebuilt_macro.outputs]
    assert orig_outputs == rebuilt_outputs == [("Merge1", "Output"), ("Merge1", "Output")]
    return PASS


def test_declare_and_rebuild_tool_form_setting_matches():
    """tool 形の ``sample_tool.setting`` を ``declare`` → ``build_setting`` で
    組み立て直したものが、元と同じ木(ツール名、型名、入力、``ViewInfo``)になり、
    ``ActiveTool`` を持たないことを確認する。宣言の名前はファイル名から取り、
    ツール名は ``.setting`` のものを保つ。"""
    path = DATA / "sample_tool.setting"
    original = document.parse(path.read_text(encoding="utf-8"))
    declaration, tools = publish.declare(original, file_stem=path.stem)
    assert declaration == publish.ToolDeclaration(name="sample_tool", source="", tool="Template")

    comp_like = document.Document(
        root=document.Table("Composition", [document.Element(document.Key("ident", "Tools"), tools)]),
        indent=original.indent,
        trailing_newline=original.trailing_newline,
    )
    rebuilt = publish.build_setting(declaration, comp_like, schema=None)
    assert rebuilt.root.get("ActiveTool") is None
    assert document.dump(rebuilt) == document.dump(document.format_compact(original))
    return PASS


def test_tool_form_declaration_round_trips_through_toml():
    """tool 形の宣言は ``form`` と ``tool`` だけを書き、読み戻すと同じ宣言になる。"""
    decl = publish.ToolDeclaration(name="01 青", source="", tool="Template")
    text = publish.declaration_to_toml(decl, source="0001_01 青.comp")
    assert 'form = "tool"' in text
    assert "[[" not in text
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "d.toml"
        path.write_text(text, encoding="utf-8")
        back = publish.load_declaration(path)
    assert back == publish.ToolDeclaration(name="01 青", source="0001_01 青.comp", tool="Template")
    return PASS


def _load_toml_text(text: str):
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "d.toml"
        path.write_text(text, encoding="utf-8")
        return publish.load_declaration(path)


def _expect_declaration_error(text: str, fragment: str) -> None:
    try:
        _load_toml_text(text)
    except publish.DeclarationError as exc:
        assert fragment in str(exc), str(exc)
        return
    raise AssertionError(f"expected DeclarationError containing {fragment!r}")


def test_load_declaration_form_defaults_to_macro():
    """``form`` を省略した宣言は macro 形として読む。"""
    decl = _load_toml_text('name = "n"\nsource = "a.comp"\n[[output]]\ntool = "M"\n')
    assert isinstance(decl, publish.MacroDeclaration)
    assert decl.form == "macro"
    return PASS


def test_load_declaration_rejects_macro_keys_in_tool_form():
    """tool 形で ``[[publish]]`` や ``[[output]]`` を書くと、無視せずにエラーにする。"""
    base = 'name = "n"\nsource = "a.comp"\nform = "tool"\ntool = "T"\n'
    _expect_declaration_error(base + '[[publish]]\ntool = "T"\ninput = "Size"\n', "'publish' is not allowed")
    _expect_declaration_error(base + '[[output]]\ntool = "T"\n', "'output' is not allowed")
    _expect_declaration_error('name = "n"\nsource = "a.comp"\nform = "tool"\n', "'tool'")
    _expect_declaration_error(
        'name = "n"\nsource = "a.comp"\ntool = "T"\n[[output]]\ntool = "T"\n', "only allowed with form"
    )
    _expect_declaration_error('name = "n"\nsource = "a.comp"\nform = "group"\n', "'form' must be one of")
    return PASS


def test_tool_form_saves_only_the_named_tool():
    """``.comp`` が複数のツールを持つ場合、tool 形は ``tool`` が指すツールだけを、名前を変えずに保存する。
    指すツールが無い場合と ``MacroOperator`` の場合はエラーにする。"""
    comp = document.parse(
        'Composition { Tools = { Background1 = Background { }, Title = TextPlus { Inputs = { }, }, '
        'M = MacroOperator { Tools = ordered() { }, }, }, }'
    )
    built = publish.build_setting(publish.ToolDeclaration(name="x", source="", tool="Title"), comp)
    tools = built.root.get("Tools")
    assert [(e.key.name, e.value.type_name) for e in tools.elements] == [("Title", "TextPlus")]
    for tool, fragment in (("Missing", "does not exist"), ("M", "is a MacroOperator")):
        try:
            publish.build_setting(publish.ToolDeclaration(name="x", source="", tool=tool), comp)
        except publish.DeclarationError as exc:
            assert fragment in str(exc), str(exc)
        else:
            raise AssertionError(f"expected DeclarationError for {tool!r}")
    return PASS


SYNTHETIC_TESTS = [
    (
        "declaration_to_toml_round_trips_through_load_declaration",
        test_declaration_to_toml_round_trips_through_load_declaration,
    ),
    (
        "declaration_to_toml_keeps_every_output_including_duplicates",
        test_declaration_to_toml_keeps_every_output_including_duplicates,
    ),
    ("declaration_to_toml_omits_defaults", test_declaration_to_toml_omits_defaults),
    (
        "expression_declared_appears_as_instance_input_expression",
        test_expression_declared_appears_as_instance_input_expression,
    ),
    ("default_written_from_explicit_comp_value", test_default_written_from_explicit_comp_value),
    ("default_not_written_without_explicit_comp_value", test_default_not_written_without_explicit_comp_value),
    (
        "control_group_assigned_only_when_multiple_members_published",
        test_control_group_assigned_only_when_multiple_members_published,
    ),
    (
        "control_group_not_assigned_for_single_member_of_multi_member_control",
        test_control_group_not_assigned_for_single_member_of_multi_member_control,
    ),
    (
        "control_group_assigned_for_user_controls_sharing_ic_control_group",
        test_control_group_assigned_for_user_controls_sharing_ic_control_group,
    ),
    ("missing_tool_raises_declaration_error_with_index", test_missing_tool_raises_declaration_error_with_index),
    (
        "missing_input_raises_declaration_error_when_schema_knows_the_type",
        test_missing_input_raises_declaration_error_when_schema_knows_the_type,
    ),
    ("missing_output_tool_raises_declaration_error", test_missing_output_tool_raises_declaration_error),
    (
        "output_declares_port_of_tool_without_schema_output_definition",
        test_output_declares_port_of_tool_without_schema_output_definition,
    ),
    (
        "output_port_not_validated_for_tool_without_schema_output_definition",
        test_output_port_not_validated_for_tool_without_schema_output_definition,
    ),
    ("build_setting_requires_at_least_one_output", test_build_setting_requires_at_least_one_output),
    ("label_omitted_writes_no_name", test_label_omitted_writes_no_name),
    (
        "declare_omits_label_for_name_less_instance_input",
        test_declare_omits_label_for_name_less_instance_input,
    ),
    ("multiple_outputs_numbered_in_order", test_multiple_outputs_numbered_in_order),
    ("duplicate_output_entries_not_deduplicated", test_duplicate_output_entries_not_deduplicated),
    ("load_declaration_requires_output", test_load_declaration_requires_output),
    ("load_declaration_parses_publish_entries", test_load_declaration_parses_publish_entries),
    ("load_comp_resolves_relative_to_declaration", test_load_comp_resolves_relative_to_declaration),
    ("declare_and_rebuild_synthetic_setting_matches", test_declare_and_rebuild_synthetic_setting_matches),
    ("declare_and_rebuild_tool_form_setting_matches", test_declare_and_rebuild_tool_form_setting_matches),
    ("tool_form_declaration_round_trips_through_toml", test_tool_form_declaration_round_trips_through_toml),
    ("load_declaration_form_defaults_to_macro", test_load_declaration_form_defaults_to_macro),
    ("load_declaration_rejects_macro_keys_in_tool_form", test_load_declaration_rejects_macro_keys_in_tool_form),
    ("tool_form_saves_only_the_named_tool", test_tool_form_saves_only_the_named_tool),
]


# --------------------------------------------------------------------------
# tests/data/sample.comp に対する検証
# --------------------------------------------------------------------------


def test_sample_comp_minimal_declaration_builds_setting():
    """``sample.comp`` から最小の宣言で ``.setting`` を組み立て、
    ``document.parse`` で読み直せて、``Tools = ordered() {`` で始まり、
    ``ActiveTool`` がマクロ名であることを確認する。

    自作の最小データであり、常にリポジトリに含まれる。
    """
    sample_path = DATA / "sample.comp"
    comp = document.parse(sample_path.read_text(encoding="utf-8"))
    schema_obj = _load_schema_or_none()
    declaration = MacroDeclaration(
        name="sample_publish_test",
        source="sample.comp",
        output=(OutputEntry(tool="Merge1"),),
        publish=(PublishEntry(tool="Template", input="StyledText", label="テキスト"),),
    )
    setting_doc = publish.build_setting(declaration, comp, schema=schema_obj)
    text = document.dump(setting_doc)

    reparsed = document.parse(text)
    assert reparsed.root.type_name is None
    tools = reparsed.root.get("Tools")
    assert isinstance(tools, document.Table)
    assert tools.type_name == "ordered()"
    active_tool = reparsed.root.get("ActiveTool")
    assert isinstance(active_tool, document.String)
    assert active_tool.value == "sample_publish_test"

    body = text.lstrip()
    assert body.startswith("{")
    after_brace = body[1:].lstrip()
    assert after_brace.startswith("Tools = ordered() {"), after_brace[:40]

    macro = tools.get("sample_publish_test")
    assert isinstance(macro, document.Table)
    assert macro.type_name == "MacroOperator"
    entry = macro.get("Inputs").get("Template_StyledText")
    assert isinstance(entry, document.Table)
    assert entry.get("SourceOp").value == "Template"
    assert entry.get("Source").value == "StyledText"

    # 「.comp から .setting へ持ち越さないもの」(Prefs・Version・
    # CustomData.TEMPLATE_ID)が実際に削除されていることを確認する。sample.comp は
    # Composition 直下にこの3つを持つ。build_setting は comp.root.get("Tools")
    # だけを取り出すため、これらは自動的に持ち越されない。ここでは
    # 書き出したテキストにそのテキストが現れないことで示す(暗黙ではなく明示する)。
    assert "TEMPLATE_ID" not in text
    assert "GuideRatio" not in text  # Prefs.Comp.FrameFormat の値
    assert "DaVinci Resolve Studio" not in text  # Composition.Version の値
    # ルートは Tools と ActiveTool の2要素だけを持つ(Prefs 等の並びの残骸が無い)。
    assert [el.key.name for el in reparsed.root.elements if el.key is not None] == [
        "Tools",
        "ActiveTool",
    ]
    # 一方、マクロの内側のツール自身が持つ UserControls(ツールレベルの属性)は
    # そのまま持ち越される。「持ち越さない」のはコンポジション直下の
    # Prefs/Version/CustomData.TEMPLATE_ID だけであり、ツール自身が持つデータは
    # そのまま素通しする対象である(「.comp の Tools をそのまま内側に入れる」)。
    original_template = comp.root.get("Tools").get("Template")
    assert original_template.get("UserControls") is not None
    rebuilt_template = macro.get("Tools").get("Template")
    assert rebuilt_template.get("UserControls") is not None

    print(f"    (sample.comp) schema loaded: {schema_obj is not None}")
    return PASS


def test_sample_toml_apply_matches_sample_setting():
    """``tests/data/sample.setting`` は ``sample.toml`` を ``apply`` した結果と
    一致する(``tests/data/`` の構成の不変条件そのもの)。``sample.comp`` を
    書き換えた場合にこの一致が壊れることを、この検査が捕まえる。
    """
    toml_path = DATA / "sample.toml"
    setting_path = DATA / "sample.setting"
    schema_obj = _load_schema_or_none()
    if schema_obj is None:
        # StyledText/Font/Alpha1/Red1Clone はスキーマの入力定義がなければ
        # known_inputs が判定できず(UserControls には無い名前のため)、
        # build_setting が DeclarationError を送出する。sample.setting 自体は
        # スキーマがある環境で生成したものであるため、この一致検査もスキーマを要る。
        return SKIP

    declaration = publish.load_declaration(toml_path)
    comp = publish.load_comp(declaration, toml_path)
    built = publish.build_setting(declaration, comp, schema=schema_obj)

    expected = setting_path.read_text(encoding="utf-8")
    actual = document.dump(built)
    assert actual == expected, "tests/data/sample.setting is stale; regenerate it with `apply`"
    return PASS


REAL_COMP_TESTS = [
    ("sample_comp_minimal_declaration_builds_setting", test_sample_comp_minimal_declaration_builds_setting),
    ("sample_toml_apply_matches_sample_setting", test_sample_toml_apply_matches_sample_setting),
]


# --------------------------------------------------------------------------
# 既存プリセットに対する往復(宣言)の検証
# --------------------------------------------------------------------------


def _collect_preset_files() -> list[pathlib.Path]:
    env_dir = os.environ.get("FUSION_MACRO_CLI_PRESETS")
    if not env_dir:
        print("    FUSION_MACRO_CLI_PRESETS is not set")
        return []
    root = pathlib.Path(env_dir)
    if not root.is_dir():
        print(f"    FUSION_MACRO_CLI_PRESETS does not exist: {root}")
        return []
    return sorted(root.rglob("*.setting"))


def _has_explicit_scalar_value(inner_tools: document.Table, tool: str, input_name: str) -> bool:
    """``inner_tools`` の中で ``tool.input_name`` が ``.comp`` の ``Inputs`` に
    明示的な、単一の値として表現できる ``Value`` を持つかどうか。

    ``publish._find_tool`` とは独立に書いた ``_find_tool``(このファイル冒頭)を
    使う。座標や色などの複合値(``Table``)は「明示的な値」に数えない
    (``publish._current_value_node`` が単一の ``Default`` として表現できる
    ものだけを対象にするのと同じ判断)。
    """
    tool_table = _find_tool(inner_tools, tool)
    if tool_table is None:
        return False
    inputs_table = tool_table.get("Inputs")
    if inputs_table is None:
        return False
    entry = inputs_table.get(input_name)
    if not isinstance(entry, document.Table):
        return False
    value_node = entry.get("Value")
    return value_node is not None and not isinstance(value_node, document.Table)


def test_roundtrip_declaration_against_real_presets():
    """既存の ``.setting``(マクロを持つもの)について、宣言を起こし、その宣言と
    内側の ``Tools`` を ``.comp`` 相当として組み立て直したものを元と比較する。

    比較するのは、公開している(ツール,入力)の集合、その並び順、表示名、
    ``Page``、``Expression``、マクロの出力(構造の5項目)。マクロの出力は
    (``SourceOp``, ``Source``) の列として比較する(``[[output]]`` が複数の
    出力を並び順のまま表現できるため、``Outputs`` の全件を失わず比較できる)。
    バイト一致は求めない。公開の集合・並び順・出力の列が崩れることは実装の
    不具合を疑う(``declare``/``build_setting`` はどちらも文書をそのまま
    読み書きするだけで、ここが崩れる理由が無い)ため検査失敗にする。表示名・
    ``Page``・``Expression`` も同じ理由(そのまま読み書きするだけの情報)で
    検査失敗の対象にする。

    ``Default`` は規約の対象を限定する条件そのものが「元に ``Default`` がある」
    「``Expression`` を持たない」「内側の ``.comp`` に明示的な ``Value`` がある」
    の3つであるため、この条件を満たす公開入力についてだけ一致を求める
    (満たさない公開入力は、Fusion 自身が既定値を補う対象であり、比較の
    根拠が無い)。この条件下で不一致があれば列挙して報告する
    (「一致させるために実装を曲げないこと」)。
    """
    files = _collect_preset_files()
    if not files:
        print("    skipping declaration round-trip test")
        return SKIP

    schema_obj = _load_schema_or_none()
    if schema_obj is None:
        print("    no schema found; ControlGroup checks are degraded")

    macro_files: list[tuple[pathlib.Path, document.Document]] = []
    for path in files:
        doc = document.parse(path.read_text(encoding="utf-8"))
        tools_table = doc.root.get("Tools")
        if tools_table is None:
            continue
        if macro_mod.read_macros(tools_table):
            macro_files.append((path, doc))

    if not macro_files:
        print("    no .setting files with a MacroOperator found")
        return SKIP

    print(f"    {len(macro_files)} .setting files contain a MacroOperator")

    ok = 0
    mismatches: dict[str, list[tuple[str, str]]] = {}
    for path, original_doc in macro_files:
        tools_table = original_doc.root.get("Tools")
        try:
            declaration, inner_tools = publish.declare(original_doc, file_stem="unused")
        except publish.DeclarationError as exc:
            mismatches[path.name] = [("error", f"declare() failed: {exc}")]
            continue

        comp_like = document.Document(
            root=document.Table(
                "Composition", [document.Element(document.Key("ident", "Tools"), inner_tools)]
            ),
            indent=original_doc.indent,
            trailing_newline=original_doc.trailing_newline,
        )
        try:
            rebuilt_doc = publish.build_setting(declaration, comp_like, schema=schema_obj)
        except publish.DeclarationError as exc:
            mismatches[path.name] = [("error", f"build_setting() failed: {exc}")]
            continue

        original_macro = macro_mod.read_macros(tools_table)[0]
        rebuilt_macro = macro_mod.read_macros(rebuilt_doc.root.get("Tools"))[0]

        # (診断名, 文言) の組で記録する。"default" だけは既存データの性質
        # (Fusion 自身が書いた計算結果のキャッシュ)による差異がありうるため
        # 報告に留め、それ以外(集合・並び順・表示名・Page・Expression・出力の列)
        # は build_setting/declare がそのまま読み書きするだけの情報であり、
        # 一致しない理由が無いため検査失敗にする。
        diffs: list[tuple[str, str]] = []
        orig_pairs = [(p.source_op, p.source) for p in original_macro.publish]
        rebuilt_pairs = [(p.source_op, p.source) for p in rebuilt_macro.publish]
        if orig_pairs != rebuilt_pairs:
            diffs.append(("pairs", f"published (tool,input) set/order: {orig_pairs} != {rebuilt_pairs}"))
        else:
            for i, (po, pr) in enumerate(zip(original_macro.publish, rebuilt_macro.publish)):
                # Name を持たない公開入力は label=None として往復する(仕様
                # 「公開の宣言」: Name の無い公開入力に label を書かない)。
                # スキーマ由来の表示名へのフォールバックは行わないため、
                # po.name と pr.name をそのまま(どちらも None ならそれも
                # 一致として)比べる。
                if po.name != pr.name:
                    diffs.append(("name", f"[{i}] {po.key}: name {po.name!r} != {pr.name!r}"))
                orig_page = po.page or "Controls"
                rebuilt_page = pr.page or "Controls"
                if orig_page != rebuilt_page:
                    diffs.append(("page", f"[{i}] {po.key}: page {orig_page!r} != {rebuilt_page!r}"))
                if (
                    po.default is not None
                    and po.expression is None
                    and po.source_op
                    and po.source
                    and _has_explicit_scalar_value(inner_tools, po.source_op, po.source)
                    and po.default != pr.default
                ):
                    diffs.append(("default", f"[{i}] {po.key}: default {po.default!r} != {pr.default!r}"))
                if po.expression != pr.expression:
                    diffs.append(
                        ("expression", f"[{i}] {po.key}: expression {po.expression!r} != {pr.expression!r}")
                    )

        orig_outputs = [(o.source_op, o.source) for o in original_macro.outputs]
        rebuilt_outputs = [(o.source_op, o.source) for o in rebuilt_macro.outputs]
        if orig_outputs != rebuilt_outputs:
            diffs.append(("outputs", f"outputs: {orig_outputs} != {rebuilt_outputs}"))

        if diffs:
            mismatches[path.name] = diffs
        else:
            ok += 1

    print(f"    {ok}/{len(macro_files)} files match (structural items + conditional Default)")
    for name, diffs in mismatches.items():
        print(f"    -- {name}")
        for _, d in diffs:
            print(f"       {d}")

    # "default"(既存データの性質による既知の差異)を除く種類が1件でもあれば、
    # 実装の不具合を疑い検査を失敗させる。
    soft_kinds = {"default"}
    structural_failures = {
        name: [d for kind, d in diffs if kind not in soft_kinds]
        for name, diffs in mismatches.items()
        if any(kind not in soft_kinds for kind, _ in diffs)
    }
    assert not structural_failures, f"structural mismatches: {structural_failures}"
    return PASS


def _is_schema_derived(
    schema_obj: schema.Schema | None, tool_type: str | None, input_name: str
) -> bool:
    """入力の制御がスキーマ由来か(``UserControls`` 由来ではないか)を返す。

    ``graph.Graph.control_of`` 自身の分岐(スキーマがこの種別を持ち、かつ
    この入力名まで知っている場合だけスキーマ側の判定へ進む)を、外側から
    判定するために同じ条件を素通しする。この条件式は ``rules.py`` の
    ``_label_control_state`` が ``from_schema`` を決めるのに使う条件と同じ
    (``schema.Schema.has_inputs``/``input_names`` はどちらも公開 API)。
    """
    return (
        schema_obj is not None
        and tool_type is not None
        and schema_obj.has_inputs(tool_type)
        and input_name in schema_obj.input_names(tool_type)
    )


def test_user_control_groups_share_control_group_like_original():
    """``UserControls`` 由来で2入力以上からなる制御に属する公開入力を、実在の
    ``.setting`` 群(``FUSION_MACRO_CLI_PRESETS``)から集め、その制御に属する
    公開入力どうしが元の ``.setting`` で同じ ``ControlGroup`` を共有していること、
    および宣言を起こして組み立て直した結果でも同じ制御の入力どうしが同じ
    ``ControlGroup`` を共有することを確かめる。番号の値そのもの
    (``[[publish]]`` の並び順で1から振り直される)ではなく、「同じ制御の
    入力が同じ番号を共有する」ことだけを問う。

    対象の選定は ``graph.Graph.control_of`` に委ね、個別のツール名・入力名は
    書かない(実データがどのツール・入力を使うかに依存しない一般形にするため)。
    該当する公開入力が1件も無ければ、それを検査失敗にはしない
    (対象が無いことは実装の不具合ではないため)。
    """
    files = _collect_preset_files()
    if not files:
        print("    skipping user-control group test")
        return SKIP

    schema_obj = _load_schema_or_none()

    checked = 0
    mismatches: dict[str, list[str]] = {}

    for path in files:
        original_doc = document.parse(path.read_text(encoding="utf-8"))
        tools_table = original_doc.root.get("Tools")
        if tools_table is None:
            continue
        if not macro_mod.read_macros(tools_table):
            continue

        g = graph.try_build_graph(tools_table, schema=schema_obj)
        if g is None:
            continue

        try:
            declaration, inner_tools = publish.declare(original_doc, file_stem="unused")
        except publish.DeclarationError:
            continue
        comp_like = document.Document(
            root=document.Table(
                "Composition", [document.Element(document.Key("ident", "Tools"), inner_tools)]
            ),
            indent=original_doc.indent,
            trailing_newline=original_doc.trailing_newline,
        )
        try:
            rebuilt_doc = publish.build_setting(declaration, comp_like, schema=schema_obj)
        except publish.DeclarationError:
            continue

        original_macro = macro_mod.read_macros(tools_table)[0]
        rebuilt_macro = macro_mod.read_macros(rebuilt_doc.root.get("Tools"))[0]

        by_source_orig = {(p.source_op, p.source): p.control_group for p in original_macro.publish}
        by_source_rebuilt = {(p.source_op, p.source): p.control_group for p in rebuilt_macro.publish}

        # 同じ制御を複数の公開入力から重複して処理しないための既訪問集合。
        seen_controls: set[tuple[str, tuple[str, ...]]] = set()
        for p in original_macro.publish:
            if p.source_op is None or p.source is None:
                continue
            tool_type = g.tool_type(p.source_op)
            if _is_schema_derived(schema_obj, tool_type, p.source):
                continue
            control = g.control_of(p.source_op, p.source)
            if control is None or len(control) < 2:
                continue
            control_key = (p.source_op, tuple(control))
            if control_key in seen_controls:
                continue
            seen_controls.add(control_key)

            published_members = [name for name in control if (p.source_op, name) in by_source_orig]
            checked += len(published_members)

            orig_values = [by_source_orig[(p.source_op, name)] for name in published_members]
            assert all(v is not None for v in orig_values), (
                f"{path.name}: {p.source_op}.{control}: original ControlGroup missing: {orig_values}"
            )
            if len(set(orig_values)) != 1:
                mismatches.setdefault(path.name, []).append(
                    f"{p.source_op}.{control}: original ControlGroup not shared: {orig_values}"
                )

            rebuilt_values = [by_source_rebuilt.get((p.source_op, name)) for name in published_members]
            assert all(v is not None for v in rebuilt_values), (
                f"{path.name}: {p.source_op}.{control}: rebuilt ControlGroup missing: {rebuilt_values}"
            )
            if len(set(rebuilt_values)) != 1:
                mismatches.setdefault(path.name, []).append(
                    f"{p.source_op}.{control}: rebuilt ControlGroup not shared: {rebuilt_values}"
                )

    print(f"    checked {checked} published inputs (UserControls-derived, multi-input controls)")
    assert not mismatches, f"ControlGroup sharing mismatches: {mismatches}"
    return PASS


REAL_SETTING_TESTS = [
    ("roundtrip_declaration_against_real_presets", test_roundtrip_declaration_against_real_presets),
    (
        "user_control_groups_share_control_group_like_original",
        test_user_control_groups_share_control_group_like_original,
    ),
]


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------


def main() -> int:
    any_failure = False
    any_skip = False
    for name, fn in [*SYNTHETIC_TESTS, *REAL_COMP_TESTS, *REAL_SETTING_TESTS]:
        print(f"--- {name}")
        try:
            outcome = fn()
        except AssertionError as exc:
            print(f"    FAILED: {exc}")
            any_failure = True
            continue
        except Exception as exc:  # noqa: BLE001 - 想定外の例外も失敗として扱う。無害化しない。
            print(f"    ERROR: {exc!r}")
            any_failure = True
            continue
        if outcome == SKIP:
            print("    skipped")
            any_skip = True
        else:
            print("    passed")

    if any_failure:
        return 1
    if any_skip:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
