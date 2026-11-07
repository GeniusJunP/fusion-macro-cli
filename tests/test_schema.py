"""schema.py の検証。

`python3 tests/test_schema.py` で単独実行できる。終了コードは
`tests/run.py` の規約に合わせる。0 は合格、1 は失敗、2 は検証用の
スキーマファイルが無く検査できなかったことを表す。

検証は2種類に分かれる。合成した小さな辞書に対するもの（`schema_dir` の
環境変数上書き、`list_schemas` の新しい順、`missing_reason` の文言変換など）は
実機のスキーマが無くても常に走る。制御の境界に関する検証
（`TextPlus` や `LightDirectional` の事実）は実機で取得したスキーマを要るため、
`schema.list_schemas()` が1件も見つからない場合はまとめて飛ばす。

制御の切り出しは `schema.py` を素通しで検証しても意味が無いため、
このファイルの `_split_controls` は `schema.Schema.controls` を呼ばず、
仕様の規則をここへ独立に書き下す。両者が同じ誤りを共有すると、
区切り方そのものに誤りがあった場合に検出できなくなるため。

出力ポート（`outputs`）は合成データでのみ検証する。実データはまだ
`outputs` を持たない旧い形のスキーマであり、`schema dump` を
再実行して取り直すまで実機での検証はできない。
"""

from __future__ import annotations

import gzip
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from fusion_macro_cli import schema  # noqa: E402


_NO_GROUP = object()  # 「ControlGroup を持たない」を表す番兵。schema.py の _NO_GROUP とは別物。


def _split_controls(inputs: dict[str, dict[str, object]]) -> list[list[str]]:
    """制御の切り出しを、`schema.py` と独立に書き下したもの。

    宣言順の入力から、`INPI_IC_ControlGroup` を持たない入力、または
    持つが `INPI_IC_ControlID` を持たない入力を制御の先頭として区切る。
    `ControlGroup` と `ControlID` を両方持つ入力（続きの形）でも、
    直前の入力が同じ `ControlGroup` の値を持たなければ単独の制御にする。
    """
    controls: list[list[str]] = []
    prev_group = _NO_GROUP
    for name, attrs in inputs.items():
        has_group = "INPI_IC_ControlGroup" in attrs
        has_id = "INPI_IC_ControlID" in attrs
        group = attrs.get("INPI_IC_ControlGroup")
        if has_group and has_id and prev_group is not _NO_GROUP and group == prev_group:
            controls[-1].append(name)
        else:
            controls.append([name])
        prev_group = group if has_group else _NO_GROUP
    return controls


def _write_gz_json(path: pathlib.Path, data: dict) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)


def _minimal_schema_dict() -> dict:
    return {
        "version": "21.0.4.0005",
        "registry": [
            {
                "id": "TextPlus",
                "class_type": 11,
                "hidden": False,
                "modifier": False,
                "category": "Generator",
                "name": "Text+",
            },
            {
                "id": "OFX_Operator",
                "class_type": 1,
                "hidden": False,
                "modifier": True,
                "category": None,
                "name": "OFX_Operator",
            },
            {
                "id": "Circle",
                "class_type": 34,
                "hidden": False,
                "modifier": False,
                "category": "Shape",
                "name": "Circle",
            },
            {
                "id": "BezierSpline",
                "class_type": 34,
                "hidden": False,
                "modifier": True,
                "category": "Tool",
                "name": "BezierSpline",
            },
        ],
        "tools": {
            "TextPlus": {
                "Font": {
                    "INPS_ID": "Font",
                    "INPS_DataType": "Text",
                    "INPB_External": True,
                    "INPID_InputControl": "FontFileControl",
                    "INPI_IC_ControlGroup": 3,
                    "INPS_ICS_ControlPage": "Text",
                },
                "Style": {
                    "INPS_ID": "Style",
                    "INPS_DataType": "Text",
                    "INPB_External": True,
                    "INPID_InputControl": "FontFileControl",
                    "INPI_IC_ControlGroup": 3,
                    "INPI_IC_ControlID": 1,
                    "INPS_ICS_ControlPage": "Text",
                },
                "Size": {
                    "INPS_ID": "Size",
                    "INPS_DataType": "Number",
                    "INPB_External": True,
                    "INPID_InputControl": "SliderControl",
                    "INPS_ICS_ControlPage": "Text",
                    "INPN_Default": 0.08,
                },
            },
            "BezierSpline": {},
        },
        "failed": [
            {"id": "OFX_Operator", "reason": "not_a_tool", "class_type": 1},
            {"id": "Circle", "reason": "add_tool_returned_none"},
        ],
    }


def _schema_dict_with_outputs() -> dict:
    """`outputs` を持つスキーマ。`TextPlus` に2つの出力ポートを持たせ、

    宣言順の保存と `OUTS_DataType` の読み出しを確認する。`BezierSpline` は
    `tools` にはあるが `outputs` には無く、「入力は取れたが出力の定義は無い」
    状態を作る。
    """
    data = _minimal_schema_dict()
    data["outputs"] = {
        "TextPlus": {
            "Output": {"OUTS_ID": "Output", "OUTS_DataType": "Image"},
            "EffectMask": {"OUTS_ID": "EffectMask", "OUTS_DataType": "Mask"},
        },
    }
    return data


# --------------------------------------------------------------------------
# 実機のスキーマを要らない検証
# --------------------------------------------------------------------------


def test_schema_dir_default() -> None:
    saved = os.environ.pop(schema.ENV_VAR, None)
    try:
        expected = (
            pathlib.Path.home() / ".local" / "share" / "fusion-macro-cli" / "schema"
        )
        assert schema.schema_dir() == expected
    finally:
        if saved is not None:
            os.environ[schema.ENV_VAR] = saved


def test_schema_dir_env_override() -> None:
    saved = os.environ.get(schema.ENV_VAR)
    try:
        os.environ[schema.ENV_VAR] = "/tmp/fusion-macro-cli-test-schema"
        assert schema.schema_dir() == pathlib.Path(
            "/tmp/fusion-macro-cli-test-schema"
        )
    finally:
        if saved is None:
            os.environ.pop(schema.ENV_VAR, None)
        else:
            os.environ[schema.ENV_VAR] = saved


def test_list_schemas_orders_newest_first() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        older = d / "fusion-20.0.0.0000.json.gz"
        newer = d / "fusion-21.0.4.0005.json.gz"
        _write_gz_json(older, _minimal_schema_dict())
        _write_gz_json(newer, _minimal_schema_dict())
        now = pathlib.Path(newer).stat().st_mtime
        os.utime(older, (now - 100, now - 100))
        os.utime(newer, (now, now))
        result = schema.list_schemas(d)
        assert result == [newer, older], result


def test_list_schemas_missing_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = pathlib.Path(tmp) / "does-not-exist"
        assert schema.list_schemas(missing) == []


def test_load_specific_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "fusion-21.0.4.0005.json.gz"
        _write_gz_json(path, _minimal_schema_dict())
        s = schema.load(path)
        assert s.version == "21.0.4.0005"


def test_load_latest_uses_newest_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        old_data = _minimal_schema_dict()
        old_data["version"] = "20.0.0.0000"
        new_data = _minimal_schema_dict()
        new_data["version"] = "21.0.4.0005"
        older = d / "fusion-20.0.0.0000.json.gz"
        newer = d / "fusion-21.0.4.0005.json.gz"
        _write_gz_json(older, old_data)
        _write_gz_json(newer, new_data)
        now = newer.stat().st_mtime
        os.utime(older, (now - 100, now - 100))
        os.utime(newer, (now, now))

        saved = os.environ.get(schema.ENV_VAR)
        try:
            os.environ[schema.ENV_VAR] = str(d)
            s = schema.load()
            assert s.version == "21.0.4.0005"
        finally:
            if saved is None:
                os.environ.pop(schema.ENV_VAR, None)
            else:
                os.environ[schema.ENV_VAR] = saved


def test_load_raises_when_no_schema_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        empty = pathlib.Path(tmp)
        saved = os.environ.get(schema.ENV_VAR)
        try:
            os.environ[schema.ENV_VAR] = str(empty)
            try:
                schema.load()
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("expected FileNotFoundError")
        finally:
            if saved is None:
                os.environ.pop(schema.ENV_VAR, None)
            else:
                os.environ[schema.ENV_VAR] = saved


def test_exists_has_inputs_missing_reason_split() -> None:
    s = schema.Schema.from_dict(_minimal_schema_dict())

    # TextPlus: registry にあり、入力定義も取得できている
    assert s.exists("TextPlus")
    assert s.has_inputs("TextPlus")
    assert s.missing_reason("TextPlus") is None

    # OFX_Operator: registry にはあるが、種別クラス1で対象外
    assert s.exists("OFX_Operator")
    assert not s.has_inputs("OFX_Operator")
    assert s.missing_reason("OFX_Operator") is not None

    # Circle: registry にはあるが、AddTool が None を返した
    assert s.exists("Circle")
    assert not s.has_inputs("Circle")
    assert s.missing_reason("Circle") is not None
    assert s.missing_reason("Circle") != s.missing_reason("OFX_Operator")

    # BezierSpline: 取得は成功したが入力定義が空
    assert s.exists("BezierSpline")
    assert s.has_inputs("BezierSpline")
    assert s.missing_reason("BezierSpline") is None
    assert s.input_names("BezierSpline") == []

    # まったく未知の種別: 3つとも「分からない」側になる
    assert not s.exists("NoSuchTool")
    assert not s.has_inputs("NoSuchTool")
    assert s.missing_reason("NoSuchTool") is None


def test_missing_reason_appends_detail() -> None:
    data = _minimal_schema_dict()
    data["failed"].append(
        {
            "id": "Broken",
            "reason": "read_inputs_raised",
            "class_type": 3,
            "detail": "RuntimeError: boom",
        }
    )
    s = schema.Schema.from_dict(data)
    reason = s.missing_reason("Broken")
    assert reason is not None
    assert "RuntimeError: boom" in reason


def test_input_attributes_and_control_group_value_vs_control_id_presence() -> None:
    s = schema.Schema.from_dict(_minimal_schema_dict())

    font = s.input_attributes("TextPlus", "Font")
    assert font.data_type == "Text"
    assert font.control_type == "FontFileControl"
    assert font.external is True
    assert font.control_group == 3
    assert font.has_control_id is False

    style = s.input_attributes("TextPlus", "Style")
    assert style.control_group == 3
    assert style.has_control_id is True

    size = s.input_attributes("TextPlus", "Size")
    assert size.control_group is None
    assert size.has_control_id is False
    assert size.default == 0.08


def test_output_names_and_type_with_outputs_present() -> None:
    s = schema.Schema.from_dict(_schema_dict_with_outputs())

    # 宣言順を保つ
    assert s.output_names("TextPlus") == ["Output", "EffectMask"]
    assert s.output_type("TextPlus", "Output") == "Image"
    assert s.output_type("TextPlus", "EffectMask") == "Mask"

    # 入力は取れたが出力の定義が無い種別（BezierSpline）は「定義なし」
    assert s.output_names("BezierSpline") is None
    assert s.output_type("BezierSpline", "Output") is None

    # 存在しないポート名も「定義なし」（例外にしない）
    assert s.output_type("TextPlus", "NoSuchPort") is None

    # registry にすら無い種別も「定義なし」
    assert s.output_names("NoSuchTool") is None
    assert s.output_type("NoSuchTool", "Output") is None


def test_output_queries_return_none_without_outputs_key() -> None:
    # outputs キー自体を持たない（今の取得済みスキーマに相当する）古い形。
    s = schema.Schema.from_dict(_minimal_schema_dict())
    assert s.output_names("TextPlus") is None
    assert s.output_type("TextPlus", "Output") is None


def test_controls_and_control_of_on_minimal_schema() -> None:
    s = schema.Schema.from_dict(_minimal_schema_dict())
    controls = s.controls("TextPlus")
    assert controls == [["Font", "Style"], ["Size"]], controls
    assert s.control_of("TextPlus", "Font") == ["Font", "Style"]
    assert s.control_of("TextPlus", "Style") == ["Font", "Style"]
    assert s.control_of("TextPlus", "Size") == ["Size"]


def test_continuation_with_mismatched_predecessor_is_standalone() -> None:
    """先頭を欠いた「続きの形」の入力が、無関係な直前の入力へ合流しないことを確認する。

    実データでの例（`Merge` の `ObjectID`。`ControlGroup` 2147483658 ・
    `ControlID` 持ちだが、直前の `CorrectEdges` は `ControlGroup` を持たず、
    本来の先頭が `GetInputList` に現れていない）を模した合成データを使う。
    """
    data = _minimal_schema_dict()
    data["tools"]["Orphan"] = {
        "CorrectEdges": {
            "INPS_ID": "CorrectEdges",
            "INPS_DataType": "Number",
        },
        "ObjectID": {
            "INPS_ID": "ObjectID",
            "INPS_DataType": "Number",
            "INPI_IC_ControlGroup": 2147483658,
            "INPI_IC_ControlID": 1,
        },
        "MaterialID": {
            "INPS_ID": "MaterialID",
            "INPS_DataType": "Number",
            "INPI_IC_ControlGroup": 2147483658,
            "INPI_IC_ControlID": 2,
        },
    }
    s = schema.Schema.from_dict(data)
    controls = s.controls("Orphan")
    # CorrectEdges は無関係。ObjectID は本来の先頭を欠くが、
    # 直後の MaterialID とは ControlGroup が一致するため同じ制御になる。
    assert controls == [["CorrectEdges"], ["ObjectID", "MaterialID"]], controls
    assert s.control_of("Orphan", "ObjectID") != s.control_of("Orphan", "CorrectEdges")


def test_unknown_tool_and_input_raise_key_error() -> None:
    s = schema.Schema.from_dict(_minimal_schema_dict())
    for call in (
        lambda: s.input_names("NoSuchTool"),
        lambda: s.input_attributes("TextPlus", "NoSuchInput"),
        lambda: s.controls("NoSuchTool"),
        lambda: s.control_of("TextPlus", "NoSuchInput"),
    ):
        try:
            call()
        except KeyError:
            pass
        else:
            raise AssertionError(f"expected KeyError from {call}")


def test_unknown_reason_code_raises() -> None:
    data = _minimal_schema_dict()
    data["failed"].append({"id": "Weird", "reason": "no_such_reason"})
    s = schema.Schema.from_dict(data)
    try:
        s.missing_reason("Weird")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unrecognized reason code")


UNCONDITIONAL_TESTS = [
    test_schema_dir_default,
    test_schema_dir_env_override,
    test_list_schemas_orders_newest_first,
    test_list_schemas_missing_directory,
    test_load_specific_path,
    test_load_latest_uses_newest_file,
    test_load_raises_when_no_schema_found,
    test_exists_has_inputs_missing_reason_split,
    test_missing_reason_appends_detail,
    test_input_attributes_and_control_group_value_vs_control_id_presence,
    test_output_names_and_type_with_outputs_present,
    test_output_queries_return_none_without_outputs_key,
    test_controls_and_control_of_on_minimal_schema,
    test_continuation_with_mismatched_predecessor_is_standalone,
    test_unknown_tool_and_input_raise_key_error,
    test_unknown_reason_code_raises,
]


# --------------------------------------------------------------------------
# 実機のスキーマを要る検証
# --------------------------------------------------------------------------


def test_real_textplus_control_boundaries(s: schema.Schema, raw: dict) -> None:
    # raw は gzip+json だけで読んだ生データ。schema.Schema を経由しないため、
    # ここでの切り出しが schema.py の実装と誤りを共有しない。
    independent = _split_controls(raw["tools"]["TextPlus"])
    via_schema = s.controls("TextPlus")
    assert independent == via_schema, "independent split disagrees with schema.Schema.controls"

    def find(name: str) -> list[str]:
        for control in via_schema:
            if name in control:
                return control
        raise AssertionError(f"{name} missing from any control")

    assert find("Font") == find("Style")
    assert find("Font") == ["Font", "Style"]

    color = find("Red1")
    assert find("Green1") == color
    assert find("Blue1") == color
    assert find("Alpha1") == color
    assert color == ["Red1", "Green1", "Blue1", "Alpha1"], color

    assert find("Start") == find("End")
    assert find("Start") == ["Start", "End"]

    assert find("Size") == ["Size"]

    # 要素1 (Red1) と要素2 (Red2) は別の制御
    assert find("Red1") != find("Red2")


def test_real_lightdirectional_shares_control_group(s: schema.Schema, raw: dict) -> None:
    assert s.exists("LightDirectional")
    assert s.has_inputs("LightDirectional")
    controls = s.controls("LightDirectional")
    assert controls == _split_controls(raw["tools"]["LightDirectional"])

    def find(name: str) -> list[str]:
        for control in controls:
            if name in control:
                return control
        raise AssertionError(f"{name} missing from any control")

    light_color = find("Red")
    assert find("Green") == light_color
    assert find("Blue") == light_color
    assert light_color == ["Red", "Green", "Blue"], light_color

    shadow_color = find("ShadowLightInputs3D.ShadowColorRed")
    assert find("ShadowLightInputs3D.ShadowColorGreen") == shadow_color
    assert find("ShadowLightInputs3D.ShadowColorBlue") == shadow_color
    assert shadow_color == [
        "ShadowLightInputs3D.ShadowColorRed",
        "ShadowLightInputs3D.ShadowColorGreen",
        "ShadowLightInputs3D.ShadowColorBlue",
    ], shadow_color

    clip = find("ShadowLightInputs3D.NearClip")
    assert find("ShadowLightInputs3D.FarClip") == clip
    assert clip == [
        "ShadowLightInputs3D.NearClip",
        "ShadowLightInputs3D.FarClip",
    ], clip

    # 3つの制御は別物だが、先頭が同じ ControlGroup を共有する
    assert light_color != shadow_color
    assert shadow_color != clip
    assert light_color != clip

    group_of_light = s.input_attributes("LightDirectional", light_color[0]).control_group
    group_of_shadow = s.input_attributes(
        "LightDirectional", shadow_color[0]
    ).control_group
    group_of_clip = s.input_attributes("LightDirectional", clip[0]).control_group
    assert group_of_light is not None
    assert group_of_light == group_of_shadow == group_of_clip


def test_real_merge_orphaned_continuation_is_standalone(
    s: schema.Schema, raw: dict
) -> None:
    """`Merge` の `ObjectID` と `ForegroundZOffset` が、無関係な直前の入力に合流しないことを確認する。

    どちらも `INPI_IC_ControlGroup` と `INPI_IC_ControlID` を両方持つ
    「続きの形」の入力だが、本来属するべき先頭（`ControlID` を持たない
    同じ `ControlGroup` の入力）が `GetInputList` に現れていない。
    """
    controls = s.controls("Merge")
    assert controls == _split_controls(raw["tools"]["Merge"])

    def find(name: str) -> list[str]:
        for control in controls:
            if name in control:
                return control
        raise AssertionError(f"{name} missing from any control")

    assert find("ObjectID") != find("CorrectEdges")
    assert find("ForegroundZOffset") != find("PerformDepthMerge")
    # ObjectID は先頭を欠くが、直後の MaterialID とは ControlGroup が一致し
    # 同じ制御になる。ForegroundZOffset は単独になる。
    assert find("ObjectID") == ["ObjectID", "MaterialID"]
    assert find("ForegroundZOffset") == ["ForegroundZOffset"]


def test_real_scan_all_tools_for_mismatched_continuation(raw: dict) -> None:
    """全種別を走査し、続きの形の入力が直前と異なる `ControlGroup` の値を持つ場合に

    合流していないことを確認する。`_split_controls`（独立実装）で全種別を切り出し、
    続きの形をした入力のうち、直前の入力の `ControlGroup` と一致しないものが
    すべて単独の制御（長さ1、または一致する後続とだけ合流した制御）の先頭に
    立っていることを検査する。
    """
    mismatched: list[tuple[str, str]] = []
    for tool_id, inputs in raw["tools"].items():
        names = list(inputs.keys())
        prev_group: object = _NO_GROUP
        controls = _split_controls(inputs)
        control_of: dict[str, list[str]] = {}
        for control in controls:
            for name in control:
                control_of[name] = control
        for name in names:
            attrs = inputs[name]
            has_group = "INPI_IC_ControlGroup" in attrs
            has_id = "INPI_IC_ControlID" in attrs
            group = attrs.get("INPI_IC_ControlGroup")
            if has_group and has_id and (
                prev_group is _NO_GROUP or group != prev_group
            ):
                mismatched.append((tool_id, name))
                # 単独（あるいは後続とだけ合流した）制御の先頭であること。
                # 直前の入力と同じ制御に入っていないことを確認する。
                assert control_of[name][0] == name, (
                    f"{tool_id}.{name} should head its own control, "
                    f"found in {control_of[name]}"
                )
            prev_group = group if has_group else _NO_GROUP

    print(f"    (scan) mismatched-predecessor continuations found: {len(mismatched)}")
    assert ("Merge", "ObjectID") in mismatched
    assert ("Merge", "ForegroundZOffset") in mismatched


REAL_SCHEMA_TESTS = [
    test_real_textplus_control_boundaries,
    test_real_lightdirectional_shares_control_group,
    test_real_merge_orphaned_continuation_is_standalone,
]

REAL_SCHEMA_RAW_ONLY_TESTS = [
    test_real_scan_all_tools_for_mismatched_continuation,
]


def main() -> int:
    failed = False

    for test in UNCONDITIONAL_TESTS:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - 個別テストの想定外の例外も FAIL として報告する
            failed = True
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")

    candidates = schema.list_schemas()
    if not candidates:
        print(f"no schema files found under {schema.schema_dir()}")
        if failed:
            return 1
        print("skipping tests that require a real schema (exit code 2)")
        return 2

    s = schema.load(candidates[0])
    # schema.py の Schema を経由せず、gzip+json だけで生データを読み直す。
    # 独立に書いた _split_controls をこの生データに対して走らせるため。
    with gzip.open(candidates[0], "rt", encoding="utf-8") as f:
        raw = json.load(f)
    for test in REAL_SCHEMA_TESTS:
        try:
            test(s, raw)
        except Exception as exc:  # noqa: BLE001 - 個別テストの想定外の例外も FAIL として報告する
            failed = True
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")

    for test in REAL_SCHEMA_RAW_ONLY_TESTS:
        try:
            test(raw)
        except Exception as exc:  # noqa: BLE001 - 個別テストの想定外の例外も FAIL として報告する
            failed = True
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
