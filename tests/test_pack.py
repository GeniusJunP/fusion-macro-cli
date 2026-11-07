"""``fusion_macro_cli.pack`` および ``cli pack`` の検査。

単独実行できる(``python3 tests/test_pack.py``)。すべて合成データ(一時
ディレクトリに自作した最小の ``Templates`` ディレクトリ)で完結するため、終了コードは
失敗が1件でもあれば1、無ければ0を返す(スキップは無い)。

検査は2種類に分かれる。

- ``pack.py`` 単体(``_iter_edit_files`` を経由する ``archive_files``/
  ``target_documents``/``write_archive``)の、``.`` 始まりの除外・
  出力先自身の除外・再現性
- ``cli.main(["pack", ...])`` を経由した、検査(``rules.check``)との
  組み合わせ(違反があれば書き出さない・終了コード1)
"""

from __future__ import annotations

import io
import pathlib
import sys
import tempfile
import zipfile
from contextlib import redirect_stderr, redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.cli as cli  # noqa: E402
import fusion_macro_cli.pack as pack  # noqa: E402

PASS = "pass"

#: 違反の無い最小の ``.setting``(``tests/test_cli.py`` の ``_MINIMAL_SETTING``と
#: 同じ形)。スキーマ無しで判定できる規則のみで違反0件になる。
_CLEAN_SETTING = """{
\tTools = ordered() {
\t\ta = MacroOperator {
\t\t\tInputs = ordered() {
\t\t\t\tBackground1_TopLeftRed = InstanceInput {
\t\t\t\t\tSourceOp = "Background1",
\t\t\t\t\tSource = "TopLeftRed",
\t\t\t\t\tPage = "Controls",
\t\t\t\t\tName = "Red",
\t\t\t\t\tControlGroup = 1,
\t\t\t\t\tDefault = 0.5
\t\t\t\t},
\t\t\t\tBackground1_TopLeftGreen = InstanceInput {
\t\t\t\t\tSourceOp = "Background1",
\t\t\t\t\tSource = "TopLeftGreen",
\t\t\t\t\tPage = "Controls",
\t\t\t\t\tControlGroup = 1
\t\t\t\t},
\t\t\t\tBackground1_TopLeftBlue = InstanceInput {
\t\t\t\t\tSourceOp = "Background1",
\t\t\t\t\tSource = "TopLeftBlue",
\t\t\t\t\tPage = "Controls",
\t\t\t\t\tControlGroup = 1
\t\t\t\t}
\t\t\t},
\t\t\tOutputs = {
\t\t\t\tMainOutput1 = InstanceOutput { SourceOp = "Background1", Source = "Output" }
\t\t\t},
\t\t\tTools = ordered() {
\t\t\t\tBackground1 = Background { Inputs = { TopLeftRed = Input { Value = 0.5 }, } }
\t\t\t}
\t\t}
\t},
\tActiveTool = "a"
}
"""

#: ``page-controls`` 規則(スキーマ不要)に違反する ``.setting``。
_VIOLATING_SETTING = _CLEAN_SETTING.replace('Page = "Controls",', 'Page = "NotControls",')


def _run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# pack.py 単体: 列挙とアーカイブ
# --------------------------------------------------------------------------


def test_archive_files_excludes_dot_prefixed_names():
    """``.`` で始まるファイル・ディレクトリは、``Edit/`` の直下・入れ子の
    どちらにあっても除外される。"""
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)
        (templates_dir / "Edit" / "Titles" / "X").mkdir(parents=True)
        (templates_dir / "Edit" / "Titles" / "X" / "a.setting").write_text("a", encoding="utf-8")
        (templates_dir / "Edit" / ".DS_Store").write_text("junk", encoding="utf-8")
        (templates_dir / "Edit" / "Titles" / "X" / ".git").mkdir()
        (templates_dir / "Edit" / "Titles" / "X" / ".git" / "y").write_text("junk", encoding="utf-8")

        files = pack.archive_files(templates_dir)
        relatives = sorted(p.relative_to(templates_dir).as_posix() for p in files)
        assert relatives == ["Edit/Titles/X/a.setting"], relatives
    return PASS


def test_archive_files_excludes_destination_inside_templates_dir():
    """出力先の ``.drfx`` が ``Templates`` ディレクトリの中にある場合、自分自身を含めない。"""
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)
        (templates_dir / "Edit" / "Titles" / "X").mkdir(parents=True)
        (templates_dir / "Edit" / "Titles" / "X" / "a.setting").write_text("a", encoding="utf-8")
        dest = templates_dir / "out.drfx"
        dest.write_bytes(b"")  # 直前の実行の残骸を想定し、先に存在させておく。

        files = pack.archive_files(templates_dir, exclude=dest)
        relatives = sorted(p.relative_to(templates_dir).as_posix() for p in files)
        assert relatives == ["Edit/Titles/X/a.setting"], relatives
    return PASS


def test_iter_edit_files_requires_edit_directory():
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)  # Edit/ を作らない。
        try:
            pack.target_documents(templates_dir)
        except FileNotFoundError:
            return PASS
        raise AssertionError("expected FileNotFoundError when Edit/ is missing")
    return PASS


def test_write_archive_is_deterministic():
    """同じ``Templates`` ディレクトリから2回書き出すと、同じバイト列になる。"""
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)
        (templates_dir / "Edit" / "Titles" / "X").mkdir(parents=True)
        (templates_dir / "Edit" / "Titles" / "X" / "a.setting").write_text(_CLEAN_SETTING, encoding="utf-8")
        (templates_dir / "Edit" / "Titles" / "Y").mkdir(parents=True)
        (templates_dir / "Edit" / "Titles" / "Y" / "b.png").write_bytes(bytes(range(256)))

        dest1 = pathlib.Path(td) / "out1.drfx"
        dest2 = pathlib.Path(td) / "out2.drfx"
        pack.write_archive(templates_dir, dest1)
        pack.write_archive(templates_dir, dest2)
        assert dest1.read_bytes() == dest2.read_bytes()
    return PASS


# --------------------------------------------------------------------------
# cli pack: 検査との組み合わせ
# --------------------------------------------------------------------------


def test_cli_pack_writes_drfx_with_only_edit_subtree():
    """最小構成: ``Edit/Titles/X/a.setting`` の他に
    ``.DS_Store``・``.git/x`` が ``Templates`` ディレクトリにあっても、アーカイブへは
    ``Edit/Titles/X/a.setting`` だけが入る。"""
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)
        (templates_dir / "Edit" / "Titles" / "X").mkdir(parents=True)
        (templates_dir / "Edit" / "Titles" / "X" / "a.setting").write_text(_CLEAN_SETTING, encoding="utf-8")
        (templates_dir / ".DS_Store").write_text("junk", encoding="utf-8")
        (templates_dir / ".git").mkdir()
        (templates_dir / ".git" / "x").write_text("junk", encoding="utf-8")

        dest = pathlib.Path(td) / "out" / "sample.drfx"
        code, out, err = _run(["pack", str(templates_dir), "-o", str(dest)])
        assert code == 0, f"stderr={err}"
        assert dest.exists()
        with zipfile.ZipFile(dest) as archive:
            assert archive.namelist() == ["Edit/Titles/X/a.setting"], archive.namelist()
    return PASS


def test_cli_pack_reports_violation_and_exits_1_without_writing():
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)
        (templates_dir / "Edit" / "Titles" / "X").mkdir(parents=True)
        (templates_dir / "Edit" / "Titles" / "X" / "bad.setting").write_text(
            _VIOLATING_SETTING, encoding="utf-8"
        )

        dest = pathlib.Path(td) / "out.drfx"
        code, out, err = _run(["pack", str(templates_dir), "-o", str(dest)])
        assert code == 1, f"stdout={out}"
        assert "page-controls" in out, out
        assert not dest.exists()
    return PASS


def test_cli_pack_missing_edit_directory_reports_error_and_exits_1():
    with tempfile.TemporaryDirectory() as td:
        templates_dir = pathlib.Path(td)  # Edit/ を作らない。
        dest = pathlib.Path(td) / "out.drfx"
        code, out, err = _run(["pack", str(templates_dir), "-o", str(dest)])
        assert code == 1, f"stdout={out}"
        assert err.startswith(f"fusion-macro-cli: {templates_dir}: "), err
        assert not dest.exists()
    return PASS


TESTS = [
    ("archive_files_excludes_dot_prefixed_names", test_archive_files_excludes_dot_prefixed_names),
    (
        "archive_files_excludes_destination_inside_templates_dir",
        test_archive_files_excludes_destination_inside_templates_dir,
    ),
    ("iter_edit_files_requires_edit_directory", test_iter_edit_files_requires_edit_directory),
    ("write_archive_is_deterministic", test_write_archive_is_deterministic),
    ("cli_pack_writes_drfx_with_only_edit_subtree", test_cli_pack_writes_drfx_with_only_edit_subtree),
    (
        "cli_pack_reports_violation_and_exits_1_without_writing",
        test_cli_pack_reports_violation_and_exits_1_without_writing,
    ),
    (
        "cli_pack_missing_edit_directory_reports_error_and_exits_1",
        test_cli_pack_missing_edit_directory_reports_error_and_exits_1,
    ),
]


def main() -> int:
    any_failure = False
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
        print("    passed" if outcome == PASS else f"    unexpected outcome: {outcome!r}")

    return 1 if any_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
