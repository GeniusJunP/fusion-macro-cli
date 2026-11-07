"""``fusion_macro_cli.graph`` の検証。

単独実行できる(``python3 tests/test_graph.py``)。終了コードは、致命的な失敗が
1件でもあれば1、失敗は無いが実データに依存する検査(既存プリセットの走査)が
対象データの不在で行えなかった場合は2、すべて実行できて失敗が無ければ0を返す
(``tests/test_document.py`` と同じ規約)。``tests/data/sample.comp``/
``sample.setting`` を使う検査は常に実行する。

合成した小さな文書に対する検証は、``document.parse`` で作った ``Table`` を
``graph.build_graph`` / ``macro_mod.read_macros`` へそのまま渡す(公開 API だけを
経由する)。式からの参照抽出は Fusion 固有のヒューリスティックであり、
内部関数を直接呼ばず、合成した
``Expression`` 文字列を実際にツールへ持たせて ``build_graph`` の出力から確認する。
"""

from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.document as document  # noqa: E402
import fusion_macro_cli.graph as graph  # noqa: E402
import fusion_macro_cli.macro as macro_mod  # noqa: E402
import fusion_macro_cli.schema as schema  # noqa: E402

SKIP = "skip"
PASS = "pass"


# --------------------------------------------------------------------------
# 合成した文書に対する検証
# --------------------------------------------------------------------------


def _tools(text: str) -> document.Table:
    """``Tools = { ... }`` の中身だけを取り出すヘルパー。"""
    doc = document.parse("{ Tools = " + text + ", }")
    tools = doc.root.get("Tools")
    assert isinstance(tools, document.Table)
    return tools


def test_connection_and_expression_edges():
    """接続と式、2種類の辺が読めること。式は同じツール内の裸の識別子と、
    他ツールを指すドット形の識別子の両方を参照として扱うことを確認する。"""
    tools = _tools(
        """{
            A = ToolA {
                Inputs = {
                    X = Input { Value = 1, },
                    Y = Input { SourceOp = "B", Source = "Output", },
                    Z = Input { Value = 2, Expression = "iif(W == 0, 1, 2) + Missing.Foo", },
                    W = Input { Value = 0, },
                },
            },
            B = ToolB {
                Inputs = {
                    In = Input { SourceOp = "Ghost", Source = "Output", },
                },
            },
        }"""
    )
    g = graph.build_graph(tools)

    assert set(g.tools()) == {"A", "B"}
    assert g.tool_type("A") == "ToolA"
    assert g.tool_type("B") == "ToolB"

    connections = [e for e in g.edges() if e.kind == "connection"]
    assert len(connections) == 2, connections
    y_edge = next(e for e in connections if e.tool == "A" and e.input == "Y")
    assert y_edge.target == graph.Target("B", "Output")
    in_edge = next(e for e in connections if e.tool == "B" and e.input == "In")
    assert in_edge.target == graph.Target("Ghost", "Output")

    expressions = [e for e in g.edges() if e.kind == "expression"]
    targets = {e.target for e in expressions}
    assert graph.Target("A", "W") in targets, "bare identifier must resolve to the owning tool"
    assert graph.Target("Missing", "Foo") in targets, "dotted identifier must resolve to the named tool"
    assert all(e.tool == "A" and e.input == "Z" for e in expressions)
    assert all(e.expression == "iif(W == 0, 1, 2) + Missing.Foo" for e in expressions)

    driven = g.driven_inputs()
    assert driven == [graph.DrivenInput("A", "Z", "iif(W == 0, 1, 2) + Missing.Foo")]

    dangling = g.dangling_edges()
    dangling_targets = {(e.tool, e.input, e.target) for e in dangling}
    assert dangling_targets == {
        ("B", "In", graph.Target("Ghost", "Output")),
        ("A", "Z", graph.Target("Missing", "Foo")),
    }, dangling_targets

    return PASS


def test_expression_excludes_lua_reserved_stdlib_and_calls():
    """Lua の予約語・標準ライブラリ名・関数呼び出しの直前の識別子を参照として扱わないこと。

    ``comp:GetPrefs().Comp.FrameFormat.Rate`` のように、関数呼び出しの戻り値への
    属性アクセスも参照として扱わないこと。
    """
    tools = _tools(
        """{
            A = ToolA {
                Inputs = {
                    Z = Input {
                        Value = 0,
                        Expression = "iif(not true, Sibling, 0) + comp.RenderHeight + math.sin(1) + comp:GetPrefs().Comp.FrameFormat.Rate",
                    },
                    Sibling = Input { Value = 1, },
                },
            },
        }"""
    )
    g = graph.build_graph(tools)
    expressions = [e for e in g.edges() if e.kind == "expression"]
    targets = {e.target for e in expressions}
    assert targets == {graph.Target("A", "Sibling")}, targets

    return PASS


def test_duplicate_tool_name_raises():
    """同じツール名が複数回現れた場合に ``DuplicateToolNameError`` を送出すること。"""
    tools = _tools(
        """{
            A = ToolA { Inputs = {}, },
            A = ToolB { Inputs = {}, },
        }"""
    )
    try:
        graph.build_graph(tools)
    except graph.DuplicateToolNameError:
        pass
    else:
        raise AssertionError("expected DuplicateToolNameError")

    return PASS


def test_build_graph_recurses_into_macro_tools():
    """マクロの内側のツールも同じ平坦なグラフに含まれ、外側のツールが
    マクロの境界をまたいで直接参照できること(マクロの外にある ``MediaOut1`` が
    マクロの内側の ``Merge1`` を直接指す、という典型的な形。モジュール docstring)。"""
    tools = _tools(
        """{
            Outer = MediaOut {
                Inputs = {
                    Input = Input { SourceOp = "Inner", Source = "Output", },
                },
            },
            MyMacro = MacroOperator {
                Inputs = ordered() {},
                Outputs = { Out1 = InstanceOutput { SourceOp = "Inner", Source = "Output", }, },
                Tools = ordered() {
                    Inner = SomeTool { Inputs = {}, },
                },
            },
        }"""
    )
    g = graph.build_graph(tools)
    assert set(g.tools()) == {"Outer", "MyMacro", "Inner"}
    assert g.dangling_edges() == []

    return PASS


def test_reachable_from_follows_connections_only_and_respects_type():
    """接続エッジだけを遡り、式エッジは辿らないこと。型を指定すると絞り込めること。"""
    fake_schema = schema.Schema.from_dict(
        {
            "version": "0",
            "registry": [],
            "tools": {
                "ToolA": {"Y": {"INPS_ID": "Y", "INPS_DataType": "Image"}},
                "ToolB": {"In": {"INPS_ID": "In", "INPS_DataType": "Mask"}},
            },
            "failed": [],
        }
    )
    tools = _tools(
        """{
            A = ToolA {
                Inputs = {
                    Y = Input { SourceOp = "B", Source = "Output", },
                    Z = Input { Value = 0, Expression = "Unreached.Value", },
                },
            },
            B = ToolB {
                Inputs = {
                    In = Input { SourceOp = "C", Source = "Output", },
                },
            },
            C = ToolC { Inputs = {}, },
        }"""
    )
    g = graph.build_graph(tools, schema=fake_schema)

    all_reachable = g.reachable_from("A")
    assert all_reachable == {"B", "C"}, all_reachable
    assert "Unreached" not in all_reachable, "expression edges must not be followed"

    image_only = g.reachable_from("A", data_type="Image")
    assert image_only == {"B"}, image_only  # A->B は Image、B->C は Mask なので止まる

    assert g.reachable_from("C") == set()

    return PASS


def test_tool_table_accessor_reaches_macro_internals():
    """``Graph.tool_table`` がツール名からそのツールの生の ``Table`` を返すこと。

    マクロの内側にあるツールも同じ平坦な名前空間で引けること、マクロ自身の名前を
    渡した場合は ``MacroOperator`` のテーブルが返り、``Inputs``・``Outputs``・
    ``Tools`` が ``document.Table.get()`` でそのまま辿れることを確認する
    (``publish.py`` ``rules.py`` ``show.py`` が個別に持つ ``_find_tool`` の置き換え先)。
    """
    tools = _tools(
        """{
            Outer = MediaOut {
                Inputs = {
                    Input = Input { SourceOp = "Inner", Source = "Output", },
                },
            },
            MyMacro = MacroOperator {
                Inputs = ordered() {
                    Inner_Value = InstanceInput { SourceOp = "Inner", Source = "Value", },
                },
                Outputs = { Out1 = InstanceOutput { SourceOp = "Inner", Source = "Output", }, },
                Tools = ordered() {
                    Inner = SomeTool { Inputs = { Value = Input { Value = 1, }, }, },
                },
            },
        }"""
    )
    g = graph.build_graph(tools)

    outer_table = g.tool_table("Outer")
    assert isinstance(outer_table, document.Table)
    assert outer_table.type_name == "MediaOut"

    inner_table = g.tool_table("Inner")
    assert isinstance(inner_table, document.Table)
    assert inner_table.type_name == "SomeTool"

    macro_table = g.tool_table("MyMacro")
    assert isinstance(macro_table, document.Table)
    assert macro_table.type_name == "MacroOperator"
    macro_inputs = macro_table.get("Inputs")
    macro_outputs = macro_table.get("Outputs")
    macro_tools = macro_table.get("Tools")
    assert isinstance(macro_inputs, document.Table) and macro_inputs.get("Inner_Value") is not None
    assert isinstance(macro_outputs, document.Table) and macro_outputs.get("Out1") is not None
    assert isinstance(macro_tools, document.Table) and macro_tools.get("Inner") is inner_table

    assert g.tool_table("NoSuchTool") is None

    return PASS


def _output_schema() -> schema.Schema:
    return schema.Schema.from_dict(
        {
            "version": "test",
            "registry": [{"id": "TextPlus"}, {"id": "PolyPath"}],
            "tools": {"TextPlus": {}, "PolyPath": {}},
            "outputs": {
                "TextPlus": {"Output": {"OUTS_ID": "Output", "OUTS_DataType": "Image"}},
                "PolyPath": {
                    "Position": {"OUTS_ID": "Position", "OUTS_DataType": "Point"},
                    "Heading": {"OUTS_ID": "Heading", "OUTS_DataType": "Number"},
                },
            },
            "failed": [],
        }
    )


def test_output_ports_uses_schema_output_definitions():
    """``output_ports`` はスキーマの出力定義(名前と ``OUTS_DataType``)を
    そのまま返す。``MacroOperator`` を特別扱いしない(``no-macro`` により
    内側にこの種別が現れることはなく、スキーマもこの種別の出力定義を
    持たないため、他の未知種別と同じく判定不能(``None``)になる)。"""
    tools = _tools("""{ T1 = TextPlus { Inputs = { }, }, }""")
    g = graph.build_graph(tools, _output_schema())
    assert g.output_ports("T1") == {"Output": "Image"}
    # スキーマにこの種別の出力定義が無ければ判定不能(None)。
    tools2 = _tools("""{ T2 = Merge { Inputs = { }, }, }""")
    g2 = graph.build_graph(tools2, _output_schema())
    assert g2.output_ports("T2") is None
    # ツール自体が存在しない場合も判定不能。
    assert g.output_ports("NoSuchTool") is None
    # MacroOperator もスキーマに出力定義を持たないため判定不能。
    tools3 = _tools(
        """{
            M = MacroOperator {
                Inputs = ordered() { },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "T1", Source = "Output", }, },
                Tools = ordered() { T1 = TextPlus { Inputs = { }, }, },
            },
        }"""
    )
    g3 = graph.build_graph(tools3, _output_schema())
    assert g3.output_ports("M") is None
    return PASS


def test_user_controls_readout():
    """``UserControls`` の各属性と、コンボの選択肢(位置引数の ``CCS_AddString``)が読めること。"""
    tools = _tools(
        """{
            A = TextPlus {
                Inputs = {},
                UserControls = ordered() {
                    Choice = {
                        { CCS_AddString = "one" },
                        { CCS_AddString = "two" },
                        LINKID_DataType = "Number",
                        INPID_InputControl = "ComboControl",
                        LINKS_Name = "選択",
                        INP_External = true,
                        INP_Integer = true,
                        IC_ControlGroup = 5,
                        IC_ControlID = 1,
                    },
                    Divider = {
                        INPID_InputControl = "LabelControl",
                        INP_Passive = true,
                        LINKID_DataType = "Number",
                    },
                },
            },
        }"""
    )
    g = graph.build_graph(tools)
    controls = g.user_controls("A")
    assert list(controls.keys()) == ["Choice", "Divider"], controls.keys()

    choice = controls["Choice"]
    assert choice.data_type == "Number"
    assert choice.control_type == "ComboControl"
    assert choice.external is True
    assert choice.choices == ["one", "two"]
    assert choice.control_group == 5
    assert choice.has_control_id is True
    assert choice.raw["INP_Integer"] is True, choice.raw

    divider = controls["Divider"]
    assert divider.control_type == "LabelControl"
    assert divider.passive is True
    assert divider.control_group is None
    assert divider.has_control_id is False
    assert divider.choices == []

    assert g.user_controls("NoSuchTool") == {}

    return PASS


def test_control_of_groups_user_controls_by_shared_control_group():
    """``Graph.control_of``: スキーマ由来の入力は ``schema.Schema.control_of`` の
    区切りを、``UserControls`` 由来の入力は同じツールの中で ``IC_ControlGroup``
    を共有する集合(``IC_ControlID`` 昇順)を返す。実データに見られる、
    ``UserControls`` として追加された色3入力に相当する形を合成する。"""
    s = schema.Schema.from_dict(
        {
            "version": "test",
            "registry": [{"id": "TextPlus"}],
            "tools": {
                "TextPlus": {
                    "Font": {"INPS_ID": "Font", "INPI_IC_ControlGroup": 3},
                    "Style": {
                        "INPS_ID": "Style",
                        "INPI_IC_ControlGroup": 3,
                        "INPI_IC_ControlID": 1,
                    },
                },
            },
            "failed": [],
        }
    )
    tools = _tools(
        """{
            A = TextPlus {
                Inputs = {},
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
                    Standalone = {
                        INPID_InputControl = "LabelControl", LINKID_DataType = "Number",
                    },
                },
            },
        }"""
    )
    g = graph.build_graph(tools, s)

    # スキーマ由来: schema.control_of の区切りがそのまま使われる。
    assert g.control_of("A", "Font") == ["Font", "Style"]
    assert g.control_of("A", "Style") == ["Font", "Style"]

    # UserControls 由来: IC_ControlGroup を共有する3件が1つの制御になり、
    # IC_ControlID の昇順に並ぶ。
    expected = ["CustomColorR", "CustomColorG", "CustomColorB"]
    assert g.control_of("A", "CustomColorR") == expected
    assert g.control_of("A", "CustomColorG") == expected
    assert g.control_of("A", "CustomColorB") == expected

    # IC_ControlGroup を持たない UserControls 入力は単独の制御になる。
    assert g.control_of("A", "Standalone") == ["Standalone"]

    # どちらの情報源からも判定できない場合は None。
    assert g.control_of("A", "NoSuchInput") is None
    assert g.control_of("NoSuchTool", "X") is None

    return PASS


def test_user_controls_fallback_for_edge_data_type():
    """入力がスキーマに無い場合、その入力自身の ``UserControls`` の
    ``LINKID_DataType`` を辺の型として使うこと(``graph.py`` docstring の仕様補完)。"""
    tools = _tools(
        """{
            A = TextPlus {
                Inputs = {
                    ColorSelect = Input { Value = 0, },
                    Y = Input { SourceOp = "B", Source = "Output", },
                },
                UserControls = ordered() {
                    ColorSelect = { LINKID_DataType = "Number", INPID_InputControl = "ComboControl", },
                },
            },
            B = ToolB { Inputs = {}, },
        }"""
    )
    g = graph.build_graph(tools)
    # Y はスキーマも UserControls も持たないため型は不明
    y_edge = next(e for e in g.edges() if e.tool == "A" and e.input == "Y")
    assert y_edge.data_type is None

    return PASS


def test_read_macros_publish_order_and_shell_expression():
    """公開入力が並び順に読め、``InstanceInput`` 自体が持つ ``Expression`` が
    内側のツールの ``Expression`` とは別に読めること。"""
    tools = _tools(
        """{
            MyMacro = MacroOperator {
                Inputs = ordered() {
                    Tool_First = InstanceInput {
                        SourceOp = "Tool", Source = "First", Name = "一", Page = "Controls",
                    },
                    Tool_Second = InstanceInput {
                        SourceOp = "Tool", Source = "Second", Page = "Controls", ControlGroup = 1,
                        Expression = "iif(First == 0, A, B)", Default = 0.5,
                    },
                },
                Outputs = {
                    MainOutput1 = InstanceOutput { SourceOp = "Tool", Source = "Output", },
                },
                Tools = ordered() {
                    Tool = SomeTool { Inputs = {}, },
                },
            },
        }"""
    )
    macros = macro_mod.read_macros(tools)
    assert len(macros) == 1
    macro = macros[0]
    assert macro.name == "MyMacro"
    assert macro.tools == frozenset({"Tool"})
    assert [p.key for p in macro.publish] == ["Tool_First", "Tool_Second"], macro.publish
    assert macro.publish[0].source_op == "Tool"
    assert macro.publish[0].source == "First"
    assert macro.publish[0].expression is None
    second = macro.publish[1]
    assert second.control_group == 1
    assert second.expression == "iif(First == 0, A, B)"
    assert second.default == 0.5
    assert macro.outputs == [macro_mod.MacroOutput("MainOutput1", "Tool", "Output")]

    # このマクロが公開する式は InstanceInput 自体のものであり、内側の Tool には
    # 何の Input も無いため、グラフ側の「式で駆動される入力」には現れない。
    g = graph.build_graph(tools)
    assert g.driven_inputs(macro.tools) == []

    return PASS


# --------------------------------------------------------------------------
# キーフレーム時刻の範囲
# --------------------------------------------------------------------------


def test_keyframe_range_scans_modifiers_recursively():
    """``KeyFrames`` を持つ全ての表(モディファイア自身のテーブル)を、マクロの
    内側を含めて再帰的に走査し、キー(時刻)の最小値・最大値を返すこと。

    キーは数値のテキスト(``document.Key.raw``)であり、小数(``[0.998749]``)も
    ``float`` へ変換して扱えることを確認する。``KeyFrames`` を持たない表
    (``KeyColorSplines`` など、キーの名前が違うもの)は対象にしないこと、
    ``KeyFrames`` が1つも無ければ ``None`` を返すことも確認する。
    """
    tools = _tools(
        """{
            Mod1 = BezierSpline {
                KeyFrames = {
                    [0] = { 0, Flags = { Linear = true } },
                    [0.998749] = { 1, Flags = { Linear = true } },
                },
            },
            MyMacro = MacroOperator {
                Inputs = ordered() {},
                Outputs = {},
                Tools = ordered() {
                    Mod2 = BezierSpline {
                        KeyFrames = { [10] = { 0.5, }, [50] = { 0.9, }, },
                    },
                    OtherMod = LUTBezier {
                        KeyColorSplines = { [0] = { [0] = { 0, }, }, },
                    },
                },
            },
        }"""
    )
    result = graph.keyframe_range(tools)
    assert result == (0.0, 50.0), result

    empty = _tools("""{ A = ToolA { Inputs = {}, }, }""")
    assert graph.keyframe_range(empty) is None

    return PASS


# --------------------------------------------------------------------------
# sample.setting に対する検証
# --------------------------------------------------------------------------


def _sample_setting_path() -> pathlib.Path:
    return HERE / "data" / "sample.setting"


def test_sample_setting_graph_and_macro():
    """``sample.setting`` からグラフを作り、ツール数・接続エッジ・式エッジ・
    マクロの公開入力の並び順が読めることを確認する。

    自作の最小データであり、常にリポジトリに含まれる。
    """
    text = _sample_setting_path().read_text(encoding="utf-8")
    doc = document.parse(text)
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)

    g = graph.build_graph(tools_table)
    assert len(g.tools()) > 0
    assert g.dangling_edges() == [], g.dangling_edges()

    connections = [e for e in g.edges() if e.kind == "connection"]
    expressions = [e for e in g.edges() if e.kind == "expression"]
    assert connections, "sample.setting must contain at least one connection edge"
    assert expressions, "sample.setting must contain at least one expression edge (Red1)"

    macros = macro_mod.read_macros(tools_table)
    assert len(macros) == 1, "sample.setting must contain exactly one macro"
    macro = macros[0]
    assert len(macro.publish) == 6, macro.publish
    assert [p.source for p in macro.publish] == [
        "StyledText",
        "Font",
        "ColorSelect",
        "Red1Clone",
        "Green1Clone",
        "Blue1Clone",
    ]
    assert len(macro.outputs) == 1
    assert macro.outputs[0].source_op == "Merge1"
    assert macro.outputs[0].source_op in macro.tools

    # Red1Clone を公開する InstanceInput 自身が Expression を持つ。
    red1clone = next(p for p in macro.publish if p.source == "Red1Clone")
    assert red1clone.expression == "iif(ColorSelect == 0, 0xE4, 0x41) / 255"

    # 式で駆動される入力(Red1)が公開されていないこと(no-driven-publish の対象外)。
    driven_pairs = {(d.tool, d.input) for d in g.driven_inputs(macro.tools)}
    assert ("Template", "Red1") in driven_pairs
    assert not any((p.source_op, p.source) in driven_pairs for p in macro.publish)

    print(f"    tools: {len(g.tools())}, connection edges: {len(connections)}, "
          f"expression edges: {len(expressions)}, published inputs: {len(macro.publish)}")

    # SizeSpline1 の KeyFrames は [0] と [24] (小数のキーの値)を持つ。
    assert graph.keyframe_range(tools_table) == (0.0, 24.0)

    return PASS


# --------------------------------------------------------------------------
# 既存プリセットに対する検証
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


def _sample_comp_path() -> pathlib.Path:
    return HERE / "data" / "sample.comp"


def test_sample_comp_keyframe_range():
    """``tests/data/sample.comp`` の ``SizeSpline1``(``BezierSpline``)が持つ
    ``KeyFrames`` のキーは ``[0]`` と ``[24]`` であり、``keyframe_range`` が
    ``(0.0, 24.0)`` を返すことを確認する。自作の最小データ
    であり、常にリポジトリに含まれる。
    """
    text = _sample_comp_path().read_text(encoding="utf-8")
    doc = document.parse(text)
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    result = graph.keyframe_range(tools_table)
    assert result == (0.0, 24.0), result

    return PASS


def test_corpus_keyframe_range_min_is_zero_or_none():
    """既存プリセット全体に対し、``keyframe_range`` の最小値が ``None`` または
    0 であることを確認する。特定の
    ファイル名は指定しない(個々のプリセットが持つキーフレームの最大値は
    プリセットごとに異なり、機械的に決められないため確認しない)。
    """
    files = _collect_preset_files()
    if not files:
        print("    skipping (FUSION_MACRO_CLI_PRESETS not available)")
        return SKIP

    checked = 0
    for path in files:
        doc = document.parse(path.read_text(encoding="utf-8"))
        tools_table = doc.root.get("Tools")
        if tools_table is None:
            continue
        result = graph.keyframe_range(tools_table)
        checked += 1
        assert result is None or result[0] == 0.0, (path.name, result)

    print(f"    checked {checked} preset file(s)")
    assert checked > 0, "no .setting files with a Tools table were found"

    return PASS


def test_corpus_reference_graph_invariants():
    """既存プリセットに対し、参照グラフの不変条件を確認する。

    参照切れ・マクロ自身の名前への画像参照・式で駆動される入力の公開・存在しない入力の
    公開が、いずれも0件であることを確認する。件数はプリセットの追加で変わるため表示に留める。

    出力への到達可能性は、出力のツール自身を含めて判定する。``TextPlus`` 単体の生成器が
    そのまま出力になるマクロは、上流に画像型の接続を持たないためである。
    """
    files = _collect_preset_files()
    if not files:
        print("    skipping corpus checks")
        return SKIP

    try:
        real_schema = schema.load()
    except FileNotFoundError:
        real_schema = None
        print("    no schema found; data-type-dependent checks will be skipped")

    macro_file_count = 0
    total_published = 0
    total_published_with_expression = 0
    dangling_total = 0
    self_image_violations: list[str] = []
    driven_publish_violations: list[str] = []
    missing_input_violations: list[str] = []
    unreachable_outputs: list[str] = []

    for path in files:
        doc = document.parse(path.read_text(encoding="utf-8"))
        tools_table = doc.root.get("Tools")
        if tools_table is None:
            continue
        macros = macro_mod.read_macros(tools_table)
        if not macros:
            continue
        macro_file_count += 1

        g = graph.build_graph(tools_table, schema=real_schema)
        dangling_total += len(g.dangling_edges())

        for macro in macros:
            total_published += len(macro.publish)
            total_published_with_expression += sum(
                1 for p in macro.publish if p.expression is not None
            )

            driven_pairs = {(d.tool, d.input) for d in g.driven_inputs(macro.tools)}
            for published in macro.publish:
                if (
                    published.source_op
                    and published.source
                    and (published.source_op, published.source) in driven_pairs
                ):
                    driven_publish_violations.append(
                        f"{path.name}:{macro.name}:{published.key}"
                    )

                if published.source_op and published.source:
                    tool_type = g.tool_type(published.source_op)
                    known = set()
                    if real_schema is not None and tool_type is not None and real_schema.has_inputs(tool_type):
                        known |= set(real_schema.input_names(tool_type))
                    known |= set(g.user_controls(published.source_op).keys())
                    if real_schema is not None and published.source not in known:
                        missing_input_violations.append(
                            f"{path.name}:{macro.name}:{published.key}"
                        )

            if real_schema is not None:
                for edge in g.edges_within(macro.tools):
                    if edge.data_type == "Image" and edge.target.tool == macro.name:
                        self_image_violations.append(f"{path.name}:{macro.name}:{edge.tool}.{edge.input}")

                for output in macro.outputs:
                    if not output.source_op:
                        unreachable_outputs.append(f"{path.name}:{macro.name}:{output.key}")
                        continue
                    reachable = {output.source_op} | g.reachable_from(output.source_op, data_type="Image")
                    if not reachable:
                        unreachable_outputs.append(f"{path.name}:{macro.name}:{output.key}")

    print(f"    macro-bearing files: {macro_file_count}")
    print(f"    published inputs: {total_published}")
    print(f"    published inputs with Expression: {total_published_with_expression}")
    print(f"    dangling edges: {dangling_total} (expected: 0)")
    print(f"    no-self-image violations: {len(self_image_violations)} (expected: 0)")
    print(f"    no-driven-publish violations: {len(driven_publish_violations)} (expected: 0)")
    print(f"    input-exists violations: {len(missing_input_violations)} (expected: 0)")
    print(f"    output-reachable violations (inclusive definition): {len(unreachable_outputs)} (expected: 0)")

    assert macro_file_count > 0, macro_file_count
    assert dangling_total == 0, dangling_total
    assert self_image_violations == [], self_image_violations
    assert driven_publish_violations == [], driven_publish_violations
    assert missing_input_violations == [], missing_input_violations
    assert unreachable_outputs == [], unreachable_outputs

    return PASS


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------

TESTS = [
    ("test_connection_and_expression_edges", test_connection_and_expression_edges),
    (
        "test_expression_excludes_lua_reserved_stdlib_and_calls",
        test_expression_excludes_lua_reserved_stdlib_and_calls,
    ),
    ("test_duplicate_tool_name_raises", test_duplicate_tool_name_raises),
    ("test_build_graph_recurses_into_macro_tools", test_build_graph_recurses_into_macro_tools),
    (
        "test_reachable_from_follows_connections_only_and_respects_type",
        test_reachable_from_follows_connections_only_and_respects_type,
    ),
    (
        "test_tool_table_accessor_reaches_macro_internals",
        test_tool_table_accessor_reaches_macro_internals,
    ),
    (
        "test_output_ports_uses_schema_output_definitions",
        test_output_ports_uses_schema_output_definitions,
    ),
    ("test_user_controls_readout", test_user_controls_readout),
    (
        "test_control_of_groups_user_controls_by_shared_control_group",
        test_control_of_groups_user_controls_by_shared_control_group,
    ),
    ("test_user_controls_fallback_for_edge_data_type", test_user_controls_fallback_for_edge_data_type),
    (
        "test_read_macros_publish_order_and_shell_expression",
        test_read_macros_publish_order_and_shell_expression,
    ),
    (
        "test_keyframe_range_scans_modifiers_recursively",
        test_keyframe_range_scans_modifiers_recursively,
    ),
    ("test_sample_setting_graph_and_macro", test_sample_setting_graph_and_macro),
    ("test_sample_comp_keyframe_range", test_sample_comp_keyframe_range),
    (
        "test_corpus_keyframe_range_min_is_zero_or_none",
        test_corpus_keyframe_range_min_is_zero_or_none,
    ),
    ("test_corpus_reference_graph_invariants", test_corpus_reference_graph_invariants),
]


def main() -> int:
    any_failure = False
    any_skip = False
    for name, fn in TESTS:
        print(f"--- {name}")
        try:
            outcome = fn()
        except AssertionError as exc:
            print(f"    FAILED: {exc}")
            any_failure = True
            continue
        except Exception as exc:  # 想定外の例外も失敗として扱う。無害化しない。
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
