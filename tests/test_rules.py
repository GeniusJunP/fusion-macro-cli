"""``fusion_macro_cli.rules`` の検証。

``python3 tests/test_rules.py`` で単独実行できる。終了コードは ``tests/run.py`` の
規約に合わせる(0=合格、1=失敗、2=検証用データの不足で一部を飛ばした)。

検証は3種類に分かれる。

- 合成した小さな文書に対する単体の判定(各規則の ``check``/``fix``)。ツール名の
  重複・キーフレーム原点・フォント・絶対パスなど、``tests/data/sample.comp`` に
  実例が無いものはここで作る
- ``tests/data/sample.comp``/``sample.setting``(自作の
  最小データ。常にリポジトリに含まれる)に既知の変換を加えたものに対する検出の
  確認(``Page`` の削除、参照先の名前の変更、キーフレーム時刻の移動、公開入力の指す入力名の変更)。
  ``sample.setting`` は ``sample.toml`` を ``apply`` した結果そのものであり、
  ``.setting`` 規則はこれをそのまま読んで確かめる
- 既存プリセット(``FUSION_MACRO_CLI_PRESETS`` 配下の ``.setting`` のうち、直下の
  ``Tools`` に ``MacroOperator`` を持つもの)に対する誤検出の確認。
  ``MacroOperator`` を持たない ``.setting`` は規約の前提を満たさないため対象にしない。
  ``label-control-passive`` を除く規則は違反0件を要求する。``label-control-passive`` は規則の要否が
  判断待ちであるため、件数を報告するに留める
"""

from __future__ import annotations

import copy
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.document as document  # noqa: E402
import fusion_macro_cli.fonts as fonts  # noqa: E402
import fusion_macro_cli.form as form_mod  # noqa: E402
import fusion_macro_cli.graph as graph  # noqa: E402
import fusion_macro_cli.macro as macro_mod  # noqa: E402
import fusion_macro_cli.rules as rules  # noqa: E402
import fusion_macro_cli.schema as schema  # noqa: E402

SKIP = "skip"
PASS = "pass"

SAMPLE_COMP = HERE / "data" / "sample.comp"
SAMPLE_SETTING = HERE / "data" / "sample.setting"
SAMPLE_MACRO_NAME = "sample"


def _load_sample() -> document.Document:
    return document.parse(SAMPLE_COMP.read_text(encoding="utf-8"))


def _load_sample_setting() -> document.Document:
    return document.parse(SAMPLE_SETTING.read_text(encoding="utf-8"))


def _load_schema() -> schema.Schema | None:
    try:
        return schema.load()
    except FileNotFoundError:
        return None


def _get(table: document.Table, *path: str) -> document.Node:
    node: document.Node = table
    for key in path:
        assert isinstance(node, document.Table), f"{key!r} を辿れない: {node!r}"
        found = node.get(key)
        assert found is not None, f"key {key!r} not found"
        node = found
    return node


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------


def test_classify_comp_and_setting():
    comp_doc = _load_sample()
    assert form_mod.classify(comp_doc) == "comp"

    setting_doc = _load_sample_setting()
    assert form_mod.classify(setting_doc) == "macro"

    malformed = document.parse("{ Foo = 1, }")
    try:
        form_mod.classify(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError("classify() should reject a document that is neither .comp nor .setting")

    tool_doc = document.parse((HERE / "data" / "sample_tool.setting").read_text(encoding="utf-8"))
    assert form_mod.classify(tool_doc) == "tool"

    two_tools = document.parse("{ Tools = ordered() { A = ToolA { }, B = ToolB { }, }, }")
    try:
        form_mod.classify(two_tools)
    except ValueError:
        pass
    else:
        raise AssertionError("classify() should reject a .setting with more than one top-level tool")
    return PASS


def test_rules_declare_the_kinds_they_apply_to():
    """ツールに対する規則は3種類すべてに、公開する入力と出力に対する規則は macro 形だけに適用する。
    ``no-macro`` は tool 形に適用しない(唯一のツールは定義から ``MacroOperator`` でない)。"""
    tool_rules = {r.id for r in rules.RULES if "tool" in r.applies_to}
    assert tool_rules == {
        "ascii-name", "unique-name", "no-dangling", "keyframe-origin", "no-absolute-path",
        "font-available", "generator-follows-format", "loader-scaled", "no-media-io",
    }, tool_rules
    for rule in rules.RULES:
        assert "macro" in rule.applies_to, rule.id
        if "comp" not in rule.applies_to:
            assert rule.applies_to == {"macro"}, rule.id
    return PASS


def test_tool_form_runs_tool_rules_only():
    """tool 形の ``.setting`` に、ツールに対する規則を適用する。参照切れの接続を ``no-dangling`` が報告し、
    ``fix`` が取り除く。macro 形の規則は ``MacroOperator`` が無くても違反を出さず、実行もしない。"""
    doc = document.parse(
        "{ Tools = ordered() { Template = TextPlus { Inputs = { "
        'Size = Input { SourceOp = "Missing", Source = "Value", }, }, }, }, }'
    )
    result = rules.check(doc)
    assert [v.rule for v in result.violations] == ["no-dangling"], result.violations
    macro_only = {r.id for r in rules.RULES if r.applies_to == {"macro"}}
    assert not macro_only & set(result.skipped), result.skipped
    fixed_doc, applied, after = rules.fix(doc)
    assert [v.rule for v in applied] == ["no-dangling"], applied
    assert after.violations == (), after.violations
    assert form_mod.classify(fixed_doc) == "tool"
    return PASS


# --------------------------------------------------------------------------
# .comp に対する規則
# --------------------------------------------------------------------------


def test_ascii_name_excludes_macro_name():
    """マクロ名(``MacroOperator`` 自身のキー)は対象外。マクロ名を日本語にした
    合成文書で、内側のツール名(ASCII)は違反にならず、マクロ名自身も
    対象にならないことを確認する。"""
    text = """{
        Tools = ordered() {
            ["マクロ"] = MacroOperator {
                Inputs = ordered() {},
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() { A = Background { Inputs = {}, }, },
            },
        },
        ActiveTool = "マクロ",
    }"""
    doc = document.parse(text)
    result = rules.check(doc)
    assert not any(v.rule == "ascii-name" for v in result.violations), result.violations
    return PASS


def test_ascii_name_detect_and_fix_inner_tool():
    """マクロの内側のツール名は対象になる。``sample.comp`` の ``Tools`` に
    非 ASCII 名のツールを合成して確かめる。"""
    doc = copy.deepcopy(_load_sample())
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    tools_table.elements.append(
        document.Element(
            document.Key("string", "日本語ツール"),
            document.Table("Background", [document.Element(document.Key("ident", "Inputs"), document.Table(None, []))]),
        )
    )

    result = rules.check(doc)
    ascii_violations = [v for v in result.violations if v.rule == "ascii-name"]
    assert any(v.location == "日本語ツール" for v in ascii_violations), ascii_violations

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "ascii-name" and v.location == "日本語ツール" for v in applied)
    assert not any(v.rule == "ascii-name" for v in final.violations)
    assert not any(v.rule == "no-dangling" for v in final.violations)  # リネームが参照を壊していない
    return PASS


def test_unique_name_detect_and_fix():
    text = """Composition {
        Tools = {
            A = ToolA { Inputs = { X = Input { Value = 1, }, }, },
            A = ToolB { Inputs = { Y = Input { SourceOp = "Ghost", Source = "Output", }, }, },
        },
    }"""
    doc = document.parse(text)
    result = rules.check(doc)
    dup = [v for v in result.violations if v.rule == "unique-name"]
    assert dup and dup[0].location == "A", dup
    # ツール名が重複している間は、グラフを要る規則(no-dangling)は判定できず飛ばす。
    assert "no-dangling" in result.skipped, result.skipped

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "unique-name" for v in applied)
    assert not any(v.rule == "unique-name" for v in final.violations)
    # 一意化した後は同じ fix() の呼び出し内で no-dangling も実行され、
    # Ghost への参照切れも直る(修復の適用順は unique-name が no-dangling より先)。
    assert any(v.rule == "no-dangling" for v in applied)
    assert not any(v.rule == "no-dangling" for v in final.violations)
    return PASS


def test_no_dangling_detect_and_fix():
    """検出の変換の1つ: 参照先の名前の変更。"""
    doc = copy.deepcopy(_load_sample())
    background_input = _get(doc.root, "Tools", "Merge1", "Inputs", "Background")
    source_op = background_input.get("SourceOp")  # type: ignore[union-attr]
    assert isinstance(source_op, document.String) and source_op.value == "Background1"
    source_op.raw = "Background1_missing"

    result = rules.check(doc)
    dangling = [v for v in result.violations if v.rule == "no-dangling"]
    assert any("Background1_missing" in v.message for v in dangling), dangling

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "no-dangling" for v in applied)
    assert not any(v.rule == "no-dangling" for v in final.violations)
    return PASS


def _add_keyframes(table: document.Table, times: list[int]) -> None:
    kf_table = document.Table(
        None,
        [
            document.Element(
                document.Key("number", str(t)),
                document.Table(None, [document.Element(None, document.Number("0"))]),
            )
            for t in times
        ],
    )
    table.elements.append(document.Element(document.Key("ident", "KeyFrames"), kf_table))


_KEYFRAME_TEXT = """{
    Tools = ordered() {
        M = MacroOperator {
            Inputs = ordered() {},
            Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
            Tools = ordered() {
                A = Background {
                    Inputs = { PerformDepthMerge = Input { Value = 0, }, },
                },
                B = Background {
                    Inputs = { XAmount = Input { Value = 0, }, },
                },
            },
        },
    },
    ActiveTool = "M",
}"""


def test_keyframe_origin_is_composition_wide():
    """検出の変換の1つ: キーフレーム時刻の移動。

    モディファイアごとではなくコンポジション全体で判定する。``TimeStretcher`` のように
    タイムリマップの開始点として意図的に0以外から始まるモディファイアが同居しても、
    他のどれかが0から始まっていれば違反にならない。``graph.keyframe_range`` を
    経由すること(独自の走査を書かない)。合成した文書を使う(``sample.comp`` は
    ``SizeSpline1`` が既に0始まりのキーフレームを持ち、この検証には向かないため)。
    """
    doc = document.parse(_KEYFRAME_TEXT)
    perform = _get(doc.root, "Tools", "M", "Tools", "A", "Inputs", "PerformDepthMerge")
    assert isinstance(perform, document.Table)
    _add_keyframes(perform, [0, 20])  # 0から始まる(直した証拠)

    xamount = _get(doc.root, "Tools", "M", "Tools", "B", "Inputs", "XAmount")
    assert isinstance(xamount, document.Table)
    _add_keyframes(xamount, [10, 30])  # TimeStretcher のような、意図的に0以外から始まるもの

    result = rules.check(doc)
    assert not any(v.rule == "keyframe-origin" for v in result.violations), (
        "0から始まるモディファイアが1つでもあれば違反にならない"
    )
    return PASS


def test_keyframe_origin_detect_and_fix():
    """コンポジション全体の最小値が0でなければ違反とし、修復は全モディファイアを
    同じ量だけ平行移動する(相対関係を保つ)。合成した文書の2つの入力へ
    ``KeyFrames`` を追加する。"""
    doc = document.parse(_KEYFRAME_TEXT)
    perform = _get(doc.root, "Tools", "M", "Tools", "A", "Inputs", "PerformDepthMerge")
    assert isinstance(perform, document.Table)
    _add_keyframes(perform, [10, 20])

    xamount = _get(doc.root, "Tools", "M", "Tools", "B", "Inputs", "XAmount")
    assert isinstance(xamount, document.Table)
    _add_keyframes(xamount, [15, 25])  # perform より5だけ後ろ(相対関係)

    result = rules.check(doc)
    kf_violations = [v for v in result.violations if v.rule == "keyframe-origin"]
    assert kf_violations, "expected a keyframe-origin violation (global minimum is 10, not 0)"

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "keyframe-origin" for v in applied)
    assert not any(v.rule == "keyframe-origin" for v in final.violations)
    perform_kf = _get(new_doc.root, "Tools", "M", "Tools", "A", "Inputs", "PerformDepthMerge", "KeyFrames")
    xamount_kf = _get(new_doc.root, "Tools", "M", "Tools", "B", "Inputs", "XAmount", "KeyFrames")
    assert isinstance(perform_kf, document.Table) and isinstance(xamount_kf, document.Table)
    perform_keys = sorted(float(el.key.raw) for el in perform_kf.elements if el.key is not None)
    xamount_keys = sorted(float(el.key.raw) for el in xamount_kf.elements if el.key is not None)
    # 全体の最小値(10)ぶん平行移動し、2つのモディファイアの相対関係(+5)を保つ。
    assert perform_keys == [0.0, 10.0], perform_keys
    assert xamount_keys == [5.0, 15.0], xamount_keys
    return PASS


def test_no_absolute_path_excludes_prefs():
    """``.comp`` の ``Prefs`` は対象外。``Prefs.Comp`` 配下へ合成した絶対パスを
    注入しても、``Tools`` の外にあるため無視されることを確認する。"""
    doc = copy.deepcopy(_load_sample())
    prefs = doc.root.get("Prefs")
    assert isinstance(prefs, document.Table)
    comp_prefs = prefs.get("Comp")
    assert isinstance(comp_prefs, document.Table)
    comp_prefs.elements.append(
        document.Element(document.Key("ident", "LastFile"), document.String("/abs/path/injected.png"))
    )
    result = rules.check(doc)
    assert not any(v.rule == "no-absolute-path" for v in result.violations), result.violations
    return PASS


def test_no_absolute_path_detects_loader_filename():
    """``Loader``(``Tools`` の内側)の絶対パスは対象のまま。"""
    doc = copy.deepcopy(_load_sample())
    inner_tools = doc.root.get("Tools")
    assert isinstance(inner_tools, document.Table)
    loader = document.Table(
        "Loader",
        [
            document.Element(
                document.Key("ident", "Inputs"),
                document.Table(
                    None,
                    [
                        document.Element(
                            document.Key("ident", "Clips"),
                            document.Table(
                                None,
                                [
                                    document.Element(
                                        None,
                                        document.Table(
                                            "Clip",
                                            [
                                                document.Element(
                                                    document.Key("ident", "Filename"),
                                                    document.String("/abs/path/x.png"),
                                                )
                                            ],
                                        ),
                                    )
                                ],
                            ),
                        )
                    ],
                ),
            )
        ],
    )
    inner_tools.elements.append(document.Element(document.Key("ident", "Loader9"), loader))

    result = rules.check(doc)
    violations = [v for v in result.violations if v.rule == "no-absolute-path"]
    assert violations and "/abs/path/x.png" in violations[0].message, violations

    _, applied, final = rules.fix(doc)
    assert not any(v.rule == "no-absolute-path" for v in applied)
    assert any(v.rule == "no-absolute-path" for v in final.violations)
    return PASS


def test_font_available_detects_missing_font():
    doc = copy.deepcopy(_load_sample())
    font_value = _get(doc.root, "Tools", "Template", "Inputs", "Font", "Value")
    assert isinstance(font_value, document.String)
    font_value.raw = "Definitely Not An Installed Font XYZ123"

    result = rules.check(doc)
    violations = [v for v in result.violations if v.rule == "font-available"]
    assert violations, "expected a font-available violation for a nonexistent font name"

    _, applied, final = rules.fix(doc)
    assert not any(v.rule == "font-available" for v in applied)
    assert any(v.rule == "font-available" for v in final.violations)
    return PASS


def test_font_available_reads_all_ttc_faces():
    """実機のフォント列挙に対する検査(``fonts.installed_font_families`` をモックしない)。

    macOS の日本語フォントは1件の ``.ttc`` が複数フェイスを収め、フェイスごとに
    別のファミリー名を名乗ることがある(例: 「ヒラギノ丸ゴ ProN W4.ttc」が
    ``Hiragino Maru Gothic ProN`` と ``Hiragino Maru Gothic Pro`` の2つ)。
    先頭フェイスしか読まないと後者しか取得できず、前者の指定が誤って違反になる。
    実行環境にこのフォントが無ければ判定できないため SKIP で飛ばす(``tests/run.py``
    の規約により、実行環境の欠落は exit 2 で表す)。
    """
    families = fonts.installed_font_families()
    if "hiragino maru gothic pron" not in families:
        print("    Hiragino Maru Gothic ProN not installed on this machine; skipping")
        return SKIP

    doc = copy.deepcopy(_load_sample())
    font_value = _get(doc.root, "Tools", "Template", "Inputs", "Font", "Value")
    assert isinstance(font_value, document.String)
    font_value.raw = "Hiragino Maru Gothic ProN"

    result = rules.check(doc)
    violations = [v for v in result.violations if v.rule == "font-available"]
    assert not violations, violations
    return PASS


def test_generator_follows_format_detect_and_fix():
    schema_obj = _load_schema()
    if schema_obj is None:
        print("    no schema found; skipping")
        return SKIP

    text = """Composition {
        Tools = {
            Bg1 = Background { Inputs = { UseFrameFormatSettings = Input { Value = 0, }, }, },
        },
    }"""
    doc = document.parse(text)
    result = rules.check(doc, schema_obj)
    violations = [v for v in result.violations if v.rule == "generator-follows-format"]
    assert violations and violations[0].location == "Bg1", violations

    new_doc, applied, final = rules.fix(doc, schema_obj)
    assert any(v.rule == "generator-follows-format" for v in applied)
    assert not any(v.rule == "generator-follows-format" for v in final.violations)
    return PASS


def test_generator_follows_format_excludes_non_generators():
    """``Merge``/``Transform`` はスキーマ上 ``UseFrameFormatSettings`` を持つが
    ``GlobalOut`` を持たず、「ジェネレータ」ではないため対象外になることを確認する。"""
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    for type_name in ("Merge", "Transform"):
        assert schema_obj.has_inputs(type_name)
        assert "UseFrameFormatSettings" in schema_obj.input_names(type_name)
        assert "GlobalOut" not in schema_obj.input_names(type_name)

    text = """Composition {
        Tools = { M1 = Merge { Inputs = {}, }, },
    }"""
    doc = document.parse(text)
    result = rules.check(doc, schema_obj)
    assert not any(v.rule == "generator-follows-format" for v in result.violations)
    return PASS


def test_loader_scaled():
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    text = """Composition {
        Tools = {
            L1 = Loader { Inputs = {}, },
            T1 = Transform {
                Inputs = {
                    Input = Input { SourceOp = "L1", Source = "Output", },
                    Size = Input { Value = 1, Expression = "comp.RenderHeight / 1080" },
                },
            },
            L2 = Loader { Inputs = {}, },
            T2 = Transform { Inputs = { Input = Input { SourceOp = "L2", Source = "Output", }, }, },
        },
    }"""
    doc = document.parse(text)
    result = rules.check(doc, schema_obj)
    violations = {v.location for v in result.violations if v.rule == "loader-scaled"}
    assert violations == {"L2"}, violations
    assert rules._RULES_BY_ID["loader-scaled"].fixable is False
    return PASS


_MACRO_INNER_TOOLS = """
    Template = TextPlus {
        Inputs = { StyledText = Input { Value = "Sample", }, },
    },
    Background1 = Background {
        Inputs = {},
    },
    Merge1 = Merge {
        Inputs = {
            Background = Input { SourceOp = "Background1", Source = "Output", },
            Foreground = Input { SourceOp = "Template", Source = "Output", },
        },
    },
"""


def _single_macro_comp() -> document.Document:
    """``sample.comp`` の主要ツール群(TextPlus+Background+Merge)を
    ``MacroOperator`` の内側に入れ、外側から ``Merge2`` でその出力へ繋いだ ``.comp``。"""
    text = f"""Composition {{
        Tools = {{
            MyMacro = MacroOperator {{
                Inputs = ordered() {{}},
                Outputs = {{ MainOutput1 = InstanceOutput {{ SourceOp = "Merge1", Source = "Output", }}, }},
                Tools = ordered() {{
                    {_MACRO_INNER_TOOLS}
                }},
            }},
            Merge2 = Merge {{
                Inputs = {{
                    Background = Input {{ SourceOp = "MyMacro", Source = "MainOutput1", }},
                }},
                ActiveTool = "MyMacro",
            }},
        }},
    }}"""
    return document.parse(text)


def test_no_macro_detect_and_fix():
    """``MacroOperator`` を含む ``.comp`` は違反になり、``fix`` で平坦化される。

    平坦化後は ``MacroOperator`` が消え、外側の ``Merge2`` の接続がマクロの
    出力(``Merge1.Output``)へ書き換わり、``no-dangling`` も0件になる。
    """
    doc = _single_macro_comp()
    result = rules.check(doc)
    macro_violations = [v for v in result.violations if v.rule == "no-macro"]
    assert any(v.location == "MyMacro" for v in macro_violations), macro_violations

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "no-macro" and v.location == "MyMacro" for v in applied)
    assert not any(v.rule == "no-macro" for v in final.violations)

    tools_table = new_doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    assert tools_table.get("MyMacro") is None
    assert isinstance(tools_table.get("Merge1"), document.Table)  # 内側のツールが直下へ移った

    merge2_bg = _get(new_doc.root, "Tools", "Merge2", "Inputs", "Background")
    assert isinstance(merge2_bg, document.Table)
    source_op = merge2_bg.get("SourceOp")
    source = merge2_bg.get("Source")
    assert isinstance(source_op, document.String) and source_op.value == "Merge1"
    assert isinstance(source, document.String) and source.value == "Output"

    # ActiveTool のような式以外の裸の参照(SourceOp/Source の組ではないもの)は
    # この規則の対象外であり、書き換えない(仕様: 接続と式のみを書き換える)。
    active_tool = _get(new_doc.root, "Tools", "Merge2", "ActiveTool")
    assert isinstance(active_tool, document.String) and active_tool.value == "MyMacro"

    assert not any(v.rule == "no-dangling" for v in final.violations), final.violations
    return PASS


def test_no_macro_double_nested_flattens_fully():
    """2重に入れ子になった ``MacroOperator`` も、``fix`` を1回呼ぶだけで
    完全に平坦化される(``_fix_no_macro`` 内のループが再帰の代わりを担う)。"""
    text = f"""Composition {{
        Tools = {{
            OuterMacro = MacroOperator {{
                Inputs = ordered() {{}},
                Outputs = {{ MainOutput1 = InstanceOutput {{ SourceOp = "MyMacro", Source = "MainOutput1", }}, }},
                Tools = ordered() {{
                    MyMacro = MacroOperator {{
                        Inputs = ordered() {{}},
                        Outputs = {{ MainOutput1 = InstanceOutput {{ SourceOp = "Merge1", Source = "Output", }}, }},
                        Tools = ordered() {{
                            {_MACRO_INNER_TOOLS}
                        }},
                    }},
                }},
            }},
            Merge2 = Merge {{
                Inputs = {{
                    Background = Input {{ SourceOp = "OuterMacro", Source = "MainOutput1", }},
                }},
            }},
        }},
    }}"""
    doc = document.parse(text)

    result = rules.check(doc)
    macro_locations = {v.location for v in result.violations if v.rule == "no-macro"}
    assert macro_locations == {"OuterMacro", "MyMacro"}, macro_locations

    new_doc, applied, final = rules.fix(doc)
    assert len([v for v in applied if v.rule == "no-macro"]) == 2
    assert not any(v.rule == "no-macro" for v in final.violations)
    assert not any(v.rule == "no-dangling" for v in final.violations), final.violations

    tools_table = new_doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    assert tools_table.get("OuterMacro") is None
    assert tools_table.get("MyMacro") is None
    assert isinstance(tools_table.get("Merge1"), document.Table)

    merge2_bg = _get(new_doc.root, "Tools", "Merge2", "Inputs", "Background")
    assert isinstance(merge2_bg, document.Table)
    source_op = merge2_bg.get("SourceOp")
    source = merge2_bg.get("Source")
    assert isinstance(source_op, document.String) and source_op.value == "Merge1"
    assert isinstance(source, document.String) and source.value == "Output"
    return PASS


def test_no_media_io_detect_and_fix():
    """``MediaIn``/``MediaOut`` を含む ``.comp`` は違反になり、``fix`` で削除される。

    ``sample.comp`` 自体は no-media-io に違反しない(規則1で ``MediaOut1`` を
    除いた)ため、``ascii-name`` の合成(``test_ascii_name_detect_and_fix_inner_tool``)
    と同じ要領で ``MediaOut`` を複製へ追加して確かめる。"""
    doc = copy.deepcopy(_load_sample())
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    tools_table.elements.append(
        document.Element(
            document.Key("ident", "MediaOut1"),
            document.Table(
                "MediaOut",
                [
                    document.Element(
                        document.Key("ident", "Inputs"),
                        document.Table(
                            None,
                            [
                                document.Element(
                                    document.Key("ident", "Input"),
                                    document.Table(
                                        "Input",
                                        [
                                            document.Element(
                                                document.Key("ident", "SourceOp"),
                                                document.String("Merge1"),
                                            ),
                                            document.Element(
                                                document.Key("ident", "Source"),
                                                document.String("Output"),
                                            ),
                                        ],
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )
    )

    result = rules.check(doc)
    media_violations = [v for v in result.violations if v.rule == "no-media-io"]
    assert any(v.location == "MediaOut1" for v in media_violations), media_violations

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "no-media-io" and v.location == "MediaOut1" for v in applied)
    assert not any(v.rule == "no-media-io" for v in final.violations)

    tools_table = new_doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    assert tools_table.get("MediaOut1") is None
    assert isinstance(tools_table.get("Merge1"), document.Table)  # 上流のツールは残る
    return PASS


# --------------------------------------------------------------------------
# .setting に対する規則
# --------------------------------------------------------------------------


def test_page_controls_detect_and_fix():
    """検出の変換の1つ: Page の削除。"""
    setting_doc = _load_sample_setting()

    before = rules.check(setting_doc)
    assert not any(v.rule == "page-controls" for v in before.violations)

    instance = _get(setting_doc.root, "Tools", SAMPLE_MACRO_NAME, "Inputs", "Template_StyledText")
    assert isinstance(instance, document.Table)
    instance.elements = [el for el in instance.elements if not (el.key is not None and el.key.name == "Page")]

    result = rules.check(setting_doc)
    violations = [v for v in result.violations if v.rule == "page-controls"]
    assert violations, "expected a page-controls violation after removing Page"

    _, applied, final = rules.fix(setting_doc)
    assert any(v.rule == "page-controls" for v in applied)
    assert not any(v.rule == "page-controls" for v in final.violations)
    return PASS


def test_input_exists_detect_and_fix():
    """検出の変換の1つ: 公開入力の指す入力名の変更。"""
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    setting_doc = _load_sample_setting()
    source_node = _get(setting_doc.root, "Tools", SAMPLE_MACRO_NAME, "Inputs", "Template_Font", "Source")
    assert isinstance(source_node, document.String)
    source_node.raw = "NoSuchInput"

    result = rules.check(setting_doc, schema_obj)
    violations = [v for v in result.violations if v.rule == "input-exists"]
    assert violations, "expected an input-exists violation after renaming the referenced input"

    _, applied, final = rules.fix(setting_doc, schema_obj)
    assert any(v.rule == "input-exists" for v in applied)
    assert not any(v.rule == "input-exists" for v in final.violations)
    return PASS


def test_input_exists_skips_type_with_no_schema_definition():
    """スキーマがその種別の入力定義を持たず、UserControls も無い場合、
    ``known_inputs`` は判定できないことを表す None を返す(graph.py)。この規則は
    それを誤検出(全公開入力が違反)へ倒さず、判定を飛ばす。"""
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    assert not schema_obj.has_inputs("NotInSchema"), (
        "test assumes 'NotInSchema' is absent from the real schema directory"
    )
    text = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {
                    A_X = InstanceInput { SourceOp = "A", Source = "X", Page = "Controls", Name = "L", },
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() {
                    A = NotInSchema { Inputs = { X = Input { Value = 1, }, }, },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text)
    g = graph.try_build_graph(doc.root.get("Tools"), schema_obj)
    assert g is not None
    assert g.known_inputs("A") is None, "expected known_inputs to be undecidable for A"

    result = rules.check(doc, schema_obj)
    assert not any(v.rule == "input-exists" for v in result.violations), result.violations
    return PASS


def test_input_exists_records_skipped_input_for_unknown_type():
    """判定できない公開入力を沈黙して飛ばさず、``CheckResult.skipped_inputs``
    へ記録することを確認する(表示を伴わない絞り込みの禁止)。

    実スキーマの有無(``FUSION_MACRO_CLI_SCHEMA``)に検証結果を左右されない
    よう、``Schema.from_dict`` で合成した最小のスキーマ(``KnownType`` のみ
    定義を持つ)を使う。``A`` の種別 ``NotInSchema`` はこのスキーマに無く、
    ``UserControls`` も持たないため、``A_X`` の公開は違反にはならず
    ``skipped_inputs`` に1件だけ記録される。
    """
    synthetic_schema = schema.Schema.from_dict(
        {
            "version": "test",
            "registry": [{"id": "KnownType"}],
            "tools": {"KnownType": {}},
            "failed": [],
        }
    )
    text = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {
                    A_X = InstanceInput { SourceOp = "A", Source = "X", Page = "Controls", Name = "L", },
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() {
                    A = NotInSchema { Inputs = { X = Input { Value = 1, }, }, },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text)

    result = rules.check(doc, synthetic_schema)
    assert not any(v.rule == "input-exists" for v in result.violations), result.violations
    assert len(result.skipped_inputs) == 1, result.skipped_inputs
    entry = result.skipped_inputs[0]
    assert entry.startswith("input-exists M:A_X: "), entry
    assert "NotInSchema" in entry, entry
    return PASS


def test_label_unique_detect_and_fix():
    text = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {
                    A_X = InstanceInput { SourceOp = "A", Source = "X", Page = "Controls", Name = "L", },
                    A_Y = InstanceInput { SourceOp = "A", Source = "Y", Page = "Controls", Name = "L", },
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() {
                    A = Background { Inputs = { X = Input { Value = 1, }, Y = Input { Value = 2, }, }, },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text)
    result = rules.check(doc)
    dup = [v for v in result.violations if v.rule == "label-unique"]
    assert dup, dup

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "label-unique" for v in applied)
    assert not any(v.rule == "label-unique" for v in final.violations)
    labels = {
        p.name
        for macro in macro_mod.read_macros(new_doc.root.get("Tools"))
        for p in macro.publish
    }
    assert labels == {"L", "L (2)"}, labels
    return PASS


def test_label_control_passive_detect_and_fix():
    """``UserControls`` 由来の ``LabelControl`` に ``INP_External = false`` が
    無い場合、違反になる(合成データ。sample.comp の ``ColorSelect`` は
    ``ComboControl`` であり、この規則の実例を持たないため)。"""
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    text = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {
                    A_Divider = InstanceInput { SourceOp = "A", Source = "Divider", Page = "Controls", Name = "区切り", },
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() {
                    A = Background {
                        Inputs = {},
                        UserControls = ordered() {
                            Divider = {
                                INPID_InputControl = "LabelControl",
                                LINKID_DataType = "Number",
                                LINKS_Name = "区切り",
                            },
                        },
                    },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text)
    result = rules.check(doc, schema_obj)
    violations = [v for v in result.violations if v.rule == "label-control-passive"]
    assert violations, "expected a label-control-passive violation for a LabelControl without INP_External = false"

    _, applied, final = rules.fix(doc, schema_obj)
    assert applied
    assert not any(v.rule == "label-control-passive" for v in final.violations)
    return PASS


def test_no_driven_publish_detect_and_fix():
    text = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {
                    A_Red1 = InstanceInput { SourceOp = "A", Source = "Red1", Page = "Controls", Name = "R", },
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() {
                    A = TextPlus {
                        Inputs = { Red1 = Input { Value = 0.5, Expression = "iif(1==1,0,1)" }, },
                    },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text)
    result = rules.check(doc)
    violations = [v for v in result.violations if v.rule == "no-driven-publish"]
    assert violations, "expected a no-driven-publish violation"

    new_doc, applied, final = rules.fix(doc)
    assert any(v.rule == "no-driven-publish" for v in applied)
    assert not any(v.rule == "no-driven-publish" for v in final.violations)
    return PASS


def _textplus_macro(publish: str, inner_inputs: str, extra_tools: str = "") -> document.Document:
    return document.parse(
        """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {
                    %s
                },
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output", }, },
                Tools = ordered() {
                    %s
                    A = TextPlus { Inputs = { %s }, },
                },
            },
        },
        ActiveTool = "M",
    }"""
        % (publish, extra_tools, inner_inputs)
    )


def test_no_connected_publish():
    """接続から値を受け取る入力の公開を違反として検出する。式を併せ持つ入力は式が優先されるため
    ``no-driven-publish`` のみが検出し、本規則は検出しない。"""
    cls = 'ACLS = StyledTextCLS { Inputs = { Text = Input { Value = "x", }, }, },'
    doc = _textplus_macro(
        'A_StyledText = InstanceInput { SourceOp = "A", Source = "StyledText", Page = "Controls", },',
        'StyledText = Input { SourceOp = "ACLS", Source = "StyledText", },',
        cls,
    )
    violations = [v for v in rules.check(doc).violations if v.rule == "no-connected-publish"]
    assert len(violations) == 1, violations
    assert "ACLS.StyledText" in violations[0].message

    doc = _textplus_macro(
        'ACLS_Text = InstanceInput { SourceOp = "ACLS", Source = "Text", Page = "Controls", },',
        'StyledText = Input { SourceOp = "ACLS", Source = "StyledText", },',
        cls,
    )
    assert not [v for v in rules.check(doc).violations if v.rule == "no-connected-publish"]

    doc = _textplus_macro(
        'A_StyledText = InstanceInput { SourceOp = "A", Source = "StyledText", Page = "Controls", },',
        'StyledText = Input { SourceOp = "ACLS", Source = "StyledText", Expression = "ACLS.Text", },',
        cls,
    )
    found = {v.rule for v in rules.check(doc).violations}
    assert "no-connected-publish" not in found and "no-driven-publish" in found, found

    _, applied, _ = rules.fix(
        _textplus_macro(
            'A_StyledText = InstanceInput { SourceOp = "A", Source = "StyledText", Page = "Controls", },',
            'StyledText = Input { SourceOp = "ACLS", Source = "StyledText", },',
            cls,
        )
    )
    assert not any(v.rule == "no-connected-publish" for v in applied)
    return PASS


def test_no_nest_publish():
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    doc = _textplus_macro(
        'A_Softness2 = InstanceInput { SourceOp = "A", Source = "Softness2", Page = "Controls", },'
        'A_SoftnessX2 = InstanceInput { SourceOp = "A", Source = "SoftnessX2", Page = "Controls", },',
        'SelectElement = Input { Value = 1, },',
    )
    violations = [v for v in rules.check(doc, schema_obj).violations if v.rule == "no-nest-publish"]
    assert [v.location for v in violations] == ["M:A_Softness2"], violations
    return PASS


def test_publish_element_selected():
    """要素の入力は、内側のツールで選択中の要素に所属するものに限り表示される。
    ``SelectElement`` は 0 起点であり、値が存在しない場合は要素 1 を表示する。"""
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    publish = (
        'A_Red1 = InstanceInput { SourceOp = "A", Source = "Red1", Page = "Controls", },'
        'A_Red1Clone = InstanceInput { SourceOp = "A", Source = "Red1Clone", Page = "Controls", },'
        'A_Thickness2 = InstanceInput { SourceOp = "A", Source = "Thickness2", Page = "Controls", },'
        'A_Center = InstanceInput { SourceOp = "A", Source = "Center", Page = "Controls", },'
    )

    def hidden(inner: str) -> dict[str, str]:
        result = rules.check(_textplus_macro(publish, inner), schema_obj)
        return {v.location: v.message for v in result.violations if v.rule == "publish-element-selected"}

    assert set(hidden("")) == {"M:A_Thickness2"}, hidden("")
    selected_outline = hidden("SelectElement = Input { Value = 1, },")
    assert set(selected_outline) == {"M:A_Red1"}, selected_outline
    assert "Publish A.Red1Clone instead" in selected_outline["M:A_Red1"]
    assert set(hidden("SelectElement = Input { Value = 2, },")) == {"M:A_Red1", "M:A_Thickness2"}

    result = rules.check(
        _textplus_macro(publish, 'SelectElement = Input { Value = 1, Expression = "1", },'), schema_obj
    )
    assert not [v for v in result.violations if v.rule == "publish-element-selected"]
    assert any(s.startswith("publish-element-selected M:A_Red1:") for s in result.skipped_inputs)
    return PASS


def test_color_control_rgb():
    """ColorControl はアルファのみ、または赤のみの公開で操作できないスウォッチとなる。
    赤・緑・青を公開すれば、アルファの有無は問わない。"""
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP

    def found(publish: str) -> list[str]:
        doc = _textplus_macro(publish, "")
        return [v.location for v in rules.check(doc, schema_obj).violations if v.rule == "color-control-rgb"]

    def pub(*inputs: str) -> str:
        return "".join(
            f'A_{name} = InstanceInput {{ SourceOp = "A", Source = "{name}", Page = "Controls", }},' for name in inputs
        )

    assert found(pub("Alpha1Clone")) == ["M:A_Alpha1Clone"], found(pub("Alpha1Clone"))
    assert found(pub("Red1Clone")) == ["M:A_Red1Clone"]
    assert found(pub("Red1Clone", "Green1Clone", "Blue1Clone")) == []
    assert found(pub("Red1Clone", "Green1Clone", "Blue1Clone", "Alpha1Clone")) == []
    return PASS


def test_output_reachable():
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    assert rules._RULES_BY_ID["output-reachable"].fixable is False

    # TextPlus 単体を出力にするマクロ(内側に消費者が無い)は違反にしない。
    # スキーマの出力定義(TextPlus.Output = Image)で直接判定できる。
    text_only = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {},
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "T1", Source = "Output", }, },
                Tools = ordered() { T1 = TextPlus { Inputs = {}, }, },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text_only)
    result = rules.check(doc, schema_obj)
    assert not any(v.rule == "output-reachable" for v in result.violations), result.violations

    # 画像出力を1つも持たないマクロは違反になる。PolyPath は Position(Point)と
    # Heading(Number)の2ポートを持つが、いずれも画像型ではない。
    no_image = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {},
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "P1", Source = "Position", }, },
                Tools = ordered() { P1 = PolyPath { Inputs = {}, }, },
            },
        },
        ActiveTool = "M",
    }"""
    doc2 = document.parse(no_image)
    result2 = rules.check(doc2, schema_obj)
    violations = [v for v in result2.violations if v.rule == "output-reachable"]
    assert violations and "no output is typed Image" in violations[0].message, violations

    # 存在しないポート名を指す出力は違反になる。
    bogus_port = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {},
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "T1", Source = "NoSuchPort", }, },
                Tools = ordered() { T1 = TextPlus { Inputs = {}, }, },
            },
        },
        ActiveTool = "M",
    }"""
    doc3 = document.parse(bogus_port)
    result3 = rules.check(doc3, schema_obj)
    violations3 = [v for v in result3.violations if v.rule == "output-reachable"]
    assert violations3 and "not a port of" in violations3[0].message, violations3

    # 2つ目以降の出力が画像型でなくても、1つ目が画像型なら違反にしない
    # (実在プリセットに見られる、PolyPath の角度出力を副次出力に持つ形と同じ)。
    secondary_non_image = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {},
                Outputs = {
                    MainOutput1 = InstanceOutput { SourceOp = "T1", Source = "Output", },
                    Output2 = InstanceOutput { SourceOp = "P1", Source = "Heading", },
                },
                Tools = ordered() {
                    T1 = TextPlus { Inputs = {}, },
                    P1 = PolyPath { Inputs = {}, },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc4 = document.parse(secondary_non_image)
    result4 = rules.check(doc4, schema_obj)
    assert not any(v.rule == "output-reachable" for v in result4.violations), result4.violations
    return PASS


def test_no_self_image():
    # data_type の判定はスキーマ(Merge.Background = Image)を経由するため、
    # no-self-image はスキーマ無しでは判定が効かない(型が不明のままで一致しない)。
    schema_obj = _load_schema()
    if schema_obj is None:
        return SKIP
    text = """{
        Tools = ordered() {
            M = MacroOperator {
                Inputs = ordered() {},
                Outputs = { MainOutput1 = InstanceOutput { SourceOp = "M1", Source = "Output", }, },
                Tools = ordered() {
                    M1 = Merge { Inputs = { Background = Input { SourceOp = "M", Source = "Output", }, }, },
                },
            },
        },
        ActiveTool = "M",
    }"""
    doc = document.parse(text)
    result = rules.check(doc, schema_obj)
    violations = [v for v in result.violations if v.rule == "no-self-image"]
    assert violations, "expected a no-self-image violation"
    assert rules._RULES_BY_ID["no-self-image"].fixable is False
    return PASS


# --------------------------------------------------------------------------
# スキーマ不在・ツール名重複の扱い
# --------------------------------------------------------------------------


def test_schema_required_rules_are_skipped_without_schema():
    comp_doc = _load_sample()
    result = rules.check(comp_doc, schema_obj=None)
    expected_skip = {r.id for r in rules.RULES if "comp" in r.applies_to and r.needs_schema}
    assert set(result.skipped) == expected_skip, result.skipped

    setting_doc = _load_sample_setting()
    result2 = rules.check(setting_doc, schema_obj=None)
    expected_skip2 = {r.id for r in rules.RULES if r.needs_schema}
    assert set(result2.skipped) == expected_skip2, result2.skipped
    return PASS


# --------------------------------------------------------------------------
# 既存プリセットに対する検証
# --------------------------------------------------------------------------


def _collect_settings() -> list[pathlib.Path]:
    """``FUSION_MACRO_CLI_PRESETS`` 配下の、macro 形または tool 形の ``.setting`` を集める。

    どちらの形でもない ``.setting``(最上位に複数のツールを持つもの等)は対象にしない。
    """
    env_dir = os.environ.get("FUSION_MACRO_CLI_PRESETS")
    if not env_dir:
        print("    FUSION_MACRO_CLI_PRESETS is not set")
        return []
    root = pathlib.Path(env_dir)
    if not root.is_dir():
        print(f"    FUSION_MACRO_CLI_PRESETS does not exist: {root}")
        return []

    files: list[pathlib.Path] = []
    for path in sorted(root.rglob("*.setting")):
        try:
            form_mod.classify(document.parse(path.read_text(encoding="utf-8")))
        except (document.ParseError, UnicodeDecodeError, ValueError):
            continue
        files.append(path)
    return files


#: 違反0件を要求する規則。``label-control-passive`` は規則の要否が判断待ちであるため除く。
_ZERO_REQUIRED_RULES: frozenset[str] = frozenset(
    {r.id for r in rules.RULES} - {"label-control-passive"}
)


def test_corpus_check():
    files = _collect_settings()
    if not files:
        print("    no macro-form or tool-form .setting files found under FUSION_MACRO_CLI_PRESETS; skipping")
        return SKIP

    schema_obj = _load_schema()
    if schema_obj is None:
        print("    no schema found; schema-dependent rules will be skipped for every file")

    per_rule: dict[str, list[str]] = {r.id: [] for r in rules.RULES}
    skipped_counts: dict[str, int] = {}
    read_errors: list[str] = []
    kinds: dict[str, int] = {}

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
            doc = document.parse(text)
            kind = form_mod.classify(doc)
        except (document.ParseError, ValueError, UnicodeDecodeError) as exc:
            read_errors.append(f"{path}: {exc!r}")
            continue
        kinds[kind] = kinds.get(kind, 0) + 1
        result = rules.check(doc, schema_obj)
        for v in result.violations:
            per_rule[v.rule].append(f"{path}:{v.location} ({v.message})")
        for rule_id in result.skipped:
            skipped_counts[rule_id] = skipped_counts.get(rule_id, 0) + 1

    print(f"    .setting files: {len(files)} ({', '.join(f'{k}: {n}' for k, n in sorted(kinds.items()))})")
    print(f"    unreadable/unclassifiable: {len(read_errors)}")
    for err in read_errors:
        print(f"      {err}")

    print("    --- violations per rule (existing presets) ---")
    for rule in rules.RULES:
        count = len(per_rule[rule.id])
        basis = "0 required" if rule.id in _ZERO_REQUIRED_RULES else "pending judgment, not asserted"
        note = f", skipped for {skipped_counts[rule.id]} files" if rule.id in skipped_counts else ""
        print(f"    {rule.id:28s} {count:6d}  [{basis}]{note}")

    for rule in rules.RULES:
        locations = per_rule[rule.id]
        if not locations:
            continue
        print(f"    --- {rule.id}: {len(locations)} violation(s) ---")
        for loc in locations:
            print(f"      {loc}")

    failures = {rid: per_rule[rid] for rid in _ZERO_REQUIRED_RULES if per_rule[rid]}
    assert not failures, (
        "rules requiring zero violations found some on the existing presets: "
        f"{sorted(failures)}"
    )
    return PASS


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------

TESTS = [
    ("test_classify_comp_and_setting", test_classify_comp_and_setting),
    ("test_rules_declare_the_kinds_they_apply_to", test_rules_declare_the_kinds_they_apply_to),
    ("test_tool_form_runs_tool_rules_only", test_tool_form_runs_tool_rules_only),
    ("test_ascii_name_excludes_macro_name", test_ascii_name_excludes_macro_name),
    ("test_ascii_name_detect_and_fix_inner_tool", test_ascii_name_detect_and_fix_inner_tool),
    ("test_unique_name_detect_and_fix", test_unique_name_detect_and_fix),
    ("test_no_dangling_detect_and_fix", test_no_dangling_detect_and_fix),
    ("test_keyframe_origin_is_composition_wide", test_keyframe_origin_is_composition_wide),
    ("test_keyframe_origin_detect_and_fix", test_keyframe_origin_detect_and_fix),
    ("test_no_absolute_path_excludes_prefs", test_no_absolute_path_excludes_prefs),
    ("test_no_absolute_path_detects_loader_filename", test_no_absolute_path_detects_loader_filename),
    ("test_font_available_detects_missing_font", test_font_available_detects_missing_font),
    ("test_font_available_reads_all_ttc_faces", test_font_available_reads_all_ttc_faces),
    ("test_generator_follows_format_detect_and_fix", test_generator_follows_format_detect_and_fix),
    (
        "test_generator_follows_format_excludes_non_generators",
        test_generator_follows_format_excludes_non_generators,
    ),
    ("test_loader_scaled", test_loader_scaled),
    ("test_no_macro_detect_and_fix", test_no_macro_detect_and_fix),
    ("test_no_macro_double_nested_flattens_fully", test_no_macro_double_nested_flattens_fully),
    ("test_no_media_io_detect_and_fix", test_no_media_io_detect_and_fix),
    ("test_page_controls_detect_and_fix", test_page_controls_detect_and_fix),
    ("test_input_exists_detect_and_fix", test_input_exists_detect_and_fix),
    (
        "test_input_exists_skips_type_with_no_schema_definition",
        test_input_exists_skips_type_with_no_schema_definition,
    ),
    (
        "test_input_exists_records_skipped_input_for_unknown_type",
        test_input_exists_records_skipped_input_for_unknown_type,
    ),
    ("test_label_unique_detect_and_fix", test_label_unique_detect_and_fix),
    ("test_label_control_passive_detect_and_fix", test_label_control_passive_detect_and_fix),
    ("test_no_driven_publish_detect_and_fix", test_no_driven_publish_detect_and_fix),
    ("test_no_connected_publish", test_no_connected_publish),
    ("test_no_nest_publish", test_no_nest_publish),
    ("test_publish_element_selected", test_publish_element_selected),
    ("test_color_control_rgb", test_color_control_rgb),
    ("test_output_reachable", test_output_reachable),
    ("test_no_self_image", test_no_self_image),
    (
        "test_schema_required_rules_are_skipped_without_schema",
        test_schema_required_rules_are_skipped_without_schema,
    ),
    ("test_corpus_check", test_corpus_check),
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
