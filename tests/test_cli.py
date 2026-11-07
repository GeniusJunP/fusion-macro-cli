"""``fusion_macro_cli.cli`` の検査。

単独実行できる(``python3 tests/test_cli.py``)。終了コードは、致命的な失敗が
1件でもあれば1、失敗は無いが実データ(``FUSION_MACRO_CLI_PRESETS``)の不在で
行えなかった検査があれば2、すべて実行できて失敗が無ければ0を返す
(``tests/run.py`` および他の test_*.py と同じ規約)。

``cli.main(argv)`` を直接呼び、終了コードと標準出力・標準エラー出力を検査する。
Resolve に接続するコマンド(``schema dump``・``preview``)は、接続せずに済む
処理(``--console``)と、接続前に失敗する場合(引数エラー)だけを検査する。
Resolve は起動しない。
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile
import zipfile
from contextlib import redirect_stderr, redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

import fusion_macro_cli.cli as cli  # noqa: E402
import fusion_macro_cli.document as document  # noqa: E402
import fusion_macro_cli.form as form_mod  # noqa: E402
import fusion_macro_cli.rules as rules  # noqa: E402
import fusion_macro_cli.show as show_mod  # noqa: E402
import test_extract  # noqa: E402 - .drt 組み立てヘルパー(_write_drt)を共有する

SKIP = "skip"
PASS = "pass"

DATA = HERE / "data"


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------


def _run(argv: list[str]) -> tuple[int, str, str]:
    """``cli.main`` を呼び、``(終了コード, 標準出力, 標準エラー出力)`` を返す。

    引数エラー(argparse)は ``SystemExit`` を送出するため、ここでは捕まえない。
    呼び出し側が意図して検査する。
    """
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def _preset_dir() -> pathlib.Path | None:
    env_dir = os.environ.get("FUSION_MACRO_CLI_PRESETS")
    if not env_dir:
        print("    FUSION_MACRO_CLI_PRESETS is not set")
        return None
    root = pathlib.Path(env_dir)
    if not root.is_dir():
        print(f"    FUSION_MACRO_CLI_PRESETS does not exist: {root}")
        return None
    return root


_APPLY_COMP = """Composition {
\tTools = {
\t\tBackground1 = Background {
\t\t\tInputs = {
\t\t\t\tTopLeftRed = Input { Value = 0.5 },
\t\t\t}
\t\t},
\t}
}
"""


def _write_apply_fixture(
    directory: pathlib.Path, *, page: str = "Controls", tool: str = "Background1"
) -> pathlib.Path:
    """``apply`` の検査に使う ``.comp`` と宣言(``.toml``)を書き出す。"""
    comp_path = directory / "source.comp"
    comp_path.write_text(_APPLY_COMP, encoding="utf-8")
    toml_text = (
        'name = "test_macro"\n'
        'source = "source.comp"\n'
        "\n"
        "[[output]]\n"
        f'tool = "{tool}"\n'
        "\n"
        "[[publish]]\n"
        f'tool = "{tool}"\n'
        'input = "TopLeftRed"\n'
        'label = "Red"\n'
        f'page = "{page}"\n'
        "\n"
        "[[publish]]\n"
        f'tool = "{tool}"\n'
        'input = "TopLeftGreen"\n'
        f'page = "{page}"\n'
        "\n"
        "[[publish]]\n"
        f'tool = "{tool}"\n'
        'input = "TopLeftBlue"\n'
        f'page = "{page}"\n'
    )
    toml_path = directory / "declare.toml"
    toml_path.write_text(toml_text, encoding="utf-8")
    return toml_path


# ascii-name 規則(comp 対象・スキーマ不要・グラフ不要)にだけ触れる合成 .comp。
# check / fix の検査を、実行環境のスキーマ有無に依存させないために使う。
_BAD_ASCII_COMP = """Composition {
\tTools = {
\t\t["日本語ツール"] = Background {
\t\t\tInputs = {
\t\t\t}
\t\t},
\t}
}
"""

_CLEAN_COMP = """Composition {
\tTools = {
\t\tBackground1 = Background {
\t\t\tInputs = {
\t\t\t}
\t\t},
\t}
}
"""

# apply が組み立てる .setting と同じ形の、貼り付け対象を1つだけ持つ最小の
# .setting。show / preview --console の検査に使う(publish.build_setting を
# 経由せず、preview.resolve_target が要求する構造(単一の MacroOperator)を
# 直接書き下す)。
_MINIMAL_SETTING = """{
\tTools = ordered() {
\t\ttest_macro = MacroOperator {
\t\t\tInputs = ordered() {
\t\t\t\tBackground1_TopLeftRed = InstanceInput {
\t\t\t\t\tSourceOp = "Background1",
\t\t\t\t\tSource = "TopLeftRed",
\t\t\t\t\tPage = "Controls",
\t\t\t\t\tName = "Red",
\t\t\t\t\tDefault = 0.5
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
\tActiveTool = "test_macro"
}
"""


def _make_corrupt_drt(path: pathlib.Path) -> None:
    """``<CompositionBA>`` の hex 文字列が壊れた ``.drt`` 相当の ZIP を作る。

    ``sources/drt.py`` の復号は要素単位・コンポジション単位の2段で失敗を
    切り分ける。ここでは BLOB のキー構造(``count`` 以下のキーの並び)自体を壊し、
    ``<CompositionBA>`` 要素全体が1件の失敗として記録される場合を検査する。
    """
    xml = (
        '<?xml version="1.0"?><Timeline><CompositionBA>'
        "not-hex-at-all"
        "</CompositionBA></Timeline>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SeqContainer/probe.xml", xml)


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------


def test_extract_writes_comp_files_from_drt():
    """自作の ``sample.comp`` を仕様の手順で組み立てた ``.drt`` から、
    ``extract`` コマンドが ``.comp`` を書き出すことを確認する
    (組み立ては ``test_extract._write_drt`` を共有する)。"""
    comp_text = test_extract.SAMPLE_COMP.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        drt_path = pathlib.Path(td) / "sample.drt"
        test_extract._write_drt(drt_path, comp_text)
        out_dir = pathlib.Path(td) / "out"
        code, out, err = _run(["extract", str(drt_path), "-o", str(out_dir)])
        assert code == 0, f"stderr={err}"
        first_line = out.splitlines()[0]
        assert first_line.startswith("fusion-macro-cli: wrote "), first_line
        written = sorted(out_dir.glob("*.comp"))
        assert written, "no .comp files were written"
        assert written[0].name == "0001_sample.comp", written[0].name
    return PASS


def test_extract_reports_decode_failure_and_exits_1():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        drt_path = base / "corrupt.drt"
        _make_corrupt_drt(drt_path)
        out_dir = base / "out"
        code, out, err = _run(["extract", str(drt_path), "-o", str(out_dir)])
        assert code == 1, f"stdout={out}"
        assert "0001" in err, err
        assert list(out_dir.glob("*.comp")) == []
    return PASS


def test_extract_non_zip_file_reports_file_error_and_exits_1():
    """対象ファイル自体が ZIP でない場合、``fusion-macro-cli: <path>: <reason>``
    の1行(「コマンド」節)を標準エラー出力へ書いて終了コード1を返す。
    traceback をそのまま出さない。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        not_zip = base / "not_a_zip.drt"
        not_zip.write_text("this is not a zip file", encoding="utf-8")
        out_dir = base / "out"
        code, out, err = _run(["extract", str(not_zip), "-o", str(out_dir)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert err.startswith(f"fusion-macro-cli: {not_zip}: "), err
        assert not out_dir.exists()
    return PASS


def test_extract_setting_writes_inner_tools_as_comp():
    """``.setting`` を受けた場合、最上位の ``MacroOperator`` の内側の ``Tools`` を
    ``0001_<マクロ名>.comp`` として書き出す。マクロの ``Inputs``/``Outputs``/
    マクロ名自体(``MacroOperator``)は持ち越さない。書き出した ``.comp`` は
    ``document.parse`` でき、``form.classify`` が
    ``"comp"`` と判定できる形(``Composition { Tools = { ... } }``、直下の
    ``Tools`` に ``ordered()`` 無し)であることを確かめる。"""
    sample_setting = DATA / "sample.setting"
    with tempfile.TemporaryDirectory() as td:
        out_dir = pathlib.Path(td) / "out"
        code, out, err = _run(["extract", str(sample_setting), "-o", str(out_dir)])
        assert code == 0, f"stderr={err}"
        written = sorted(out_dir.glob("*.comp"))
        assert [p.name for p in written] == ["0001_sample.comp"], written

        written_text = written[0].read_text(encoding="utf-8")
        written_doc = document.parse(written_text)
        assert form_mod.classify(written_doc) == "comp"
        assert form_mod.classify(written_doc) == "comp"
        assert not any(
            isinstance(el.value, document.Table) and el.value.type_name == "MacroOperator"
            for el in written_doc.root.elements
        ), "extracted .comp must not contain the MacroOperator wrapper"
        written_tools = written_doc.root.get("Tools")
        assert isinstance(written_tools, document.Table)
        assert written_tools.type_name is None  # ordered() を持ち越さない

        original_doc = document.parse(sample_setting.read_text(encoding="utf-8"))
        macro_element = original_doc.root.get("Tools").elements[0]
        inner_tools = macro_element.value.get("Tools")

        # 中身が名前の並びだけでなく再帰的に一致することを、両者を同じ手順
        # (Composition でくるみ format_compact を通す)で組み立てて文字列比較
        # することで確かめる(名前の並びだけの比較では値の書き換えを見逃す)。
        expected_root = document.Table(
            "Composition",
            [document.Element(document.Key("ident", "Tools"), document.Table(None, list(inner_tools.elements)))],
        )
        expected_doc = document.format_compact(
            document.Document(root=expected_root, indent=original_doc.indent, trailing_newline=True)
        )
        assert written_text == document.dump(expected_doc)
    return PASS


def test_extract_setting_with_multiple_top_level_tools_exits_1():
    """最上位に複数のツールがある文書は ``.setting`` として扱えない
    (「最上位が MacroOperator でない文書、または最上位に複数のツールがある
    文書は .setting として扱えない」)。1行のエラーを標準エラー出力へ書いて
    終了コード1を返し、出力先は作らない。"""
    text = """{
\tTools = ordered() {
\t\tA = Background { Inputs = {}, },
\t\tB = Background { Inputs = {}, },
\t},
}
"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        not_a_macro = base / "not_a_macro.setting"
        not_a_macro.write_text(text, encoding="utf-8")
        out_dir = base / "out"
        code, out, err = _run(["extract", str(not_a_macro), "-o", str(out_dir)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert err.startswith(f"fusion-macro-cli: {not_a_macro}: "), err
        assert not out_dir.exists()
    return PASS


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------


def test_declare_writes_a_toml_that_apply_accepts():
    """``declare`` の出力をそのまま ``apply`` に渡して ``.setting`` が作れる。

    既存のプリセットを作り直す手順が CLI だけで完結することを確かめる
    (宣言を手で書き下ろすと、2つ目の出力のような項目が失われるため)。
    """
    with tempfile.TemporaryDirectory() as td:
        work = pathlib.Path(td)
        setting = work / "sample.setting"
        setting.write_text((DATA / "sample.setting").read_text(encoding="utf-8"), encoding="utf-8")

        # extract が書き出す .comp を、declare の既定の source がそのまま指す。
        code, out, err = _run(["extract", str(setting), "-o", str(work)])
        assert code == 0, err
        code, out, err = _run(["declare", str(setting), "-o", str(work / "sample.toml")])
        assert code == 0, err
        code, out, err = _run(["apply", str(work / "sample.toml"), "-o", str(work / "out")])
        assert code == 0, err
        assert (work / "out" / "sample.setting").is_file()


def test_declare_without_output_writes_to_stdout():
    with tempfile.TemporaryDirectory() as td:
        setting = pathlib.Path(td) / "sample.setting"
        setting.write_text((DATA / "sample.setting").read_text(encoding="utf-8"), encoding="utf-8")
        code, out, err = _run(["declare", str(setting)])
        assert code == 0, err
        assert out.startswith("name = ")
        assert "[[output]]" in out


def test_declare_source_defaults_to_the_extracted_comp_name():
    """macro 形の既定の source は、extract がマクロ名から付ける .comp の名前になる。"""
    with tempfile.TemporaryDirectory() as td:
        setting = pathlib.Path(td) / "タイトル_青.setting"
        setting.write_text((DATA / "sample.setting").read_text(encoding="utf-8"), encoding="utf-8")
        code, out, _err = _run(["declare", str(setting)])
        assert code == 0
        assert 'source = "0001_sample.comp"' in out
        code, out, _err = _run(["declare", str(setting), "--source", "別の名前.comp"])
        assert 'source = "別の名前.comp"' in out


def test_declare_nonexistent_path_reports_error_and_exits_1():
    code, _out, err = _run(["declare", "/nonexistent.setting"])
    assert code == 1
    assert err.startswith("fusion-macro-cli: /nonexistent.setting: ")


def test_show_comp_prints_candidates_header():
    sample_comp = DATA / "sample.comp"
    with tempfile.TemporaryDirectory() as td:
        # 実行環境の既定のスキーマのディレクトリに依存させないため、必ず空のディレクトリを
        # --schema で渡し、「スキーマ無し」の場合で確認する。
        empty_schema_dir = pathlib.Path(td)
        code, out, err = _run(["show", str(sample_comp), "--schema", str(empty_schema_dir)])
        assert code == 0, f"stderr={err}"
        first_line = out.splitlines()[0]
        assert first_line == f"candidates for publishing (.comp): {sample_comp}"
    return PASS


def test_show_setting_prints_published_header():
    with tempfile.TemporaryDirectory() as td:
        setting_path = pathlib.Path(td) / "test_macro.setting"
        setting_path.write_text(_MINIMAL_SETTING, encoding="utf-8")
        code, out, err = _run(["show", str(setting_path)])
        assert code == 0, f"stderr={err}"
        first_line = out.splitlines()[0]
        assert first_line == f"published inputs (.setting): {setting_path}"
    return PASS


def test_show_nonexistent_path_reports_error_and_exits_1():
    """存在しないパスを渡した場合、check/fix と同様に traceback ではなく
    1行のエラーを標準エラー出力へ書いて終了コード1を返す。"""
    with tempfile.TemporaryDirectory() as td:
        missing = pathlib.Path(td) / "does_not_exist.comp"
        code, out, err = _run(["show", str(missing)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert out == "", f"stdout={out!r}"
        assert err.startswith(f"fusion-macro-cli: {missing}: "), err
    return PASS


def test_show_malformed_document_reports_file_error_and_exits_1():
    """対象が ``.comp``/``.setting`` のどちらとしても解析できない場合、
    ``fusion-macro-cli: <path>: <reason>`` の1行を標準エラー出力へ書いて
    終了コード1を返す(traceback をそのまま出さない)。"""
    with tempfile.TemporaryDirectory() as td:
        bad = pathlib.Path(td) / "bad.setting"
        bad.write_text("Foo = 1", encoding="utf-8")
        code, out, err = _run(["show", str(bad)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert out == "", f"stdout={out!r}"
        assert err.startswith(f"fusion-macro-cli: {bad}: "), err
    return PASS


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def test_apply_writes_setting_file():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        toml_path = _write_apply_fixture(base)
        out_dir = base / "out"
        code, out, err = _run(["apply", str(toml_path), "-o", str(out_dir)])
        assert code == 0, f"stderr={err}"
        first_line = out.splitlines()[0]
        assert first_line.startswith("fusion-macro-cli: wrote "), first_line
        assert (out_dir / "test_macro.setting").exists()
    return PASS


def test_apply_dry_run_does_not_write():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        toml_path = _write_apply_fixture(base)
        out_dir = base / "out"
        code, out, err = _run(["apply", str(toml_path), "-o", str(out_dir), "--dry-run"])
        assert code == 0, f"stderr={err}"
        assert "published inputs (.setting): test_macro" in out
        assert not out_dir.exists() or list(out_dir.glob("*.setting")) == []
    return PASS


def test_apply_dry_run_without_output_option_succeeds():
    """``--dry-run`` は書き出しを行わないため ``-o`` を要求しない。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        toml_path = _write_apply_fixture(base)
        code, out, err = _run(["apply", str(toml_path), "--dry-run"])
        assert code == 0, f"stderr={err}"
        assert "published inputs (.setting): test_macro" in out
    return PASS


def test_apply_missing_output_option_without_dry_run_exits_2():
    """``--dry-run`` を付けない ``apply`` は ``-o`` を要求する。argparse の
    他の必須引数エラーと同じ処理(終了コード2)で止まる。宣言ファイルが
    存在しなくても、この検査は宣言を読む前に行われる。"""
    with tempfile.TemporaryDirectory() as td:
        missing_toml = pathlib.Path(td) / "does_not_exist.toml"
        try:
            _run(["apply", str(missing_toml)])
        except SystemExit as exc:
            assert exc.code == 2, exc.code
            return PASS
        raise AssertionError("expected SystemExit for a missing required option")


def test_apply_declaration_error_on_missing_tool_exits_1():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        toml_path = _write_apply_fixture(base, tool="NoSuchTool")
        out_dir = base / "out"
        code, out, err = _run(["apply", str(toml_path), "-o", str(out_dir)])
        assert code == 1
        assert "NoSuchTool" in err, err
        assert not out_dir.exists() or list(out_dir.glob("*.setting")) == []
    return PASS


def test_apply_violation_exits_1_without_writing():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        toml_path = _write_apply_fixture(base, page="NotControls")
        out_dir = base / "out"
        code, out, err = _run(["apply", str(toml_path), "-o", str(out_dir)])
        assert code == 1, f"stdout={out}"
        assert "page-controls" in err, err
        assert not out_dir.exists() or list(out_dir.glob("*.setting")) == []
    return PASS


def test_apply_sample_toml_writes_setting_that_passes_check():
    """``tests/data/sample.toml`` を一時ディレクトリへ ``apply`` し、生成物を
    ``check`` に通す end-to-end の検査。``sample.comp`` は no-media-io に
    違反しない(規則1)ため、双方とも終了コード0になる。"""
    with tempfile.TemporaryDirectory() as td:
        out_dir = pathlib.Path(td)
        code, out, err = _run(["apply", str(DATA / "sample.toml"), "-o", str(out_dir)])
        assert code == 0, f"stderr={err}"
        setting_path = out_dir / "sample.setting"
        assert setting_path.exists()

        code, out, err = _run(["check", str(setting_path)])
        assert code == 0, f"stdout={out} stderr={err}"
    return PASS


# --------------------------------------------------------------------------
# check / fix
# --------------------------------------------------------------------------


def test_check_reports_violation_and_exits_1():
    with tempfile.TemporaryDirectory() as td:
        comp_path = pathlib.Path(td) / "bad.comp"
        comp_path.write_text(_BAD_ASCII_COMP, encoding="utf-8")
        code, out, err = _run(["check", str(comp_path)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert "ascii-name" in out
    return PASS


def test_check_clean_file_exits_0():
    with tempfile.TemporaryDirectory() as td:
        comp_path = pathlib.Path(td) / "clean.comp"
        comp_path.write_text(_CLEAN_COMP, encoding="utf-8")
        code, out, err = _run(["check", str(comp_path)])
        assert code == 0, f"stdout={out} stderr={err}"
    return PASS


def test_check_malformed_document_reports_file_error_and_exits_1():
    """対象が ``.comp``/``.setting`` のどちらとしても解析できない場合(構文の
    誤り、または構造がどちらでもない文書)、``fusion-macro-cli: <path>: <reason>``
    の1行を標準エラー出力へ書いて終了コード1を返す(traceback をそのまま
    出さない)。他のファイルの処理は続ける("check_directory" 側で確認する)。
    """
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        # 開き括弧が閉じておらず document.parse が ParseError を送出する。
        bad_syntax = base / "bad_syntax.comp"
        bad_syntax.write_text("Composition { Tools = { ", encoding="utf-8")
        code, out, err = _run(["check", str(bad_syntax)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert err.startswith(f"fusion-macro-cli: {bad_syntax}: "), err

        # 構文は正しいが .comp/.setting のどちらの構造でもなく、
        # rules.classify が ValueError を送出する。
        neither = base / "neither.comp"
        neither.write_text("{ Foo = 1, }", encoding="utf-8")
        code2, out2, err2 = _run(["check", str(neither)])
        assert code2 == 1, f"stdout={out2} stderr={err2}"
        assert err2.startswith(f"fusion-macro-cli: {neither}: "), err2
    return PASS


def test_check_directory_checks_every_file_in_it():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        (base / "bad.comp").write_text(_BAD_ASCII_COMP, encoding="utf-8")
        (base / "clean.comp").write_text(_CLEAN_COMP, encoding="utf-8")
        code, out, err = _run(["check", str(base)])
        assert code == 1, f"stdout={out}"
        assert "bad.comp" in out
        assert "clean.comp" not in out
    return PASS


def test_check_empty_directory_reports_error_and_exits_1():
    """``.comp``/``.setting`` を1つも持たないディレクトリを渡した場合、
    標準出力へは何も書かず、標準エラー出力へ理由を書いて終了コード1を返す
    (対象0件で沈黙してはならない)。"""
    with tempfile.TemporaryDirectory() as td:
        code, out, err = _run(["check", td])
        assert code == 1, f"stdout={out} stderr={err}"
        assert out == "", f"stdout={out!r}"
        assert err == f"fusion-macro-cli: no .comp or .setting under {td}\n", err
    return PASS


def test_check_nonexistent_path_reports_error_and_exits_1():
    """存在しないパスを渡した場合、``FileNotFoundError`` の traceback ではなく、
    1行のエラーを標準エラー出力へ書いて終了コード1を返す。"""
    with tempfile.TemporaryDirectory() as td:
        missing = pathlib.Path(td) / "does_not_exist.comp"
        code, out, err = _run(["check", str(missing)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert out == "", f"stdout={out!r}"
        assert err == f"fusion-macro-cli: {missing}: no such file\n", err
    return PASS


def test_fix_empty_directory_reports_error_and_exits_1_without_creating_output():
    """``fix`` が対象0件のディレクトリを渡された場合も ``check`` と同様に
    エラーで終了する。``-o`` を指定していても、エラー終了時に出力先
    ディレクトリを作ってはならない(mkdir はエラー判定より後に行う)。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td) / "empty"
        base.mkdir()
        out_dir = pathlib.Path(td) / "out"
        code, out, err = _run(["fix", str(base), "-o", str(out_dir)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert out == "", f"stdout={out!r}"
        assert err == f"fusion-macro-cli: no .comp or .setting under {base}\n", err
        assert not out_dir.exists(), "output directory must not be created on error"
    return PASS


def test_fix_nonexistent_path_reports_error_and_exits_1():
    with tempfile.TemporaryDirectory() as td:
        missing = pathlib.Path(td) / "does_not_exist.setting"
        code, out, err = _run(["fix", str(missing)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert out == "", f"stdout={out!r}"
        assert err == f"fusion-macro-cli: {missing}: no such file\n", err
    return PASS


def test_fix_overwrites_in_place_by_default():
    with tempfile.TemporaryDirectory() as td:
        comp_path = pathlib.Path(td) / "bad.comp"
        comp_path.write_text(_BAD_ASCII_COMP, encoding="utf-8")
        code, out, err = _run(["fix", str(comp_path)])
        assert code == 0, f"stdout={out} stderr={err}"
        assert "ascii-name" in out
        fixed_text = comp_path.read_text(encoding="utf-8")
        assert "日本語ツール" not in fixed_text
    return PASS


def test_fix_writes_to_output_directory_when_given():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        comp_path = base / "bad.comp"
        comp_path.write_text(_BAD_ASCII_COMP, encoding="utf-8")
        out_dir = base / "out"
        code, out, err = _run(["fix", str(comp_path), "-o", str(out_dir)])
        assert code == 0, f"stdout={out} stderr={err}"
        assert comp_path.read_text(encoding="utf-8") == _BAD_ASCII_COMP
        fixed_text = (out_dir / "bad.comp").read_text(encoding="utf-8")
        assert "日本語ツール" not in fixed_text
    return PASS


# --------------------------------------------------------------------------
# schema dump / preview (--console 経由のみ。Resolve には接続しない)
# --------------------------------------------------------------------------


def test_schema_dump_console_prints_exec_line_without_connecting():
    with tempfile.TemporaryDirectory() as td:
        schema_dir = pathlib.Path(td)
        code, out, err = _run(["schema", "dump", "-o", str(schema_dir), "--console"])
        assert code == 0, f"stderr={err}"
        first_line = out.splitlines()[0]
        assert first_line.startswith("exec(open("), first_line
        assert (schema_dir / "_dump.py3").exists()
    return PASS


def test_preview_console_prints_exec_line_without_connecting():
    with tempfile.TemporaryDirectory() as td:
        setting_path = pathlib.Path(td) / "test_macro.setting"
        setting_path.write_text(_MINIMAL_SETTING, encoding="utf-8")
        code, out, err = _run(["preview", str(setting_path), "--console"])
        assert code == 0, f"stderr={err}"
        lines = out.splitlines()
        assert lines[0].startswith("exec(open("), lines[0]
        # --console は Resolve への接続を待てないため、結果ファイルの場所も
        # あわせて表示する。
        assert "results will be written to" in lines[1], lines
    return PASS


# --------------------------------------------------------------------------
# --help (「コマンド」節: apply/show の epilog に書式・列の意味を載せる)
# --------------------------------------------------------------------------


def _help_text(argv: list[str]) -> str:
    """``argv`` を実行し、``--help`` による ``SystemExit`` を確かめた上で
    標準出力の全文を返す。"""
    out = io.StringIO()
    with redirect_stdout(out):
        try:
            cli.main(argv)
        except SystemExit as exc:
            assert exc.code == 0, exc.code
        else:
            raise AssertionError("expected SystemExit for --help")
    return out.getvalue()


def test_apply_help_includes_declaration_format():
    text = _help_text(["apply", "--help"])
    assert "Publish declaration" in text
    assert "[[publish]]" in text
    assert "label" in text
    return PASS


def test_show_help_includes_column_help():
    text = _help_text(["show", "--help"])
    assert "IC_NAME" in text
    assert "DIFF" in text
    assert "same" in text and "diff" in text and "unknown" in text
    return PASS


# --------------------------------------------------------------------------
# 引数エラー(接続に至る前に argparse が終了コード2で止まる場合)
# --------------------------------------------------------------------------


def test_extract_missing_output_option_exits_2():
    with tempfile.TemporaryDirectory() as td:
        drt_path = pathlib.Path(td) / "x.drt"
        try:
            _run(["extract", str(drt_path)])
        except SystemExit as exc:
            assert exc.code == 2, exc.code
            return PASS
        raise AssertionError("expected SystemExit for a missing required option")


def test_unknown_command_exits_2():
    try:
        _run(["not-a-command"])
    except SystemExit as exc:
        assert exc.code == 2, exc.code
        return PASS
    raise AssertionError("expected SystemExit for an unknown command")


def test_schema_dump_without_subcommand_exits_2():
    try:
        _run(["schema"])
    except SystemExit as exc:
        assert exc.code == 2, exc.code
        return PASS
    raise AssertionError("expected SystemExit for a missing schema subcommand")


# --------------------------------------------------------------------------
# 実データ: FUSION_MACRO_CLI_PRESETS 配下の既存 .setting
# --------------------------------------------------------------------------


def test_show_setting_on_existing_preset():
    root = _preset_dir()
    if root is None:
        return SKIP
    matches = sorted(root.rglob("*.setting"))
    if not matches:
        print("    no .setting files found under FUSION_MACRO_CLI_PRESETS")
        return SKIP
    path = matches[0]
    code, out, err = _run(["show", str(path)])
    assert code == 0, f"stderr={err}"
    kind = form_mod.classify(document.parse(path.read_text(encoding="utf-8")))
    header = {
        "macro": f"published inputs (.setting): {path}",
        "tool": f"inputs of the saved tool (tool-form .setting): {path}",
    }[kind]
    assert out.splitlines()[0] == header
    return PASS


def test_check_existing_preset_runs_without_crashing():
    root = _preset_dir()
    if root is None:
        return SKIP
    matches = sorted(root.rglob("*.setting"))
    if not matches:
        print("    no .setting files found under FUSION_MACRO_CLI_PRESETS")
        return SKIP
    path = matches[0]
    code, out, err = _run(["check", str(path)])
    assert code in (0, 1), f"unexpected exit code {code}, stderr={err}"
    return PASS


# --------------------------------------------------------------------------
# preview_timeline_name
# --------------------------------------------------------------------------


def test_preview_timeline_name_includes_execution_time():
    """タイムライン名は ``fusion-macro-cli preview <実行時刻>`` で、
    実行時刻はローカル時刻を秒まで書く。時分秒の間にコロンを入れない
    (``CreateEmptyTimeline`` がコロンを含む名前を受け付けないため)。"""
    import datetime

    now = datetime.datetime(2026, 9, 12, 14, 3, 27)
    name = cli.preview_timeline_name(now)
    assert name == "fusion-macro-cli preview 2026-09-12 140327", name
    assert ":" not in name
    return PASS


def test_preview_timeline_name_defaults_to_current_time():
    """``now`` を省略すると呼び出し時点の時刻(秒まで)が使われる。"""
    import datetime

    before = datetime.datetime.now().replace(microsecond=0)
    name = cli.preview_timeline_name()
    after = datetime.datetime.now().replace(microsecond=0)
    # 実行に1秒以上かかることは無い想定だが、境界を跨いだ場合でも通るよう
    # before から after までの全ての秒を許容する。
    candidates = set()
    cursor = before
    while cursor <= after:
        candidates.add(f"fusion-macro-cli preview {cursor:%Y-%m-%d %H%M%S}")
        cursor += datetime.timedelta(seconds=1)
    assert name in candidates, name
    assert ":" not in name
    return PASS


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------

def test_extract_tool_form_setting_writes_the_tool_as_comp():
    """tool 形の ``.setting`` を受けた場合、保存したツールを名前を変えずに
    ``0001_<.setting の名前>.comp`` として書き出す。"""
    sample = DATA / "sample_tool.setting"
    with tempfile.TemporaryDirectory() as td:
        out_dir = pathlib.Path(td) / "out"
        code, out, err = _run(["extract", str(sample), "-o", str(out_dir)])
        assert code == 0, f"stderr={err}"
        written = sorted(out_dir.glob("*.comp"))
        assert [p.name for p in written] == ["0001_sample_tool.comp"], written
        doc = document.parse(written[0].read_text(encoding="utf-8"))
        assert form_mod.classify(doc) == "comp"
        tools = doc.root.get("Tools")
        assert [(e.key.name, e.value.type_name) for e in tools.elements] == [("Template", "TextPlus")]
    return PASS


def test_tool_form_round_trips_through_extract_declare_apply():
    """tool 形の ``.setting`` を extract → declare → apply で往復させると、元と同じ文書になる。"""
    sample = DATA / "sample_tool.setting"
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        assert _run(["extract", str(sample), "-o", str(base)])[0] == 0
        toml = base / "sample_tool.toml"
        code, _out, err = _run(["declare", str(sample), "-o", str(toml), "--source", "0001_sample_tool.comp"])
        assert code == 0, err
        text = toml.read_text(encoding="utf-8")
        assert 'form = "tool"' in text and 'tool = "Template"' in text, text
        code, _out, err = _run(["apply", str(toml), "-o", str(base / "out")])
        assert code == 0, err
        rebuilt = (base / "out" / "sample_tool.setting").read_text(encoding="utf-8")
        assert rebuilt == sample.read_text(encoding="utf-8")
    return PASS


def test_check_fix_show_accept_several_paths():
    """``check`` ``fix`` ``show`` は複数のパスを受け付け、ディレクトリは直下の対象へ展開する。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        sub = base / "sub"
        sub.mkdir()
        (base / "bad.comp").write_text(_BAD_ASCII_COMP, encoding="utf-8")
        (sub / "clean.comp").write_text(_CLEAN_COMP, encoding="utf-8")
        tool_setting = DATA / "sample_tool.setting"

        code, out, err = _run(["check", str(base / "bad.comp"), str(sub), str(tool_setting)])
        assert code == 1, f"stdout={out} stderr={err}"
        assert "bad.comp" in out and "clean.comp" not in out and "sample_tool" not in out, out

        code, out, err = _run(["show", str(sub), str(tool_setting)])
        assert code == 0, err
        assert f"candidates for publishing (.comp): {sub / 'clean.comp'}" in out, out
        assert f"inputs of the saved tool (tool-form .setting): {tool_setting}" in out, out

        out_dir = base / "out"
        code, out, err = _run(["fix", str(base / "bad.comp"), str(sub), "-o", str(out_dir)])
        assert code == 0, f"stdout={out} stderr={err}"
        assert sorted(p.name for p in out_dir.iterdir()) == ["bad.comp", "clean.comp"]
    return PASS


TESTS = [
    ("extract_tool_form_setting_writes_the_tool_as_comp", test_extract_tool_form_setting_writes_the_tool_as_comp),
    ("tool_form_round_trips_through_extract_declare_apply", test_tool_form_round_trips_through_extract_declare_apply),
    ("check_fix_show_accept_several_paths", test_check_fix_show_accept_several_paths),
    ("declare_writes_a_toml_that_apply_accepts", test_declare_writes_a_toml_that_apply_accepts),
    ("declare_without_output_writes_to_stdout", test_declare_without_output_writes_to_stdout),
    (
        "declare_source_defaults_to_the_extracted_comp_name",
        test_declare_source_defaults_to_the_extracted_comp_name,
    ),
    (
        "declare_nonexistent_path_reports_error_and_exits_1",
        test_declare_nonexistent_path_reports_error_and_exits_1,
    ),
    ("extract_writes_comp_files_from_drt", test_extract_writes_comp_files_from_drt),
    (
        "extract_reports_decode_failure_and_exits_1",
        test_extract_reports_decode_failure_and_exits_1,
    ),
    (
        "extract_non_zip_file_reports_file_error_and_exits_1",
        test_extract_non_zip_file_reports_file_error_and_exits_1,
    ),
    (
        "extract_setting_writes_inner_tools_as_comp",
        test_extract_setting_writes_inner_tools_as_comp,
    ),
    (
        "extract_setting_with_multiple_top_level_tools_exits_1",
        test_extract_setting_with_multiple_top_level_tools_exits_1,
    ),
    ("show_comp_prints_candidates_header", test_show_comp_prints_candidates_header),
    ("show_setting_prints_published_header", test_show_setting_prints_published_header),
    (
        "show_nonexistent_path_reports_error_and_exits_1",
        test_show_nonexistent_path_reports_error_and_exits_1,
    ),
    (
        "show_malformed_document_reports_file_error_and_exits_1",
        test_show_malformed_document_reports_file_error_and_exits_1,
    ),
    ("apply_writes_setting_file", test_apply_writes_setting_file),
    ("apply_dry_run_does_not_write", test_apply_dry_run_does_not_write),
    ("apply_dry_run_without_output_option_succeeds", test_apply_dry_run_without_output_option_succeeds),
    (
        "apply_missing_output_option_without_dry_run_exits_2",
        test_apply_missing_output_option_without_dry_run_exits_2,
    ),
    (
        "apply_declaration_error_on_missing_tool_exits_1",
        test_apply_declaration_error_on_missing_tool_exits_1,
    ),
    ("apply_violation_exits_1_without_writing", test_apply_violation_exits_1_without_writing),
    (
        "apply_sample_toml_writes_setting_that_passes_check",
        test_apply_sample_toml_writes_setting_that_passes_check,
    ),
    ("check_reports_violation_and_exits_1", test_check_reports_violation_and_exits_1),
    (
        "check_malformed_document_reports_file_error_and_exits_1",
        test_check_malformed_document_reports_file_error_and_exits_1,
    ),
    ("check_clean_file_exits_0", test_check_clean_file_exits_0),
    ("check_directory_checks_every_file_in_it", test_check_directory_checks_every_file_in_it),
    (
        "check_empty_directory_reports_error_and_exits_1",
        test_check_empty_directory_reports_error_and_exits_1,
    ),
    (
        "check_nonexistent_path_reports_error_and_exits_1",
        test_check_nonexistent_path_reports_error_and_exits_1,
    ),
    (
        "fix_empty_directory_reports_error_and_exits_1_without_creating_output",
        test_fix_empty_directory_reports_error_and_exits_1_without_creating_output,
    ),
    (
        "fix_nonexistent_path_reports_error_and_exits_1",
        test_fix_nonexistent_path_reports_error_and_exits_1,
    ),
    ("fix_overwrites_in_place_by_default", test_fix_overwrites_in_place_by_default),
    ("fix_writes_to_output_directory_when_given", test_fix_writes_to_output_directory_when_given),
    (
        "schema_dump_console_prints_exec_line_without_connecting",
        test_schema_dump_console_prints_exec_line_without_connecting,
    ),
    (
        "preview_console_prints_exec_line_without_connecting",
        test_preview_console_prints_exec_line_without_connecting,
    ),
    ("apply_help_includes_declaration_format", test_apply_help_includes_declaration_format),
    ("show_help_includes_column_help", test_show_help_includes_column_help),
    ("extract_missing_output_option_exits_2", test_extract_missing_output_option_exits_2),
    ("unknown_command_exits_2", test_unknown_command_exits_2),
    ("schema_dump_without_subcommand_exits_2", test_schema_dump_without_subcommand_exits_2),
    ("show_setting_on_existing_preset", test_show_setting_on_existing_preset),
    ("check_existing_preset_runs_without_crashing", test_check_existing_preset_runs_without_crashing),
    (
        "preview_timeline_name_includes_execution_time",
        test_preview_timeline_name_includes_execution_time,
    ),
    (
        "preview_timeline_name_defaults_to_current_time",
        test_preview_timeline_name_defaults_to_current_time,
    ),
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
        except Exception as exc:  # noqa: BLE001 - 想定外の例外も失敗として扱う。無害化しない。
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
