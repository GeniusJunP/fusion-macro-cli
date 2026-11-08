"""システムにインストールされたフォントの列挙と、Text+ の ``Font``/``Style`` との照合。

フォントのディレクトリの列挙は :func:`font_dirs` と :func:`font_files`、ファイルの解析は
:mod:`fusion_macro_cli.sfnt`、照合は :class:`InstalledFonts` が行う。
関数はキャッシュを持たないため、同じ実行の中で何度も照合する呼び出し側が :func:`installed_fonts` の結果を持ち回る。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import sys
from typing import Final, Iterable, Iterator

from fusion_macro_cli import sfnt

__all__ = ["Face", "InstalledFonts", "font_dirs", "font_files", "faces_of", "scan", "installed_fonts"]

_FONT_SUFFIXES: Final[frozenset[str]] = frozenset({".ttf", ".otf", ".ttc", ".otc"})


def font_dirs() -> tuple[pathlib.Path, ...]:
    """実行中の OS のフォントのディレクトリを返す。存在しないディレクトリも含む。"""
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return (
            pathlib.Path("/System/Library/Fonts"),
            pathlib.Path("/Library/Fonts"),
            home / "Library" / "Fonts",
        )
    if sys.platform == "win32":
        # TODO: Morisawa Desktop Manager が Windows でフォントを置くディレクトリは未確認。
        #   この2か所以外に置き、レジストリだけで登録する場合は、ここに加える。
        windir = pathlib.Path(os.environ.get("WINDIR", r"C:\Windows"))
        dirs = [windir / "Fonts"]
        if "LOCALAPPDATA" in os.environ:
            dirs.append(pathlib.Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
        return tuple(dirs)
    # 仕様補完: Linux 版の Resolve が fontconfig の既定のディレクトリを読むと仮定する。Resolve での確認はしていない。
    return (
        pathlib.Path("/usr/share/fonts"),
        pathlib.Path("/usr/local/share/fonts"),
        home / ".local" / "share" / "fonts",
        home / ".fonts",
    )


def font_files(dirs: Iterable[pathlib.Path]) -> Iterator[pathlib.Path]:
    """ディレクトリの下(サブディレクトリを含む)の sfnt のファイルを返す。存在しないディレクトリは飛ばす。"""
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() in _FONT_SUFFIXES and path.is_file():
                yield path


@dataclasses.dataclass(frozen=True)
class Face:
    """Text+ の ``Font`` と ``Style`` に書く値の組。"""

    family: str
    style: str


def faces_of(names: sfnt.FaceNames) -> set[Face]:
    """1つのフェイスを、Text+ の ``Font``/``Style`` として照合できる組に変換する。

    Text+ は ``Font`` に Typographic Family(name ID 16)、``Style`` に Typographic Subfamily(17)を書く
    (``A P-OTF Ryumin Pr6N``/``M-KL``、``Hiragino Sans``/``W8``)。16/17 を持たないフォントは
    Family/Subfamily(1/2)を書く(``Reggae One``/``Regular``)。可変フォントは名前付きインスタンスを
    スタイルとして書く(``Noto Sans JP``/``Black``)。
    仕様補完: 16/17 を持つフォントでも 1/2 の組を加える。Text+ が 1/2 を書く例は見つかっていないが、
    加えても別のフォントと誤って一致することは無いため。
    """
    faces: set[Face] = set()
    family = names.typographic_family or names.family
    style = names.typographic_subfamily or names.subfamily
    if family and style:
        faces.add(Face(family, style))
    if names.family and names.subfamily:
        faces.add(Face(names.family, names.subfamily))
    if family:
        faces.update(Face(family, instance) for instance in names.instances)
    return faces


def _key(name: str) -> str:
    return name.strip().casefold()


@dataclasses.dataclass(frozen=True)
class InstalledFonts:
    """走査の結果。``unreadable`` は sfnt として読めなかったファイルと理由である。"""

    faces: frozenset[Face]
    unreadable: tuple[tuple[pathlib.Path, str], ...] = ()
    _styles_by_family: dict[str, frozenset[str]] = dataclasses.field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        keys: dict[str, set[str]] = {}
        for face in self.faces:
            keys.setdefault(_key(face.family), set()).add(_key(face.style))
        # frozen のため __setattr__ を経由できない。
        object.__setattr__(self, "_styles_by_family", {k: frozenset(v) for k, v in keys.items()})

    def has(self, family: str, style: str | None) -> bool:
        """``Font``/``Style`` の組がインストールされているかを返す。大文字と小文字は区別しない。

        ``style`` が ``None`` であればファミリーだけで判定する。
        """
        styles = self._styles_by_family.get(_key(family))
        if styles is None:
            return False
        return style is None or _key(style) in styles


def scan(dirs: Iterable[pathlib.Path]) -> InstalledFonts:
    """ディレクトリの下のフォントを読む。読めないファイルは飛ばし、``InstalledFonts.unreadable`` に記録する。"""
    faces: set[Face] = set()
    unreadable: list[tuple[pathlib.Path, str]] = []
    for path in font_files(dirs):
        try:
            for names in sfnt.read_faces(path.read_bytes()):
                faces |= faces_of(names)
        except (OSError, sfnt.SfntError) as exc:
            unreadable.append((path, str(exc)))
    return InstalledFonts(frozenset(faces), tuple(unreadable))


def installed_fonts() -> InstalledFonts:
    """実行中の OS のフォントのディレクトリを走査する。呼ぶたびに読み直す。"""
    return scan(font_dirs())
