"""``resolve/dump.py`` の検査。

Resolve には接続しない範囲で検査する。生成したスクリプトの構造、
``--console`` の出力、出力ファイル名の決め方、書き出す JSON の構造を検査する。
Fusion 内部スクリプトの実際の中身は、フェイクの ``fusion`` / ``resolve``
オブジェクトを渡して ``exec`` することで検査する。これは
Resolve の実機なしに、Fusion 内部で走るコードそのものを検査するための
唯一の手段であり、``build_script`` が生成した文字列の断片を
個別にテストする方式は取らない（生成の実装と検査の実装が同じ誤りを
共有し得るため）。Resolve が要る部分（実際の GetRegList・AddTool の挙動）は実機で確認する。

終了コードは 0 が合格、1 が失敗。
"""

import gzip
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fusion_macro_cli import schema  # noqa: E402
from fusion_macro_cli.resolve import dump  # noqa: E402


# ---------------------------------------------------------------------------
# フェイクの Fusion / Resolve スクリプティング API。
# ---------------------------------------------------------------------------


class FakeInput:
    """``Tool.GetInputList()`` が返す要素。"""

    def __init__(self, attrs):
        self._attrs = attrs

    def GetAttrs(self):
        return dict(self._attrs)


class FakeOutput:
    """``Tool.GetOutputList()`` が返す要素。構造は ``FakeInput`` と同じ
    （``GetAttrs()`` のみ）だが、Fusion 側の別オブジェクトを表すため
    別クラスにして区別する。"""

    def __init__(self, attrs):
        self._attrs = attrs

    def GetAttrs(self):
        return dict(self._attrs)


class FakeTool:
    """``Comp.AddTool`` が返すツール。

    ``spec`` は {入力名: 属性辞書} の初期集合。``growth`` は
    {Enabled<n> の n: 追加で現れる入力の {name: attrs}} を持ち、
    多要素ツールの展開を模す。``raise_on_get_input_list`` を立てると
    ``GetInputList`` が例外を投げる（read_inputs_raised の検査用）。
    ``output_spec`` は {出力ポート名: 属性辞書}（既定は出力ポート無し）。
    ``raise_on_get_output_list`` を立てると ``GetOutputList`` が例外を投げる。
    """

    def __init__(self, spec, growth=None, raise_on_get_input_list=False,
                 output_spec=None, raise_on_get_output_list=False):
        self._names = list(spec.keys())
        self._attrs = dict(spec)
        self._growth = growth or {}
        self._raise_on_get_input_list = raise_on_get_input_list
        self._output_names = list((output_spec or {}).keys())
        self._output_attrs = dict(output_spec or {})
        self._raise_on_get_output_list = raise_on_get_output_list
        self.deleted = False
        self.set_inputs = []

    def GetInputList(self):
        if self._raise_on_get_input_list:
            raise RuntimeError("boom: cannot read inputs")
        return {i + 1: FakeInput(self._attrs[n]) for i, n in enumerate(self._names)}

    def GetOutputList(self):
        if self._raise_on_get_output_list:
            raise RuntimeError("boom: cannot read outputs")
        return {
            i + 1: FakeOutput(self._output_attrs[n])
            for i, n in enumerate(self._output_names)
        }

    def SetInput(self, name, value):
        self.set_inputs.append((name, value))
        if name in self._growth:
            for extra_name, extra_attrs in self._growth[name].items():
                if extra_name not in self._names:
                    self._names.append(extra_name)
                    self._attrs[extra_name] = extra_attrs
        return None

    def Delete(self):
        self.deleted = True


class FakeComp:
    """``Item.GetFusionCompByIndex`` が返すコンポジション。

    ``tool_factories`` は {registry の id: () -> FakeTool、または
    None（AddTool が None を返す場合）、または例外を送出する呼び出し可能}。
    """

    def __init__(self, tool_factories):
        self._tool_factories = tool_factories
        self.locked = False
        self.added_log = []  # [(tool_id, tool)]

    def Lock(self):
        self.locked = True

    def AddTool(self, tool_id):
        factory = self._tool_factories.get(tool_id)
        if factory is None:
            return None
        tool = factory()
        self.added_log.append((tool_id, tool))
        return tool


class FakeItem:
    def __init__(self, comp):
        self._comp = comp

    def GetFusionCompByIndex(self, index):
        return self._comp


class FakeTimeline:
    def __init__(self, name, comp, insert_returns_none=False, no_items=False):
        self._name = name
        self._comp = comp
        self._insert_returns_none = insert_returns_none
        self._no_items = no_items
        self.inserted = False

    def GetName(self):
        return self._name

    def InsertFusionCompositionIntoTimeline(self):
        self.inserted = True
        if self._insert_returns_none:
            return None
        return object()

    def GetItemListInTrack(self, track_type, index):
        if self._no_items:
            return []
        return [FakeItem(self._comp)]


class FakeMediaPool:
    def __init__(self, timeline_factory):
        self._timeline_factory = timeline_factory
        self.created = []
        self.deleted = []
        self.create_returns_none = False

    def CreateEmptyTimeline(self, name):
        if self.create_returns_none:
            return None
        timeline = self._timeline_factory(name)
        self.created.append(timeline)
        return timeline

    def DeleteTimelines(self, timelines):
        self.deleted.extend(timelines)
        return True


class FakeProject:
    def __init__(self, media_pool, name="_dump_probe"):
        self._media_pool = media_pool
        self._name = name
        self._timelines = []

    def GetMediaPool(self):
        return self._media_pool

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetName(self):
        return self._name


class FakeProjectManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class FakeResolve:
    def __init__(self, project_manager, *, pages_blocked=False):
        self._pm = project_manager
        self.page_history = []
        # プロジェクトマネージャーやダイアログの表示中は、OpenPage が None を返しページが開かない。
        self._pages_blocked = pages_blocked

    def GetProjectManager(self):
        return self._pm

    def OpenPage(self, page):
        if self._pages_blocked:
            return None
        self.page_history.append(page)
        return True

    def GetCurrentPage(self):
        return self.page_history[-1] if self.page_history else None


class FakeFusion:
    def __init__(self, version, registry_attrs):
        self._version = version
        # registry_attrs: [(id, attrs)] の順序付きリスト。GetRegList の
        # 順を保つ必要はないが、決定的な検査のため順序を保持する。
        self._registry_attrs = registry_attrs

    def GetAttrs(self):
        return {"FUSIONS_Version": self._version}

    def GetRegList(self, typemask):
        assert typemask == 1
        return {
            i + 1: _RegEntry(tool_id)
            for i, (tool_id, _attrs) in enumerate(self._registry_attrs)
        }

    def GetRegAttrs(self, tool_id):
        for rid, attrs in self._registry_attrs:
            if rid == tool_id:
                return dict(attrs)
        raise KeyError(tool_id)


class _RegEntry:
    def __init__(self, tool_id):
        self.ID = tool_id


# ---------------------------------------------------------------------------
# 検査対象のシナリオを組み立てるヘルパー。
# ---------------------------------------------------------------------------


def _default_registry_and_tools():
    """一通りの分岐を踏む、決定的なレジストリとツール群を作る。"""

    registry_attrs = [
        ("Background", {"REGI_ClassType": 11.0, "REGB_Hide": False,
                         "REGS_Category": "Generator", "REGS_Name": "Background"}),
        ("AdminOnly", {"REGI_ClassType": 1.0, "REGB_Hide": False,
                        "REGS_Category": None, "REGS_Name": "AdminOnly"}),
        ("NoneOnAdd", {"REGI_ClassType": 3.0, "REGB_Hide": False,
                        "REGS_Category": "Generator", "REGS_Name": "NoneOnAdd"}),
        ("RaisesOnAdd", {"REGI_ClassType": 3.0, "REGB_Hide": False,
                          "REGS_Category": "Generator", "REGS_Name": "RaisesOnAdd"}),
        ("RaisesOnRead", {"REGI_ClassType": 3.0, "REGB_Hide": False,
                           "REGS_Category": "Generator", "REGS_Name": "RaisesOnRead"}),
        ("EmptyInputs", {"REGI_ClassType": 3.0, "REGB_Hide": True,
                          "REGS_Category": "Modifier", "REGS_Name": "EmptyInputs"}),
        ("MultiElement", {"REGI_ClassType": 3.0, "REGB_Hide": False,
                           "REGS_Category": "Generator", "REGS_Name": "MultiElement"}),
        # bit1（値2）が立っていない = モディファイア。
        ("Follower", {"REGI_ClassType": 5.0, "REGB_Hide": False,
                       "REGS_Category": "Modifier", "REGS_Name": "Follower"}),
        ("TwoOutputs", {"REGI_ClassType": 3.0, "REGB_Hide": False,
                         "REGS_Category": "Generator", "REGS_Name": "TwoOutputs"}),
        ("RaisesOnReadOutput", {"REGI_ClassType": 3.0, "REGB_Hide": False,
                                 "REGS_Category": "Generator",
                                 "REGS_Name": "RaisesOnReadOutput"}),
    ]

    def raise_factory():
        raise RuntimeError("boom: AddTool raised")

    background_spec = {
        "SettingsNest": {
            "INPS_ID": "SettingsNest", "INPS_DataType": "Number",
            "INPI_IC_ControlGroup": 0, "INPI_IC_ControlID": 0,
            "INPB_External": False, "INPN_Default": 1.0,
        },
    }
    # PolyPath を模した、Image 型を持たない2出力の種別（output-reachable の
    # 「画像を出す出力が1つある」判定がこの構成を区別できる必要がある）。
    two_outputs_spec = {
        "Position": {"OUTS_ID": "Position", "OUTS_DataType": "Point",
                      "OUTS_Name": "Position", "OUTS_PassThrough": False},
        "Heading": {"OUTS_ID": "Heading", "OUTS_DataType": "Number"},
    }

    multi_spec = {
        "Red1": {"INPS_ID": "Red1", "INPS_DataType": "Number",
                  "INPB_External": True, "INPI_IC_ControlGroup": 10},
    }
    multi_growth = {
        "Enabled2": {
            "Red2": {"INPS_ID": "Red2", "INPS_DataType": "Number",
                      "INPB_External": True, "INPI_IC_ControlGroup": 11},
        },
        "Enabled3": {
            "Red3": {"INPS_ID": "Red3", "INPS_DataType": "Number",
                      "INPB_External": True, "INPI_IC_ControlGroup": 12},
        },
        # Enabled4 以降は存在しない（無害な無視で展開が止まることを検査する）。
    }

    follower_spec = {
        "Input": {"INPS_ID": "Input", "INPS_DataType": "Number",
                   "INPB_External": True},
    }

    # False 相当の値を1つ含め、出力側にも入力と同じフィルタが効くことを検査する。
    background_output_spec = {
        "Output": {"OUTS_ID": "Output", "OUTS_DataType": "Image",
                    "OUTS_Name": "出力", "OUTS_PassThrough": False},
    }

    tool_factories = {
        "Background": lambda: FakeTool(dict(background_spec),
                                        output_spec=dict(background_output_spec)),
        "NoneOnAdd": None,
        "RaisesOnAdd": raise_factory,
        "RaisesOnRead": lambda: FakeTool({}, raise_on_get_input_list=True),
        "EmptyInputs": lambda: FakeTool({}),
        "MultiElement": lambda: FakeTool(dict(multi_spec), growth=multi_growth),
        "Follower": lambda: FakeTool(dict(follower_spec)),
        "TwoOutputs": lambda: FakeTool({}, output_spec=dict(two_outputs_spec)),
        "RaisesOnReadOutput": lambda: FakeTool(
            {"X": {"INPS_ID": "X", "INPS_DataType": "Number", "INPB_External": True}},
            raise_on_get_output_list=True,
        ),
        # AdminOnly は class_type=1 のため AddTool 自体を呼ばない設計だが、
        # 万一呼ばれたときに検査が壊れないよう None を返す処理も用意する。
        "AdminOnly": None,
    }

    return registry_attrs, tool_factories


def _run_script(registry_attrs, tool_factories, schema_dir, *, project_name="_dump_probe",
                 create_returns_none=False, insert_returns_none=False, no_items=False,
                 comp_is_none=False, no_project=False, pages_blocked=False):
    """フェイクを組み立て、生成したスクリプトを exec する。標準出力を捨てる。"""

    fusion = FakeFusion("99.9.9.9999", registry_attrs)
    comp = None if comp_is_none else FakeComp(tool_factories)
    timeline = FakeTimeline(
        "_fusion_macro_cli_schema_dump", comp,
        insert_returns_none=insert_returns_none, no_items=no_items,
    )
    media_pool = FakeMediaPool(lambda name: timeline)
    media_pool.create_returns_none = create_returns_none
    project = None if no_project else FakeProject(media_pool, name=project_name)
    pm = FakeProjectManager(project)
    resolve = FakeResolve(pm, pages_blocked=pages_blocked)

    script = dump.build_script(schema_dir)
    glb = {"fusion": fusion, "resolve": resolve, "__name__": "__fusion_dump_test__"}
    with redirect_stdout(io.StringIO()):
        exec(compile(script, "<dump script>", "exec"), glb)
    return glb, fusion, comp, timeline, media_pool, project


class BuildScriptTest(unittest.TestCase):
    def test_script_compiles(self):
        script = dump.build_script(Path("/tmp/schema"))
        compile(script, "<script>", "exec")

    def test_script_embeds_absolute_schema_dir(self):
        script = dump.build_script(Path("relative/schema"))
        self.assertIn(str(Path("relative/schema").resolve()), script)

    def test_script_is_identical_regardless_of_console_mode(self):
        # console 経由か RunScript 経由かでスクリプトの中身は変わらない。
        # build_script は console フラグを取らないこと自体がその保証になる。
        a = dump.build_script(Path("/tmp/schema"))
        b = dump.build_script(Path("/tmp/schema"))
        self.assertEqual(a, b)


class WriteScriptAndConsoleCommandTest(unittest.TestCase):
    def test_write_script_creates_dir_and_file(self):
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td) / "nested" / "schema"
            path = dump.write_script(schema_dir)
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "_dump.py3")
            self.assertEqual(path.read_text(encoding="utf-8"), dump.build_script(schema_dir))

    def test_console_command_is_a_single_exec_line(self):
        with tempfile.TemporaryDirectory() as td:
            script_path = Path(td) / "_dump.py3"
            script_path.write_text("pass", encoding="utf-8")
            line = dump.console_command(script_path)
            self.assertEqual(line.count("\n"), 0)
            self.assertTrue(line.startswith("exec(open("))
            self.assertIn(str(script_path.resolve()), line)
            self.assertIn('encoding="utf-8"', line)


class SchemaDirDelegationTest(unittest.TestCase):
    """スキーマのディレクトリのパス規約は ``schema.schema_dir()`` に統一してある。

    このモジュール自身は既定値を持たず、``schema_dir`` を省略したときに
    ``schema.schema_dir()`` を読みにいくことだけを検査する
    （既定値そのものの規約は ``tests/test_schema.py`` が検査する）。
    """

    def test_run_falls_back_to_schema_schema_dir(self):
        with tempfile.TemporaryDirectory() as td:
            expected = Path(td) / "delegated-schema-dir"
            original = schema.schema_dir
            schema.schema_dir = lambda: expected
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = dump.run(schema_dir=None, console=True)
                self.assertEqual(code, 0)
                self.assertTrue((expected / "_dump.py3").is_file())
            finally:
                schema.schema_dir = original


class InternalScriptTest(unittest.TestCase):
    """フェイクの fusion / resolve を渡してスクリプトを exec し、
    書き出された JSON の構造を検査する。"""

    def _dump_and_load(self, **kwargs):
        registry_attrs, tool_factories = _default_registry_and_tools()
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td)
            _run_script(registry_attrs, tool_factories, schema_dir, **kwargs)
            out_path = schema_dir / "fusion-99.9.9.9999.json.gz"
            self.assertTrue(out_path.is_file(), "expected output file was not written")
            with gzip.open(out_path, "rt", encoding="utf-8") as f:
                return json.load(f)

    def test_output_filename_uses_fusion_version(self):
        registry_attrs, tool_factories = _default_registry_and_tools()
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td)
            _run_script(registry_attrs, tool_factories, schema_dir)
            self.assertTrue((schema_dir / "fusion-99.9.9.9999.json.gz").is_file())

    def test_top_level_keys(self):
        data = self._dump_and_load()
        self.assertEqual(
            set(data.keys()), {"version", "registry", "tools", "outputs", "failed"}
        )
        self.assertEqual(data["version"], "99.9.9.9999")

    def test_registry_entries_and_order(self):
        data = self._dump_and_load()
        ids = [entry["id"] for entry in data["registry"]]
        self.assertEqual(
            ids,
            ["Background", "AdminOnly", "NoneOnAdd", "RaisesOnAdd", "RaisesOnRead",
             "EmptyInputs", "MultiElement", "Follower", "TwoOutputs",
             "RaisesOnReadOutput"],
        )
        by_id = {entry["id"]: entry for entry in data["registry"]}
        self.assertEqual(
            by_id["Background"],
            {"id": "Background", "class_type": 11, "hidden": False,
             "modifier": False, "category": "Generator", "name": "Background"},
        )
        # bit1（値2）が立っていない Follower（class_type=5）はモディファイア。
        self.assertTrue(by_id["Follower"]["modifier"])
        self.assertFalse(by_id["Background"]["modifier"])
        # class_type=1 は modifier のビット規則ではモディファイア側になるが、
        # not_a_tool として failed に記録されるため tools には現れない。
        self.assertTrue(by_id["AdminOnly"]["modifier"])
        self.assertIsNone(by_id["AdminOnly"]["category"])

    def test_failed_reasons(self):
        data = self._dump_and_load()
        failed_by_id = {entry["id"]: entry for entry in data["failed"]}
        self.assertEqual(failed_by_id["AdminOnly"]["reason"], "not_a_tool")
        self.assertEqual(failed_by_id["AdminOnly"]["class_type"], 1)
        self.assertEqual(failed_by_id["NoneOnAdd"]["reason"], "add_tool_returned_none")
        self.assertEqual(failed_by_id["RaisesOnAdd"]["reason"], "add_tool_raised")
        self.assertIn("boom: AddTool raised", failed_by_id["RaisesOnAdd"]["detail"])
        self.assertEqual(failed_by_id["RaisesOnRead"]["reason"], "read_inputs_raised")
        self.assertIn("boom: cannot read inputs", failed_by_id["RaisesOnRead"]["detail"])
        # 出力側の読み取り例外も、入力用に定義済みの read_inputs_raised を使う
        # （出力専用の符号は仕様に無い4つのどれにも当たらないため増やさない）。
        self.assertEqual(
            failed_by_id["RaisesOnReadOutput"]["reason"], "read_inputs_raised"
        )
        self.assertIn(
            "boom: cannot read outputs", failed_by_id["RaisesOnReadOutput"]["detail"]
        )
        self.assertEqual(len(data["failed"]), 5)

    def test_output_read_failure_leaves_no_partial_tools_or_outputs_entry(self):
        # 入力の読み取りには成功し出力の読み取りだけが例外を投げた場合でも、
        # tool_id が tools/outputs と failed の双方に載ってはならない
        # （registry/tools/failed が種別ごとに排他という前提を保つ）。
        data = self._dump_and_load()
        self.assertNotIn("RaisesOnReadOutput", data["tools"])
        self.assertNotIn("RaisesOnReadOutput", data["outputs"])

    def test_empty_input_definition_is_tools_not_failed(self):
        data = self._dump_and_load()
        self.assertNotIn("EmptyInputs", [e["id"] for e in data["failed"]])
        self.assertIn("EmptyInputs", data["tools"])
        self.assertEqual(data["tools"]["EmptyInputs"], {})
        # 出力の定義が無い種別も outputs には空の辞書として記録される
        # （tools の「取得はできたが定義が無い」規約と揃える）。
        self.assertIn("EmptyInputs", data["outputs"])
        self.assertEqual(data["outputs"]["EmptyInputs"], {})

    def test_output_ports_and_filtering(self):
        data = self._dump_and_load()
        background_outputs = data["outputs"]["Background"]
        self.assertEqual(set(background_outputs.keys()), {"Output"})
        self.assertEqual(background_outputs["Output"]["OUTS_DataType"], "Image")
        # False 相当の属性はフィルタで除外される。
        self.assertNotIn("OUTS_PassThrough", background_outputs["Output"])

        two_outputs = data["outputs"]["TwoOutputs"]
        self.assertEqual(list(two_outputs.keys()), ["Position", "Heading"])
        self.assertEqual(two_outputs["Position"]["OUTS_DataType"], "Point")
        self.assertEqual(two_outputs["Heading"]["OUTS_DataType"], "Number")
        self.assertNotIn("Output", two_outputs)

    def test_default_valued_attributes_are_dropped(self):
        data = self._dump_and_load()
        settings_nest = data["tools"]["Background"]["SettingsNest"]
        # False / 0 相当のキーは除外される。
        self.assertNotIn("INPI_IC_ControlID", settings_nest)
        self.assertNotIn("INPI_IC_ControlGroup", settings_nest)
        self.assertNotIn("INPB_External", settings_nest)
        # 非既定値は残る。
        self.assertEqual(settings_nest["INPS_ID"], "SettingsNest")
        self.assertEqual(settings_nest["INPN_Default"], 1.0)

    def test_multi_element_expansion(self):
        data = self._dump_and_load()
        multi = data["tools"]["MultiElement"]
        self.assertEqual(set(multi.keys()), {"Red1", "Red2", "Red3"})
        self.assertEqual(multi["Red2"]["INPI_IC_ControlGroup"], 11)

    def test_tools_added_during_the_run_are_all_removed(self):
        registry_attrs, tool_factories = _default_registry_and_tools()
        with tempfile.TemporaryDirectory() as td:
            _glb, _fusion, comp, _tl, _mp, _proj = _run_script(
                registry_attrs, tool_factories, Path(td)
            )
            self.assertTrue(comp.locked)
            # AddTool が呼ばれた全種別（probe の Background を含む）が
            # 最終的に Delete されていること。RaisesOnRead は GetInputList
            # で例外を投げた後も finally で削除されるはずである。
            self.assertTrue(comp.added_log, "expected at least one AddTool call")
            for tool_id, tool in comp.added_log:
                self.assertTrue(tool.deleted, "%s was not deleted" % tool_id)

    def test_dedicated_timeline_is_created_and_deleted(self):
        registry_attrs, tool_factories = _default_registry_and_tools()
        with tempfile.TemporaryDirectory() as td:
            _glb, _fusion, _comp, timeline, media_pool, _proj = _run_script(
                registry_attrs, tool_factories, Path(td)
            )
            self.assertIn(timeline, media_pool.created)
            self.assertIn(timeline, media_pool.deleted)
            self.assertTrue(timeline.inserted)

    def test_ends_on_edit_page(self):
        registry_attrs, tool_factories = _default_registry_and_tools()
        with tempfile.TemporaryDirectory() as td:
            glb, _fusion, _comp, _tl, _mp, _proj = _run_script(
                registry_attrs, tool_factories, Path(td)
            )
            resolve = glb["resolve"]
            # タイムラインの作成は Edit ページで、ツールの生成は Fusion ページで行う。
            self.assertEqual(resolve.page_history[:2], ["edit", "fusion"])
            self.assertEqual(resolve.page_history[-1], "edit")


class InternalScriptFailureModesTest(unittest.TestCase):
    """準備段階の異常系。いずれも例外で止まり、書き込み先を汚さない。"""

    def _expect_runtime_error(self, **kwargs):
        registry_attrs, tool_factories = _default_registry_and_tools()
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td)
            with self.assertRaises(RuntimeError):
                _run_script(registry_attrs, tool_factories, schema_dir, **kwargs)
            self.assertFalse(
                (schema_dir / "fusion-99.9.9.9999.json.gz").exists(),
                "must not write output when preparation failed",
            )

    def test_no_project_open(self):
        self._expect_runtime_error(no_project=True)

    def test_create_empty_timeline_returns_none(self):
        self._expect_runtime_error(create_returns_none=True)

    def test_no_page_can_be_opened(self):
        """プロジェクトマネージャーの表示中のように、Edit ページを開けない場合はエラーで止まる。"""
        self._expect_runtime_error(pages_blocked=True)

    def test_insert_fusion_composition_returns_none(self):
        self._expect_runtime_error(insert_returns_none=True)

    def test_no_items_after_insertion(self):
        self._expect_runtime_error(no_items=True)

    def test_probe_tool_rejected(self):
        # Background 自体が AddTool で None を返す環境 = 実体のない
        # コンポジション。1件だけ確かめて即座にエラーにする。
        registry_attrs, tool_factories = _default_registry_and_tools()
        tool_factories["Background"] = None
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td)
            with self.assertRaises(RuntimeError):
                _run_script(registry_attrs, tool_factories, schema_dir)
            self.assertFalse((schema_dir / "fusion-99.9.9.9999.json.gz").exists())

    def test_cleanup_runs_even_when_probe_is_rejected(self):
        registry_attrs, tool_factories = _default_registry_and_tools()
        tool_factories["Background"] = None
        with tempfile.TemporaryDirectory() as td:
            fusion = FakeFusion("99.9.9.9999", registry_attrs)
            comp = FakeComp(tool_factories)
            timeline = FakeTimeline("_fusion_macro_cli_schema_dump", comp)
            media_pool = FakeMediaPool(lambda name: timeline)
            project = FakeProject(media_pool)
            pm = FakeProjectManager(project)
            resolve = FakeResolve(pm)
            script = dump.build_script(Path(td))
            glb = {"fusion": fusion, "resolve": resolve}
            with self.assertRaises(RuntimeError):
                with redirect_stdout(io.StringIO()):
                    exec(compile(script, "<dump script>", "exec"), glb)
            self.assertIn(timeline, media_pool.deleted)
            self.assertEqual(resolve.page_history[-1], "edit")


class RunConnectionTest(unittest.TestCase):
    """``run()`` の Resolve 接続まわり。DaVinciResolveScript をフェイクに差し替える。"""

    def setUp(self):
        self._old_module = sys.modules.get("DaVinciResolveScript")

    def tearDown(self):
        if self._old_module is None:
            sys.modules.pop("DaVinciResolveScript", None)
        else:
            sys.modules["DaVinciResolveScript"] = self._old_module

    def test_console_mode_does_not_touch_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = dump.run(schema_dir=schema_dir, console=True)
            self.assertEqual(code, 0)
            self.assertEqual(buf.getvalue().strip(), dump.console_command(schema_dir / "_dump.py3"))
            self.assertTrue((schema_dir / "_dump.py3").is_file())

    def test_connection_failure_reports_error_and_exits_1(self):
        class FakeModule:
            @staticmethod
            def scriptapp(name):
                return None

        sys.modules["DaVinciResolveScript"] = FakeModule()
        with tempfile.TemporaryDirectory() as td:
            with redirect_stdout(io.StringIO()):
                code = dump.run(schema_dir=Path(td), console=False)
            self.assertEqual(code, 1)

    def test_successful_connection_calls_run_script(self):
        calls = []

        class FakeFusionApp:
            def RunScript(self, path):
                calls.append(path)
                return None

        class FakeResolveApp:
            def Fusion(self):
                return FakeFusionApp()

        class FakeModule:
            @staticmethod
            def scriptapp(name):
                assert name == "Resolve"
                return FakeResolveApp()

        sys.modules["DaVinciResolveScript"] = FakeModule()
        with tempfile.TemporaryDirectory() as td:
            schema_dir = Path(td)
            code = dump.run(schema_dir=schema_dir, console=False)
            self.assertEqual(code, 0)
            self.assertEqual(calls, [str(schema_dir / "_dump.py3")])


if __name__ == "__main__":
    unittest.main()
