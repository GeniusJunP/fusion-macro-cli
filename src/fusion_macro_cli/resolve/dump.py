"""``schema dump`` コマンド。Fusion からツール種別ごとの入力と出力ポートの定義を取得する。"""

from __future__ import annotations

from pathlib import Path

from fusion_macro_cli import schema
from fusion_macro_cli.resolve import _common

SCRIPT_FILENAME = "_dump.py3"


def build_script(schema_dir: Path) -> str:
    """Fusion の内部で走るスキーマ取得スクリプトを組み立てる。ファイル名はスクリプトがバージョンから決める。"""
    # コンソールの作業ディレクトリに依存しないよう絶対パスを埋め込む。
    schema_dir_literal = repr(str(schema_dir.resolve()))
    return _common.render_template(_SCRIPT_TEMPLATE, {"__SCHEMA_DIR__": schema_dir_literal})


def write_script(schema_dir: Path) -> Path:
    """:func:`build_script` の出力をスキーマのディレクトリへ書き出し、そのパスを返す。"""
    return _common.write_script(schema_dir, SCRIPT_FILENAME, build_script(schema_dir))


def console_command(script_path: Path) -> str:
    """Fusion ページのコンソールへ貼り付ける1行を返す。"""
    return _common.console_command(script_path)


def run(schema_dir: Path | None = None, console: bool = False) -> int:
    """``schema dump`` を実行し、終了コードを返す。``RunScript`` の完了は待たない。"""
    if schema_dir is None:
        schema_dir = schema.schema_dir()

    script_path = write_script(schema_dir)
    return _common.launch(script_path, console=console)


_SCRIPT_TEMPLATE = _common.read_script_template("dump_script.py3")
