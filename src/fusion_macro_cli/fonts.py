"""システムにインストールされたフォントのファミリー名の列挙。

標準ライブラリにフォント列挙の API が無いため、sfnt(TrueType/OpenType)の ``name`` テーブルを直接読む。
"""

from __future__ import annotations

import functools
import pathlib
import struct
from typing import Final

__all__ = ["installed_font_families"]

#: macOS のフォントのディレクトリ。仕様補完: Windows のフォントのディレクトリは扱わない。
_FONT_DIRS: Final[tuple[pathlib.Path, ...]] = (
    pathlib.Path("/System/Library/Fonts"),
    pathlib.Path("/Library/Fonts"),
    pathlib.Path.home() / "Library" / "Fonts",
)


def _decode_name_record(data: bytes, platform_id: int, encoding_id: int) -> str | None:
    if platform_id in (0, 3):  # Unicode, Windows
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            return None
    if platform_id == 1:  # Macintosh。簡易実装: MacRoman 以外のエンコーディングは扱わない
        try:
            return data.decode("mac_roman")
        except UnicodeDecodeError:
            return None
    return None


def _read_name_table_families(font_bytes: bytes, table_offset: int) -> set[str]:
    families: set[str] = set()
    _fmt, count, string_offset = struct.unpack_from(">HHH", font_bytes, table_offset)
    record_base = table_offset + 6
    storage_base = table_offset + string_offset
    for i in range(count):
        rec_off = record_base + i * 12
        platform_id, encoding_id, _lang, name_id, length, offset = struct.unpack_from(
            ">HHHHHH", font_bytes, rec_off
        )
        if name_id not in (1, 16):  # Font Family / Typographic Family
            continue
        start = storage_base + offset
        raw = font_bytes[start : start + length]
        name = _decode_name_record(raw, platform_id, encoding_id)
        if name:
            families.add(name.strip())
    return families


def _sfnt_table_offset(font_bytes: bytes, base: int, tag: bytes) -> int | None:
    num_tables = struct.unpack_from(">H", font_bytes, base + 4)[0]
    record_base = base + 12
    for i in range(num_tables):
        rec_off = record_base + i * 16
        rec_tag, _checksum, offset, _length = struct.unpack_from(">4sIII", font_bytes, rec_off)
        if rec_tag == tag:
            return offset
    return None


def _read_font_family_names(path: pathlib.Path) -> set[str]:
    """TTF/OTF/TTC の ``name`` テーブルからファミリー名を読む。

    TTC は1ファイルに複数のファミリーを収める(ヒラギノ丸ゴの ProN と Pro)ため、全フェイスを読む。
    """
    data = path.read_bytes()
    if len(data) < 12:
        return set()
    tag = data[:4]
    if tag == b"ttcf":
        (num_fonts,) = struct.unpack_from(">I", data, 8)
        bases = list(struct.unpack_from(f">{num_fonts}I", data, 12))
    else:
        bases = [0]
    families: set[str] = set()
    for base in bases:
        name_offset = _sfnt_table_offset(data, base, b"name")
        if name_offset is not None:
            families |= _read_name_table_families(data, name_offset)
    return families


@functools.lru_cache(maxsize=1)
def installed_font_families() -> frozenset[str]:
    """システムにインストールされたフォントのファミリー名を小文字化して返す。"""
    families: set[str] = set()
    for directory in _FONT_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                for name in _read_font_family_names(path):
                    families.add(name.lower())
            except (struct.error, OSError, IndexError):
                # 簡易実装: 壊れた、または対応していない sfnt 構造のフォントは無視する。
                continue
    return frozenset(families)
