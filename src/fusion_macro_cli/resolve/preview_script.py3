# -*- coding: utf-8 -*-
# fusion-macro-cli: preview
# 
# Fusion の内部(コンソールの exec、または fusion.RunScript)で走り、resolve と bmd をグローバルとして使う。
# __TIMELINE_NAME__ などのプレースホルダは preview.build_script() が Python リテラルへ置換する。
# 
# Resolve の挙動に由来する制約:
#   - comp.Paste() は、コンポジションが Fusion ページに表示されていなければ False を返す。挿入した項目の
#     先頭へ再生ヘッドを移し、Edit ページを経て Fusion ページへ戻ると表示される
#   - 再生ヘッドが項目の内部にあると、挿入はその位置で直前の項目を分断する。存在しない末尾へ再生ヘッドを
#     先回りさせても分断する
#   - comp.Render() は comp.CurrentTime を描いたフレームへ動かし、SetCurrentTimecode() では挿入位置が戻らない
#   - 書き込み系の呼び出しが揃って None を返し始めた場合はモーダルダイアログが開いている疑いがあり、以後の処理を
#     信頼できないため停止する。貼り付け、再生ヘッドの移動、接続、Render の失敗はその .setting の失敗として記録して続ける
import json
import os
import shutil
import tempfile

TIMELINE_NAME = __TIMELINE_NAME__
PROJECT_NAME = __PROJECT_NAME__
TARGETS = __TARGETS__
RESIZE_WIDTH = __RESIZE_WIDTH__
RESIZE_HEIGHT = __RESIZE_HEIGHT__
RESULT_PATH = __RESULT_PATH__

# print() は CLI にも Resolve のログにも出力されないため、.setting ごとの結果を結果ファイルへ書く。
STATE = {"targets": [], "complete": False, "error": None, "project": None}


def _write_result(state):
    # 読み手が書きかけの内容を読まないよう、同じディレクトリへ書いてから置き換える。
    tmp_path = RESULT_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp_path, RESULT_PATH)


def _numbered_path(clip_path, frame):
    # Saver はファイル名へ4桁のフレーム番号を挿入して書き出す("thumb.png" は "thumb0000.png")。
    root, ext = os.path.splitext(clip_path)
    return "%s%04d%s" % (root, frame, ext)


def _to_timecode(frame, fps_int, drop_frame):
    # 再生ヘッドの移動を GetCurrentTimecode() との文字列比較で確かめるため、ドロップフレームの書式に合わせる。
    # ドロップフレームでは各分の先頭で番号を 30 fps 系は 2 個、60 fps 系は 4 個飛ばし、10 分ごとの先頭では飛ばさない。
    # 区切りはフレームの前だけがセミコロンとなる。
    frame = int(frame)
    if drop_frame:
        dropped = 2 * (fps_int // 30)
        frames_per_10min = fps_int * 60 * 10 - 9 * dropped
        frames_per_min = fps_int * 60 - dropped
        tens = frame // frames_per_10min
        rest = frame % frames_per_10min
        frame += dropped * 9 * tens
        if rest >= dropped:
            frame += dropped * ((rest - dropped) // frames_per_min)
        separator = ";"
    else:
        separator = ":"
    ff = frame % fps_int
    total_seconds = frame // fps_int
    ss = total_seconds % 60
    total_minutes = total_seconds // 60
    mm = total_minutes % 60
    hh = total_minutes // 60
    return "%02d:%02d:%02d%s%02d" % (hh, mm, ss, separator, ff)


# 再生ヘッドの移動は表示状態に依存して1回目が反映されないことがあるため、有限回だけ試す。
_PLAYHEAD_ATTEMPTS = 3


def _move_playhead(timeline, timecode):
    # SetCurrentTimecode は成功を返しても表示が追いついていないことがあるため、GetCurrentTimecode で確かめる。
    for _ in range(_PLAYHEAD_ATTEMPTS):
        timeline.SetCurrentTimecode(timecode)
        if timeline.GetCurrentTimecode() == timecode:
            return True
    return False


def _same_value(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same_value(a[k], b[k]) for k in a)
    return a == b


def _check_expression(comp, check, frame):
    # 一致すれば None、食い違えば記録用の辞書を返す。
    tool = comp.FindTool(check["tool"])
    if tool is None:
        return dict(check, reason="tool_not_found")
    port = getattr(tool, check["input"], None)
    if port is None:
        return dict(check, reason="input_not_found")
    bare = tool.GetInput(check["input"], frame)
    port.SetExpression(check["qualified"])
    try:
        qualified = tool.GetInput(check["input"], frame)
    finally:
        port.SetExpression(check["expression"])
    if _same_value(bare, qualified):
        return None
    return dict(check, reason="value_differs", bare_value=repr(bare), qualified_value=repr(qualified))


def _item_ids(items):
    # ラッパーオブジェクトは呼び出しのたびに新しく作られ比較に使えないため、GetUniqueId() で識別する。
    return dict((it.GetUniqueId(), it) for it in items)


try:
    pm = resolve.GetProjectManager()
    # 未保存の変更があるプロジェクトを開き直すと保存を尋ねるダイアログが開くため、開いているプロジェクトは読み直さない。
    current = pm.GetCurrentProject()
    if PROJECT_NAME is not None and (current is None or current.GetName() != PROJECT_NAME):
        if pm.LoadProject(PROJECT_NAME) is None:
            raise RuntimeError("LoadProject returned None for " + PROJECT_NAME)
    project = pm.GetCurrentProject()
    if project is None:
        raise RuntimeError("no project is open in Resolve")
    # タイムラインを作ったプロジェクトを後から確認できるよう、名前を記録する。
    STATE["project"] = project.GetName()
    _write_result(STATE)

    media_pool = project.GetMediaPool()

    # 起動直後はどのページも開いておらず、その状態では CreateEmptyTimeline が None を返す。
    # タイムラインが無いと Fusion ページは開けないため、先に Edit ページを開く。
    # プロジェクトマネージャーやダイアログの表示中は OpenPage が効かないため、エラーにする。
    resolve.OpenPage("edit")
    if resolve.GetCurrentPage() is None:
        raise RuntimeError("no page can be opened in Resolve; the Project Manager or a dialog is shown. Open a project, close the dialog, or pass --project")
    timeline = media_pool.CreateEmptyTimeline(TIMELINE_NAME)
    if timeline is None:
        raise RuntimeError("CreateEmptyTimeline returned None for " + TIMELINE_NAME)

    # Edit ページのままコンポジションへ AddTool や Paste を行うと Resolve がクラッシュする。
    resolve.OpenPage("fusion")

    fps_int = int(round(float(timeline.GetSetting("timelineFrameRate"))))
    drop_frame = str(timeline.GetSetting("timelineDropFrameTimecode")) == "1"

    for index, target in enumerate(TARGETS):
        setting_path = target["setting"]
        # フォントサンプルでは上書きしたコピーを貼り付けるため、貼り付け元は記録用のパスと別の変数にする。
        paste_path = target["paste"]
        tool_name = target["tool"]
        frame_requested = target["frame"]
        clip_path = target["output"]
        frame_used = frame_requested

        expression_mismatches = []

        def _record(status, reason=None):
            STATE["targets"].append(
                {
                    "setting": setting_path,
                    "output": clip_path,
                    "frame_requested": frame_requested,
                    "frame_used": frame_used,
                    "status": status,
                    "reason": reason,
                    "expression_mismatches": expression_mismatches,
                }
            )
            _write_result(STATE)

        # Render 後の comp.CurrentTime = 0 によるリセットは反映されないことがあるため、挿入の直前に再生ヘッドを最後の項目の先頭(必ず存在する境界)へ移動する。
        # Render の後は Fusion ページのままでは位置を読めないため、Edit ページへ移る。
        existing = timeline.GetItemListInTrack("video", 1) or []
        if existing:
            resolve.OpenPage("edit")
            boundary = max(it.GetStart() for it in existing)
            boundary_tc = _to_timecode(boundary, fps_int, drop_frame)
            if not _move_playhead(timeline, boundary_tc):
                raise RuntimeError(
                    "could not move the playhead to a clip boundary before inserting "
                    + setting_path
                )

        before_ids = _item_ids(timeline.GetItemListInTrack("video", 1))
        if timeline.InsertFusionCompositionIntoTimeline() is None:
            raise RuntimeError(
                "InsertFusionCompositionIntoTimeline returned None for " + setting_path
            )
        after_ids = _item_ids(timeline.GetItemListInTrack("video", 1))
        new_ids = [i for i in after_ids if i not in before_ids]
        if len(new_ids) != 1:
            raise RuntimeError(
                "expected exactly one new timeline item after insertion for %s, found %d"
                % (setting_path, len(new_ids))
            )
        item = after_ids[new_ids[0]]

        # 挿入した項目の長さを超えるフレームは Render が失敗する。明示された --frame は補正せず失敗とする。
        last_frame = item.GetEnd() - item.GetStart() - 1
        if frame_requested is None:
            frame_used = last_frame
        elif frame_requested > last_frame:
            _record("failed", "frame_out_of_range")
            continue

        # Paste が通るよう、挿入した項目のコンポジションを Fusion ページに表示させる。
        start_tc = _to_timecode(item.GetStart(), fps_int, drop_frame)
        if not _move_playhead(timeline, start_tc):
            _record("failed", "playhead")
            continue
        resolve.OpenPage("edit")
        resolve.OpenPage("fusion")

        comp = item.GetFusionCompByIndex(1)
        if comp is None:
            raise RuntimeError("GetFusionCompByIndex returned None for " + setting_path)

        # 表示中のコンポジションが挿入直後の空のもの(MediaOut1 のみ)でなければ、狙った項目に到達していない。
        tool_names = [t.GetAttrs().get("TOOLS_Name") for t in comp.GetToolList().values()]
        if tool_names != ["MediaOut1"]:
            raise RuntimeError(
                "Fusion page is not showing the newly inserted empty composition for "
                "%s (found tools: %r)" % (setting_path, tool_names)
            )

        pasted = comp.Paste(bmd.readfile(paste_path))
        if not pasted and index == 0:
            # 1件目に限り1回だけ再試行する。
            pasted = comp.Paste(bmd.readfile(paste_path))
        if not pasted:
            _record("failed", "paste")
            continue

        macro = comp.FindTool(tool_name)
        if macro is None:
            _record("failed", "find_tool")
            continue

        # macro.Output は表示言語で翻訳された出力名を使い None を返すため、位置で取る。
        outputs = macro.GetOutputList()
        macro_output = outputs.get(1) if outputs else None

        resize = comp.AddTool("BetterResize")
        resize.SetInput("Width", RESIZE_WIDTH)
        resize.SetInput("Height", RESIZE_HEIGHT)
        resize.Input.ConnectTo(macro_output)

        # Saver はファイル名末尾の数字を取り除いてから連番を付けるため、末尾が数字でない固定名で書いてから改名する。
        # 仕様補完: os.replace が原子的に移動できるよう、一時ディレクトリは出力先の隣に作る。
        render_tmp_dir = tempfile.mkdtemp(dir=os.path.dirname(clip_path))
        render_clip_path = os.path.join(render_tmp_dir, "render.png")

        saver = comp.AddTool("Saver")
        saver.Input.ConnectTo(resize.Output)
        saver.SetInput("Clip", render_clip_path)

        try:
            # Saver の入力が未接続のまま Render すると、以後の書き込み系の呼び出しを止めるダイアログが開き、comp.Lock() でも抑制できない。
            if saver.Input.GetConnectedOutput() is None:
                _record("failed", "not_connected")
                continue

            # 成功時にも完了を告げるダイアログが開くが、Render を comp.Lock() で挟むと抑制できる。
            comp.Lock()
            try:
                rendered = comp.Render({"Start": frame_used, "End": frame_used, "Wait": True})
            finally:
                comp.Unlock()
            if not rendered:
                _record("failed", "render")
                continue

            rendered_path = _numbered_path(render_clip_path, frame_used)
            os.replace(rendered_path, clip_path)

            # タイムラインに残す項目が絵を持つよう MediaOut1 へマクロの出力を接続する。削除の巻き添えを避けるため、先に BetterResize と Saver を消す。
            saver.Delete()
            resize.Delete()
            saver = None
            resize = None

            media_out = comp.FindTool("MediaOut1")
            if media_out is None:
                _record("failed", "media_out_not_found")
                continue
            # ConnectTo(None) は例外を送出せずに何もしないため、接続の結果を確かめる。
            media_out.Input.ConnectTo(macro_output)
            if media_out.Input.GetConnectedOutput() is None:
                _record("failed", "media_out_not_connected")
                continue

            # 挿入した項目はどれも "Fusion Composition" という名前になり見分けられないため、マクロ名を付ける。
            item.SetName(tool_name)
            if item.GetName() != tool_name:
                _record("failed", "set_name")
                continue

            # 裸の名前が UserControls の入力以外へ解決されていないかを照合する。差し替えた式は元へ戻す。
            for check in target["expression_checks"]:
                mismatch = _check_expression(comp, check, frame_used)
                if mismatch is not None:
                    expression_mismatches.append(mismatch)

            _record("ok")
        finally:
            # comp.Undo() では追加したツールが残るため、名指しで削除する。
            if saver is not None:
                saver.Delete()
            if resize is not None:
                resize.Delete()
            shutil.rmtree(render_tmp_dir, ignore_errors=True)
            # Render が動かした comp.CurrentTime を戻さないと、次の挿入が直前の項目を分断する。
            comp.CurrentTime = 0

    resolve.OpenPage("edit")
    STATE["complete"] = True
    _write_result(STATE)
except Exception as exc:
    STATE["error"] = "%s: %s" % (type(exc).__name__, exc)
    STATE["complete"] = True
    _write_result(STATE)
    raise
