"""``resolve/preview.py`` の検査。

Resolve には接続しない範囲で検査する。``resolve_target`` によるフレームと
出力 PNG パスの決定、生成したスクリプトの構造、``resolve/_common.py`` の
関数(``dump.py`` と共有)、そして生成したスクリプトを偽の ``resolve`` /
``bmd`` に対して ``exec`` することで、Fusion 内部で走る手順の順序と
後始末を検査する。この手法は ``test_dump.py`` と同じであり、Resolve の
実機なしに Fusion 内部で走るコードそのものを検査するための唯一の手段である。
Resolve が要る部分(実際の Paste・Render の挙動)は実機で確認する。

``fusion.RunScript`` は内部スクリプトの完了を待てず、``print()`` も CLI へは
出力されない(実測)。そのため内部スクリプトは .setting ごとの結果を JSON の結果
ファイルへ書き、``preview.run`` がそれを読んで標準出力へ書く。この検査では、
内部スクリプトを ``exec`` した後に結果ファイルの中身を読んで検査する。

終了コードは 0 が合格、1 が失敗。
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fusion_macro_cli.resolve import _common, preview  # noqa: E402


# ---------------------------------------------------------------------------
# ``.setting`` のフィクスチャ。
# ---------------------------------------------------------------------------


def _setting_text(tool_name: str, keyframe_block: str = "") -> str:
    """``resolve_target`` が読める最小限の ``.setting`` テキストを作る。

    ``Tools`` に ``MacroOperator`` を1つだけ持つという ``apply`` の契約
    (「apply/組み立ての手順」)を満たす。``keyframe_block`` を渡すと
    ``KeyFrames`` を持つモディファイアを内側へ追加できる。
    """
    # キーは常に ["..."] の括弧文字列形で書く。Fusion 自身も、識別子として
    # 不正な文字(日本語など)を含む名前をこの形で書き出す(実例:
    # ["タイトル_青"] = MacroOperator { ... })。ASCII のみの
    # 名前でもこの形は正しく解析できるため、テストの側は形を使い分けない。
    return (
        "{\n"
        "\tTools = ordered() {\n"
        f'\t\t["{tool_name}"] = MacroOperator {{\n'
        "\t\t\tInputs = ordered() {},\n"
        "\t\t\tOutputs = ordered() {},\n"
        "\t\t\tTools = ordered() {\n"
        "\t\t\t\tInner = Background {\n"
        "\t\t\t\t\tInputs = {},\n"
        "\t\t\t\t},\n"
        f"{keyframe_block}"
        "\t\t\t},\n"
        "\t\t},\n"
        "\t},\n"
        f'\tActiveTool = "{tool_name}",\n'
        "}\n"
    )


_KEYFRAME_BLOCK = (
    "\t\t\t\tMod1 = BezierSpline {\n"
    "\t\t\t\t\tKeyFrames = {\n"
    "\t\t\t\t\t\t[0] = { 0, },\n"
    "\t\t\t\t\t\t[24.4] = { 1, },\n"
    "\t\t\t\t\t},\n"
    "\t\t\t\t},\n"
)


# ---------------------------------------------------------------------------
# ``resolve_target`` の検査。Resolve には触れない、純粋な解析とパス決定。
# ---------------------------------------------------------------------------


class ResolveTargetTest(unittest.TestCase):
    def test_default_frame_is_left_to_the_inner_script(self):
        """``--frame`` 省略時、描くフレームは項目の最終フレームであり、
        項目の長さは Resolve 側にしか無い。この関数は ``None`` を通す。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "タイトル_青.setting"
            path.write_text(_setting_text("タイトル_青", _KEYFRAME_BLOCK), encoding="utf-8")
            target = preview.resolve_target(path, None)
            self.assertEqual(target.tool_name, "タイトル_青")
            self.assertIsNone(target.frame)
            self.assertEqual(target.output_path, path.with_name("タイトル_青.png"))

    def test_keyframes_do_not_affect_the_default_frame(self):
        """キーフレームを持つプリセットと持たないプリセットで、決まる値は変わらない。
        式で駆動されるアニメーションはキーフレームを1つも持たないため、
        キーフレーム時刻から既定フレームを決めない。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "no_keyframes.setting"
            path.write_text(_setting_text("NoKeyframes"), encoding="utf-8")
            target = preview.resolve_target(path, None)
            self.assertIsNone(target.frame)
            self.assertEqual(target.output_path, path.with_name("no_keyframes.png"))

    def test_explicit_frame_is_kept_and_suffixes_filename(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "thumb.setting"
            path.write_text(_setting_text("Thumb", _KEYFRAME_BLOCK), encoding="utf-8")
            target = preview.resolve_target(path, 42)
            self.assertEqual(target.frame, 42)
            self.assertEqual(target.output_path, path.with_name("thumb_f42.png"))

    def test_rejects_setting_without_exactly_one_top_level_tool(self):
        with tempfile.TemporaryDirectory() as td:
            empty_path = Path(td) / "empty.setting"
            empty_path.write_text("{\n\tTools = ordered() {},\n}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                preview.resolve_target(empty_path, None)

            two_path = Path(td) / "two.setting"
            two_path.write_text(
                "{\n\tTools = ordered() {\n"
                "\t\tA = MacroOperator { Inputs = ordered() {}, Outputs = ordered() {}, Tools = ordered() {}, },\n"
                "\t\tB = MacroOperator { Inputs = ordered() {}, Outputs = ordered() {}, Tools = ordered() {}, },\n"
                "\t},\n}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                preview.resolve_target(two_path, None)


# ---------------------------------------------------------------------------
# スクリプト生成そのものの検査。
# ---------------------------------------------------------------------------


class BuildScriptTest(unittest.TestCase):
    def _target(self, name="Thumb", frame=None, output="thumb.png"):
        return preview.PreviewTarget(
            setting_path=Path(f"{name}.setting"),
            tool_name=name,
            frame=frame,
            output_path=Path(output),
        )

    def test_script_compiles(self):
        script = preview.build_script([self._target()], "preview-timeline", Path("result.json"))
        compile(script, "<script>", "exec")

    def test_script_embeds_targets_and_timeline_name(self):
        target = self._target(name="タイトル_青", frame=7, output="タイトル_青_f7.png")
        script = preview.build_script([target], "my-preview-timeline", Path("result.json"))
        self.assertIn("my-preview-timeline", script)
        self.assertIn(str(target.setting_path.resolve()), script)
        self.assertIn(str(target.output_path.resolve()), script)
        self.assertIn("タイトル_青", script)
        self.assertIn("7", script)

    def test_script_embeds_resize_dimensions(self):
        script = preview.build_script([self._target()], "tl", Path("result.json"))
        self.assertIn("RESIZE_WIDTH = 103", script)
        self.assertIn("RESIZE_HEIGHT = 58", script)

    def test_script_embeds_result_path(self):
        script = preview.build_script([self._target()], "tl", Path("/tmp/my_result.json"))
        self.assertIn("/tmp/my_result.json", script)

    def test_script_is_deterministic(self):
        targets = [self._target(name="A"), self._target(name="B", output="b.png")]
        a = preview.build_script(targets, "tl", Path("result.json"))
        b = preview.build_script(targets, "tl", Path("result.json"))
        self.assertEqual(a, b)


class WriteScriptAndConsoleCommandTest(unittest.TestCase):
    def _target(self):
        return preview.PreviewTarget(
            setting_path=Path("thumb.setting"),
            tool_name="Thumb",
            frame=None,
            output_path=Path("thumb.png"),
        )

    def test_write_script_defaults_to_os_temp_dir(self):
        target = self._target()
        result_path = Path(tempfile.gettempdir()) / preview.RESULT_FILENAME
        path = preview.write_script([target], "tl", result_path)
        try:
            self.assertEqual(path.parent, Path(tempfile.gettempdir()))
            self.assertEqual(path.name, "_preview.py3")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                preview.build_script([target], "tl", result_path),
            )
        finally:
            path.unlink(missing_ok=True)

    def test_write_script_honors_explicit_directory(self):
        with tempfile.TemporaryDirectory() as td:
            target = self._target()
            directory = Path(td) / "nested"
            result_path = directory / preview.RESULT_FILENAME
            path = preview.write_script([target], "tl", result_path, directory=directory)
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, directory)

    def test_console_command_is_a_single_exec_line(self):
        with tempfile.TemporaryDirectory() as td:
            script_path = Path(td) / "_preview.py3"
            script_path.write_text("pass", encoding="utf-8")
            line = preview.console_command(script_path)
            self.assertEqual(line.count("\n"), 0)
            self.assertTrue(line.startswith("exec(open("))
            self.assertIn(str(script_path.resolve()), line)


class ScriptFilenameExtensionTest(unittest.TestCase):
    """``_common.write_script`` が ``.py3`` 以外の拡張子を拒むことを検査する。

    ``fusion.RunScript`` は ``.py`` を環境設定次第で何もせずに戻すため、
    ``dump.py`` / ``preview.py`` のどちらも常に ``.py3`` を渡す契約になっている。
    """

    def test_rejects_non_py3_filename(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                _common.write_script(Path(td), "_preview.py", "pass")


# ---------------------------------------------------------------------------
# フェイクの Resolve スクリプティング API。
# ---------------------------------------------------------------------------


class FakeInputPort:
    """Tool の入力ポート(``tool.Input``)。``ConnectTo`` と
    ``GetConnectedOutput`` だけを持つ。"""

    def __init__(self):
        self.connected = None
        self.connect_calls = []

    def ConnectTo(self, output):
        self.connect_calls.append(output)
        self.connected = output

    def GetConnectedOutput(self):
        return self.connected


class FakeDisconnectedInputPort(FakeInputPort):
    """``ConnectTo`` を受けても接続状態を反映しない入力ポート。

    BetterResize と Saver は既知の組み込みツールであり、両者を繋ぐ
    ``saver.Input.ConnectTo(resize.Output)`` 自体は(``resize`` や ``saver``
    の生成が成功している限り)常に構造的に成立してしまう
    (ポートはツールが存在する限り接続の有無に関わらず実在する)。そのため、
    「``saver.Input.GetConnectedOutput()`` が None を返す」という状況を
    実機のどんな条件が引き起こすかはこのテストでは決め打たず、
    シグナルそのものを直接注入して、コードがそれに正しく応答する
    (Render を呼ばず、失敗を記録して次へ進む)ことだけを検査する。
    """

    def ConnectTo(self, output):
        self.connect_calls.append(output)
        # 意図的に connected を更新しない。


class FakeOutputPort:
    """Tool の出力ポート(``tool.Output``)。識別のためだけの軽量オブジェクト。

    ポートはツールが存在する限り常に存在するという Fusion のポートモデルを
    そのまま表す。「上流が無いから Output が None になる」という
    意味論は持ち込まない。
    """

    def __init__(self, owner_name):
        self.owner_name = owner_name


class FakeImage:
    """``output.GetValue(time)`` が返す Image。実機で確認した ``DataWindow`` ``Width`` ``Height`` の属性だけを持つ。"""

    def __init__(self, data_window, width=3840.0, height=2160.0):
        self.DataWindow = data_window
        self.Width = width
        self.Height = height


class FakeImageOutputPort(FakeOutputPort):
    """``GetValue`` で Image を返す出力ポート。PipeRouter の出力に使用する。"""

    def __init__(self, owner_name, image):
        super().__init__(owner_name)
        self.image = image
        self.get_value_calls = []

    def GetValue(self, time):
        self.get_value_calls.append(time)
        return self.image


class FakeTool:
    def __init__(self, name, outputs=None):
        self.name = name
        self.Input = FakeInputPort()
        self.Output = FakeOutputPort(name)
        self.set_inputs = []
        self.deleted = False
        self._outputs = outputs

    def SetInput(self, key, value):
        self.set_inputs.append((key, value))

    def GetOutputList(self):
        if self._outputs is None:
            return {1: self.Output}
        return dict(self._outputs)

    def Delete(self):
        self.deleted = True


class _NamedTool:
    """``comp.GetToolList()`` の要素。名前を読めるだけの軽量フェイク。"""

    def __init__(self, name):
        self._name = name

    def GetAttrs(self):
        return {"TOOLS_Name": self._name}


_MISSING = object()


class FakeComp:
    """``item.GetFusionCompByIndex(1)`` が返す、1つの ``.setting`` に
    対応する挿入済みコンポジション。

    ``paste_results`` は ``Paste`` の返り値を呼び出し順に返す(リスト)。
    ``macro`` は ``Paste`` が成功した後に ``FindTool`` が返すべきツール
    (``None`` なら find_tool の失敗を模す)。``render_result`` が偽なら
    ``Render`` は失敗する。``initial_tool_names`` は Paste 前の
    ``GetToolList()`` が返す名前(既定は挿入直後の空コンポジションと同じ
    ``["MediaOut1"]``。これ以外を渡すと、preview.py が Paste の前に行う
    「Fusion ページが本当に挿入直後の空コンポジションを開いているか」の
    確認を落第させられる)。``ignore_current_time_reset`` は、``Render`` の後の
    ``comp.CurrentTime = 0`` によるリセットが反映されない実機の挙動を注入する。
    """

    def __init__(
        self,
        paste_results,
        macro=None,
        render_result=True,
        saver_input_port=None,
        initial_tool_names=("MediaOut1",),
        media_out=_MISSING,
        media_out_input_port=None,
        ignore_current_time_reset=False,
        image=_MISSING,
    ):
        # PipeRouter の出力の GetValue が返す Image。既定はフレームの一部だけに描画した Image。
        self.image = FakeImage({1: 1516, 2: 23, 3: 2338, 4: 345}) if image is _MISSING else image
        # 実機では comp.Render の後に comp.CurrentTime = 0 でリセットしても、
        # 戻らないことがある(既定フレームを項目の最終フレームにした実行で、
        # 1フレームの断片が生じた)。真にすると代入を無視し、
        # 再生ヘッドが項目の内部に残った状態を再現する。
        self._ignore_current_time_reset = ignore_current_time_reset
        self._paste_results = list(paste_results)
        self.paste_calls = []
        self.macro = macro
        self._render_result = render_result
        self._saver_input_port = saver_input_port
        self.render_calls = []
        # MediaOut1 は挿入直後の空のコンポジションが最初から持つツールであり、
        # BetterResize/Saver と違って comp.AddTool では作らない。既定では
        # FakeTool("MediaOut1") を用意する。media_out=None を渡すと
        # comp.FindTool("MediaOut1") が None を返す(見つからない)状況を模せる。
        self.media_out = FakeTool("MediaOut1") if media_out is _MISSING else media_out
        if media_out_input_port is not None and self.media_out is not None:
            self.media_out.Input = media_out_input_port
        # Lock/Render/Unlock の呼び出し順を記録する。"Render completed!" の
        # モーダルダイアログが Render の前後を comp.Lock()/Unlock() で
        # 挟むことでしか抑制できない(実機で確認済み)ため、呼び出し順そのものを
        # 検査対象にする。
        self.render_lock_order = []
        self.added = []
        self._last_saver = None
        self.find_tool_calls = []
        self._tool_names = list(initial_tool_names)
        # 実機で確認: Render は comp.CurrentTime(Fusion ページ自身のローカル
        # 時間で、タイムラインの再生ヘッド/GetCurrentTimecode とは別の状態)を
        # 描画したフレームへ動かす副作用を持つ。次の項目の挿入は、この値
        # (項目の先頭からの相対位置)が0(項目の先頭)でないと直前の項目を
        # 分断する。FakeTimeline.InsertFusionCompositionIntoTimeline が
        # _bind_timeline で「最後に有効化されたコンポジション」として結び付け、
        # 挿入のたびにこの値を参照する。
        self._current_time = 0
        self._owning_timeline = None
        self._item_start = None

    @property
    def CurrentTime(self):
        return self._current_time

    @CurrentTime.setter
    def CurrentTime(self, value):
        if self._ignore_current_time_reset:
            return
        self._current_time = value
        # 実機では comp.CurrentTime の変更がタイムラインの再生ヘッドにも
        # 及ぶ(項目の先頭 + この値の絶対位置になる)。
        if self._owning_timeline is not None and self._item_start is not None:
            self._owning_timeline._current_frame = self._item_start + value

    def _bind_timeline(self, timeline, item_start):
        self._owning_timeline = timeline
        self._item_start = item_start
        timeline._last_active_comp = self

    def GetToolList(self):
        return {i + 1: _NamedTool(n) for i, n in enumerate(self._tool_names)}

    def Paste(self, data):
        self.paste_calls.append(data)
        result = self._paste_results.pop(0)
        if result:
            # 貼り付け後にマクロ名がツール一覧へ増える。汎用の名前ではなく
            # このcompに紐付くマクロの名前を使うのは、2件目の貼り付けが
            # 誤って1件目のcompへ貼り付けられることを検出するため。誤っていれば
            # 別のcompの`GetToolList()`にこの名前が含まれて検査が失敗する。
            pasted_name = self.macro.name if self.macro is not None else "PastedMacro"
            self._tool_names = self._tool_names + [pasted_name]
        return result

    def FindTool(self, name):
        self.find_tool_calls.append(name)
        if name == "MediaOut1":
            return self.media_out
        if self.macro is not None and name == self.macro.name:
            return self.macro
        return None

    def AddTool(self, tool_id):
        tool = FakeTool(tool_id)
        if tool_id == "Saver":
            if self._saver_input_port is not None:
                tool.Input = self._saver_input_port
            self._last_saver = tool
        if tool_id == "PipeRouter":
            tool.Output = FakeImageOutputPort(tool_id, self.image)
        self.added.append(tool)
        return tool

    def GetAttrs(self):
        return {"COMPN_GlobalStart": 0.0, "COMPN_RenderStart": 0.0}

    def Lock(self):
        self.render_lock_order.append("lock")

    def Unlock(self):
        self.render_lock_order.append("unlock")

    def Render(self, params):
        self.render_lock_order.append("render")
        self.render_calls.append(params)
        if not self._render_result:
            return False
        clip = dict(self._last_saver.set_inputs)["Clip"]
        frame = params["Start"]
        root, ext = os.path.splitext(clip)
        rendered_path = "%s%04d%s" % (root, frame, ext)
        Path(rendered_path).write_bytes(b"FAKE-PNG")
        # 実機で確認した副作用の再現(このクラスの CurrentTime を見よ)。
        # ignore_current_time_reset が真でも、Render 自身による移動は起きる。
        self._current_time = frame
        if self._owning_timeline is not None and self._item_start is not None:
            self._owning_timeline._current_frame = self._item_start + frame
        return True


_FPS = 24
_ITEM_DURATION = 120
# 内部スクリプトが再生ヘッドの移動を試す回数(``_PLAYHEAD_ATTEMPTS``)。
# テンプレート文字列の中の定数であり、モジュールの属性としては参照できない
# ため、テスト側で独立に持つ。注入がこの回数だけ失敗させるために使う。
_PLAYHEAD_ATTEMPTS = 3


def _tc(frame, fps=_FPS, drop_frame=False):
    """テスト側で独立に書いたフレーム→タイムコード変換。

    ``preview.py`` の ``_to_timecode`` と同じ結果になるが、意図的に別実装として
    書く(往復の検査で解析と書き出しを別実装にする、という他モジュールの
    テスト方針に倣う。片方だけの丸め間違いを両側が共有すると検出できない)。
    ドロップフレームは、各分の先頭でタイムコードの番号を飛ばす処理を
    表(``_DROP_FRAME_SKIPS``)で持ち、計算式を共有しない形で書く。
    """
    frame = int(frame)
    if drop_frame:
        dropped = 2 * (fps // 30)
        # 1分ごとに dropped 個の番号を飛ばし、10分ごとの先頭では飛ばさない。
        minute = 0
        remaining = frame
        skipped = 0
        while True:
            length = fps * 60 - (0 if minute % 10 == 0 else dropped)
            if remaining < length:
                break
            remaining -= length
            minute += 1
            if minute % 10 != 0:
                skipped += dropped
        frame += skipped
        separator = ";"
    else:
        separator = ":"
    ff = frame % fps
    total_seconds = frame // fps
    ss = total_seconds % 60
    total_minutes = total_seconds // 60
    mm = total_minutes % 60
    hh = total_minutes // 60
    return "%02d:%02d:%02d%s%02d" % (hh, mm, ss, separator, ff)


class _TimelineItemRecord:
    """挿入された1項目の実体。``FakeItem`` はこれを指すラッパとして
    ``GetItemListInTrack`` の呼び出しごとに新しく作られる。"""

    def __init__(self, unique_id, start, end, comp, name_sticks=True):
        # 実機では挿入した項目の名前は "Fusion Composition" で始まる。
        self.name = "Fusion Composition"
        self.name_sticks = name_sticks
        self.unique_id = unique_id
        self.start = start
        self.end = end
        self.comp = comp


class FakeItem:
    """``name_sticks`` が偽なら ``SetName`` が名前を変えない実機の挙動を模す。

    項目の名前はどの ``.setting`` からでも "Fusion Composition" になる。
    preview はここへマクロ名を入れてタイムラインを確認用にする。
    """

    def __init__(self, record):
        self._record = record

    def SetName(self, name):
        if self._record.name_sticks:
            self._record.name = name
            return True
        return False

    def GetName(self):
        return self._record.name

    def GetUniqueId(self):
        return self._record.unique_id

    def GetStart(self):
        return self._record.start

    def GetEnd(self):
        return self._record.end

    def GetFusionCompByIndex(self, index):
        return self._record.comp


class FakeTimeline:
    """``comps`` はターゲットの順に対応する ``FakeComp`` のリスト。

    実機で確認した次の性質を模す。

    - ``GetItemListInTrack`` は呼ぶたびに新しい ``FakeItem`` ラッパを返す
      (実機の ``PyRemoteObject`` がそうであるように、オブジェクトの ``==``
      に頼った新規項目の判定は常に真になって壊れる。``GetUniqueId()`` だけが
      安定した同定手段になる)
    - ``InsertFusionCompositionIntoTimeline`` の直後、再生ヘッドは自動的に
      新規項目の末尾へ進む
    - ``SetCurrentTimecode`` は、現在ポピュレートされている範囲
      (``0`` 以上、最後の項目の終端未満)の外を指定すると ``False`` を返し、
      再生ヘッドを動かさない

    ``insert_returns_none_at`` / ``new_item_count_at`` は異常系の注入用
    (0始まりの挿入回インデックス)。``fail_move_at`` は、挿入直後の
    ``SetCurrentTimecode``(項目の先頭への移動)を強制的に失敗させる
    ターゲットインデックスの集合。``item_duration`` は挿入する項目の長さ
    (既定 120 フレーム。既定フレームが項目の長さを超える検査で変える)。
    """

    def __init__(
        self,
        name,
        comps,
        insert_returns_none_at=frozenset(),
        new_item_count_at=None,
        fail_move_at=frozenset(),
        item_duration=_ITEM_DURATION,
        fps=_FPS,
        drop_frame=False,
        name_does_not_stick_at=frozenset(),
    ):
        self._name_does_not_stick_at = name_does_not_stick_at
        self._fps = fps
        self._drop_frame = drop_frame
        self._name = name
        self._comps = list(comps)
        self._insert_returns_none_at = insert_returns_none_at
        self._new_item_count_at = new_item_count_at or {}
        self._fail_move_at = fail_move_at
        self._item_duration = item_duration
        self._records = []
        self.insert_calls = 0
        self._post_insert_moves = 0
        self._current_frame = None
        self._next_id = 0
        # comp.CurrentTime(FakeComp を見よ)を持つ、最後に挿入された
        # コンポジション。次の挿入がこの値を参照して分断を再現する。
        self._last_active_comp = None

    def GetName(self):
        return self._name

    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return str(self._fps)
        if key == "timelineDropFrameTimecode":
            return "1" if self._drop_frame else "0"
        raise AssertionError("unexpected timeline setting " + key)

    def InsertFusionCompositionIntoTimeline(self):
        index = self.insert_calls
        self.insert_calls += 1
        self._post_insert_moves = 0
        if index in self._insert_returns_none_at:
            return None
        # 実機で確認した副作用: 再生ヘッドが項目の内部(境界でない位置)に
        # あると、その位置で既存の項目が割れて断片(新しい id を持つ末尾の
        # 切れ端)が生じる。comp.Render が再生ヘッドを描画したフレームへ
        # 動かすため、preview.py は挿入の直前に再生ヘッドを既存の最後の
        # 項目の先頭(境界)へ移動することでこれを避ける。
        abs_pos = self._current_frame
        if abs_pos is not None:
            for record in self._records:
                if record.start < abs_pos < record.end:
                    tail = _TimelineItemRecord(self._next_id, abs_pos, record.end, record.comp)
                    self._next_id += 1
                    record.end = abs_pos
                    self._records.append(tail)
                    break
        count = self._new_item_count_at.get(index, 1)
        for _ in range(count):
            start = len(self._records) * self._item_duration
            end = start + self._item_duration
            comp = self._comps[index] if index < len(self._comps) else FakeComp([True])
            comp._bind_timeline(self, start)
            self._records.append(
                _TimelineItemRecord(
                    self._next_id,
                    start,
                    end,
                    comp,
                    name_sticks=index not in self._name_does_not_stick_at,
                )
            )
            self._next_id += 1
        # 実機の挙動: 挿入の直後、再生ヘッドは新規項目の末尾へ自動的に進む。
        self._current_frame = self._records[-1].end
        return object()

    def GetItemListInTrack(self, track_type, index):
        assert track_type == "video"
        assert index == 1
        return [FakeItem(r) for r in self._records]  # 呼び出しごとに新しいラッパ

    def SetCurrentTimecode(self, tc):
        # preview.py は1件につき、挿入の直前に境界(既存の最後の項目の先頭)へ
        # 移動する呼び出しと、挿入の直後に新しい項目の先頭へ動かす呼び出しを行う。
        # 後者は反映されるまで有限回(_PLAYHEAD_ATTEMPTS)繰り返されるため、
        # ``fail_move_at`` が指す .setting ではその回数だけ失敗させる。以後の
        # 呼び出し(次の .setting の境界への移動)は通常どおり動かす。
        if (
            self.insert_calls - 1 in self._fail_move_at
            and self._post_insert_moves < _PLAYHEAD_ATTEMPTS
        ):
            self._post_insert_moves += 1
            return False
        frame = self._parse_tc(tc)
        max_frame = self._records[-1].end if self._records else 0
        if frame < 0 or frame >= max_frame:
            return False
        self._current_frame = frame
        return True

    def GetCurrentTimecode(self):
        if self._current_frame is None:
            return None
        return _tc(self._current_frame, self._fps, self._drop_frame)

    def _parse_tc(self, tc):
        # 与えられた文字列が自分の書式で書かれた既知のフレームを指すかを、
        # 書式の解析ではなく総当たりの照合で判定する。書式(区切りと番号の
        # 飛び)が違えばどのフレームにも一致せず、-1 を返して範囲外になる。
        last = self._records[-1].end if self._records else 0
        for frame in range(0, last + 1):
            if _tc(frame, self._fps, self._drop_frame) == tc:
                return frame
        return -1


class FakeMediaPool:
    def __init__(self, timeline, create_returns_none=False):
        self._timeline = timeline
        self.create_returns_none = create_returns_none
        self.created_names = []

    def CreateEmptyTimeline(self, name):
        self.created_names.append(name)
        if self.create_returns_none:
            return None
        return self._timeline


class FakeProject:
    def __init__(self, media_pool, name="FakeProject"):
        self._media_pool = media_pool
        self._name = name

    def GetName(self):
        return self._name

    def GetMediaPool(self):
        return self._media_pool


class FakeProjectManager:
    """``load_results`` はプロジェクト名から ``LoadProject`` の戻り値への対応。
    名前が無ければ ``None`` を返す(開けなかった場合の実機の戻り値)。"""

    def __init__(self, project, load_results=None):
        self._project = project
        self._load_results = load_results or {}
        self.load_calls = []

    def GetCurrentProject(self):
        return self._project

    def LoadProject(self, name):
        self.load_calls.append(name)
        return self._load_results.get(name)


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


class FakeBmd:
    def __init__(self):
        self.read_calls = []

    def readfile(self, path):
        self.read_calls.append(path)
        return {"read": path}


# ---------------------------------------------------------------------------
# 検査対象のシナリオを組み立てるヘルパー。
# ---------------------------------------------------------------------------


def _target(tmp_dir, name, frame=None, tool_name=None):
    """``frame=None`` は ``--frame`` を省略した場合(項目の最終フレームを描く)。"""
    return preview.PreviewTarget(
        setting_path=Path(tmp_dir) / f"{name}.setting",
        tool_name=tool_name or name,
        frame=frame,
        output_path=Path(tmp_dir) / f"{name}.png",
    )


def _exec_script(
    targets,
    timeline_name,
    timeline,
    project,
    result_path,
    project_name=None,
    pm=None,
    framing="frame",
    content_margin=preview.DEFAULT_CONTENT_MARGIN,
):
    """内部スクリプトを組み立てて ``exec`` する。

    ``framing`` の既定は、追加するツールが BetterResize と Saver だけの ``frame`` とする。
    ``content`` で追加するツールと2回の Render は :class:`ContentFramingTest` で検査する。

    スクリプトが例外を送出する場合はそのまま呼び出し側へ伝播させる
    (``self.assertRaises`` で受ける)。結果ファイルへの読み書きは、
    実際のアーキテクチャと同じくスクリプト自身がディスクへ行うため、
    ここでは読み戻さない(``_read_result`` を別途呼ぶ)。
    """
    if pm is None:
        pm = FakeProjectManager(project)
    resolve_fake = FakeResolve(pm)
    bmd_fake = FakeBmd()
    script = preview.build_script(
        targets, timeline_name, result_path, project_name, framing, content_margin
    )
    glb = {"resolve": resolve_fake, "bmd": bmd_fake, "__name__": "__preview_script_test__"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        exec(compile(script, "<preview script>", "exec"), glb)
    return resolve_fake, buf.getvalue()


def _script_globals():
    """内部スクリプトを1件の成功シナリオで走らせ、その名前空間を返す。

    ``_to_timecode`` のような補助関数はスクリプトのテンプレート文字列の中に
    あり、``preview`` モジュールの属性としては存在しない(Fusion 内部の
    インタプリタで走るコードであるため)。単体で突き合わせるにはここから取る。
    """
    with tempfile.TemporaryDirectory() as td:
        comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
        timeline = FakeTimeline("tl", [comp])
        project = FakeProject(FakeMediaPool(timeline))
        pm = FakeProjectManager(project)
        glb = {
            "resolve": FakeResolve(pm),
            "bmd": FakeBmd(),
            "__name__": "__preview_script_test__",
        }
        script = preview.build_script(
            [_target(td, "Blue")], "tl", Path(td) / "result.json"
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            exec(compile(script, "<preview script>", "exec"), glb)
        return glb


def _read_result(result_path):
    return json.loads(result_path.read_text(encoding="utf-8"))


def _find_record(data, setting_path):
    for record in data["targets"]:
        if record["setting"] == str(Path(setting_path).resolve()):
            return record
    raise AssertionError(f"no result record for {setting_path}")


class InternalScriptHappyPathTest(unittest.TestCase):
    def test_two_settings_are_pasted_rendered_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=5)]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            resolve_fake, _output = _exec_script(
                targets, "preview-timeline", timeline, project, result_path
            )
            data = _read_result(result_path)

            self.assertEqual(media_pool.created_names, ["preview-timeline"])
            self.assertEqual(resolve_fake.page_history[:2], ["edit", "fusion"])
            self.assertEqual(resolve_fake.page_history[-1], "edit")
            self.assertTrue(data["complete"])
            self.assertIsNone(data["error"])

            for target, comp in ((targets[0], comp_blue), (targets[1], comp_red)):
                self.assertTrue(target.output_path.is_file(), f"{target.output_path} was not written")
                record = _find_record(data, target.setting_path)
                self.assertEqual(record["status"], "ok")
                self.assertIsNone(record["reason"])
                self.assertEqual(record["frame_requested"], record["frame_used"])
                self.assertEqual(record["output"], str(target.output_path.resolve()))
                # Render は comp.Lock()/Unlock() で挟まれる(順に1回ずつ)。
                self.assertEqual(comp.render_lock_order, ["lock", "render", "unlock"])
                # BetterResize と Saver は名指しで削除され、2個だけ追加されている。
                self.assertEqual(len(comp.added), 2)
                for tool in comp.added:
                    self.assertTrue(tool.deleted, f"{tool.name} was not deleted")
                resize, saver = comp.added
                self.assertEqual(resize.name, "BetterResize")
                self.assertEqual(saver.name, "Saver")
                self.assertEqual(dict(resize.set_inputs)["Width"], 103)
                self.assertEqual(dict(resize.set_inputs)["Height"], 58)
                # Saver には最終的な名前ではなく、末尾が数字で終わらない固定名
                # (render.png)を渡す(Saver は末尾の数字を取り除いて連番を
                # 付けるため)。最終的な名前への移動は Render の後で行う。
                saver_clip = dict(saver.set_inputs)["Clip"]
                self.assertEqual(os.path.basename(saver_clip), "render.png")
                self.assertNotEqual(saver_clip, str(target.output_path.resolve()))
                # macro の出力 -> resize -> saver の順に接続されている。
                self.assertIs(resize.Input.connected, comp.macro.Output)
                self.assertIs(saver.Input.connected, resize.Output)
                # 2件目が誤って1件目のcompへ貼り付けられる不具合(実機で発生した
                # ものと同じ形)が起きていないことを、compごとのツール集合が
                # 「MediaOut1 + そのマクロ自身」ちょうど2件であることで確認する。
                self.assertEqual(
                    {t.GetAttrs()["TOOLS_Name"] for t in comp.GetToolList().values()},
                    {"MediaOut1", comp.macro.name},
                )
                # Saver の一時ディレクトリは後始末で消えている。
                self.assertFalse(Path(saver_clip).parent.exists())
                # タイムラインに残る項目が絵を持つ条件: MediaOut1 の入力へ
                # マクロの出力が接続されている。BetterResize/Saver を削除した
                # 後に接続するため、削除済みであることも併せて確認する。
                self.assertTrue(resize.deleted)
                self.assertTrue(saver.deleted)
                self.assertIs(comp.media_out.Input.connected, comp.macro.Output)

    def test_first_paste_false_is_retried_once(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0)]
            comp = FakeComp(paste_results=[False, True], macro=FakeTool("Blue"))
            timeline = FakeTimeline("preview-timeline", [comp])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)

            self.assertEqual(len(comp.paste_calls), 2)
            self.assertTrue(targets[0].output_path.is_file())
            data = _read_result(result_path)
            self.assertEqual(_find_record(data, targets[0].setting_path)["status"], "ok")

    def test_first_paste_fails_twice_is_recorded_and_does_not_stop(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(paste_results=[False, False], macro=FakeTool("Blue"))
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)
            data = _read_result(result_path)

            self.assertEqual(len(comp_blue.paste_calls), 2)
            self.assertFalse(targets[0].output_path.exists())
            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "paste")
            # 1件目が失敗しても2件目は処理され続ける(ループにせず次へ進む)。
            self.assertTrue(targets[1].output_path.is_file())
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")
            # 失敗した1件目には BetterResize/Saver が追加されていない
            # (comp.FindTool より前で continue するため)。
            self.assertEqual(comp_blue.added, [])
            self.assertTrue(data["complete"])

    def test_find_tool_returning_none_is_recorded_and_does_not_stop(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(paste_results=[True], macro=None)
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)
            data = _read_result(result_path)

            self.assertFalse(targets[0].output_path.exists())
            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "find_tool")
            self.assertTrue(targets[1].output_path.is_file())

    def test_disconnected_saver_input_skips_render_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(
                paste_results=[True],
                macro=FakeTool("Blue"),
                saver_input_port=FakeDisconnectedInputPort(),
            )
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)
            data = _read_result(result_path)

            self.assertFalse(targets[0].output_path.exists())
            self.assertEqual(comp_blue.render_calls, [], "Render must not be called when disconnected")
            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "not_connected")
            self.assertEqual(len(comp_blue.added), 2)
            for tool in comp_blue.added:
                self.assertTrue(tool.deleted, "resize/saver must still be cleaned up on skip")
            self.assertTrue(targets[1].output_path.is_file())

    def test_render_failure_is_recorded_and_does_not_stop(self):
        """defect 1: Render の失敗はこの .setting だけの不具合であり得るため、
        記録して次の .setting へ進む(以前は例外を送出して全体を止めていた)。"""
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=3), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"), render_result=False)
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)
            data = _read_result(result_path)

            # Render が失敗して continue する前に、comp.Unlock() は必ず呼ばれる
            # (finally で Lock/Unlock を閉じるため)。
            self.assertEqual(comp_blue.render_lock_order, ["lock", "render", "unlock"])
            # Render が失敗しても、追加した BetterResize/Saver は削除される。
            self.assertEqual(len(comp_blue.added), 2)
            for tool in comp_blue.added:
                self.assertTrue(tool.deleted)
            self.assertFalse(targets[0].output_path.exists())
            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "render")
            # 1件目が失敗しても2件目は処理され続ける。
            self.assertTrue(targets[1].output_path.is_file())
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")
            self.assertTrue(data["complete"])
            self.assertIsNone(data["error"])

    def test_media_out_not_found_is_recorded_and_does_not_stop(self):
        """defect 1: MediaOut1 が見つからない場合、PNG は書けていても
        タイムラインの項目は絵を持てない。失敗として記録して次へ進む。"""
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"), media_out=None)
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)
            data = _read_result(result_path)

            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "media_out_not_found")
            # BetterResize/Saver はすでに削除されている(MediaOut1 の接続は
            # その後で行うため)。
            self.assertEqual(len(comp_blue.added), 2)
            for tool in comp_blue.added:
                self.assertTrue(tool.deleted)
            self.assertTrue(targets[1].output_path.is_file())
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")
            self.assertTrue(data["complete"])

    def test_media_out_connection_not_reflected_is_recorded_and_does_not_stop(self):
        """defect 1: ConnectTo(None) は例外を投げずに何もしないため、
        接続後に GetConnectedOutput() が None のままであれば失敗として記録する
        (沈黙して進めない)。"""
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(
                paste_results=[True],
                macro=FakeTool("Blue"),
                media_out_input_port=FakeDisconnectedInputPort(),
            )
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("preview-timeline", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "preview-timeline", timeline, project, result_path)
            data = _read_result(result_path)

            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "media_out_not_connected")
            # ConnectTo は呼ばれている(記録されているのは反映されなかったこと)。
            self.assertEqual(comp_blue.media_out.Input.connect_calls, [comp_blue.macro.Output])
            self.assertTrue(targets[1].output_path.is_file())
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")
            self.assertTrue(data["complete"])

    def test_playhead_move_failure_is_recorded_and_does_not_stop(self):
        """defect 1(実機で観測): 挿入した項目の先頭へ再生ヘッドを動かせない
        場合、以前は例外を送出して以降の .setting が未実行になっていた。
        記録して次の .setting へ進む。"""
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0), _target(td, "Red", frame=0)]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("tl", [comp_blue, comp_red], fail_move_at={0})
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            self.assertEqual(comp_blue.paste_calls, [], "must not paste before the page is focused")
            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "playhead")
            # 1件目が失敗しても2件目は処理され続ける。
            self.assertTrue(targets[1].output_path.is_file())
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")
            self.assertTrue(data["complete"])


class FrameRangeCorrectionTest(unittest.TestCase):
    """defect 3: 既定フレームが項目の最終フレーム(``item.GetEnd() -
    item.GetStart() - 1``)になること、および ``--frame`` 明示時に
    範囲外なら補正せず失敗として記録する規則。"""

    def test_default_frame_is_the_last_frame_of_the_item(self):
        with tempfile.TemporaryDirectory() as td:
            # 既定の項目長は120フレーム、最終フレームは119。
            targets = [_target(td, "Blue")]
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            timeline = FakeTimeline("tl", [comp])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            record = _find_record(data, targets[0].setting_path)
            self.assertEqual(record["status"], "ok")
            self.assertIsNone(record["frame_requested"])
            self.assertEqual(record["frame_used"], 119)
            self.assertEqual(comp.render_calls, [{"Start": 119, "End": 119, "Wait": True}])
            self.assertTrue(targets[0].output_path.is_file())

    def test_explicit_frame_out_of_range_is_recorded_without_correction(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [
                _target(td, "Blue", frame=128),
                _target(td, "Red"),
            ]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("tl", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            blue_record = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue_record["status"], "failed")
            self.assertEqual(blue_record["reason"], "frame_out_of_range")
            # 補正しない: frame_used は要求どおりのまま。
            self.assertEqual(blue_record["frame_requested"], 128)
            self.assertEqual(blue_record["frame_used"], 128)
            self.assertFalse(targets[0].output_path.exists())
            # Paste すら試みない(範囲外と分かった時点で continue する)。
            self.assertEqual(comp_blue.paste_calls, [])
            self.assertEqual(comp_blue.render_calls, [])
            # 1件目が失敗しても2件目は処理され続ける。
            self.assertTrue(targets[1].output_path.is_file())
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")


class PlayheadBoundaryBeforeInsertTest(unittest.TestCase):
    """挿入の直前に再生ヘッドを境界へ移動することで、``comp.CurrentTime = 0``
    によるリセットが反映されない場合でも項目が分断されないこと。

    実機で見つかった欠陥: 既定フレームを項目の最終フレームにすると、
    ``Render`` の後の再生ヘッドは項目の末尾から1フレーム内側に残る。
    リセットが反映されない場合があり、次の挿入がその位置で直前の項目を
    割って1フレームの断片を作り、新規項目が2件になって実行全体が止まった。
    """

    def test_ineffective_current_time_reset_does_not_split_the_previous_item(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue"), _target(td, "Red")]
            # 1件目はリセットが反映されない(再生ヘッドが項目の内部に残る)。
            comp_blue = FakeComp(
                paste_results=[True],
                macro=FakeTool("Blue"),
                ignore_current_time_reset=True,
            )
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("tl", [comp_blue, comp_red])
            project = FakeProject(FakeMediaPool(timeline))
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            for target in targets:
                record = _find_record(data, target.setting_path)
                self.assertEqual(record["status"], "ok", record)
            # 分断された断片が無く、項目は2件のまま長さも揃っている。
            items = timeline.GetItemListInTrack("video", 1)
            self.assertEqual(len(items), 2)
            self.assertEqual(
                [it.GetEnd() - it.GetStart() for it in items],
                [_ITEM_DURATION, _ITEM_DURATION],
            )

    def test_boundary_move_failure_stops_instead_of_splitting(self):
        """境界へ置けない場合は、分断される前に止まる。"""
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue"), _target(td, "Red")]
            # 1件目はリセットが反映されず、再生ヘッドが項目の内部に残る。
            comp_blue = FakeComp(
                paste_results=[True],
                macro=FakeTool("Blue"),
                ignore_current_time_reset=True,
            )
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("tl", [comp_blue, comp_red])
            # 2件目の挿入の直前、境界への移動が反映されない状態を作る。
            original = timeline.SetCurrentTimecode
            state = {"calls": 0}

            def failing(tc):
                state["calls"] += 1
                if state["calls"] <= 1:
                    return original(tc)
                return False

            timeline.SetCurrentTimecode = failing
            project = FakeProject(FakeMediaPool(timeline))
            result_path = Path(td) / "result.json"

            with self.assertRaises(RuntimeError):
                _exec_script(targets, "tl", timeline, project, result_path)


class TimelineItemNameTest(unittest.TestCase):
    """残すタイムラインの項目に、そのプリセットの名前が付くこと。

    挿入した項目の名前はどの ``.setting`` からでも "Fusion Composition"
    になり、複数を並べても見分けが付かない。タイムラインは人間が確認するためのもの
    として残すものであるため、マクロ名を項目の名前にする。
    """

    def test_item_is_named_after_the_macro(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue"), _target(td, "Red")]
            comps = [
                FakeComp(paste_results=[True], macro=FakeTool("Blue")),
                FakeComp(paste_results=[True], macro=FakeTool("Red")),
            ]
            timeline = FakeTimeline("tl", comps)
            project = FakeProject(FakeMediaPool(timeline))
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)

            names = [it.GetName() for it in timeline.GetItemListInTrack("video", 1)]
            self.assertEqual(names, ["Blue", "Red"])

    def test_name_that_does_not_stick_is_recorded_and_does_not_stop(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue"), _target(td, "Red")]
            comps = [
                FakeComp(paste_results=[True], macro=FakeTool("Blue")),
                FakeComp(paste_results=[True], macro=FakeTool("Red")),
            ]
            timeline = FakeTimeline("tl", comps, name_does_not_stick_at={0})
            project = FakeProject(FakeMediaPool(timeline))
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            blue = _find_record(data, targets[0].setting_path)
            self.assertEqual(blue["status"], "failed")
            self.assertEqual(blue["reason"], "set_name")
            # PNG は書けている(名前は描画の後の工程であるため)。
            self.assertTrue(targets[0].output_path.is_file())
            # 2件目は続けて処理される。
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")


class DropFrameTimecodeTest(unittest.TestCase):
    """ドロップフレーム(29.97 / 59.94 系)のタイムラインで再生ヘッドを
    項目の先頭へ動かせること。

    実機で見つかった欠陥: ノンドロップフレームの単純な変換で組み立てた
    文字列は ``timeline.GetCurrentTimecode()`` の戻り値と一致せず、
    移動の確認が常に失敗して全ての .setting が ``playhead`` で失敗する。
    """

    def test_drop_frame_timeline_moves_the_playhead(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue"), _target(td, "Red")]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("tl", [comp_blue, comp_red], fps=30, drop_frame=True)
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            for target in targets:
                record = _find_record(data, target.setting_path)
                self.assertEqual(record["status"], "ok", record)
                self.assertTrue(target.output_path.is_file())

    def test_drop_frame_numbers_skip_at_each_minute(self):
        """``_to_timecode`` のドロップフレーム変換そのものを、テスト側の
        独立実装(``_tc``)と突き合わせる。"""
        to_timecode = _script_globals()["_to_timecode"]
        for frame in (0, 1, 1799, 1800, 1801, 3600, 17981, 17982, 108000):
            with self.subTest(frame=frame):
                self.assertEqual(to_timecode(frame, 30, True), _tc(frame, 30, True))
                self.assertEqual(to_timecode(frame, 30, False), _tc(frame, 30, False))

    def test_drop_frame_separator_is_a_semicolon_before_the_frame(self):
        to_timecode = _script_globals()["_to_timecode"]
        self.assertEqual(to_timecode(1800, 30, True), "00:01:00;02")
        self.assertEqual(to_timecode(1800, 30, False), "00:01:00:00")


class ProjectSelectionTest(unittest.TestCase):
    """``--project`` を指定した場合に ``LoadProject`` で開くこと、および
    使ったプロジェクト名を結果ファイルへ残すこと。"""

    def _scene(self, td):
        comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
        timeline = FakeTimeline("tl", [comp])
        media_pool = FakeMediaPool(timeline)
        return timeline, FakeProject(media_pool, name="opened project")

    def test_current_project_is_used_and_recorded_without_the_option(self):
        with tempfile.TemporaryDirectory() as td:
            timeline, project = self._scene(td)
            pm = FakeProjectManager(project)
            result_path = Path(td) / "result.json"

            _exec_script(
                [_target(td, "Blue")], "tl", timeline, project, result_path, pm=pm
            )

            self.assertEqual(pm.load_calls, [])
            self.assertEqual(_read_result(result_path)["project"], "opened project")

    def test_already_open_project_is_not_reloaded(self):
        """開いているプロジェクトを指定した場合、LoadProject を呼ばない。

        開き直しは、未保存の変更があると保存を尋ねるモーダルダイアログを開き、
        以後の呼び出しが返らなくなる(実機で確認)。
        """
        with tempfile.TemporaryDirectory() as td:
            timeline, project = self._scene(td)
            pm = FakeProjectManager(project)
            result_path = Path(td) / "result.json"

            _exec_script(
                [_target(td, "Blue")],
                "tl",
                timeline,
                project,
                result_path,
                project_name="opened project",
                pm=pm,
            )

            self.assertEqual(pm.load_calls, [])
            self.assertEqual(_read_result(result_path)["project"], "opened project")

    def test_named_project_is_loaded_before_creating_the_timeline(self):
        with tempfile.TemporaryDirectory() as td:
            timeline, project = self._scene(td)
            pm = FakeProjectManager(project, load_results={"other": project})
            result_path = Path(td) / "result.json"

            _exec_script(
                [_target(td, "Blue")],
                "tl",
                timeline,
                project,
                result_path,
                project_name="other",
                pm=pm,
            )

            self.assertEqual(pm.load_calls, ["other"])
            self.assertEqual(_read_result(result_path)["project"], "opened project")

    def test_load_project_returning_none_raises(self):
        with tempfile.TemporaryDirectory() as td:
            timeline, project = self._scene(td)
            pm = FakeProjectManager(project)  # どの名前も開けない
            result_path = Path(td) / "result.json"

            with self.assertRaises(RuntimeError):
                _exec_script(
                    [_target(td, "Blue")],
                    "tl",
                    timeline,
                    project,
                    result_path,
                    project_name="missing",
                    pm=pm,
                )


class RenderPlayheadSideEffectTest(unittest.TestCase):
    """Render による再生ヘッドの移動が、次の挿入を分断しないこと。

    ``comp.Render({"Start": F, "End": F})`` はコンポジションの時間だけでなく
    タイムライン自身の再生ヘッドも、描画したフレーム F に対応する絶対位置
    (項目の先頭 + F)へ動かす副作用を持つ(実機で確認)。F が項目の先頭(0)
    以外だと再生ヘッドは項目の内部(境界でない位置)に残り、その状態で次の
    .setting を挿入すると直前の項目を分断してしまい、
    ``InsertFusionCompositionIntoTimeline`` の直後に得られる新規項目が1件で
    なくなる(既定フレームの補正により0以外のフレームを描画する処理が実際に
    使われて初めて顕在化した)。preview.py は Render の直後に再生ヘッドを
    項目の先頭へ明示的に戻すことでこれを避ける。
    """

    def test_nonzero_render_frame_does_not_corrupt_next_insertion(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [
                _target(td, "Blue", frame=119),
                _target(td, "Red"),
            ]
            comp_blue = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            comp_red = FakeComp(paste_results=[True], macro=FakeTool("Red"))
            timeline = FakeTimeline("tl", [comp_blue, comp_red])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            result_path = Path(td) / "result.json"

            _exec_script(targets, "tl", timeline, project, result_path)
            data = _read_result(result_path)

            self.assertEqual(timeline.insert_calls, 2)
            self.assertIsNone(data["error"])
            self.assertEqual(_find_record(data, targets[0].setting_path)["status"], "ok")
            self.assertEqual(_find_record(data, targets[1].setting_path)["status"], "ok")
            self.assertTrue(targets[0].output_path.is_file())
            self.assertTrue(targets[1].output_path.is_file())


class InternalScriptStopRuleTest(unittest.TestCase):
    """停止規則: プロジェクト未オープン・タイムライン/コンポジションの取得
    失敗など、以後の全ての .setting の処理が信頼できなくなる4条件に限り、
    再試行せず例外を送出して止まる。このとき結果ファイルにも ``error`` が
    記録される(CLI 側がこれを読んで1行のエラーを報告できるようにするため)。
    """

    def test_no_project_open_raises(self):
        with tempfile.TemporaryDirectory() as td:
            pm = FakeProjectManager(None)
            resolve_fake = FakeResolve(pm)
            result_path = Path(td) / "result.json"
            script = preview.build_script([], "tl", result_path)
            with self.assertRaises(RuntimeError):
                with redirect_stdout(io.StringIO()):
                    exec(compile(script, "<preview script>", "exec"), {"resolve": resolve_fake, "bmd": FakeBmd()})
            data = _read_result(result_path)
            self.assertTrue(data["complete"])
            self.assertIn("no project is open", data["error"])

    def test_create_empty_timeline_returns_none_raises(self):
        with tempfile.TemporaryDirectory() as td:
            timeline = FakeTimeline("tl", [])
            media_pool = FakeMediaPool(timeline, create_returns_none=True)
            project = FakeProject(media_pool)
            pm = FakeProjectManager(project)
            resolve_fake = FakeResolve(pm)
            result_path = Path(td) / "result.json"
            script = preview.build_script([], "tl", result_path)
            with self.assertRaises(RuntimeError):
                with redirect_stdout(io.StringIO()):
                    exec(compile(script, "<preview script>", "exec"), {"resolve": resolve_fake, "bmd": FakeBmd()})
            data = _read_result(result_path)
            self.assertTrue(data["complete"])
            self.assertIn("CreateEmptyTimeline returned None", data["error"])

    def test_insert_fusion_composition_returns_none_raises(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0)]
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            timeline = FakeTimeline("tl", [comp], insert_returns_none_at={0})
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            pm = FakeProjectManager(project)
            resolve_fake = FakeResolve(pm)
            result_path = Path(td) / "result.json"
            script = preview.build_script(targets, "tl", result_path)
            with self.assertRaises(RuntimeError):
                with redirect_stdout(io.StringIO()):
                    exec(
                        compile(script, "<preview script>", "exec"),
                        {"resolve": resolve_fake, "bmd": FakeBmd()},
                    )
            data = _read_result(result_path)
            self.assertTrue(data["complete"])
            self.assertIn("InsertFusionCompositionIntoTimeline returned None", data["error"])

    def test_unexpected_new_item_count_raises(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0)]
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"))
            timeline = FakeTimeline("tl", [comp], new_item_count_at={0: 2})
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            pm = FakeProjectManager(project)
            resolve_fake = FakeResolve(pm)
            result_path = Path(td) / "result.json"
            script = preview.build_script(targets, "tl", result_path)
            with self.assertRaises(RuntimeError):
                with redirect_stdout(io.StringIO()):
                    exec(
                        compile(script, "<preview script>", "exec"),
                        {"resolve": resolve_fake, "bmd": FakeBmd()},
                    )
            data = _read_result(result_path)
            self.assertTrue(data["complete"])

    def test_unexpected_tools_before_paste_raises(self):
        """停止条件: Fusion ページが挿入直後の空の
        コンポジション(``MediaOut1`` のみ)以外を開いていたら、狙った項目に
        たどり着けていないため、Paste を試みずに止まる。"""
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue", frame=0)]
            comp = FakeComp(
                paste_results=[True],
                macro=FakeTool("Blue"),
                initial_tool_names=("MediaOut1", "LeftoverTool"),
            )
            timeline = FakeTimeline("tl", [comp])
            media_pool = FakeMediaPool(timeline)
            project = FakeProject(media_pool)
            pm = FakeProjectManager(project)
            resolve_fake = FakeResolve(pm)
            result_path = Path(td) / "result.json"
            script = preview.build_script(targets, "tl", result_path)
            with self.assertRaises(RuntimeError) as ctx:
                with redirect_stdout(io.StringIO()):
                    exec(
                        compile(script, "<preview script>", "exec"),
                        {"resolve": resolve_fake, "bmd": FakeBmd()},
                    )
            self.assertIn("LeftoverTool", str(ctx.exception))
            self.assertEqual(comp.paste_calls, [])
            data = _read_result(result_path)
            self.assertTrue(data["complete"])
            self.assertIn("LeftoverTool", data["error"])


# ---------------------------------------------------------------------------
# 結果ファイルの待ち受けと報告(``preview._wait_for_result`` / ``_report``)。
# CLI プロセス自身の標準出力・終了コードを検査する。
# ---------------------------------------------------------------------------


class WaitForResultTest(unittest.TestCase):
    def test_all_ok_prints_writes_and_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "setting": "a.setting",
                                "output": "a.png",
                                "frame_requested": 24,
                                "frame_used": 24,
                                "status": "ok",
                                "reason": None,
                            }
                        ],
                        "complete": True,
                        "error": None,
                    }
                ),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = preview._wait_for_result(result_path, 1, timeout=1.0, poll_interval=0.01)
            self.assertEqual(code, 0)
            self.assertIn("wrote a.png", buf.getvalue())

    def test_project_name_is_printed(self):
        """preview はタイムラインをプロジェクトへ作り足すため、どの
        プロジェクトへ作ったかを表示する(内部スクリプトの print() は
        出力されないため、結果ファイル経由でここに出る)。"""
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "setting": "a.setting",
                                "output": "a.png",
                                "frame_requested": None,
                                "frame_used": 119,
                                "status": "ok",
                                "reason": None,
                            }
                        ],
                        "complete": True,
                        "error": None,
                        "project": "some project",
                    }
                ),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = preview._wait_for_result(result_path, 1, timeout=1.0, poll_interval=0.01)
            self.assertEqual(code, 0)
            self.assertIn("using project some project", buf.getvalue())

    def test_failed_target_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "setting": "a.setting",
                                "output": "a.png",
                                "frame_requested": 0,
                                "frame_used": 0,
                                "status": "failed",
                                "reason": "paste",
                            }
                        ],
                        "complete": True,
                        "error": None,
                    }
                ),
                encoding="utf-8",
            )
            buf_out, buf_err = io.StringIO(), io.StringIO()
            from contextlib import redirect_stderr

            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                code = preview._wait_for_result(result_path, 1, timeout=1.0, poll_interval=0.01)
            self.assertEqual(code, 1)
            self.assertIn("a.setting", buf_out.getvalue())
            self.assertIn("paste", buf_out.getvalue())
            self.assertIn("failure", buf_err.getvalue())

    def test_hard_stop_error_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "result.json"
            result_path.write_text(
                json.dumps({"targets": [], "complete": True, "error": "RuntimeError: boom"}),
                encoding="utf-8",
            )
            from contextlib import redirect_stderr

            buf_err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
                code = preview._wait_for_result(result_path, 1, timeout=1.0, poll_interval=0.01)
            self.assertEqual(code, 1)
            self.assertIn("boom", buf_err.getvalue())

    def test_missing_result_file_times_out_and_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "does_not_exist.json"
            from contextlib import redirect_stderr

            buf_err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
                code = preview._wait_for_result(result_path, 1, timeout=0.03, poll_interval=0.01)
            self.assertEqual(code, 1)
            self.assertIn("timed out", buf_err.getvalue())

    def test_incomplete_result_file_shows_partial_content_and_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "setting": "a.setting",
                                "output": "a.png",
                                "frame_requested": 0,
                                "frame_used": 0,
                                "status": "ok",
                                "reason": None,
                            }
                        ],
                        "complete": False,
                        "error": None,
                    }
                ),
                encoding="utf-8",
            )
            from contextlib import redirect_stderr

            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                code = preview._wait_for_result(result_path, 1, timeout=0.03, poll_interval=0.01)
            self.assertEqual(code, 1)
            self.assertIn("wrote a.png", buf_out.getvalue())
            self.assertIn("timed out", buf_err.getvalue())


# ---------------------------------------------------------------------------
# ``run()`` の Resolve 接続まわり。DaVinciResolveScript をフェイクに差し替える。
# ---------------------------------------------------------------------------


class RunConnectionTest(unittest.TestCase):
    def setUp(self):
        self._old_module = sys.modules.get("DaVinciResolveScript")

    def tearDown(self):
        if self._old_module is None:
            sys.modules.pop("DaVinciResolveScript", None)
        else:
            sys.modules["DaVinciResolveScript"] = self._old_module

    def _setting_path(self, td):
        path = Path(td) / "thumb.setting"
        path.write_text(_setting_text("Thumb"), encoding="utf-8")
        return path

    def test_console_mode_does_not_touch_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            setting_path = self._setting_path(td)
            script_dir = Path(td) / "scripts"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = preview.run(
                    [setting_path],
                    frame=None,
                    timeline_name="preview-timeline",
                    console=True,
                    directory=script_dir,
                )
            self.assertEqual(code, 0)
            script_path = script_dir / "_preview.py3"
            self.assertTrue(script_path.is_file())
            lines = buf.getvalue().splitlines()
            self.assertEqual(lines[0], preview.console_command(script_path))
            result_path = script_dir / preview.RESULT_FILENAME
            self.assertIn(str(result_path), lines[1])

    def test_connection_failure_reports_error_and_exits_1(self):
        class FakeModule:
            @staticmethod
            def scriptapp(name):
                return None

        sys.modules["DaVinciResolveScript"] = FakeModule()
        with tempfile.TemporaryDirectory() as td:
            setting_path = self._setting_path(td)
            with redirect_stdout(io.StringIO()):
                code = preview.run(
                    [setting_path],
                    frame=None,
                    timeline_name="preview-timeline",
                    console=False,
                    directory=Path(td) / "scripts",
                )
            self.assertEqual(code, 1)

    def test_successful_connection_calls_run_script(self):
        calls = []

        class FakeFusionApp:
            def RunScript(self, path):
                calls.append(path)
                # 実機では fusion.RunScript は起動するだけで戻り、内部
                # スクリプトが後から結果ファイルを書く(実測。preview.run
                # docstring を見よ)。ここでは代わりに即座に完了済みの結果を
                # 書き、preview.run のポーリングが1回目の読み取りで
                # 見つけられることを確かめる。
                result_path = Path(path).parent / preview.RESULT_FILENAME
                result_path.write_text(
                    json.dumps({"targets": [], "complete": True, "error": None}),
                    encoding="utf-8",
                )
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
            setting_path = self._setting_path(td)
            script_dir = Path(td) / "scripts"
            code = preview.run(
                [setting_path],
                frame=None,
                timeline_name="preview-timeline",
                console=False,
                directory=script_dir,
            )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [str(script_dir / "_preview.py3")])

    def test_stale_result_file_from_a_previous_run_is_not_mistaken_for_completion(self):
        """実機で見つかった不具合。

        ``fusion.RunScript`` は起動するだけで戻るため、``run()`` が起動直後に
        結果ファイルを読むと、新しいスクリプトがまだ書き始めていない場合に
        前回の実行が残した「完了済み」の結果ファイルをそのまま読んでしまい、
        実際にはまだ実行中の今回の処理を「即座に完了した」と誤認する
        ``run()`` は起動前に既存の結果ファイルを削除することでこれを防ぐ。
        """

        class FakeFusionApp:
            def RunScript(self, path):
                # 実機の RunScript を模す: 起動するだけで、結果ファイルへは
                # 一切書かない(新しいスクリプトがまだ何も書いていない状態)。
                return None

        class FakeResolveApp:
            def Fusion(self):
                return FakeFusionApp()

        class FakeModule:
            @staticmethod
            def scriptapp(name):
                return FakeResolveApp()

        sys.modules["DaVinciResolveScript"] = FakeModule()
        with tempfile.TemporaryDirectory() as td:
            setting_path = self._setting_path(td)
            script_dir = Path(td) / "scripts"
            script_dir.mkdir()
            stale_result_path = script_dir / preview.RESULT_FILENAME
            stale_result_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "setting": "stale.setting",
                                "output": "stale.png",
                                "frame_requested": 0,
                                "frame_used": 0,
                                "status": "ok",
                                "reason": None,
                            }
                        ],
                        "complete": True,
                        "error": None,
                    }
                ),
                encoding="utf-8",
            )
            from contextlib import redirect_stderr

            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                code = preview.run(
                    [setting_path],
                    frame=None,
                    timeline_name="preview-timeline",
                    console=False,
                    directory=script_dir,
                    timeout=0.03,
                )
            # 古い結果を「今回の成功」として報告してはならない。
            self.assertNotIn("stale.setting", buf_out.getvalue())
            self.assertEqual(code, 1)
            self.assertIn("timed out", buf_err.getvalue())


class _FakeExpressionPort:
    def __init__(self, tool, name):
        self._tool = tool
        self._name = name

    def SetExpression(self, expression):
        self._tool.expression_history.append((self._name, expression))
        self._tool.expressions[self._name] = expression


class FakeExpressionTool(FakeTool):
    """式を差し替えると ``values`` に登録した評価値を返すツール。"""

    def __init__(self, name, expressions, values):
        super().__init__(name)
        self.expressions = dict(expressions)
        self.values = values
        self.expression_history = []

    def __getattr__(self, input_name):
        return _FakeExpressionPort(self, input_name)

    def GetInput(self, input_name, frame):
        return self.values[self.expressions[input_name]]


class ExpressionCheckTest(unittest.TestCase):
    _SETTING = """{
	Tools = ordered() {
		M = MacroOperator {
			Inputs = ordered() {},
			Outputs = { MainOutput1 = InstanceOutput { SourceOp = "A", Source = "Output" } },
			Tools = ordered() {
				A = TextPlus {
					Inputs = {
						Center = Input { Expression = "Point(Gap + 0.1, time)" },
						Size = Input { Expression = "0.1" },
						Gap = Input { Value = 0 }
					},
					UserControls = ordered() { Gap = { LINKID_DataType = "Number", INPID_InputControl = "SliderControl" } }
				}
			}
		}
	},
	ActiveTool = "M"
}
"""

    def test_resolve_target_pairs_expressions_with_user_control_names_qualified(self):
        """``UserControls`` の名前だけを修飾し、組み込みの ``time`` と定数式は対象外とする。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "M.setting"
            path.write_text(self._SETTING, encoding="utf-8")
            target = preview.resolve_target(path, None)
        self.assertEqual(
            target.expression_checks,
            (preview.ExpressionCheck("A", "Center", "Point(Gap + 0.1, time)", "Point(A.Gap + 0.1, time)"),),
        )

    def _run(self, bare_value, qualified_value):
        with tempfile.TemporaryDirectory() as td:
            check = preview.ExpressionCheck("M", "Center", "Point(Gap, 0)", "Point(M.Gap, 0)")
            target = preview.PreviewTarget(
                setting_path=Path(td) / "M.setting",
                tool_name="M",
                frame=0,
                output_path=Path(td) / "M.png",
                expression_checks=(check,),
            )
            macro = FakeExpressionTool(
                "M",
                {"Center": "Point(Gap, 0)"},
                {"Point(Gap, 0)": bare_value, "Point(M.Gap, 0)": qualified_value},
            )
            comp = FakeComp(paste_results=[True], macro=macro)
            timeline = FakeTimeline("tl", [comp])
            result_path = Path(td) / "result.json"
            _exec_script([target], "tl", timeline, FakeProject(FakeMediaPool(timeline)), result_path)
            data = _read_result(result_path)
            with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = preview._report(result_path, data)
            return _find_record(data, target.setting_path), macro, code, err.getvalue()

    def test_differing_values_are_reported_and_fail_the_command(self):
        record, macro, code, err = self._run({1: 157.0, 2: 0.0}, {1: 0.0, 2: 0.0})
        self.assertEqual(record["status"], "ok")
        self.assertEqual(len(record["expression_mismatches"]), 1)
        self.assertEqual(record["expression_mismatches"][0]["reason"], "value_differs")
        self.assertEqual(code, 1)
        self.assertIn("resolves a bare name", err)
        # 差し替えた式は元へ戻す
        self.assertEqual(macro.expressions["Center"], "Point(Gap, 0)")

    def test_equal_values_pass(self):
        record, macro, code, _ = self._run({1: 0.1, 2: 0.0}, {1: 0.1, 2: 0.0})
        self.assertEqual(record["expression_mismatches"], [])
        self.assertEqual(code, 0)
        self.assertEqual(
            macro.expression_history, [("Center", "Point(M.Gap, 0)"), ("Center", "Point(Gap, 0)")]
        )


# ---------------------------------------------------------------------------
# フォントサンプルの上書き。
# ---------------------------------------------------------------------------


_TEXT_STYLE_SETTING = (
    "{\n"
    "\tTools = ordered() {\n"
    "\t\tTemplate = TextPlus {\n"
    "\t\t\tInputs = {\n"
    "\t\t\t\tCenter = Input { Value = { 0.25, 0.75 }, },\n"
    '\t\t\t\tStyledText = Input { Value = "01\u30b4\u767d\u9752", },\n'
    "\t\t\t\tSize = Input { Value = 0.052083, },\n"
    "\t\t\t},\n"
    "\t\t},\n"
    "\t},\n"
    "}\n"
)


class SampleTextTest(unittest.TestCase):
    """``--sample-text`` が貼り付け用のコピーだけを書き換えることの検査。

    文字スタイルのプリセットは tool 形であり、``Tools`` に ``MacroOperator`` ではなく素の
    ``TextPlus`` を1つ持つ。``resolve_target`` はどちらの形も受け付けるが、サンプルの上書きは
    ``Text+`` の値を持つ tool 形だけに行う。
    """

    def _target(self, directory, sample):
        setting_path = Path(directory) / "01\u30b4\u767d\u9752.setting"
        setting_path.write_text(_TEXT_STYLE_SETTING, encoding="utf-8")
        sample_dir = Path(directory) / "sample"
        return setting_path, preview.resolve_target(setting_path, None, sample, sample_dir)

    def test_sample_overrides_only_the_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            setting_path, target = self._target(directory, preview.SampleText())

            self.assertEqual(target.tool_name, "Template")
            # サムネイルは元の ``.setting`` の隣へ書く。
            self.assertEqual(target.output_path, setting_path.with_suffix(".png"))
            # 貼り付け元はコピーであり、元のファイルは変わらない。
            self.assertNotEqual(target.source_path, setting_path)
            self.assertEqual(setting_path.read_text(encoding="utf-8"), _TEXT_STYLE_SETTING)

            copy = target.source_path.read_text(encoding="utf-8")
            self.assertIn('StyledText = Input { Value = "Ag", }', copy)
            self.assertIn(f"Size = Input {{ Value = {preview.DEFAULT_SAMPLE_SIZE!r}, }}", copy)
            self.assertIn("Center = Input { Value = { 0.5, 0.5 }, }", copy)

    def test_without_sample_the_original_is_pasted(self):
        with tempfile.TemporaryDirectory() as directory:
            setting_path, target = self._target(directory, None)
            self.assertEqual(target.source_path, setting_path)

    def test_missing_input_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            setting_path = Path(directory) / "bare.setting"
            setting_path.write_text(
                "{\n\tTools = ordered() {\n\t\tTemplate = TextPlus {\n"
                "\t\t\tInputs = {},\n\t\t},\n\t},\n}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as caught:
                preview.resolve_target(
                    setting_path, None, preview.SampleText(), Path(directory) / "sample"
                )
            self.assertIn("StyledText", str(caught.exception))

    def test_macro_form_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            setting_path = Path(directory) / "macro.setting"
            setting_path.write_text(_setting_text("M"), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                preview.resolve_target(
                    setting_path, None, preview.SampleText(), Path(directory) / "sample"
                )
            self.assertIn("tool-form", str(caught.exception))


# ---------------------------------------------------------------------------
# ``--framing content``: 描画内容の範囲を切り抜いてサムネイルにする。
# ---------------------------------------------------------------------------


class ContentFramingTest(unittest.TestCase):
    def _run(self, td, comp, content_margin=preview.DEFAULT_CONTENT_MARGIN):
        targets = [_target(td, comp.macro.name, frame=0)]
        timeline = FakeTimeline("preview-timeline", [comp])
        project = FakeProject(FakeMediaPool(timeline))
        result_path = Path(td) / "result.json"
        _exec_script(
            targets,
            "preview-timeline",
            timeline,
            project,
            result_path,
            framing="content",
            content_margin=content_margin,
        )
        return targets[0], _find_record(_read_result(result_path), targets[0].setting_path)

    def test_crop_is_set_from_the_data_window_and_rendered_again(self):
        with tempfile.TemporaryDirectory() as td:
            # 幅 800、高さ 100 の範囲。余白 10% で 960 x 120、比率 103:58 に合わせて高さを 540.6 へ広げる。
            image = FakeImage({1: 1000, 2: 1000, 3: 1800, 4: 1100})
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"), image=image)
            target, record = self._run(td, comp)

            self.assertEqual(record["status"], "ok")
            self.assertEqual(record["framing"], "content")
            self.assertEqual(record["content_window"], [920, 780, 960, 541])
            self.assertTrue(target.output_path.is_file())
            # 1回目はフレーム全体、2回目は切り抜いた範囲を描画する。
            self.assertEqual(
                comp.render_lock_order, ["lock", "render", "unlock", "lock", "render", "unlock"]
            )
            router, crop, resize, saver = comp.added
            self.assertEqual(
                [t.name for t in comp.added], ["PipeRouter", "Crop", "BetterResize", "Saver"]
            )
            self.assertEqual(router.Output.get_value_calls, [0])
            self.assertEqual(
                dict(crop.set_inputs),
                {"XOffset": 920, "YOffset": 780, "XSize": 960, "YSize": 541},
            )
            # macro -> PipeRouter -> Crop -> BetterResize -> Saver の順に接続されている。
            self.assertIs(router.Input.connected, comp.macro.Output)
            self.assertIs(crop.Input.connected, router.Output)
            self.assertIs(resize.Input.connected, crop.Output)
            self.assertIs(saver.Input.connected, resize.Output)
            for tool in comp.added:
                self.assertTrue(tool.deleted, f"{tool.name} was not deleted")
            # タイムラインに残る項目はマクロの出力を直接持つ。
            self.assertIs(comp.media_out.Input.connected, comp.macro.Output)

    def test_window_is_shifted_inside_the_frame(self):
        with tempfile.TemporaryDirectory() as td:
            # 左下の角に接する範囲。広げた範囲はフレームの外へはみ出すため、原点へ寄せる。
            image = FakeImage({1: 0, 2: 0, 3: 400, 4: 100})
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"), image=image)
            _target_, record = self._run(td, comp)
            x, y, w, h = record["content_window"]
            self.assertEqual((x, y), (0, 0))
            self.assertAlmostEqual(w / h, 103 / 58, places=2)

    def test_window_larger_than_the_frame_is_capped_at_the_frame(self):
        with tempfile.TemporaryDirectory() as td:
            image = FakeImage({1: 10, 2: 10, 3: 3830, 4: 2150})
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"), image=image)
            _target_, record = self._run(td, comp)
            x, y, w, h = record["content_window"]
            self.assertLessEqual(x + w, 3840)
            self.assertLessEqual(y + h, 2160)
            self.assertGreaterEqual(min(x, y), 0)
            self.assertAlmostEqual(w / h, 103 / 58, places=2)

    def test_margin_option_is_applied(self):
        with tempfile.TemporaryDirectory() as td:
            # 余白 0 では、比率を合わせる側(高さ)だけが広がり、幅は範囲そのものになる。
            image = FakeImage({1: 1000, 2: 1000, 3: 1800, 4: 1100})
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"), image=image)
            _target_, record = self._run(td, comp, content_margin=0.0)
            self.assertEqual(record["content_window"][0], 1000)
            self.assertEqual(record["content_window"][2], 800)

    def test_transparent_frame_keeps_the_whole_frame_and_warns(self):
        with tempfile.TemporaryDirectory() as td:
            image = FakeImage({1: 0, 2: 0, 3: 0, 4: 0})
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"), image=image)
            target, record = self._run(td, comp)
            self.assertEqual(record["status"], "ok")
            self.assertIsNone(record["content_window"])
            self.assertEqual(comp.render_lock_order, ["lock", "render", "unlock"])
            crop = comp.added[1]
            self.assertEqual(crop.set_inputs, [])
            self.assertTrue(target.output_path.is_file())

            result_path = Path(td) / "result.json"
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                code = preview._report(result_path, _read_result(result_path))
            self.assertEqual(code, 0)
            self.assertIn("fully transparent", buf_err.getvalue())

    def test_missing_image_is_recorded_as_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            comp = FakeComp(paste_results=[True], macro=FakeTool("Blue"), image=None)
            target, record = self._run(td, comp)
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["reason"], "content_image")
            self.assertFalse(target.output_path.exists())
            for tool in comp.added:
                self.assertTrue(tool.deleted)


class FramingOptionTest(unittest.TestCase):
    def test_build_script_rejects_unknown_framing_and_negative_margin(self):
        with tempfile.TemporaryDirectory() as td:
            targets = [_target(td, "Blue")]
            with self.assertRaises(ValueError):
                preview.build_script(targets, "tl", Path(td) / "r.json", framing="fit")
            with self.assertRaises(ValueError):
                preview.build_script(targets, "tl", Path(td) / "r.json", content_margin=-0.1)

    def _console_script(self, td, setting_text, **kwargs):
        setting_path = Path(td) / "thumb.setting"
        setting_path.write_text(setting_text, encoding="utf-8")
        script_dir = Path(td) / "scripts"
        with redirect_stdout(io.StringIO()):
            preview.run(
                [setting_path],
                frame=None,
                timeline_name="tl",
                console=True,
                directory=script_dir,
                **kwargs,
            )
        return (script_dir / preview.SCRIPT_FILENAME).read_text(encoding="utf-8")

    def test_default_framing_is_content_with_the_default_margin(self):
        with tempfile.TemporaryDirectory() as td:
            script = self._console_script(td, _setting_text("Thumb"))
            self.assertIn("FRAMING = 'content'", script)
            self.assertIn("CONTENT_MARGIN = 0.1", script)

    def test_sample_text_defaults_to_the_whole_frame(self):
        with tempfile.TemporaryDirectory() as td:
            script = self._console_script(td, _TEXT_STYLE_SETTING, sample=preview.SampleText())
            self.assertIn("FRAMING = 'frame'", script)

    def test_explicit_framing_overrides_the_sample_default(self):
        with tempfile.TemporaryDirectory() as td:
            script = self._console_script(
                td,
                _TEXT_STYLE_SETTING,
                sample=preview.SampleText(),
                framing="content",
                content_margin=0.25,
            )
            self.assertIn("FRAMING = 'content'", script)
            self.assertIn("CONTENT_MARGIN = 0.25", script)


if __name__ == "__main__":
    unittest.main()
