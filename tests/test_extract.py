"""``fusion_macro_cli.sources.drt`` の検査。

単独実行できる(``python3 tests/test_extract.py``)。すべて合成データ・自作データ
(``tests/data/sample.comp``)で完結するため、終了コードは失敗が1件でもあれば1、
無ければ0を返す(スキップは無い)。

``.drt`` のフィクスチャには依存しない。代わりに
``tests/data/sample.comp`` を仕様の手順(zlib 圧縮、BLOB のキー構造、``<CompositionBA>``
を含む XML、ZIP)で**逆方向に組み立てた** ``.drt`` を、このファイル自身が作る。
組み立ての実装は ``sources/drt.py`` の復号手順の検証専用であり、
``sources/drt.py`` 自体には持ち込まない(本体が独自にテストデータを生成できて
しまうと、本体の実装誤りとテストの生成誤りが相殺し合う恐れがあるため)。
"""

from __future__ import annotations

import dataclasses
import pathlib
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.document as document  # noqa: E402
from fusion_macro_cli.sources import drt  # noqa: E402

SKIP = "skip"
PASS = "pass"

SAMPLE_COMP = HERE / "data" / "sample.comp"

# BLOB 中の値の型符号(sources/drt.py の _TYPE_* と同じ値)。テスト側の組み立てと
# 本体の復号を独立させるため、定数を import せずここでも定義する。
_TYPE_UINT32 = 2
_TYPE_STRING = 10
_TYPE_BYTES = 12


# --------------------------------------------------------------------------
# 逆方向の組み立て(テスト専用)
# --------------------------------------------------------------------------


def _force_inline(node: document.Node) -> document.Node:
    """テーブルを再帰的に1行(``inline``)へ強制する。

    ``.drt`` の平文コンポジションは全体が1行に詰まっている(``document.py`` の
    78文字規則とは無関係に、Fusion が保存時にそう書き出す)。この性質を
    ``document.format_compact`` ではなく専用の再帰で再現する
    (``format_compact`` は78文字を超えると複数行のままにするため使えない)。
    """
    if isinstance(node, document.Table):
        new_elements = [document.Element(el.key, _force_inline(el.value)) for el in node]
        return document.Table(node.type_name, new_elements, True, node.trailing_comma)
    return node


def _dump_inline(table: document.Table) -> str:
    inlined = _force_inline(table)
    return document.dump(document.Document(root=inlined, indent="", trailing_newline=False))


def _normalize_for_comparison(doc: document.Document) -> str:
    """比較用に、行の折り方(``format_compact``)とコンマの有無を揃えて書き出す。

    仕様の実測(「``.drt`` からの取り出し」)が復号結果と ``comp.Save()`` の
    比較を「行の折り方とコンマの有無を揃えた上での比較」と明記している通り、
    末尾コンマの有無は書式であって構造の差ではない。復元されたテキストは
    ``Tools`` が Composition の最後のフィールドとして追加されるため
    (``_restore_text`` の実装)末尾コンマを持たないが、手書きの ``sample.comp``
    は Fusion の慣例に合わせて持つ。この差を無視するため、全テーブルの
    ``trailing_comma`` を真に揃えてから ``format_compact`` を通す。
    """

    def _force_trailing_comma(node: document.Node) -> document.Node:
        if isinstance(node, document.Table):
            new_elements = [
                document.Element(el.key, _force_trailing_comma(el.value)) for el in node
            ]
            return document.Table(node.type_name, new_elements, node.inline, True)
        return node

    normalized = document.Document(
        root=_force_trailing_comma(doc.root), indent=doc.indent, trailing_newline=False
    )
    return document.dump(document.format_compact(normalized))


def _build_n_data(comp_text: str) -> bytes:
    """``.comp`` テキストから ``<n>_data`` のバイト列を組み立てる。

    仕様の復号手順(「extract」節)を逆にたどる。``Tools`` テーブルの中身を
    zlib 圧縮し、平文部を ``Compressed = true, }`` で終わる形に作り替え、
    両者を連結する。
    """
    doc = document.parse(comp_text)
    tools_table = doc.root.get("Tools")
    assert isinstance(tools_table, document.Table)

    # 圧縮対象は Tools テーブルの中身("{ ... }" の文字列表現そのもの)。
    tools_text = _dump_inline(tools_table)
    tools_bytes = tools_text.encode("utf-8") + b"\x00"  # 展開後の末尾に付く1バイトの NUL
    compressed_tools = zlib.compress(tools_bytes)
    tools_payload = struct.pack("<I", len(tools_bytes)) + compressed_tools

    # 平文部: Tools を Compressed = true に差し替えたテーブルを1行で書き出す。
    head_elements = [
        el for el in doc.root.elements if el.key is None or el.key.name != "Tools"
    ]
    head_elements.append(document.Element(document.Key("ident", "Compressed"), document.Boolean(True)))
    head_table = document.Table("Composition", head_elements, True, True)
    plain = _dump_inline(head_table)
    assert plain.endswith("Compressed = true, }"), plain[-40:]

    return plain.encode("utf-8") + b"\x00" + tools_payload


def _build_blob_hex(entries: list[tuple[str, int, object]]) -> str:
    """BLOB のキー構造(``count`` 以下のキーの並び)を ``entries`` から組み立て、hex文字列で返す。

    ``entries`` は ``(キー, 値の型符号, 値)`` の列。値の型は
    :data:`_TYPE_UINT32`/``_TYPE_STRING``/``_TYPE_BYTES`` のいずれか。
    """
    body = struct.pack(">II", 1, len(entries))  # version, 要素数
    for key, value_type, value in entries:
        key_bytes = key.encode("utf-16-be")
        body += struct.pack(">I", len(key_bytes)) + key_bytes
        body += struct.pack(">I", value_type)
        body += b"\x00"  # 1バイトのフラグ(意味を定めない)
        if value_type == _TYPE_UINT32:
            body += struct.pack(">I", value)
        elif value_type == _TYPE_STRING:
            value_bytes = value.encode("utf-16-be")
            body += struct.pack(">I", len(value_bytes)) + value_bytes
        elif value_type == _TYPE_BYTES:
            body += struct.pack(">I", len(value)) + value
        else:
            raise ValueError(value_type)

    compressed_body = zlib.compress(body)
    blob = struct.pack(">I", len(body)) + compressed_body
    return blob.hex()


def _write_seq_container_xml(path: pathlib.Path, hex_text: str) -> None:
    """``hex_text`` を ``<CompositionBA>`` に埋め込んだ ``.drt`` 相当の ZIP を書く。"""
    root = ET.Element("Root")
    ba = ET.SubElement(root, "CompositionBA")
    ba.text = hex_text
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("SeqContainer/00000000-0000-0000-0000-000000000000.xml", xml_bytes)


def _write_drt(path: pathlib.Path, comp_text: str) -> None:
    """``comp_text`` を由来する ``.comp`` テキストとして、``.drt`` 相当の ZIP を書く。"""
    n_data = _build_n_data(comp_text)
    entries = [
        ("count", _TYPE_UINT32, 1),
        ("0_name", _TYPE_STRING, "Composition 1"),
        ("0_mediaIdList", _TYPE_STRING, ""),
        ("0_data", _TYPE_BYTES, n_data),
    ]
    _write_seq_container_xml(path, _build_blob_hex(entries))


# --------------------------------------------------------------------------
# 取り出し(sample.comp との往復)
# --------------------------------------------------------------------------


def test_drt_roundtrip_from_sample_comp():
    """``sample.comp`` を仕様の手順で ``.drt`` へ組み立て、``drt.extract`` で
    復号した結果が元の ``sample.comp`` と一致することを確認する。

    比較は行の折り方(``format_compact``)とコンマの有無を揃えた上で行う
    (仕様の実測と同じ基準。「取り出し」節を見よ)。
    """
    comp_text = SAMPLE_COMP.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        drt_path = pathlib.Path(tmp) / "sample.drt"
        _write_drt(drt_path, comp_text)

        compositions = drt.extract(drt_path)
        assert len(compositions) == 1, compositions
        comp = compositions[0]
        assert comp.reason is None, comp.reason
        assert comp.text is not None

        # CustomData.TEMPLATE_ID の末尾セグメントから名前が導かれること。
        assert comp.derived_name == "sample", comp.derived_name
        assert drt.output_filename(comp) == "0001_sample.comp"

        original = _normalize_for_comparison(document.parse(comp_text))
        restored = _normalize_for_comparison(document.parse(comp.text))
        assert restored == original

    return PASS


def test_drt_extracts_from_xml_with_invalid_tag_names():
    """``<ListMgt::LmVersionTable>`` のような、XML の名前として不正なタグ
    (コロンを2つ含む)が周囲に混在していても、``<CompositionBA>`` の中身を
    取り出せることを確認する(仕様「extract」: XML として解析しない)。
    標準の XML パーサ(``xml.etree``)はこの文書を拒む(``ルークスHWS_007.drp``
    で292箇所実測)ため、まずそのことをこのテスト自身で確かめる
    (フィクスチャが無意味でないことの保証)。
    """
    comp_text = SAMPLE_COMP.read_text(encoding="utf-8")
    n_data = _build_n_data(comp_text)
    entries = [
        ("count", _TYPE_UINT32, 1),
        ("0_name", _TYPE_STRING, "Composition 1"),
        ("0_mediaIdList", _TYPE_STRING, ""),
        ("0_data", _TYPE_BYTES, n_data),
    ]
    hex_text = _build_blob_hex(entries)

    xml_bytes = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Root><ListMgt::LmVersionTable a="1"/><CompositionBA>'
        + hex_text.encode("ascii")
        + b"</CompositionBA></Root>"
    )
    try:
        ET.fromstring(xml_bytes)
    except ET.ParseError:
        pass
    else:
        raise AssertionError(
            "fixture must be rejected by xml.etree for this test to be meaningful"
        )

    with tempfile.TemporaryDirectory() as tmp:
        drt_path = pathlib.Path(tmp) / "malformed.drt"
        with zipfile.ZipFile(drt_path, "w") as zf:
            zf.writestr("SeqContainer/00000000-0000-0000-0000-000000000000.xml", xml_bytes)

        compositions = drt.extract(drt_path)
        assert len(compositions) == 1, compositions
        comp = compositions[0]
        assert comp.reason is None, comp.reason
        assert comp.derived_name == "sample"

    return PASS


def test_drt_records_one_failure_when_closing_tag_is_missing():
    """開始タグに対応する終了タグが無い場合、そのメンバー(``SeqContainer``
    の XML)を表す1件の失敗として記録する(仕様「extract」)。"""
    comp_text = SAMPLE_COMP.read_text(encoding="utf-8")
    n_data = _build_n_data(comp_text)
    entries = [
        ("count", _TYPE_UINT32, 1),
        ("0_name", _TYPE_STRING, "Composition 1"),
        ("0_mediaIdList", _TYPE_STRING, ""),
        ("0_data", _TYPE_BYTES, n_data),
    ]
    hex_text = _build_blob_hex(entries)
    xml_bytes = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<Root><CompositionBA>" + hex_text.encode("ascii") + b"</Root>"  # 終了タグ無し
    )

    with tempfile.TemporaryDirectory() as tmp:
        drt_path = pathlib.Path(tmp) / "unterminated.drt"
        with zipfile.ZipFile(drt_path, "w") as zf:
            zf.writestr("SeqContainer/00000000-0000-0000-0000-000000000000.xml", xml_bytes)

        compositions = drt.extract(drt_path)
        assert len(compositions) == 1, compositions
        assert compositions[0].text is None
        assert compositions[0].reason is not None and "closing tag" in compositions[0].reason
    return PASS


def test_drt_non_ascii_body_is_a_single_element_failure():
    """``<CompositionBA>`` の中身(本来 hex 文字列)が ASCII に変換できない
    場合、その要素だけの失敗として記録し、アーカイブ全体の復号を止めない。
    """
    comp_text = SAMPLE_COMP.read_text(encoding="utf-8")
    n_data = _build_n_data(comp_text)
    entries = [
        ("count", _TYPE_UINT32, 1),
        ("0_name", _TYPE_STRING, "Composition 1"),
        ("0_mediaIdList", _TYPE_STRING, ""),
        ("0_data", _TYPE_BYTES, n_data),
    ]
    good_hex = _build_blob_hex(entries)

    with tempfile.TemporaryDirectory() as tmp:
        drt_path = pathlib.Path(tmp) / "mixed.drt"
        root = ET.Element("Root")
        bad = ET.SubElement(root, "CompositionBA")
        bad.text = "not-hex-éé"  # 非 ASCII
        good = ET.SubElement(root, "CompositionBA")
        good.text = good_hex
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(drt_path, "w") as zf:
            zf.writestr("SeqContainer/00000000-0000-0000-0000-000000000000.xml", xml_bytes)

        compositions = drt.extract(drt_path)
        assert len(compositions) == 2, compositions
        assert compositions[0].text is None
        assert compositions[0].reason is not None
        assert compositions[1].reason is None
        assert compositions[1].derived_name == "sample"

    return PASS


def test_drt_multiple_compositions_in_one_blob():
    """``count`` が2以上の場合、``<n>`` ごとに1件の :class:`ExtractedComposition`
    が得られることを確認する(仕様の「取り出し/条件」)。
    """
    comp_text = SAMPLE_COMP.read_text(encoding="utf-8")
    n_data = _build_n_data(comp_text)
    entries = [
        ("count", _TYPE_UINT32, 2),
        ("0_name", _TYPE_STRING, "Composition 1"),
        ("0_mediaIdList", _TYPE_STRING, ""),
        ("0_data", _TYPE_BYTES, n_data),
        ("1_name", _TYPE_STRING, "Composition 2"),
        ("1_mediaIdList", _TYPE_STRING, ""),
        ("1_data", _TYPE_BYTES, n_data),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        drt_path = pathlib.Path(tmp) / "sample_multi.drt"
        _write_seq_container_xml(drt_path, _build_blob_hex(entries))

        compositions = drt.extract(drt_path)
        assert len(compositions) == 2, compositions
        assert [c.sequence for c in compositions] == [1, 2]
        assert all(c.reason is None for c in compositions)
        assert all(c.derived_name == "sample" for c in compositions)

    return PASS


# --------------------------------------------------------------------------
# 出力ファイル名
# --------------------------------------------------------------------------


def test_output_filename_format():
    """出力ファイル名が ``<4桁連番>_<名前>.comp`` の形で、名前が
    ``CustomData.TEMPLATE_ID`` の末尾のセグメントから取られることを確認する。

    フィクスチャに依存せず、抽出結果を模した :class:`drt.ExtractedComposition`
    を直接組み立てて検査する。
    """
    comp = drt.ExtractedComposition(
        sequence=3,
        name="Composition 1",
        source_xml="SeqContainer/dummy.xml",
        text="Composition { Tools = { } }",
        derived_name="タイトル_青",
        reason=None,
    )
    assert drt.output_filename(comp) == "0003_タイトル_青.comp"

    # TEMPLATE_ID を持たず BLOB の <n>_name を使う場合も同じ形になる。
    comp_fallback = dataclasses.replace(comp, derived_name="Composition 1")
    assert drt.output_filename(comp_fallback) == "0003_Composition 1.comp"

    # 4桁ゼロ埋めであること(2桁の連番でも4桁になる)。
    comp_small = dataclasses.replace(comp, sequence=7)
    assert drt.output_filename(comp_small).startswith("0007_")

    # 5桁以上の連番でも4桁未満に切り詰めない(単純な :04d の仕様として確認する)。
    comp_large = dataclasses.replace(comp, sequence=12345)
    assert drt.output_filename(comp_large).startswith("12345_")

    return PASS


def test_output_filename_sanitizes_illegal_characters():
    """名前に含まれるファイル名として使えない文字が ``_`` に置換されることを確認する。"""
    comp = drt.ExtractedComposition(
        sequence=1,
        name="Composition 1",
        source_xml="SeqContainer/dummy.xml",
        text="Composition { Tools = { } }",
        derived_name='a/b\\c:d*e?f"g<h>i|j',
        reason=None,
    )
    filename = drt.output_filename(comp)
    assert filename == "0001_a_b_c_d_e_f_g_h_i_j.comp", filename

    comp_control = dataclasses.replace(comp, derived_name="x\ty\nz")
    filename_control = drt.output_filename(comp_control)
    assert filename_control == "0001_x_y_z.comp", filename_control
    return PASS


def test_output_filename_rejects_failed_composition():
    """復号に失敗したコンポジション(``text`` が ``None``)にはファイル名を
    決められず、``ValueError`` を送出することを確認する。"""
    comp = drt.ExtractedComposition(
        sequence=1,
        name="",
        source_xml="SeqContainer/dummy.xml",
        text=None,
        derived_name=None,
        reason="failed to decode composition blob: bad data",
    )
    try:
        drt.output_filename(comp)
    except ValueError:
        pass
    else:
        raise AssertionError("output_filename must raise ValueError for a failed composition")
    return PASS


def test_output_filename_distinguishes_duplicate_names():
    """同じ名前が複数ある場合、連番で区別されることを確認する。"""
    first = drt.ExtractedComposition(
        sequence=1,
        name="Composition 1",
        source_xml="SeqContainer/dummy.xml",
        text="Composition { Tools = { } }",
        derived_name="タイトル",
        reason=None,
    )
    second = dataclasses.replace(first, sequence=2)
    assert drt.output_filename(first) != drt.output_filename(second)
    assert drt.output_filename(first) == "0001_タイトル.comp"
    assert drt.output_filename(second) == "0002_タイトル.comp"
    return PASS


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------

TESTS = [
    ("test_drt_roundtrip_from_sample_comp", test_drt_roundtrip_from_sample_comp),
    (
        "test_drt_extracts_from_xml_with_invalid_tag_names",
        test_drt_extracts_from_xml_with_invalid_tag_names,
    ),
    (
        "test_drt_records_one_failure_when_closing_tag_is_missing",
        test_drt_records_one_failure_when_closing_tag_is_missing,
    ),
    (
        "test_drt_non_ascii_body_is_a_single_element_failure",
        test_drt_non_ascii_body_is_a_single_element_failure,
    ),
    ("test_drt_multiple_compositions_in_one_blob", test_drt_multiple_compositions_in_one_blob),
    ("test_output_filename_format", test_output_filename_format),
    ("test_output_filename_sanitizes_illegal_characters", test_output_filename_sanitizes_illegal_characters),
    ("test_output_filename_rejects_failed_composition", test_output_filename_rejects_failed_composition),
    ("test_output_filename_distinguishes_duplicate_names", test_output_filename_distinguishes_duplicate_names),
]


def main() -> int:
    any_failure = False
    any_skip = False
    for name, fn in TESTS:
        print(f"--- {name}")
        try:
            outcome = fn()
        except AssertionError as exc:
            print(f"    FAILED: {exc}")
            any_failure = True
            continue
        except Exception as exc:  # 想定外の例外も失敗として扱う。無害化しない。
            print(f"    ERROR: {exc!r}")
            any_failure = True
            continue
        if outcome == SKIP:
            print("    skipped")
            any_skip = True
        else:
            print("    passed")

    if any_failure:
        return 1
    if any_skip:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
