"""sfnt(TrueType/OpenType)のファイルから、フェイスごとの名前を読む。

ファイルのバイト列だけを扱い、ディレクトリの走査と名前の照合は :mod:`fusion_macro_cli.fonts` が行う。
標準ライブラリにフォントを読む API が無いため、``name`` テーブルと ``fvar`` テーブルを直接読む。
"""

from __future__ import annotations

import dataclasses
import struct

__all__ = ["SfntError", "FaceNames", "read_faces"]

_FAMILY = 1
_SUBFAMILY = 2
_TYPOGRAPHIC_FAMILY = 16
_TYPOGRAPHIC_SUBFAMILY = 17

_PLATFORM_UNICODE = 0
_PLATFORM_MACINTOSH = 1
_PLATFORM_WINDOWS = 3
_WINDOWS_EN_US = 0x0409
_MACINTOSH_ENGLISH = 0
_MACINTOSH_ROMAN = 0


class SfntError(ValueError):
    """sfnt として読めないバイト列であることを表す。"""


@dataclasses.dataclass(frozen=True)
class FaceNames:
    """1つのフェイスの英語の名前。``name`` テーブルに無い名前は ``None`` である。

    ``instances`` は可変フォントの名前付きインスタンス(``fvar``)のサブファミリー名である。
    可変フォントの ``name`` テーブルの name ID 2/17 は既定のインスタンスの名前だけを持つため、別に読む。
    """

    family: str | None
    subfamily: str | None
    typographic_family: str | None
    typographic_subfamily: str | None
    instances: tuple[str, ...] = ()


def read_faces(data: bytes) -> list[FaceNames]:
    """TTF/OTF/TTC/OTC のバイト列から、全フェイスの名前を返す。読めない構造であれば ``SfntError`` を送出する。

    TTC は1ファイルに複数のファミリーを収める(ヒラギノ丸ゴの ProN と Pro)ため、全フェイスを読む。
    """
    try:
        if data[:4] == b"ttcf":
            (num_fonts,) = struct.unpack_from(">I", data, 8)
            bases = struct.unpack_from(f">{num_fonts}I", data, 12)
        else:
            bases = (0,)
        return [_read_face(data, base) for base in bases]
    except (struct.error, IndexError, UnicodeDecodeError) as exc:
        raise SfntError(f"malformed sfnt: {exc}") from exc


def _read_face(data: bytes, base: int) -> FaceNames:
    tables = _table_offsets(data, base)
    if b"name" not in tables:
        raise SfntError(f"no name table in the face at offset {base}")
    names = _read_name_table(data, tables[b"name"])
    instances: tuple[str, ...] = ()
    if b"fvar" in tables:
        instances = tuple(
            names[name_id] for name_id in _fvar_instance_name_ids(data, tables[b"fvar"]) if name_id in names
        )
    return FaceNames(
        family=names.get(_FAMILY),
        subfamily=names.get(_SUBFAMILY),
        typographic_family=names.get(_TYPOGRAPHIC_FAMILY),
        typographic_subfamily=names.get(_TYPOGRAPHIC_SUBFAMILY),
        instances=instances,
    )


def _table_offsets(data: bytes, base: int) -> dict[bytes, int]:
    (num_tables,) = struct.unpack_from(">H", data, base + 4)
    offsets = {}
    for i in range(num_tables):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, base + 12 + i * 16)
        offsets[tag] = offset
    return offsets


def _record_priority(platform_id: int, encoding_id: int, language_id: int) -> int | None:
    """英語の名前として採る record の優先度(小さいほど優先)を返す。採らない record は ``None`` である。

    仕様補完: 名前は英語の record だけを採る。Edit/ の実データの ``Font`` と ``Style`` は、
    ヒラギノやモリサワのように日本語名を持つフォントでも英語名(``Hiragino Sans``、``A P-OTF Ryumin Pr6N``)であるため。
    """
    if platform_id == _PLATFORM_WINDOWS and language_id == _WINDOWS_EN_US:
        return 0
    if platform_id == _PLATFORM_UNICODE:
        return 1
    if platform_id == _PLATFORM_MACINTOSH and encoding_id == _MACINTOSH_ROMAN and language_id == _MACINTOSH_ENGLISH:
        return 2
    return None


def _decode(raw: bytes, platform_id: int) -> str:
    if platform_id == _PLATFORM_MACINTOSH:
        return raw.decode("mac_roman")
    return raw.decode("utf-16-be")


def _read_name_table(data: bytes, offset: int) -> dict[int, str]:
    """name ID ごとに、最も優先度の高い英語の名前を返す。"""
    _format, count, string_offset = struct.unpack_from(">HHH", data, offset)
    storage = offset + string_offset
    best: dict[int, tuple[int, str]] = {}
    for i in range(count):
        platform_id, encoding_id, language_id, name_id, length, str_offset = struct.unpack_from(
            ">HHHHHH", data, offset + 6 + i * 12
        )
        priority = _record_priority(platform_id, encoding_id, language_id)
        if priority is None or (name_id in best and best[name_id][0] <= priority):
            continue
        start = storage + str_offset
        if start + length > len(data):
            raise SfntError(f"name record {name_id} points past the end of the file")
        best[name_id] = (priority, _decode(data[start : start + length], platform_id).strip())
    return {name_id: name for name_id, (_priority, name) in best.items() if name}


def _fvar_instance_name_ids(data: bytes, offset: int) -> list[int]:
    _major, _minor, axes_offset, _reserved, axis_count, axis_size, instance_count, instance_size = (
        struct.unpack_from(">HHHHHHHH", data, offset)
    )
    first = offset + axes_offset + axis_count * axis_size
    return [struct.unpack_from(">H", data, first + i * instance_size)[0] for i in range(instance_count)]
