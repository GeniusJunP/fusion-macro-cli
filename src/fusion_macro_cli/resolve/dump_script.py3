# -*- coding: utf-8 -*-
# fusion-macro-cli: schema dump
# 
# Fusion の内部(コンソールの exec、または fusion.RunScript)で走り、fusion と resolve をグローバルとして使う。
# __SCHEMA_DIR__ は dump.build_script() が絶対パスの Python リテラルへ置換する。
import gzip
import json
import os

SCHEMA_DIR = __SCHEMA_DIR__
TIMELINE_NAME = "_fusion_macro_cli_schema_dump"
PROBE_TOOL_ID = "Background"


def _is_default_value(value):
    # スクリプティングの橋渡しは未設定の属性にも False、0、'' を補って返す。INPI_IC_ControlID の有無で制御の先頭を
    # 判定できるよう、既定値にあたる値を持つ属性を落とす。
    return value is None or value is False or value == 0 or value == ""


def _read_input_attrs(tool):
    # 多要素ツールは要素を有効にして初めてその要素の入力が現れる。有効化の入力を示す属性は無いため、Fusion の命名規約である
    # Enabled<n> を入力が増えなくなるまで順に立てる。
    inputs = tool.GetInputList()
    n = 2
    while True:
        tool.SetInput("Enabled%d" % n, 1)
        grown = tool.GetInputList()
        if len(grown) == len(inputs):
            break
        inputs = grown
        n += 1
    result = {}
    for i in range(1, len(inputs) + 1):
        attrs = inputs[i].GetAttrs()
        name = attrs["INPS_ID"]
        result[name] = dict(
            (k, v) for k, v in attrs.items() if not _is_default_value(v)
        )
    return result


def _read_output_attrs(tool):
    # 出力ポートの属性のキーは OUTS_* である。
    outputs = tool.GetOutputList()
    result = {}
    for i in range(1, len(outputs) + 1):
        attrs = outputs[i].GetAttrs()
        name = attrs["OUTS_ID"]
        result[name] = dict(
            (k, v) for k, v in attrs.items() if not _is_default_value(v)
        )
    return result


def _registry_entry(tool_id, attrs):
    class_type = int(attrs.get("REGI_ClassType") or 0)
    return {
        "id": tool_id,
        "class_type": class_type,
        # bit1（値2）が立っていない項目はモディファイア。
        "hidden": bool(attrs.get("REGB_Hide")),
        "modifier": not bool(class_type & 2),
        "category": attrs.get("REGS_Category"),
        "name": attrs.get("REGS_Name"),
    }


version = fusion.GetAttrs()["FUSIONS_Version"]
out_path = os.path.join(SCHEMA_DIR, "fusion-%s.json.gz" % version)

pm = resolve.GetProjectManager()
project = pm.GetCurrentProject()
if project is None:
    raise RuntimeError("no project is open in Resolve")

# 多数の生成と削除の後に GetRegList を呼ぶと Resolve がクラッシュするため、生成より前に列挙する。
# typemask=1 はツール、モディファイア、ホスト側の内部オペレーターを表す。
reg_table = fusion.GetRegList(1)
registry = []
for i in range(1, len(reg_table) + 1):
    tool_id = reg_table[i].ID
    attrs = fusion.GetRegAttrs(tool_id)
    registry.append(_registry_entry(tool_id, attrs))

media_pool = project.GetMediaPool()

# 前の実行が残したツールにさらにツールを追加しないよう、取得のたびに専用のタイムラインを作って最後に消す。
for i in range(1, project.GetTimelineCount() + 1):
    existing = project.GetTimelineByIndex(i)
    if existing.GetName() == TIMELINE_NAME:
        media_pool.DeleteTimelines([existing])
        break

# 起動直後はどのページも開いておらず、その状態では CreateEmptyTimeline が None を返す。
# プロジェクトマネージャーやダイアログの表示中は OpenPage が効かないため、エラーにする。
resolve.OpenPage("edit")
if resolve.GetCurrentPage() is None:
    raise RuntimeError("no page can be opened in Resolve; the Project Manager or a dialog is shown. Open a project, close the dialog, or pass --project")
timeline = media_pool.CreateEmptyTimeline(TIMELINE_NAME)
if timeline is None:
    raise RuntimeError("CreateEmptyTimeline returned None for " + TIMELINE_NAME)

# Edit ページのまま取得したコンポジションへ AddTool を呼ぶと Resolve がクラッシュする。
resolve.OpenPage("fusion")
if timeline.InsertFusionCompositionIntoTimeline() is None:
    resolve.OpenPage("edit")
    media_pool.DeleteTimelines([timeline])
    raise RuntimeError("InsertFusionCompositionIntoTimeline returned None")

items = timeline.GetItemListInTrack("video", 1)
if not items:
    resolve.OpenPage("edit")
    media_pool.DeleteTimelines([timeline])
    raise RuntimeError("dedicated timeline has no item after insertion")

comp = items[0].GetFusionCompByIndex(1)
if comp is None:
    resolve.OpenPage("edit")
    media_pool.DeleteTimelines([timeline])
    raise RuntimeError("GetFusionCompByIndex returned None")

# ロックしないと、Loader 系がファイル選択のダイアログを開き、ペイントストロークの生成で Fusion がクラッシュする。
comp.Lock()

added = []  # 例外で抜けた場合も消すための追跡。


def _cleanup():
    for tool in list(added):
        tool.Delete()
        added.remove(tool)
    resolve.OpenPage("edit")
    media_pool.DeleteTimelines([timeline])


try:
    # タイムラインの無いプロジェクトでは AddTool が None を返し、全種別が失敗として記録されたまま正常終了するため、最初に1件で確かめる。
    probe = comp.AddTool(PROBE_TOOL_ID)
    if probe is None:
        raise RuntimeError(
            "AddTool(%r) returned None; this composition does not accept "
            "tool creation (open a real composition on the Fusion page "
            "before running schema dump)" % PROBE_TOOL_ID
        )
    added.append(probe)
    probe.Delete()
    added.remove(probe)

    tools = {}
    outputs = {}
    failed = []
    for entry in registry:
        tool_id = entry["id"]
        class_type = entry["class_type"]
        if class_type == 1:
            # ホスト側の内部オペレーターはツールではないため生成しない。
            failed.append(
                {"id": tool_id, "reason": "not_a_tool", "class_type": class_type}
            )
            continue
        try:
            tool = comp.AddTool(tool_id)
        except Exception as exc:
            failed.append(
                {
                    "id": tool_id,
                    "reason": "add_tool_raised",
                    "class_type": class_type,
                    "detail": str(exc),
                }
            )
            continue
        if tool is None:
            failed.append(
                {
                    "id": tool_id,
                    "reason": "add_tool_returned_none",
                    "class_type": class_type,
                }
            )
            continue
        added.append(tool)
        try:
            # tools と failed が種別ごとに排他となるよう、入力と出力を両方読み終えてから代入する。出力側の例外も同じ符号で記録する。
            input_attrs = _read_input_attrs(tool)
            output_attrs = _read_output_attrs(tool)
            tools[tool_id] = input_attrs
            outputs[tool_id] = output_attrs
        except Exception as exc:
            failed.append(
                {
                    "id": tool_id,
                    "reason": "read_inputs_raised",
                    "class_type": class_type,
                    "detail": str(exc),
                }
            )
        tool.Delete()
        added.remove(tool)
finally:
    _cleanup()

data = {
    "version": version,
    "registry": registry,
    "tools": tools,
    "outputs": outputs,
    "failed": failed,
}
os.makedirs(SCHEMA_DIR, exist_ok=True)
with gzip.open(out_path, "wt", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)

print("fusion-macro-cli: wrote " + out_path)
