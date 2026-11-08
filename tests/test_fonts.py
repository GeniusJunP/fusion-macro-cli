"""``fusion_macro_cli.sfnt`` と ``fusion_macro_cli.fonts`` の検査。

``python3 tests/test_fonts.py`` で単独実行できる。終了コードは ``tests/run.py`` の規約に合わせる。
実機のフォントに依存しないよう、``name`` テーブルと ``fvar`` テーブルだけを持つ最小の sfnt を組み立てて読む。
"""

from __future__ import annotations

import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.fonts as fonts  # noqa: E402
import fusion_macro_cli.sfnt as sfnt  # noqa: E402

PASS = "pass"

_WINDOWS = (3, 1, 0x0409)
_WINDOWS_JA = (3, 1, 0x0411)
_MAC = (1, 0, 0)


def _name_table(records: list[tuple[tuple[int, int, int], int, str]]) -> bytes:
    """``((platform, encoding, language), name_id, text)`` の並びから ``name`` テーブルを作る。"""
    storage = b""
    entries = b""
    for (platform_id, encoding_id, language_id), name_id, text in records:
        raw = text.encode("mac_roman" if platform_id == 1 else "utf-16-be")
        entries += struct.pack(">HHHHHH", platform_id, encoding_id, language_id, name_id, len(raw), len(storage))
        storage += raw
    return struct.pack(">HHH", 0, len(records), 6 + len(entries)) + entries + storage


def _fvar_table(instance_name_ids: list[int]) -> bytes:
    """軸が1つで、インスタンスごとに subfamilyNameID を持つ ``fvar`` テーブルを作る。"""
    axis = struct.pack(">4siiiHH", b"wght", 100 << 16, 400 << 16, 900 << 16, 0, 256)
    instances = b"".join(struct.pack(">HHi", name_id, 0, 400 << 16) for name_id in instance_name_ids)
    return struct.pack(">HHHHHHHH", 1, 0, 16, 2, 1, 20, len(instance_name_ids), 8) + axis + instances


def _face(tables: dict[bytes, bytes], start: int = 0) -> tuple[bytes, bytes]:
    """テーブルディレクトリと、テーブルの本体を返す。``start`` はファイル内でのこのフェイスの先頭位置である。"""
    header_size = 12 + 16 * len(tables)
    directory = struct.pack(">IHHHH", 0x00010000, len(tables), 0, 0, 0)
    body = b""
    for tag, data in tables.items():
        directory += struct.pack(">4sIII", tag, 0, start + header_size + len(body), len(data))
        body += data
    return directory, body


def _sfnt(tables: dict[bytes, bytes]) -> bytes:
    directory, body = _face(tables)
    return directory + body


def _ttc(faces: list[dict[bytes, bytes]]) -> bytes:
    header_size = 12 + 4 * len(faces)
    offsets: list[int] = []
    blob = b""
    for tables in faces:
        offsets.append(header_size + len(blob))
        directory, body = _face(tables, header_size + len(blob))
        blob += directory + body
    return b"ttcf" + struct.pack(f">HHI{len(faces)}I", 1, 0, len(faces), *offsets) + blob


def test_reads_typographic_names_and_prefers_english():
    data = _sfnt(
        {
            b"name": _name_table(
                [
                    (_WINDOWS_JA, 16, "ヒラギノ角ゴシック"),
                    (_WINDOWS, 1, "Sample Sans W8"),
                    (_WINDOWS, 2, "Regular"),
                    (_WINDOWS, 16, "Sample Sans"),
                    (_WINDOWS, 17, "W8"),
                    (_MAC, 16, "Mac Name"),
                ]
            )
        }
    )
    [face] = sfnt.read_faces(data)
    assert face == sfnt.FaceNames("Sample Sans W8", "Regular", "Sample Sans", "W8"), face
    assert fonts.faces_of(face) == {fonts.Face("Sample Sans", "W8"), fonts.Face("Sample Sans W8", "Regular")}
    return PASS


def test_falls_back_to_family_and_subfamily():
    [face] = sfnt.read_faces(_sfnt({b"name": _name_table([(_MAC, 1, "Plain One"), (_MAC, 2, "Regular")])}))
    assert fonts.faces_of(face) == {fonts.Face("Plain One", "Regular")}, face
    return PASS


def test_reads_variable_font_instances():
    name = _name_table(
        [
            (_WINDOWS, 1, "Var Sans Thin"),
            (_WINDOWS, 2, "Regular"),
            (_WINDOWS, 16, "Var Sans"),
            (_WINDOWS, 17, "Thin"),
            (_WINDOWS, 256, "Black"),
            (_WINDOWS, 257, "ExtraLight"),
        ]
    )
    [face] = sfnt.read_faces(_sfnt({b"name": name, b"fvar": _fvar_table([256, 257])}))
    assert face.instances == ("Black", "ExtraLight"), face
    assert fonts.Face("Var Sans", "Black") in fonts.faces_of(face)
    return PASS


def test_reads_every_face_of_a_collection():
    faces = [
        {b"name": _name_table([(_WINDOWS, 16, "Coll Pro"), (_WINDOWS, 17, "W4"), (_WINDOWS, 1, "x"), (_WINDOWS, 2, "y")])},
        {b"name": _name_table([(_WINDOWS, 16, "Coll ProN"), (_WINDOWS, 17, "W4"), (_WINDOWS, 1, "x"), (_WINDOWS, 2, "y")])},
    ]
    families = [f.typographic_family for f in sfnt.read_faces(_ttc(faces))]
    assert families == ["Coll Pro", "Coll ProN"], families
    return PASS


def test_malformed_data_raises():
    for data in (b"", b"\x00\x01\x00\x00\x00\x05", _sfnt({b"head": b"\x00" * 4})):
        try:
            sfnt.read_faces(data)
        except sfnt.SfntError:
            continue
        raise AssertionError(f"expected SfntError for {data!r}")
    return PASS


def test_scan_records_unreadable_files_and_matches_pairs():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "sub").mkdir()
        good = _sfnt({b"name": _name_table([(_WINDOWS, 16, "Sample Sans"), (_WINDOWS, 17, "W6"), (_WINDOWS, 1, "a"), (_WINDOWS, 2, "b")])})
        (root / "sub" / "good.OTF").write_bytes(good)
        (root / "broken.ttf").write_bytes(b"not a font")
        (root / "notes.txt").write_bytes(b"ignored")
        installed = fonts.scan([root, root / "missing"])

    assert [p.name for p, _reason in installed.unreadable] == ["broken.ttf"], installed.unreadable
    assert installed.has("sample sans", "w6")
    assert installed.has("Sample Sans", None)
    assert not installed.has("Sample Sans", "W8")
    assert not installed.has("Other", None)
    return PASS


def test_scan_reads_again_on_each_call():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        assert not fonts.scan([root]).faces
        (root / "a.ttf").write_bytes(_sfnt({b"name": _name_table([(_WINDOWS, 1, "Late"), (_WINDOWS, 2, "Regular")])}))
        assert fonts.scan([root]).has("Late", "Regular")
    return PASS


TESTS = [
    ("test_reads_typographic_names_and_prefers_english", test_reads_typographic_names_and_prefers_english),
    ("test_falls_back_to_family_and_subfamily", test_falls_back_to_family_and_subfamily),
    ("test_reads_variable_font_instances", test_reads_variable_font_instances),
    ("test_reads_every_face_of_a_collection", test_reads_every_face_of_a_collection),
    ("test_malformed_data_raises", test_malformed_data_raises),
    ("test_scan_records_unreadable_files_and_matches_pairs", test_scan_records_unreadable_files_and_matches_pairs),
    ("test_scan_reads_again_on_each_call", test_scan_reads_again_on_each_call),
]


def main() -> int:
    any_failure = False
    for name, fn in TESTS:
        print(f"--- {name}")
        try:
            fn()
        except AssertionError as exc:
            print(f"    FAILED: {exc}")
            any_failure = True
            continue
        except Exception as exc:  # 想定外の例外も失敗として扱う。無害化しない。
            print(f"    ERROR: {exc!r}")
            any_failure = True
            continue
        print("    passed")
    return 1 if any_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
