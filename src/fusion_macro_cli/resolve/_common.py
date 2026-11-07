"""``schema dump`` と ``preview`` が共有する、Fusion 内部スクリプトの書き出しと起動。"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

# DaVinciResolveScript が import できない場合に探す、macOS の既定のインストール位置。
_MAC_MODULES_DIR = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting/Modules"
)


class ResolveConnectionError(RuntimeError):
    """Resolve への接続に失敗したことを表す。Free では外部スクリプティングを使用できない。"""


def connect():
    """``DaVinciResolveScript`` 経由で ``resolve`` と ``fusion`` を得る。"""
    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]
    except ImportError:
        if _MAC_MODULES_DIR not in sys.path:
            sys.path.append(_MAC_MODULES_DIR)
        try:
            import DaVinciResolveScript as dvr  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ResolveConnectionError(
                "could not import DaVinciResolveScript; is DaVinci Resolve installed?"
            ) from exc

    resolve_app = dvr.scriptapp("Resolve")
    if resolve_app is None:
        raise ResolveConnectionError(
            "could not connect to DaVinci Resolve; is it running, and is "
            "external scripting enabled? (Free edition cannot use this path; "
            "use --console instead)"
        )
    fusion_app = resolve_app.Fusion()
    if fusion_app is None:
        raise ResolveConnectionError("Resolve.Fusion() returned no object")
    return resolve_app, fusion_app


def read_script_template(filename: str) -> str:
    """このパッケージに同梱した Fusion 内部スクリプトのテンプレートを読む。"""
    return resources.files(__package__).joinpath(filename).read_text(encoding="utf-8")


def render_template(template: str, substitutions: dict[str, str]) -> str:
    """``__KEY__`` 形式のプレースホルダを置換する。値は ``repr()`` で作った Python リテラルを渡す。"""
    result = template
    for key, value in substitutions.items():
        result = result.replace(key, value)
    return result


def write_script(directory: Path, filename: str, source: str) -> Path:
    """スクリプトをファイルへ書き出し、そのパスを返す。

    ``fusion.RunScript`` は ``.py`` を環境設定「Default Python Version」のインタプリタで実行し、
    設定が Python 3 でない環境では何も実行せず戻る。そのため拡張子を ``.py3`` に限定する。
    """
    if not filename.endswith(".py3"):
        raise ValueError(
            f"script filename must end with .py3 (RunScript honors the OS "
            f"default for .py, which can silently do nothing): {filename!r}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    script_path = directory / filename
    script_path.write_text(source, encoding="utf-8")
    return script_path


def console_command(script_path: Path) -> str:
    """Fusion ページのコンソール(言語を ``Py3`` に切り替えたもの)へ貼り付ける1行を返す。"""
    return f'exec(open({str(script_path.resolve())!r}, encoding="utf-8").read())'


def launch(script_path: Path, *, console: bool) -> int:
    """コンソール用の1行を表示するか、``RunScript`` で起動する。

    ``RunScript`` は完了を待たずに戻るため、終了コード 0 は起動できたことのみを示す。
    """
    if console:
        print(console_command(script_path))
        return 0

    try:
        _, fusion_app = connect()
    except ResolveConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    fusion_app.RunScript(str(script_path))
    return 0
