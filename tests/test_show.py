"""``fusion_macro_cli.show`` の検証。

単独実行できる(``python3 tests/test_show.py``)。終了コードは、致命的な失敗が
1件でもあれば1、失敗は無いが実データに依存する検査(``FUSION_MACRO_CLI_PRESETS``
配下の既存 ``.setting`` の走査)が対象データの不在で行えなかった場合は2、
すべて実行できて失敗が無ければ0を返す(``tests/test_document.py``
``tests/test_graph.py`` と同じ規約)。``tests/data/sample.comp`` を使う検査は
常に実行する。

合成した小さな文書とスキーマに対する検証は、``document.parse`` /
``schema.Schema.from_dict`` の出力を ``show.py`` の公開関数へそのまま渡す
(公開 API だけを経由する)。列の切り出し(制御・既定値との差・通知の有無)は
``show.py`` の内部実装であるため、``schema.py`` のテストのような二重実装比較は行わない。
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
import fusion_macro_cli.form as form_mod  # noqa: E402
import fusion_macro_cli.graph as graph  # noqa: E402
import fusion_macro_cli.schema as schema  # noqa: E402
import fusion_macro_cli.show as show  # noqa: E402

SKIP = "skip"
PASS = "pass"


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------


def _doc_with_tools(text: str) -> document.Document:
    """``{ Tools = <text>, }`` の形にして解析し、``Document`` を返すヘルパー。

    ``show_comp`` / ``show_setting`` は ``Document`` を受け取るため(``doc.root``
    が ``Composition`` かどうかで種類を判別する)、``Tools`` テーブルの中身だけを
    切り出す test_graph.py の ``_tools`` とは戻り値が異なる。
    """
    doc = document.parse("{ Tools = " + text + ", }")
    tools = doc.root.get("Tools")
    assert isinstance(tools, document.Table)
    return doc


def _sample_comp_path() -> pathlib.Path:
    return HERE / "data" / "sample.comp"


def _minimal_schema() -> schema.Schema:
    """制御・既定値の判定を一通り試せる、最小限の合成スキーマ。

    ``ToolA`` は次を持つ。

    - ``Head``      単独の制御。既定値0、外部から設定できる
    - ``Font``      ``Style`` と対になる制御の先頭。既定値なし(既定が偽の値の意)
    - ``Style``     同じ制御の続き。既定値なし
    - ``Hidden``    外部から設定できない(``INPB_External`` を持たない)
    - ``Untouched`` 外部から設定できるが、文書側に値を持たない(既定値10)

    ``ToolB`` は接続用(``INPB_External`` を持つ ``In`` 1つ)。
    ``ToolC`` はスキーマに入力定義を持たない(``tools`` に現れない)。
    """
    data = {
        "version": "0.0.0.0000",
        "registry": [
            {"id": "ToolA", "class_type": 3, "hidden": False, "modifier": False, "category": "Tool", "name": "ToolA"},
            {"id": "ToolB", "class_type": 3, "hidden": False, "modifier": False, "category": "Tool", "name": "ToolB"},
        ],
        "tools": {
            "ToolA": {
                "Head": {
                    "INPS_ID": "Head",
                    "INPS_DataType": "Number",
                    "INPB_External": True,
                    "INPID_InputControl": "SliderControl",
                    "INPN_Default": 0,
                    "INPS_IC_Name": "頭",
                },
                "Font": {
                    "INPS_ID": "Font",
                    "INPS_DataType": "Text",
                    "INPB_External": True,
                    "INPID_InputControl": "FontFileControl",
                    "INPI_IC_ControlGroup": 5,
                },
                "Style": {
                    "INPS_ID": "Style",
                    "INPS_DataType": "Text",
                    "INPB_External": True,
                    "INPID_InputControl": "FontFileControl",
                    "INPI_IC_ControlGroup": 5,
                    "INPI_IC_ControlID": 1,
                },
                "Hidden": {
                    "INPS_ID": "Hidden",
                    "INPS_DataType": "Number",
                },
                "Untouched": {
                    "INPS_ID": "Untouched",
                    "INPS_DataType": "Number",
                    "INPB_External": True,
                    "INPN_Default": 10,
                },
            },
            "ToolB": {
                "In": {
                    "INPS_ID": "In",
                    "INPS_DataType": "Image",
                    "INPB_External": True,
                },
            },
        },
        "failed": [],
    }
    return schema.Schema.from_dict(data)


# --------------------------------------------------------------------------
# 種類の判別(拡張子ではなく構造)
# --------------------------------------------------------------------------


def test_detect_kind_by_structure_not_extension():
    comp_doc = document.parse('Composition { Tools = { A = ToolA { }, }, }')
    setting_doc = document.parse('{ Tools = ordered() { A = ToolA { }, }, ActiveTool = "A", }')
    assert form_mod.classify(comp_doc) == "comp"
    assert form_mod.classify(setting_doc) == "tool"

    # 同じ内容を、拡張子だけ変えたファイルから読んでも同じ判別になることを、
    # show() の入口(パスを受け取る側)を通して確認する。
    with tempfile.TemporaryDirectory() as tmp:
        comp_as_txt = pathlib.Path(tmp) / "sample.txt"
        comp_as_txt.write_text('Composition { Tools = { A = ToolA { }, }, }', encoding="utf-8")
        setting_as_dat = pathlib.Path(tmp) / "sample.dat"
        setting_as_dat.write_text(
            '{ Tools = ordered() { A = ToolA { }, }, ActiveTool = "A", }', encoding="utf-8"
        )
        out_comp = show.show(comp_as_txt, schema=None)
        out_setting = show.show(setting_as_dat)
        assert out_comp.splitlines()[0].startswith("candidates for publishing (.comp):")
        assert out_setting.splitlines()[0].startswith("inputs of the saved tool (tool-form .setting):")

    return PASS


# --------------------------------------------------------------------------
# .comp: 合成データによる列の切り出し
# --------------------------------------------------------------------------


def test_comp_external_filter_and_control_head():
    """外部から設定できない入力が除外され、制御の先頭が正しく立つこと。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
                Inputs = {
                    Head = Input { Value = 3, },
                    Style = Input { Value = "Bold", },
                    Hidden = Input { Value = 99, },
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=True)
    lines = [l.strip() for l in text.splitlines() if "\t" in l and not l.startswith("HEAD")]
    by_input = {l.split("\t")[1]: l for l in lines}

    assert "Hidden" not in by_input, "INPB_External を持たない入力は候補に出てはならない"
    assert "Head" in by_input
    assert by_input["Head"].startswith("head\tHead\t")

    # Font は文書に値を持たないが、同じ制御の Style が変更されているため
    # (制御の先頭であること自体は常に真になるが)一覧に含まれる。
    assert "Font" in by_input
    font_fields = by_input["Font"].split("\t")
    assert font_fields[0] == "head"
    assert "Style" in font_fields[4]  # SIBLINGS 列

    style_fields = by_input["Style"].split("\t")
    assert style_fields[0] == "-", "続きの入力は先頭であってはならない"
    assert style_fields[8] == "diff"  # DIFF 列: "Bold" != 既定(無し=偽)

    return PASS


def test_comp_default_view_collapses_boring_and_all_reveals_them():
    """既定値どおり・式も接続も無い入力は既定表示で件数のみになり、--all で全て出る。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
                Inputs = {
                    Head = Input { Value = 5, },
                },
            },
        }"""
    )
    s = _minimal_schema()
    default_view = show.show_comp(doc, filename="x.comp", schema=s, show_all=False)
    all_view = show.show_comp(doc, filename="x.comp", schema=s, show_all=True)

    assert "Untouched" not in default_view
    assert "further input(s) unchanged" in default_view
    assert "Untouched" in all_view
    assert "further input(s) unchanged" not in all_view

    return PASS


def test_comp_sibling_notability_propagates_within_displayed_siblings_only():
    """同じ制御の他の入力が既定と異なる場合、自分自身は既定のままでも先に並ぶこと。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
                Inputs = {
                    Style = Input { Value = "Bold", },
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=False)
    assert "\tFont\t" in text
    assert "\tStyle\t" in text
    assert "\tHead\t" not in text  # Head 自身も制御も無関係なので既定表示では出ない
    assert "2 further input(s) unchanged" in text  # Head と Untouched の2件

    return PASS


def test_comp_expression_and_connection_columns():
    doc = _doc_with_tools(
        """{
            A = ToolA {
                Inputs = {
                    Head = Input { Value = 1, Expression = "iif(true, 1, 2)", },
                },
            },
            B = ToolB {
                Inputs = {
                    In = Input { SourceOp = "A", Source = "Output", },
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=True)
    lines = {l.split("\t")[1]: l.split("\t") for l in text.splitlines() if "\t" in l and not l.startswith("HEAD")}
    assert lines["Head"][9] == "iif(true, 1, 2)"  # EXPRESSION 列
    assert lines["In"][10] == "A.Output"  # CONNECTION 列

    return PASS


def test_comp_ic_name_column_shows_schema_control_name():
    """``IC_NAME`` 列はスキーマの ``INPS_IC_Name``(``Name`` を省略したときに
    インスペクタへ出る名前)を示す。``UserControls`` 由来の入力はこの属性に
    相当するものを持たず、``-`` になる。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
                Inputs = {
                    Head = Input { Value = 3, },
                },
                UserControls = ordered() {
                    Extra = {
                        INPID_InputControl = "ComboControl",
                        LINKID_DataType = "Number",
                        LINKS_Name = "追加の制御",
                    },
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=True)
    lines = {
        l.split("\t")[1]: l.split("\t")
        for l in text.splitlines()
        if "\t" in l and not l.startswith("HEAD")
    }
    assert lines["Head"][2] == "頭"  # IC_NAME 列(スキーマの INPS_IC_Name)
    assert lines["Extra"][2] == "-"  # UserControls 由来は IC_NAME を持たない
    return PASS


def test_comp_no_schema_for_this_type_falls_back_per_tool():
    """スキーマ全体はあるが、この型の入力定義だけが無い場合の個別フォールバック。"""
    doc = _doc_with_tools(
        """{
            C = ToolC {
                Inputs = {
                    Whatever = Input { Value = 42, },
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=False)
    assert "no schema for type 'ToolC'" in text
    assert "\tWhatever\t" in text
    fields = [l for l in text.splitlines() if l.strip().startswith("head\tWhatever")][0].split("\t")
    assert fields[-1] == "no-schema"

    return PASS


def test_comp_user_controls_always_listed_and_marked():
    """``UserControls`` の入力は、既定と同じでも常に一覧に含まれ、印(SOURCE 列)を持つ。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
                UserControls = ordered() {
                    Extra = {
                        INPID_InputControl = "ComboControl",
                        LINKID_DataType = "Number",
                        LINKS_Name = "追加の制御",
                    },
                },
                Inputs = {
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=False)
    fields = [l for l in text.splitlines() if "\tExtra\t" in l][0].split("\t")
    assert fields[-1] == "user-controls"

    return PASS


def test_comp_user_controls_head_and_siblings_grouped_by_shared_control_group():
    """``UserControls`` 同士の制御は、同じ ``IC_ControlGroup`` の値を共有する
    入力の集合として ``HEAD``/``SIBLINGS`` が出ること。先頭は ``IC_ControlID``
    が最小の入力になる(``UserControls`` として追加された色3入力に相当する)。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
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
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=False)
    lines = {
        l.split("\t")[1]: l.split("\t")
        for l in text.splitlines()
        if "\t" in l and not l.startswith("HEAD")
    }

    assert lines["CustomColorR"][0] == "head"
    assert lines["CustomColorG"][0] == "-"
    assert lines["CustomColorB"][0] == "-"
    assert lines["CustomColorR"][4] == "CustomColorG,CustomColorB"  # SIBLINGS 列
    assert lines["CustomColorG"][4] == "CustomColorR,CustomColorB"

    return PASS


def test_comp_duplicate_tool_name_propagates():
    doc = _doc_with_tools(
        """{
            A = ToolA { Inputs = { }, },
            A = ToolA { Inputs = { }, },
        }"""
    )
    try:
        show.show_comp(doc, filename="x.comp", schema=None)
    except graph.DuplicateToolNameError:
        pass
    else:
        raise AssertionError("expected DuplicateToolNameError to propagate")

    return PASS


def test_comp_composite_value_is_unknown_and_notable():
    """座標のような複合値は差分を判定せず、常に注目対象として表示されること。"""
    doc = _doc_with_tools(
        """{
            A = ToolA {
                Inputs = {
                    Head = Input { Value = { 0.5, 0.5 }, },
                },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=False)
    fields = [l.strip() for l in text.splitlines() if "\tHead\t" in l][0].split("\t")
    assert fields[8] == "unknown"  # DIFF 列
    # 複合値は「判定できない」だけで既定扱いにはならないため、既定表示でも
    # 件数の行(「... N further input(s) unchanged」)に含まれず、Head 自身の行が出る。
    assert fields[0] == "head" and fields[1] == "Head"

    return PASS


def test_comp_macro_operator_block_shows_inputs_and_outputs():
    """``.comp`` に含まれる ``MacroOperator`` は通常の入力候補の列ではなく、
    その ``Inputs``(``InstanceInput``)と ``Outputs``(``InstanceOutput``)を
    列挙する専用のブロックになる。テンプレートから挿入したタイトルが
    何を公開していたかを、平坦化(``fix``)の前に読む材料になる。"""
    doc = _doc_with_tools(
        """{
            MyMacro = MacroOperator {
                Inputs = ordered() {
                    A_X = InstanceInput {
                        SourceOp = "A", Source = "X", Name = "エックス",
                        Page = "Controls", Default = 1,
                    },
                },
                Outputs = { Out1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() { A = ToolA { Inputs = { }, }, },
            },
        }"""
    )
    text = show.show_comp(doc, filename="x.comp", schema=_minimal_schema(), show_all=False)
    lines = text.splitlines()
    header_index = lines.index("== MyMacro (MacroOperator) ==")
    block = lines[header_index : header_index + 5]

    assert block[1] == "KEY\tNAME\tTOOL\tINPUT\tGROUP\tPAGE\tDEFAULT\tEXPRESSION"
    input_row = block[2].split("\t")
    assert input_row[:6] == ["A_X", "エックス", "A", "X", "-", "Controls"]

    assert block[3] == "KEY\tSOURCE_OP\tSOURCE"
    assert block[4] == "Out1\tA\tOutput"

    # 内側のツール(A)はマクロのブロックとは別に、通常どおり自身のブロックも持つ
    # (iter_tools がマクロの内側へ再帰するため)。
    assert "== A (ToolA) ==" in lines[header_index + 5 :]

    return PASS


# --------------------------------------------------------------------------
# .setting: 合成データ
# --------------------------------------------------------------------------


def test_setting_publish_order_group_and_expression():
    doc = _doc_with_tools(
        """{
            M = MacroOperator {
                Inputs = ordered() {
                    A_X = InstanceInput {
                        SourceOp = "A", Source = "X", Name = "エックス",
                        Page = "Controls", ControlGroup = 1, Default = 1,
                    },
                    A_Y = InstanceInput {
                        SourceOp = "A", Source = "Y",
                        Page = "Controls", ControlGroup = 1,
                        Expression = "iif(A_X == 1, 2, 3)", Default = 2,
                    },
                },
                Outputs = { Out1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() { A = ToolA { Inputs = { }, }, },
            },
        }"""
    )
    text = show.show_setting(doc, filename="x.setting")
    lines = text.splitlines()
    assert lines[0] == "published inputs (.setting): x.setting"
    body = [l.strip() for l in lines if l.strip().startswith("A_")]
    assert body[0].startswith("A_X\tエックス\tA\tX\t1\tControls\t1\t-")
    assert body[1].startswith("A_Y\t-\tA\tY\t1\tControls\t2\tiif(A_X == 1, 2, 3)")

    return PASS


def test_tool_form_setting_lists_the_saved_tools_inputs():
    doc = _doc_with_tools("""{ A = ToolA { Inputs = { Size = Input { Value = 2, }, }, }, }""")
    text = show.show_setting(doc, filename="x.setting")
    lines = text.splitlines()
    assert lines[0] == "inputs of the saved tool (tool-form .setting): x.setting"
    assert show._COMP_HEADER in lines
    assert "== A (ToolA) ==" in lines
    assert any(line.startswith("head\tSize\t") for line in lines), text

    return PASS


SYNTHETIC_TESTS = [
    ("detect_kind_by_structure_not_extension", test_detect_kind_by_structure_not_extension),
    ("comp_external_filter_and_control_head", test_comp_external_filter_and_control_head),
    ("comp_default_view_collapses_boring_and_all_reveals_them", test_comp_default_view_collapses_boring_and_all_reveals_them),
    (
        "comp_sibling_notability_propagates_within_displayed_siblings_only",
        test_comp_sibling_notability_propagates_within_displayed_siblings_only,
    ),
    ("comp_expression_and_connection_columns", test_comp_expression_and_connection_columns),
    ("comp_ic_name_column_shows_schema_control_name", test_comp_ic_name_column_shows_schema_control_name),
    ("comp_no_schema_for_this_type_falls_back_per_tool", test_comp_no_schema_for_this_type_falls_back_per_tool),
    ("comp_user_controls_always_listed_and_marked", test_comp_user_controls_always_listed_and_marked),
    (
        "comp_user_controls_head_and_siblings_grouped_by_shared_control_group",
        test_comp_user_controls_head_and_siblings_grouped_by_shared_control_group,
    ),
    ("comp_duplicate_tool_name_propagates", test_comp_duplicate_tool_name_propagates),
    ("comp_composite_value_is_unknown_and_notable", test_comp_composite_value_is_unknown_and_notable),
    (
        "comp_macro_operator_block_shows_inputs_and_outputs",
        test_comp_macro_operator_block_shows_inputs_and_outputs,
    ),
    ("setting_publish_order_group_and_expression", test_setting_publish_order_group_and_expression),
    ("tool_form_setting_lists_the_saved_tools_inputs", test_tool_form_setting_lists_the_saved_tools_inputs),
]


# --------------------------------------------------------------------------
# 自作データ: tests/data/sample.comp
# --------------------------------------------------------------------------


def test_sample_comp_candidates_include_marked_user_controls():
    """``sample.comp`` から候補の表が出て、``UserControls`` の入力が印付きで含まれること。

    実機のスキーマがあれば使い、無ければ ``schema=None`` で
    (フォールバックの処理も併せて)確認する。自作の
    最小データであり、常にリポジトリに含まれる。
    """
    path = _sample_comp_path()

    real_schema = None
    candidates = schema.list_schemas()
    if candidates:
        real_schema = schema.load(candidates[0])
    else:
        print("    no schema found; running with schema=None as well")

    for label, s in (("with schema", real_schema), ("without schema", None)):
        text = show.show(path, schema=s, show_all=True)
        assert text.splitlines()[0] == f"candidates for publishing (.comp): {path}"
        assert "== Template (TextPlus) ==" in text
        assert "\tColorSelect\t" in text
        uc_lines = [l for l in text.splitlines() if l.strip().endswith("user-controls")]
        assert uc_lines, f"{label}: no user-controls rows found"
        assert any("ColorSelect" in l for l in uc_lines), label

    if real_schema is not None:
        default_view = show.show(path, schema=real_schema, show_all=False)
        all_view = show.show(path, schema=real_schema, show_all=True)
        assert len(all_view) > len(default_view)
        assert "further input(s) unchanged" in default_view
        assert "\tRed1\t" in default_view  # 式を持つ入力は既定表示でも消えない
        assert "\tGreen1\t" in default_view  # 既定と異なる値を持つ
        assert "\tBlue1\t" in default_view  # 既定と異なる値を持つ

        # 同じ制御の他の入力(Red1/Green1/Blue1)が既定と異なるため、自分自身は
        # 既定のままの Alpha1 も既定表示で消えないこと(兄弟への通知の伝播)。
        # Alpha1 自体も 0.8 という既定と異なる値を明示的に持つ。
        assert "\tAlpha1\t" in default_view
        # Size はモディファイア(SizeSpline1)へ接続されており、接続を持つ入力
        # として既定表示でも消えないこと。
        assert "\tSize\t" in default_view

    no_schema_view = show.show(path, schema=None)
    assert "no schema loaded" in no_schema_view

    return PASS


def test_sample_comp_kind_survives_extension_rename():
    """同じ内容を別拡張子のファイルから読んでも、判別・本文が変わらないこと。"""
    path = _sample_comp_path()

    text_original = path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        renamed = pathlib.Path(tmp) / "sample_renamed.notcomp"
        renamed.write_text(text_original, encoding="utf-8")
        out_a = show.show(path, schema=None)
        out_b = show.show(renamed, schema=None)
        # 1行目はファイル名を含むため、それを除いた本体が一致することを確認する。
        assert out_a.splitlines()[1:] == out_b.splitlines()[1:]
        assert out_a.splitlines()[0].startswith("candidates for publishing (.comp):")
        assert out_b.splitlines()[0].startswith("candidates for publishing (.comp):")

    return PASS


REAL_COMP_TESTS = [
    (
        "sample_comp_candidates_include_marked_user_controls",
        test_sample_comp_candidates_include_marked_user_controls,
    ),
    ("sample_comp_kind_survives_extension_rename", test_sample_comp_kind_survives_extension_rename),
]


# --------------------------------------------------------------------------
# 実データ: FUSION_MACRO_CLI_PRESETS 配下の既存 .setting
# --------------------------------------------------------------------------


def _preset_dir() -> pathlib.Path | None:
    env_dir = os.environ.get("FUSION_MACRO_CLI_PRESETS")
    if not env_dir:
        print("    FUSION_MACRO_CLI_PRESETS is not set")
        return None
    root = pathlib.Path(env_dir)
    if not root.is_dir():
        print(f"    FUSION_MACRO_CLI_PRESETS does not exist: {root}")
        return None
    return root


def _published_expression_key(path: pathlib.Path) -> str | None:
    """直下の ``MacroOperator`` の公開入力のうち、``Expression`` を持つものの最初のキーを返す。"""
    try:
        doc = document.parse(path.read_text(encoding="utf-8"))
    except (document.ParseError, UnicodeDecodeError):
        return None
    tools = doc.root.get("Tools")
    if tools is None:
        return None
    for tool in tools:
        if not (isinstance(tool.value, document.Table) and tool.value.type_name == "MacroOperator"):
            continue
        inputs = tool.value.get("Inputs")
        if inputs is None:
            continue
        for entry in inputs:
            if isinstance(entry.value, document.Table) and entry.value.get("Expression") is not None:
                return entry.key.name if entry.key is not None else None
    return None


def test_setting_shows_expression_on_published_input():
    """``Expression`` を持つ公開入力がある ``.setting`` から結果の表が出て、
    その公開入力の行に ``Expression`` 列が表示されること。
    """
    root = _preset_dir()
    if root is None:
        return SKIP

    found = None
    for path in sorted(root.rglob("*.setting")):
        key = _published_expression_key(path)
        if key is not None:
            found = (path, key)
            break
    if found is None:
        print("    no .setting with a published input that has an Expression found under FUSION_MACRO_CLI_PRESETS")
        return SKIP

    path, key = found
    text = show.show(path)
    assert text.splitlines()[0] == f"published inputs (.setting): {path}"
    rows = [l for l in text.splitlines() if l.strip().split("\t")[0] == key]
    assert rows, f"{key} row not found"
    fields = rows[0].strip().split("\t")
    assert fields[-1] != "-", f"Expression column must be populated for {key}"

    return PASS


def test_all_presets_render_without_error():
    """``FUSION_MACRO_CLI_PRESETS`` 配下の全 ``.setting`` に対して、
    例外を出さず表が組み立てられること(構造検査ではなく健全性の確認)。
    """
    root = _preset_dir()
    if root is None:
        return SKIP

    paths = sorted(root.rglob("*.setting"))
    if not paths:
        print("    no .setting files found")
        return SKIP

    total_rows = 0
    tool_form = 0
    for path in paths:
        text = show.show(path)
        if form_mod.classify(document.parse(path.read_text(encoding="utf-8"))) == "tool":
            assert text.splitlines()[0].startswith("inputs of the saved tool (tool-form .setting):")
            tool_form += 1
            continue
        assert text.splitlines()[0].startswith("published inputs (.setting):")
        total_rows += sum(
            1 for l in text.splitlines() if "\t" in l and l != show._SETTING_HEADER
        )
    print(
        f"    (corpus) {len(paths)} .setting files ({tool_form} tool form), "
        f"{total_rows} published-input rows total"
    )
    assert total_rows > 0

    return PASS


REAL_SETTING_TESTS = [
    (
        "setting_shows_expression_on_published_input",
        test_setting_shows_expression_on_published_input,
    ),
    ("all_presets_render_without_error", test_all_presets_render_without_error),
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
