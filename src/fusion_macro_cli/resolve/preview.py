"""``preview`` コマンド。

``.setting`` を Resolve のコンポジションへ貼り付けて PNG を書き、確認用のタイムラインを残す。
内部スクリプトの ``print()`` は CLI にも Resolve のログにも出力されないため、``.setting`` ごとの結果を
JSON の結果ファイルに書かせ、CLI はその出現を待って読む。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from fusion_macro_cli import document
from fusion_macro_cli import expression as expression_mod
from fusion_macro_cli import form as form_mod
from fusion_macro_cli import graph as graph_mod
from fusion_macro_cli.resolve import _common

SCRIPT_FILENAME = "_preview.py3"

RESULT_FILENAME = "_preview_result.json"

# フォントサンプルとして上書きした ``.setting`` のコピーを書き出すディレクトリ名。
SAMPLE_DIRNAME = "_preview_sample"

# Templates ディレクトリのサムネイルの大きさ。
THUMBNAIL_WIDTH = 103
THUMBNAIL_HEIGHT = 58

# 仕様補完: 結果ファイルを確認する間隔(秒)。
_POLL_INTERVAL_SECONDS = 1.0

# ``--timeout`` を省略した場合の、``.setting`` 1件あたりの待ち時間の上限(秒)。
_DEFAULT_TIMEOUT_PER_TARGET_SECONDS = 120.0

# フォントサンプルのサムネイルに表示する文字列の既定値。
DEFAULT_SAMPLE_TEXT = "Ag"

# 仕様補完: フォントサンプルの文字の大きさ。Text+ の ``Size`` はフレームの高さに対する比であり、
# サムネイルの中で文字が占める割合をこの1つの値で揃える。
DEFAULT_SAMPLE_SIZE = 0.5

# フォントサンプルで上書きする入力。``Center`` は既定から動かされているプリセットがあるためサムネイルの中央へ固定する。
_SAMPLE_CENTER = (0.5, 0.5)


@dataclass(frozen=True)
class SampleText:
    """文字スタイルのプリセットをフォントサンプルとして描画するための上書き値。

    文字スタイルのプリセットは ``StyledText`` にプリセット名を、``Size`` に本文として妥当な小さい値を持つ。
    そのまま描画するとサムネイルの中では読めない細線になるため、サンプル用の文字と大きさで上書きしてから貼り付ける。
    """

    text: str = DEFAULT_SAMPLE_TEXT
    size: float = DEFAULT_SAMPLE_SIZE


@dataclass(frozen=True)
class PreviewTarget:
    """1つの ``.setting`` について、Fusion 内部スクリプトへ埋め込む確定値。

    Fusion 内部の実行環境は ``fusion_macro_cli`` を import できないため、``.setting`` の解析はこのプロセスで済ませる。
    ``frame`` が ``None`` の場合は、項目の長さを取得できる Fusion 内部スクリプトが最終フレームを描画する。
    """

    setting_path: Path
    tool_name: str
    frame: int | None
    output_path: Path
    expression_checks: tuple["ExpressionCheck", ...] = ()
    paste_path: Path | None = None

    @property
    def source_path(self) -> Path:
        """Fusion へ貼り付ける ``.setting`` のパス。フォントサンプルの上書きを行った場合は一時ファイルを指す。"""
        return self.paste_path if self.paste_path is not None else self.setting_path


@dataclass(frozen=True)
class ExpressionCheck:
    """裸の名前で ``UserControls`` の入力を参照する式と、その名前をツール名で修飾した式の組。

    Fusion 内部スクリプトは両者の評価値を比べ、一致しなければ裸の名前が別のものへ解決されたと判定する。
    """

    tool: str
    input: str
    expression: str
    qualified: str


def resolve_target(
    setting_path: Path,
    frame: int | None,
    sample: SampleText | None = None,
    sample_dir: Path | None = None,
) -> PreviewTarget:
    """``.setting`` を読み、貼り付けるツール名、描くフレーム、出力する PNG のパスを決める。

    ``comp.ActiveTool`` は挿入直後の選択状態に依存するため、ツールは名前で引く。
    式で駆動されるアニメーションはキーフレームを持たないため、既定のフレームはキーフレーム時刻ではなく項目の最終フレームとする。
    ``frame`` を指定した場合は ``<名前>_f<N>.png`` へ書き、項目の長さを超えても補正しない。
    ``sample`` を渡すと、上書きしたコピーを ``sample_dir`` へ書き、貼り付けにはそちらを使う。
    """
    text = setting_path.read_text(encoding="utf-8")
    doc = document.parse(text)
    try:
        kind = form_mod.classify(doc)
    except ValueError as exc:
        raise ValueError(f"{setting_path}: {exc}") from exc
    if kind == "comp":
        raise ValueError(f"{setting_path}: a .comp cannot be pasted; pass a .setting")
    top = form_mod.top_tool(doc)
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)
    tool_name = top.name

    if frame is None:
        output_path = setting_path.with_name(f"{setting_path.stem}.png")
    else:
        output_path = setting_path.with_name(f"{setting_path.stem}_f{frame}.png")

    paste_path = None
    if sample is not None:
        if sample_dir is None:
            raise ValueError("sample_dir is required when sample is given")
        if kind != "tool":
            # MacroOperator の Inputs は InstanceInput であり、Text+ の値を持たないため、上書きする対象が無い。
            raise ValueError(
                f"{setting_path}: --sample-text needs a tool-form .setting (a Text+ saved as it is); "
                "this is a macro-form .setting"
            )
        paste_path = _write_sample_setting(setting_path, doc, top.table, sample, sample_dir)

    return PreviewTarget(
        setting_path=setting_path,
        tool_name=tool_name,
        frame=frame,
        output_path=output_path,
        expression_checks=_expression_checks(tools_table),
        paste_path=paste_path,
    )


def _write_sample_setting(
    setting_path: Path,
    doc: document.Document,
    tool: document.Node,
    sample: SampleText,
    directory: Path,
) -> Path:
    """``StyledText`` ``Size`` ``Center`` をサンプルの値へ上書きしたコピーを書き、そのパスを返す。

    上書きは解析済みの文書の上で行う。Fusion のマクロは内部のツールを ``comp.FindTool`` で引けないため、
    貼り付けた後に Fusion の API から書き換える方法は使用できない。
    """
    inputs = tool.get("Inputs") if isinstance(tool, document.Table) else None
    if not isinstance(inputs, document.Table):
        raise ValueError(f"{setting_path}: the top-level tool has no Inputs table")

    _set_string(setting_path, inputs, "StyledText", sample.text)
    _set_number(setting_path, inputs, "Size", sample.size)
    _set_point(setting_path, inputs, "Center", _SAMPLE_CENTER)

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / setting_path.name
    path.write_text(document.dump(doc), encoding="utf-8")
    return path


def _input_value(setting_path: Path, inputs: document.Table, name: str) -> document.Node:
    """``Inputs`` の ``<名前> = Input { Value = ... }`` の ``Value`` のノードを返す。"""
    entry = inputs.get(name)
    if not isinstance(entry, document.Table):
        raise ValueError(f"{setting_path}: input {name} is absent or is not a table")
    value = entry.get("Value")
    if value is None:
        raise ValueError(f"{setting_path}: input {name} has no Value")
    return value


def _set_string(setting_path: Path, inputs: document.Table, name: str, text: str) -> None:
    value = _input_value(setting_path, inputs, name)
    if not isinstance(value, document.String):
        raise ValueError(f"{setting_path}: input {name} is not a string")
    value.raw = document.encode_string(text)


def _set_number(setting_path: Path, inputs: document.Table, name: str, number: float) -> None:
    value = _input_value(setting_path, inputs, name)
    if not isinstance(value, document.Number):
        raise ValueError(f"{setting_path}: input {name} is not a number")
    value.raw = repr(number)


def _set_point(
    setting_path: Path, inputs: document.Table, name: str, point: tuple[float, float]
) -> None:
    value = _input_value(setting_path, inputs, name)
    if not isinstance(value, document.Table) or len(value.elements) != 2:
        raise ValueError(f"{setting_path}: input {name} is not a pair of numbers")
    for element, number in zip(value.elements, point):
        if not isinstance(element.value, document.Number):
            raise ValueError(f"{setting_path}: input {name} is not a pair of numbers")
        element.value.raw = repr(number)


def _expression_checks(tools_table: document.Table) -> tuple[ExpressionCheck, ...]:
    """``UserControls`` の入力名を裸で参照する式を列挙し、修飾した式と組にする。"""
    g = graph_mod.build_graph(tools_table)
    checks = []
    for driven in g.driven_inputs():
        names = g.user_controls(driven.tool).keys()
        qualified = expression_mod.qualify_same_tool_references(driven.expression, driven.tool, names)
        if qualified != driven.expression:
            checks.append(ExpressionCheck(driven.tool, driven.input, driven.expression, qualified))
    return tuple(checks)


def build_script(
    targets: Sequence[PreviewTarget],
    timeline_name: str,
    result_path: Path,
    project_name: str | None = None,
) -> str:
    """Fusion の内部で走る preview スクリプトを組み立てる。

    ``project_name`` を渡すとそのプロジェクトを開いてからタイムラインを作り、使ったプロジェクト名を結果ファイルへ記録する。
    """
    targets_literal = repr(
        [
            {
                "setting": str(target.setting_path.resolve()),
                "paste": str(target.source_path.resolve()),
                "tool": target.tool_name,
                "frame": target.frame,
                "output": str(target.output_path.resolve()),
                "expression_checks": [asdict(check) for check in target.expression_checks],
            }
            for target in targets
        ]
    )
    return _common.render_template(
        _SCRIPT_TEMPLATE,
        {
            "__TIMELINE_NAME__": repr(timeline_name),
            "__PROJECT_NAME__": repr(project_name),
            "__TARGETS__": targets_literal,
            "__RESIZE_WIDTH__": repr(THUMBNAIL_WIDTH),
            "__RESIZE_HEIGHT__": repr(THUMBNAIL_HEIGHT),
            "__RESULT_PATH__": repr(str(Path(result_path).resolve())),
        },
    )


def write_script(
    targets: Sequence[PreviewTarget],
    timeline_name: str,
    result_path: Path,
    directory: Path | None = None,
    project_name: str | None = None,
) -> Path:
    """:func:`build_script` の出力を書き出し、そのパスを返す。``directory`` を省略すると OS の一時ディレクトリを使う。"""
    if directory is None:
        directory = Path(tempfile.gettempdir())
    return _common.write_script(
        directory,
        SCRIPT_FILENAME,
        build_script(targets, timeline_name, result_path, project_name),
    )


def console_command(script_path: Path) -> str:
    """Fusion ページのコンソールへ貼り付ける1行を返す。"""
    return _common.console_command(script_path)


def _read_result(result_path: Path) -> dict | None:
    """結果ファイルを読む。存在しない、または書き込みの途中で解析できない場合は ``None`` を返す。"""
    try:
        with open(result_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _report(result_path: Path, data: dict | None) -> int:
    """結果ファイルの内容を表示し、終了コードを返す。内部スクリプトの ``print()`` は CLI に出力されないため、ここで表示する。"""
    if data is None:
        print(f"fusion-macro-cli: timed out waiting for {result_path}", file=sys.stderr)
        return 1

    project = data.get("project")
    if project is not None:
        print("fusion-macro-cli: using project " + project)

    mismatch_count = 0
    for record in data.get("targets", []):
        for mismatch in record.get("expression_mismatches", []):
            mismatch_count += 1
            print(
                "fusion-macro-cli: %s: expression on %s.%s resolves a bare name to something other "
                "than the UserControls input (%s): %r evaluates to %s, %r evaluates to %s"
                % (
                    record["setting"],
                    mismatch["tool"],
                    mismatch["input"],
                    mismatch["reason"],
                    mismatch["expression"],
                    mismatch.get("bare_value"),
                    mismatch["qualified"],
                    mismatch.get("qualified_value"),
                ),
                file=sys.stderr,
            )
        if record["status"] == "ok":
            print("fusion-macro-cli: wrote " + record["output"])
        else:
            print(
                "fusion-macro-cli: preview failed for %s at %s"
                % (record["setting"], record["reason"])
            )

    if not data.get("complete"):
        print(f"fusion-macro-cli: timed out waiting for {result_path}", file=sys.stderr)
        return 1

    error = data.get("error")
    if error:
        print(f"fusion-macro-cli: {error}", file=sys.stderr)
        return 1

    failed = [record for record in data.get("targets", []) if record["status"] != "ok"]
    if mismatch_count:
        print(
            "fusion-macro-cli: preview found %d expression(s) whose bare names do not resolve "
            "to their UserControls inputs" % mismatch_count,
            file=sys.stderr,
        )
    if failed:
        print(
            "fusion-macro-cli: preview finished with %d failure(s)" % len(failed),
            file=sys.stderr,
        )
    return 1 if failed or mismatch_count else 0


def _wait_for_result(
    result_path: Path,
    target_count: int,
    *,
    timeout: float | None,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
) -> int:
    """結果ファイルの ``complete`` が真になるまで待ち、終了コードを返す。"""
    if timeout is None:
        timeout = _DEFAULT_TIMEOUT_PER_TARGET_SECONDS * target_count
    deadline = time.monotonic() + timeout
    data = _read_result(result_path)
    while not (data is not None and data.get("complete")):
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
        data = _read_result(result_path)
    return _report(result_path, data)


def run(
    setting_paths: Sequence[Path],
    *,
    frame: int | None,
    timeline_name: str,
    console: bool = False,
    directory: Path | None = None,
    timeout: float | None = None,
    project_name: str | None = None,
    sample: SampleText | None = None,
) -> int:
    """``preview`` を実行し、終了コードを返す。

    ``RunScript`` は完了を待たずに戻るため、内部スクリプトが書く結果ファイルを待って結果を得る。
    前回の結果ファイルを今回の完了と誤認しないよう、起動の前に削除する。
    """
    if directory is None:
        directory = Path(tempfile.gettempdir())
    # 上書きしたコピーは、元の ``.setting`` と名前が同じでも区別できるよう別のディレクトリへ書き出す。
    sample_dir = directory / SAMPLE_DIRNAME
    targets = [resolve_target(Path(p), frame, sample, sample_dir) for p in setting_paths]
    result_path = directory / RESULT_FILENAME
    script_path = write_script(
        targets,
        timeline_name,
        result_path,
        directory=directory,
        project_name=project_name,
    )
    result_path.unlink(missing_ok=True)

    if console:
        code = _common.launch(script_path, console=True)
        print(f"fusion-macro-cli: results will be written to {result_path}")
        return code

    code = _common.launch(script_path, console=False)
    if code != 0:
        return code
    return _wait_for_result(result_path, len(targets), timeout=timeout)


_SCRIPT_TEMPLATE = _common.read_script_template("preview_script.py3")
