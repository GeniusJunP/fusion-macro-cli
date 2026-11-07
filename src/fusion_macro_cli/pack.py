"""``Templates`` ディレクトリの ``Edit/`` 以下を ``.drfx``(拡張子の異なる ZIP)へアーカイブする。"""

from __future__ import annotations

import pathlib
import zipfile
from typing import Final

__all__ = ["DRFX_STRUCTURE_HELP", "archive_files", "target_documents", "write_archive"]

#: ``pack --help`` の epilog に載せる、``.drfx`` の構造の説明。
DRFX_STRUCTURE_HELP: Final[str] = """\
Structure of a .drfx file:

  A .drfx file is a ZIP archive; only the extension differs from a plain
  .zip. Its layout mirrors Fusion's Templates/ directory: everything
  under the Templates directory's Edit/ subdirectory (for example
  "Edit/Titles/<category>/some_title.setting") is archived as-is, with
  archive paths relative to the Templates directory and no directory renaming.

  Every file under Edit/ is included except ones whose name (or an
  enclosing directory's name) starts with "." (.git, .DS_Store, .venv,
  and similar). There is no extension allowlist: a preset's referenced
  images and fonts can be of any type.
"""

#: この文字で始まる名前のファイルとディレクトリ(``.git``、``.DS_Store`` など)はアーカイブしない。
_DOT_PREFIX = "."


def _iter_edit_files(store_dir: pathlib.Path) -> list[pathlib.Path]:
    """``store_dir/Edit`` 以下のファイルを、相対パスの順に列挙する。"""
    edit_dir = store_dir / "Edit"
    if not edit_dir.is_dir():
        raise FileNotFoundError(f"no Edit/ directory under {store_dir}")

    files: list[pathlib.Path] = []
    for path in edit_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(store_dir)
        if any(part.startswith(_DOT_PREFIX) for part in relative.parts):
            continue
        files.append(path)
    files.sort(key=lambda p: p.relative_to(store_dir).as_posix())
    return files


def target_documents(store_dir: pathlib.Path) -> list[pathlib.Path]:
    """``Edit/`` 以下の ``.comp`` と ``.setting`` をサブディレクトリを含めて列挙する。"""
    return [p for p in _iter_edit_files(store_dir) if p.suffix in (".comp", ".setting")]


def archive_files(store_dir: pathlib.Path, *, exclude: pathlib.Path | None = None) -> list[pathlib.Path]:
    """アーカイブへ含めるファイルを列挙する。``Templates`` ディレクトリの中にある出力先(``exclude``)は含めない。"""
    files = _iter_edit_files(store_dir)
    if exclude is None:
        return files
    exclude_resolved = exclude.resolve()
    return [p for p in files if p.resolve() != exclude_resolved]


def write_archive(store_dir: pathlib.Path, dest: pathlib.Path) -> None:
    """``store_dir/Edit`` 以下を ``dest`` へ ZIP として書く。

    同じ内容から同じバイト列を得るため、更新時刻とパーミッションは持ち込まず日時を固定する。
    """
    files = archive_files(store_dir, exclude=dest)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = path.relative_to(store_dir).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
