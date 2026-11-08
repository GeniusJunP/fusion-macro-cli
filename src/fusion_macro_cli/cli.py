"""コマンドラインの入口。

ファイルの探索、スキーマの読み込み、「検査してから書く」ためのモジュールの組み合わせ、標準出力への書き込みと終了コードの決定を担う。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tomllib
import zipfile
from datetime import datetime

from fusion_macro_cli import document
from fusion_macro_cli import fonts
from fusion_macro_cli import form as form_mod
from fusion_macro_cli import pack as pack_mod
from fusion_macro_cli import publish
from fusion_macro_cli import rules
from fusion_macro_cli import schema as schema_mod
from fusion_macro_cli import show as show_mod
from fusion_macro_cli.resolve import dump as dump_mod
from fusion_macro_cli.resolve import preview as preview_mod
from fusion_macro_cli.sources import drt

#: 対象ファイルに起因する失敗として1行で報告する例外の型。プログラム自身の不具合は含めない。
#: ``form.classify`` の ``ValueError`` は ``document.ParseError`` と別に、読み込みの箇所が個別に捕捉する。
_FILE_ERRORS = (
    document.ParseError,
    publish.DeclarationError,
    zipfile.BadZipFile,
    tomllib.TOMLDecodeError,
    UnicodeDecodeError,
    FileNotFoundError,
    IsADirectoryError,
)


def _file_error(path: object, exc: Exception) -> str:
    """対象ファイルに起因する失敗を、パスを添えた1行のメッセージへ変換する。"""
    return f"fusion-macro-cli: {path}: {exc}"

#: check / fix / show がディレクトリを受けた場合に対象とする拡張子。
_TARGET_SUFFIXES = (".comp", ".setting")


def preview_timeline_name(now: datetime | None = None) -> str:
    """preview が作るタイムラインの名前を、実行時刻から決める。

    ``CreateEmptyTimeline`` はコロンを含む名前を受け付けないため、時分秒を区切らない。
    """
    if now is None:
        now = datetime.now()
    return f"fusion-macro-cli preview {now:%Y-%m-%d %H%M%S}"


# --------------------------------------------------------------------------
# スキーマの読み込み
# --------------------------------------------------------------------------


def _load_schema(schema_dir: pathlib.Path | None) -> schema_mod.Schema | None:
    """最新のスキーマを読む。スキーマが無くても各コマンドは動作するため、無ければ ``None`` を返す。"""
    try:
        if schema_dir is not None:
            candidates = schema_mod.list_schemas(schema_dir)
            if not candidates:
                return None
            return schema_mod.load(candidates[0])
        return schema_mod.load()
    except FileNotFoundError:
        return None


def _scan_fonts() -> fonts.InstalledFonts:
    """システムのフォントを1回走査し、読めなかったファイルを標準エラーに書く。``check`` の全ファイルで結果を共有する。"""
    installed = fonts.installed_fonts()
    for path, reason in installed.unreadable:
        print(f"fusion-macro-cli: warning: cannot read font {path}: {reason}", file=sys.stderr)
    return installed


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------


def extracted_comp_name(path: pathlib.Path, doc: document.Document) -> str:
    """``.setting`` から ``extract`` が書き出す ``.comp`` のファイル名。``declare`` の既定の ``source`` もこれを使う。

    macro 形はマクロ名を使う。tool 形のツール名(多くは ``Template``)はファイルごとに一意でないため、``.setting`` の名前を使う。
    """
    stem = form_mod.top_tool(doc).name if form_mod.classify(doc) == "macro" else path.stem
    return f"0001_{drt.safe_filename(stem)}.comp"


def _extract_from_setting(path: pathlib.Path, output: str) -> int:
    """``.setting`` のツールを ``.comp`` として書き出す。

    macro 形は内側の ``Tools`` を書き出し、マクロの ``Inputs`` と ``Outputs`` は持ち越さない。tool 形は保存したツールを書き出す。
    """
    doc = _read_and_classify(path)
    if doc is None:
        return 1
    kind = form_mod.classify(doc)
    if kind == "comp":
        print(_file_error(path, ValueError("not a .setting document (root type is 'Composition')")), file=sys.stderr)
        return 1

    top = form_mod.top_tool(doc)
    if kind == "macro":
        inner_tools = top.table.get("Tools")
        if not isinstance(inner_tools, document.Table):
            print(_file_error(path, ValueError(f"MacroOperator {top.name!r} has no Tools table")), file=sys.stderr)
            return 1
        elements = list(inner_tools.elements)
    else:
        elements = [el for el in doc.root.get("Tools").elements if el.key is not None]  # type: ignore[union-attr]

    # .comp の最上位の Tools は ordered() を伴わない(comp.Save() の出力に合わせる)。
    comp_tools = document.Table(None, elements)
    root = document.Table("Composition", [document.Element(document.Key("ident", "Tools"), comp_tools)])
    built = document.Document(root=root, indent=doc.indent, trailing_newline=True)
    built = document.format_compact(built)

    output_dir = pathlib.Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / extracted_comp_name(path, doc)
    dest.write_text(document.dump(built), encoding="utf-8")
    print(f"fusion-macro-cli: wrote {dest}")
    return 0


def cmd_declare(args: argparse.Namespace) -> int:
    """既存の ``.setting`` から、その形の宣言を起こし、TOML を書く。"""
    path = pathlib.Path(args.setting)
    try:
        doc = document.parse(path.read_text(encoding="utf-8"))
        declaration, _inner = publish.declare(doc, file_stem=path.stem)
    except _FILE_ERRORS as exc:
        print(_file_error(path, exc), file=sys.stderr)
        return 1

    # .setting は元の .comp のパスを持たないため、既定では extract が書き出す名前を使う。
    source = args.source if args.source is not None else extracted_comp_name(path, doc)
    text = publish.declaration_to_toml(declaration, source=source)
    if args.output is None:
        sys.stdout.write(text)
        return 0
    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"fusion-macro-cli: wrote {out}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    source_path = pathlib.Path(args.source)
    try:
        is_archive = zipfile.is_zipfile(source_path)
    except _FILE_ERRORS as exc:
        print(_file_error(args.source, exc), file=sys.stderr)
        return 1
    if not is_archive:
        # 種類は拡張子ではなく ZIP であるかで判別する。
        return _extract_from_setting(source_path, args.output)

    try:
        compositions = drt.extract(args.source)
    except _FILE_ERRORS as exc:
        print(_file_error(args.source, exc), file=sys.stderr)
        return 1
    output_dir = pathlib.Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    for composition in compositions:
        if composition.text is None:
            failures.append(composition)
            continue
        filename = drt.output_filename(composition)
        dest = output_dir / filename
        dest.write_text(composition.text, encoding="utf-8")
        print(f"fusion-macro-cli: wrote {dest}")

    for composition in failures:
        print(
            f"error: composition {composition.sequence:04d} ({composition.name!r}) "
            f"failed to decode: {composition.reason}",
            file=sys.stderr,
        )
    return 1 if failures else 0


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------


def cmd_show(args: argparse.Namespace) -> int:
    schema_obj = _load_schema(args.schema)
    paths = _expand_targets(args.paths)
    if paths is None:
        return 1
    any_error = False
    for path in paths:
        try:
            text = show_mod.show(path, schema=schema_obj, show_all=args.all)
        except (*_FILE_ERRORS, ValueError) as exc:
            print(_file_error(path, exc), file=sys.stderr)
            any_error = True
            continue
        sys.stdout.write(text)
    return 1 if any_error else 0


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def _print_check_result(
    result: rules.CheckResult, *, prefix: str, stream, violation_label: str = ""
) -> bool:
    """検査の結果を1件1行で書き、違反があれば ``True`` を返す。

    文書の種類に適用しない規則は書かない。適用の対象は規則の定義であり、``check --help`` が一覧を示す。
    """
    for violation in result.violations:
        print(
            f"{prefix}{violation_label}{violation.rule} {violation.location}: {violation.message}",
            file=stream,
        )
    for rule_id in result.skipped:
        print(f"{prefix}skipped rule (no schema): {rule_id}", file=stream)
    for entry in result.skipped_inputs:
        print(f"{prefix}skipped input: {entry}", file=stream)
    return bool(result.violations)


def cmd_apply(args: argparse.Namespace) -> int:
    schema_obj = _load_schema(args.schema)

    try:
        declaration = publish.load_declaration(args.declaration)
    except _FILE_ERRORS as exc:
        print(_file_error(args.declaration, exc), file=sys.stderr)
        return 1

    comp_path = pathlib.Path(args.declaration).parent / declaration.source
    try:
        comp_doc = publish.load_comp(declaration, args.declaration)
    except _FILE_ERRORS as exc:
        print(_file_error(comp_path, exc), file=sys.stderr)
        return 1

    try:
        setting_doc = publish.build_setting(declaration, comp_doc, schema=schema_obj)
    except publish.DeclarationError as exc:
        print(_file_error(args.declaration, exc), file=sys.stderr)
        return 1

    result = rules.check(setting_doc, schema_obj=schema_obj, installed=_scan_fonts())

    if args.dry_run:
        # --dry-run でも、違反があれば通らない宣言であることを終了コードで示す。
        _print_check_result(result, prefix="", stream=sys.stdout, violation_label="violation: ")
        sys.stdout.write(
            show_mod.show_setting(setting_doc, filename=declaration.name, schema=schema_obj)
        )
        return 1 if result.violations else 0

    if result.violations:
        _print_check_result(result, prefix="", stream=sys.stderr, violation_label="violation: ")
        return 1

    output_dir = pathlib.Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{declaration.name}.setting"
    dest.write_text(document.dump(setting_doc), encoding="utf-8")
    print(f"fusion-macro-cli: wrote {dest}")
    return 0


# --------------------------------------------------------------------------
# check / fix
# --------------------------------------------------------------------------


def _expand_targets(path_args: list[str]) -> list[pathlib.Path] | None:
    """対象のパスを、引数の順に、ディレクトリであれば直下の ``.comp`` と ``.setting`` へ展開する。

    パスが無い、またはディレクトリに対象が無い場合はエラーを書き、``None`` を返す。
    """
    paths: list[pathlib.Path] = []
    for path_arg in path_args:
        target = pathlib.Path(path_arg)
        if not target.exists():
            print(f"fusion-macro-cli: {path_arg}: no such file", file=sys.stderr)
            return None
        if not target.is_dir():
            paths.append(target)
            continue
        found = sorted(p for p in target.iterdir() if p.is_file() and p.suffix in _TARGET_SUFFIXES)
        if not found:
            print(f"fusion-macro-cli: no .comp or .setting under {path_arg}", file=sys.stderr)
            return None
        paths.extend(found)
    return paths


def _read_and_classify(path: pathlib.Path) -> document.Document | None:
    """対象ファイルを読んで分類する。失敗した場合はエラーを書いて ``None`` を返し、呼び出し側は次のファイルへ進む。"""
    try:
        text = path.read_text(encoding="utf-8")
        doc = document.parse(text)
        form_mod.classify(doc)
    except (*_FILE_ERRORS, ValueError) as exc:
        print(_file_error(path, exc), file=sys.stderr)
        return None
    return doc


def _check_paths(paths: list[pathlib.Path], schema_obj: schema_mod.Schema | None) -> bool:
    """各ファイルを検査して結果を書き、違反または読み込みの失敗があれば ``True`` を返す。"""
    any_violation = False
    installed = _scan_fonts()
    for path in paths:
        doc = _read_and_classify(path)
        if doc is None:
            any_violation = True
            continue
        result = rules.check(doc, schema_obj=schema_obj, installed=installed)
        if _print_check_result(result, prefix=f"{path}: ", stream=sys.stdout):
            any_violation = True
    return any_violation


def cmd_check(args: argparse.Namespace) -> int:
    schema_obj = _load_schema(args.schema)
    paths = _expand_targets(args.paths)
    if paths is None:
        return 1
    return 1 if _check_paths(paths, schema_obj) else 0


def cmd_fix(args: argparse.Namespace) -> int:
    schema_obj = _load_schema(args.schema)
    # エラーで終了する場合に空の出力ディレクトリを残さないよう、判定の後に作る。
    paths = _expand_targets(args.paths)
    if paths is None:
        return 1

    output_dir = pathlib.Path(args.output) if args.output else None
    if output_dir is not None:
        # 異なるディレクトリにある同名のファイルは -o の中で上書きし合うため、書く前にエラーとする。
        # 仕様補完: ディレクトリ構造を -o の下に再現する案もあるが、入力のどこを基準にするかが定まらないため採らない。
        names = [p.name for p in paths]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            print(
                f"fusion-macro-cli: -o would receive several files named {', '.join(duplicates)}",
                file=sys.stderr,
            )
            return 1
        output_dir.mkdir(parents=True, exist_ok=True)

    any_unfixed = False
    installed = _scan_fonts()
    for path in paths:
        doc = _read_and_classify(path)
        if doc is None:
            any_unfixed = True
            continue
        fixed_doc, applied, result = rules.fix(doc, schema_obj=schema_obj, installed=installed)
        for violation in applied:
            print(f"{path}: fixed {violation.rule} {violation.location}: {violation.message}")
        if _print_check_result(result, prefix=f"{path}: ", stream=sys.stdout):
            any_unfixed = True

        dest = (output_dir / path.name) if output_dir is not None else path
        dest.write_text(document.dump(fixed_doc), encoding="utf-8")
    return 1 if any_unfixed else 0


# --------------------------------------------------------------------------
# pack
# --------------------------------------------------------------------------


def cmd_pack(args: argparse.Namespace) -> int:
    """``Templates`` ディレクトリを検査し、違反が無ければ ``.drfx`` にアーカイブする。"""
    schema_obj = _load_schema(args.schema)
    templates_dir = pathlib.Path(args.dir)
    dest = pathlib.Path(args.output)

    try:
        targets = pack_mod.target_documents(templates_dir)
    except _FILE_ERRORS as exc:
        print(_file_error(templates_dir, exc), file=sys.stderr)
        return 1

    if _check_paths(targets, schema_obj):
        return 1

    # エラーで終了する場合に空のディレクトリを残さないよう、検査の後に作る。
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        pack_mod.write_archive(templates_dir, dest)
    except _FILE_ERRORS as exc:
        print(_file_error(templates_dir, exc), file=sys.stderr)
        return 1

    print(f"fusion-macro-cli: wrote {dest}")
    return 0


# --------------------------------------------------------------------------
# schema dump
# --------------------------------------------------------------------------


def cmd_schema_dump(args: argparse.Namespace) -> int:
    schema_dir = pathlib.Path(args.output) if args.output else None
    return dump_mod.run(schema_dir=schema_dir, console=args.console)


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


def cmd_preview(args: argparse.Namespace) -> int:
    setting_paths = [pathlib.Path(p) for p in args.settings]
    # ``--sample-size`` だけを指定してもフォントサンプルにはしない。フォントサンプルとして描画するかどうかは ``--sample-text`` が決める。
    sample = None
    if args.sample_text is not None:
        sample = preview_mod.SampleText(text=args.sample_text, size=args.sample_size)
    return preview_mod.run(
        setting_paths,
        frame=args.frame,
        timeline_name=preview_timeline_name(),
        project_name=args.project,
        console=args.console,
        timeout=args.timeout,
        sample=sample,
        framing=args.framing,
        content_margin=args.content_margin,
    )


# --------------------------------------------------------------------------
# 引数解析
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-macro-cli",
        description="Manage Fusion macro presets extracted from DaVinci Resolve.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_extract = subparsers.add_parser(
        "extract", help="extract compositions from a .drt/.drp archive or a .setting"
    )
    p_extract.add_argument("source", help="path to a .drt, .drp, or .setting file")
    p_extract.add_argument("-o", "--output", required=True, help="output directory")
    p_extract.set_defaults(func=cmd_extract)

    p_declare = subparsers.add_parser(
        "declare",
        help="derive a publish declaration of the same form from an existing .setting",
    )
    p_declare.add_argument("setting", help="path to a .setting file")
    p_declare.add_argument(
        "-o", "--output", default=None, help="output .toml path (default: standard output)"
    )
    p_declare.add_argument(
        "--source",
        default=None,
        help="value of the declaration's source field "
        "(default: the .comp file name that extract writes for this .setting)",
    )
    p_declare.set_defaults(func=cmd_declare)

    p_show = subparsers.add_parser(
        "show",
        help="show publishing candidates (.comp), published inputs (macro-form .setting), "
        "or the saved tool's inputs (tool-form .setting)",
        epilog=show_mod.COLUMN_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_show.add_argument(
        "paths", nargs="+", help="paths to .comp/.setting files or directories"
    )
    p_show.add_argument(
        "--all", action="store_true", help="list every input instead of only the notable ones"
    )
    p_show.add_argument(
        "--schema", type=pathlib.Path, default=None, help="override the schema directory"
    )
    p_show.set_defaults(func=cmd_show)

    p_apply = subparsers.add_parser(
        "apply",
        help="apply a publish declaration and write a .setting of the declared form",
        epilog=publish.DECLARATION_FORMAT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_apply.add_argument("declaration", help="path to a publish declaration (.toml)")
    p_apply.add_argument(
        "-o",
        "--output",
        default=None,
        help="output directory (required unless --dry-run is given)",
    )
    p_apply.add_argument(
        "--dry-run", action="store_true", help="check and show the result without writing"
    )
    p_apply.add_argument(
        "--schema", type=pathlib.Path, default=None, help="override the schema directory"
    )
    # -o の要否は --dry-run の有無で変わり、argparse の required= では表せないため main() が判定する。
    p_apply.set_defaults(func=cmd_apply)

    p_check = subparsers.add_parser(
        "check",
        help="check .comp/.setting files, or every one in a directory",
        epilog=rules.RULES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_check.add_argument(
        "paths", nargs="+", help="paths to .comp/.setting files or directories"
    )
    p_check.add_argument(
        "--schema", type=pathlib.Path, default=None, help="override the schema directory"
    )
    p_check.set_defaults(func=cmd_check)

    p_fix = subparsers.add_parser(
        "fix",
        help="fix violations that have a repair procedure",
        epilog=rules.RULES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fix.add_argument(
        "paths", nargs="+", help="paths to .comp/.setting files or directories"
    )
    p_fix.add_argument(
        "-o",
        "--output",
        default=None,
        help="output directory (default: overwrite the input file in place)",
    )
    p_fix.add_argument(
        "--schema", type=pathlib.Path, default=None, help="override the schema directory"
    )
    p_fix.set_defaults(func=cmd_fix)

    p_pack = subparsers.add_parser(
        "pack",
        help="check a Templates directory and archive it into a .drfx",
        epilog=pack_mod.DRFX_STRUCTURE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_pack.add_argument("dir", help="path to the Templates directory (must contain Edit/)")
    p_pack.add_argument("-o", "--output", required=True, help="output .drfx path")
    p_pack.add_argument(
        "--schema", type=pathlib.Path, default=None, help="override the schema directory"
    )
    p_pack.set_defaults(func=cmd_pack)

    p_schema = subparsers.add_parser("schema", help="schema-related commands")
    schema_subparsers = p_schema.add_subparsers(dest="schema_command", required=True)
    p_schema_dump = schema_subparsers.add_parser(
        "dump", help="fetch tool input/output definitions from a running Fusion"
    )
    p_schema_dump.add_argument(
        "-o", "--output", default=None, help="schema directory (default: the default schema directory)"
    )
    p_schema_dump.add_argument(
        "--console",
        action="store_true",
        help="do not connect to Resolve; print a line to paste into the Fusion console instead",
    )
    p_schema_dump.set_defaults(func=cmd_schema_dump)

    p_preview = subparsers.add_parser(
        "preview", help="paste .setting files into Fusion and render PNG thumbnails"
    )
    p_preview.add_argument("settings", nargs="+", help="paths to .setting files")
    p_preview.add_argument("--frame", type=int, default=None, help="frame number to render")
    p_preview.add_argument(
        "--project",
        default=None,
        help="Resolve project to create the timeline in (default: the open project)",
    )
    p_preview.add_argument(
        "--console",
        action="store_true",
        help="do not connect to Resolve; print a line to paste into the Fusion console instead",
    )
    p_preview.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="seconds to wait for the result file (default: 120 per .setting file)",
    )
    p_preview.add_argument(
        "--sample-text",
        nargs="?",
        const=preview_mod.DEFAULT_SAMPLE_TEXT,
        default=None,
        help="render the preset as a type sample: replace its text with this string "
        f"(default when the flag is given without a value: {preview_mod.DEFAULT_SAMPLE_TEXT!r})",
    )
    p_preview.add_argument(
        "--sample-size",
        type=float,
        default=preview_mod.DEFAULT_SAMPLE_SIZE,
        help="Text+ Size to use with --sample-text "
        f"(default: {preview_mod.DEFAULT_SAMPLE_SIZE})",
    )
    p_preview.add_argument(
        "--framing",
        choices=preview_mod.FRAMINGS,
        default=None,
        help="content: crop the thumbnail to the non-transparent area of the frame; "
        "frame: scale the whole frame "
        f"(default: {preview_mod.DEFAULT_FRAMING}, or {preview_mod.DEFAULT_SAMPLE_FRAMING} with --sample-text)",
    )
    p_preview.add_argument(
        "--content-margin",
        type=float,
        default=preview_mod.DEFAULT_CONTENT_MARGIN,
        help="margin added on each side of the content area with --framing content, "
        f"as a fraction of its width and height (default: {preview_mod.DEFAULT_CONTENT_MARGIN})",
    )
    p_preview.set_defaults(func=cmd_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is cmd_apply and not args.dry_run and args.output is None:
        parser.error("-o/--output is required unless --dry-run is given")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
