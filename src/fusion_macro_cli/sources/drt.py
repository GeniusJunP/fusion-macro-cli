"""``.drt`` / ``.drp`` からのコンポジションの取り出し。

``SeqContainer/<uuid>.xml`` の ``<CompositionBA>`` 要素は次の形式である。

    hex 文字列 -> バイト列
      [BE uint32 展開後サイズ][zlib 圧縮データ]
        -> [BE uint32 version][BE uint32 要素数]
           {[BE uint32 キーの長さ][UTF-16BE のキー][BE uint32 型][1 バイトのフラグ][値]}

``<n>_data``(型12)が ``.comp`` の本体であり、NUL で終端される。平文部が ``Compressed = true, }`` で終わる場合は、
続く ``[LE uint32 展開後サイズ][zlib 圧縮データ]`` が ``Tools`` の中身であり、マーカーの位置へ ``Tools = <中身> }`` を差し込んで復元する。
タイムラインへ挿入したコンポジションは ``<n>_name`` が常に ``Composition 1`` となるため、出力名には ``CustomData.TEMPLATE_ID`` を優先する。
"""

from __future__ import annotations

import dataclasses
import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

from fusion_macro_cli import document

#: ファイル名として使えない文字。制御文字も対象。
_ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_filename(name: str) -> str:
    """ファイル名に使えない文字と制御文字を ``_`` へ置換する。"""
    return _ILLEGAL_FILENAME_CHARS_RE.sub("_", name)

#: ``<CompositionBA>`` の開始タグ(属性付きを含む)。``.drp`` は XML の名前として不正なタグ
#: (``<ListMgt::LmVersionTable>``)を含み標準の解析器が文書全体を拒むため、XML として解析せずバイト列で照合する。
_OPEN_TAG_RE = re.compile(rb"<CompositionBA(?:\s[^>]*)?>")
_CLOSE_TAG = b"</CompositionBA>"

#: 平文部の末尾がこのマーカーであれば、続けて圧縮された Tools が来る。マーカーの ``}`` は Composition を閉じる括弧である。
_COMPRESSED_MARKER = "Compressed = true, }"

# BLOB 中の値の型符号。
_TYPE_UINT32 = 2
_TYPE_STRING = 10
_TYPE_BYTES = (11, 12)


@dataclasses.dataclass(frozen=True)
class ExtractedComposition:
    """``.drt`` / ``.drp`` から取り出した1つのコンポジション。

    ``sequence`` はファイル内の出現順の通し番号である。復号に失敗した場合は ``text`` と ``derived_name`` が ``None`` となり、``reason`` に理由が入る。
    """

    sequence: int
    name: str
    source_xml: str
    text: str | None
    derived_name: str | None
    reason: str | None


def extract(path: Path | str) -> list[ExtractedComposition]:
    """``.drt`` / ``.drp`` からコンポジションの列を取り出す。

    失敗は要素単位とコンポジション単位で切り分ける。終了タグが見つからない場合はメンバー全体を1件の失敗とし、
    BLOB のキー構造が壊れている場合は要素全体を1件の失敗とする。個々の ``<n>`` の失敗は他の ``<n>`` の抽出を止めない。
    """
    zip_path = Path(path)
    result: list[ExtractedComposition] = []
    sequence = 1
    with zipfile.ZipFile(zip_path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name.startswith("SeqContainer/") and name.endswith(".xml")
        ]
        for member in members:
            raw = archive.read(member)
            pos = 0
            while True:
                open_match = _OPEN_TAG_RE.search(raw, pos)
                if open_match is None:
                    break
                start = open_match.end()
                end = raw.find(_CLOSE_TAG, start)
                if end == -1:
                    result.append(
                        ExtractedComposition(
                            sequence=sequence,
                            name="",
                            source_xml=member,
                            text=None,
                            derived_name=None,
                            reason="found <CompositionBA> opening tag with no matching "
                            "</CompositionBA> closing tag",
                        )
                    )
                    sequence += 1
                    break
                hex_bytes = raw[start:end].strip()
                sequence = _extract_one_element(hex_bytes, member, sequence, result)
                pos = end + len(_CLOSE_TAG)
    return result


def _extract_one_element(
    hex_bytes: bytes, source_xml: str, sequence: int, result: list[ExtractedComposition]
) -> int:
    """1つの ``<CompositionBA>`` 要素を復号して ``result`` へ追記し、次の ``sequence`` を返す。"""
    try:
        hex_text = hex_bytes.decode("ascii")
        blob = _parse_blob(hex_text)
    except Exception as exc:  # noqa: BLE001 - 復号失敗を理由として記録するため捕捉する
        result.append(
            ExtractedComposition(
                sequence=sequence,
                name="",
                source_xml=source_xml,
                text=None,
                derived_name=None,
                reason=f"failed to decode composition blob: {exc}",
            )
        )
        return sequence + 1

    count = blob["count"]
    for n in range(count):
        name = blob.get(f"{n}_name", "")
        if not isinstance(name, str):
            name = ""
        text: str | None
        derived_name: str | None
        reason: str | None
        try:
            data = blob[f"{n}_data"]
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError(f"{n}_data has an unexpected type")
            text = _restore_text(bytes(data))
            derived_name = _derive_name(text, name)
            reason = None
        except Exception as exc:  # noqa: BLE001 - この <n> の失敗として記録する
            text = None
            derived_name = None
            reason = f"failed to decode composition {n}: {exc}"
        result.append(
            ExtractedComposition(
                sequence=sequence,
                name=name,
                source_xml=source_xml,
                text=text,
                derived_name=derived_name,
                reason=reason,
            )
        )
        sequence += 1
    return sequence


def _read_exact(data: bytes, pos: int, length: int, what: str) -> bytes:
    """``data[pos:pos+length]`` を取り出す。長さが不足する場合はサイレントに切り詰めず、例外を送出する。"""
    chunk = data[pos : pos + length]
    if len(chunk) != length:
        raise ValueError(f"truncated {what}: expected {length} bytes, got {len(chunk)}")
    return chunk


def _parse_blob(hex_text: str) -> dict[str, Any]:
    """``<CompositionBA>`` の hex 文字列を、キーから値への辞書へ復号する。値は型2が ``int``、型10が ``str``、型11と12が ``bytes`` である。"""
    raw = bytes.fromhex(hex_text)
    expanded_size = struct.unpack_from(">I", raw, 0)[0]
    data = zlib.decompress(raw[4:])
    if len(data) != expanded_size:
        raise ValueError(
            f"blob size mismatch: expected {expanded_size}, got {len(data)}"
        )

    pos = 0
    _version, element_count = struct.unpack_from(">II", data, pos)
    pos += 8

    entries: dict[str, Any] = {}
    for _ in range(element_count):
        key_len = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        key = _read_exact(data, pos, key_len, "blob key").decode("utf-16-be")
        pos += key_len

        value_type = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        # 1バイトのフラグは意味が不明であり、読み飛ばす。
        pos += 1

        if value_type == _TYPE_UINT32:
            value: Any = struct.unpack_from(">I", data, pos)[0]
            pos += 4
        elif value_type == _TYPE_STRING:
            value_len = struct.unpack_from(">I", data, pos)[0]
            pos += 4
            value = _read_exact(data, pos, value_len, f"string value of {key!r}").decode(
                "utf-16-be"
            )
            pos += value_len
        elif value_type in _TYPE_BYTES:
            value_len = struct.unpack_from(">I", data, pos)[0]
            pos += 4
            value = _read_exact(data, pos, value_len, f"bytes value of {key!r}")
            pos += value_len
        else:
            raise ValueError(f"unknown value type {value_type} for key {key!r}")

        entries[key] = value

    if "count" not in entries or not isinstance(entries["count"], int):
        raise ValueError("blob has no integer 'count' key")
    return entries


def _restore_text(data: bytes) -> str:
    """``<n>_data`` のバイト列から ``.comp`` テキストを復元する。圧縮された ``Tools`` はマーカーの位置へ差し込む。"""
    nul_index = data.find(b"\x00")
    if nul_index == -1:
        raise ValueError("composition data has no NUL terminator")
    plain = data[:nul_index].decode("utf-8")

    if _COMPRESSED_MARKER not in plain:
        return plain
    if not plain.endswith(_COMPRESSED_MARKER):
        # マーカーが平文部の末尾以外にある形は復元の手順が定まらないため、失敗とする。
        raise ValueError(
            "found 'Compressed = true, }' but not at the end of the plain part"
        )

    rest = data[nul_index + 1 :]
    if len(rest) < 4:
        raise ValueError("missing compressed Tools payload after the NUL terminator")
    expanded_size = struct.unpack_from("<I", rest, 0)[0]
    tools_bytes = zlib.decompress(rest[4:])
    if len(tools_bytes) != expanded_size:
        raise ValueError(
            f"Tools payload size mismatch: expected {expanded_size}, got {len(tools_bytes)}"
        )
    # 展開した Tools の末尾に NUL が1バイト付く。文字列の一部ではないため除く。
    if tools_bytes.endswith(b"\x00"):
        tools_bytes = tools_bytes[:-1]
    tools_text = tools_bytes.decode("utf-8")

    head = plain[: -len(_COMPRESSED_MARKER)]
    return f"{head}Tools = {tools_text} }}"


def _derive_name(text: str, blob_name: str) -> str:
    """出力名の元を、``CustomData.TEMPLATE_ID`` の末尾のセグメント、無ければ BLOB の ``<n>_name`` から決める。"""
    parsed = document.parse(text)
    custom_data = parsed.root.get("CustomData")
    template_id: str | None = None
    if isinstance(custom_data, document.Table):
        node = custom_data.get("TEMPLATE_ID")
        if isinstance(node, document.String):
            template_id = node.value
    if template_id:
        return template_id.rstrip("/").rsplit("/", 1)[-1]
    return blob_name


def output_filename(composition: ExtractedComposition) -> str:
    """出力ファイル名(``<4桁連番>_<名前>.comp``)を決める。復号に失敗したコンポジションでは ``ValueError`` を送出する。"""
    if composition.text is None or composition.derived_name is None:
        raise ValueError(
            f"composition {composition.sequence} failed to decode "
            f"({composition.reason}); it has no output filename"
        )
    safe_name = safe_filename(composition.derived_name)
    return f"{composition.sequence:04d}_{safe_name}.comp"
