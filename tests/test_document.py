"""``fusion_macro_cli.document`` の検査。

単独実行できる(``python3 tests/test_document.py``)。終了コードは、
致命的な失敗が1件でもあれば1、失敗は無いが実データに依存する往復検査
(``FUSION_MACRO_CLI_PRESETS`` または Fusion の ``Templates`` ディレクトリ)が
対象データの不在で行えなかった場合は2、すべて実行できて失敗が無ければ
0を返す。``tests/data/sample.comp``/``sample.setting`` を使う検査は常に
実行する(自作データとしてリポジトリに含まれるため)。

往復テストと字句比較テストで使う字句解析は ``document.py`` と独立に書く。
同じ実装を共有すると、字句の切り方そのものに誤りがあった場合に
両側が同じように誤り、比較が成立しないため。
"""

import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import fusion_macro_cli.document as document  # noqa: E402

SKIP = "skip"
PASS = "pass"


# --------------------------------------------------------------------------
# エスケープ
# --------------------------------------------------------------------------


def test_escape_roundtrip():
    """encode_string と decode_string が互いの逆であることを、
    値からの往復とテキストからの往復の両方で確認する。"""

    values = [
        "",
        "abc",
        "改行\nを含む",
        '引用符"を含む',
        "バックスラッシュ\\を含む",
        '全部\n"\\\n入り混じる',
        "タイトル_青",
    ]
    for value in values:
        raw = document.encode_string(value)
        assert document.decode_string(raw) == value, f"value roundtrip failed for {value!r}"

    raws = [
        "",
        "abc",
        "line1\\nline2",
        'quote\\"here',
        "back\\\\slash",
        'mix\\n\\"\\\\end',
    ]
    for raw in raws:
        value = document.decode_string(raw)
        assert document.encode_string(value) == raw, f"raw roundtrip failed for {raw!r}"

    try:
        document.decode_string("bad\\qescape")
    except document.ParseError:
        pass
    else:
        raise AssertionError("decode_string must raise ParseError for an unknown escape")

    return PASS


# --------------------------------------------------------------------------
# 構造の基本的な解析
# --------------------------------------------------------------------------


def _sample_comp_path() -> pathlib.Path:
    return HERE / "data" / "sample.comp"


def _sample_setting_path() -> pathlib.Path:
    return HERE / "data" / "sample.setting"


def test_parse_sample_comp():
    """``tests/data/sample.comp`` を解析し、
    テーブルの走査・キーでの取得・型名・位置引数の要素が読めることを確認する。

    自作の最小データであり、常にリポジトリに含まれるため
    スキップしない。
    """
    text = _sample_comp_path().read_text(encoding="utf-8")
    doc = document.parse(text)

    assert doc.root.type_name == "Composition"
    # Fusion 自身が書き出す字下げは常にタブ
    assert doc.indent == "\t", f"expected tab indent, got {doc.indent!r}"

    tools = doc.root.get("Tools")
    assert isinstance(tools, document.Table)
    # .comp の最上位の Tools は ordered() が付かない
    assert tools.type_name is None, f"expected no type name on top-level Tools, got {tools.type_name!r}"

    template = tools.get("Template")
    assert isinstance(template, document.Table)
    assert template.type_name == "TextPlus"

    user_controls = template.get("UserControls")
    assert isinstance(user_controls, document.Table)
    assert user_controls.type_name == "ordered()"
    color_select = user_controls.get("ColorSelect")
    assert isinstance(color_select, document.Table)

    # UserControls のコンボ選択肢は位置引数の要素(キーを持たない要素)として
    # 現れる。位置引数の要素を持つテーブルが読めることをここで確認する。
    choice_tables = [el for el in color_select if el.key is None]
    assert len(choice_tables) == 3, "ColorSelect must have 3 positional choice tables"
    for choice in choice_tables:
        assert isinstance(choice.value, document.Table)
        assert choice.value.get("CCS_AddString") is not None

    render_range = doc.root.get("RenderRange")
    assert isinstance(render_range, document.Table)
    assert len(render_range.elements) == 2
    assert all(el.key is None for el in render_range)
    assert all(isinstance(el.value, document.Number) for el in render_range)

    return PASS


def test_parse_sample_setting():
    """``tests/data/sample.setting`` を解析し、``InstanceInput`` を持つ
    ``ordered()`` の ``Inputs`` テーブルが読めることを確認する。
    """
    text = _sample_setting_path().read_text(encoding="utf-8")
    doc = document.parse(text)

    tools = doc.root.get("Tools")
    assert isinstance(tools, document.Table)
    assert tools.type_name == "ordered()"

    macro = tools.get("sample")
    assert isinstance(macro, document.Table)
    assert macro.type_name == "MacroOperator"

    inputs = macro.get("Inputs")
    assert isinstance(inputs, document.Table)
    assert inputs.type_name == "ordered()"
    assert inputs.elements, "the sample macro publishes at least one input"
    for element in inputs:
        assert element.key is not None, "InstanceInput elements are keyed"
        assert isinstance(element.value, document.Table)
        assert element.value.type_name == "InstanceInput"
        assert element.value.get("SourceOp") is not None
        assert element.value.get("Source") is not None

    inner_tools = macro.get("Tools")
    assert isinstance(inner_tools, document.Table)
    assert inner_tools.type_name == "ordered()"
    assert inner_tools.elements

    return PASS


def test_number_literals_preserved():
    """数値を解析せずテキストのまま保持することを確認する。

    Python の float を経由すると "0.10" は "0.1" に、"1e-06" は指数表記のまま
    でも桁が変わりうるほか、"-0" は "0" になるなどテキストが変わる。
    合成した文書で、これらのテキストが変化せずに保持されることを確認する。
    """
    text = '{\n\tA = 0.10,\n\tB = 1e-06,\n\tC = -0,\n\tD = 119,\n}\n'
    doc = document.parse(text)
    assert doc.root.get("A").raw == "0.10"
    assert doc.root.get("B").raw == "1e-06"
    assert doc.root.get("C").raw == "-0"
    assert doc.root.get("D").raw == "119"
    assert document.dump(doc) == text
    return PASS


# --------------------------------------------------------------------------
# 整形(78文字規則)
# --------------------------------------------------------------------------


def _padded_table(char_count: int) -> document.Table:
    """``{ S = "xxx..." }`` の1行描画がちょうど char_count 文字になるテーブル。

    "{ S = \\"\\" }" が10文字で、"x" が1文字増えるごとに1文字伸びる。
    """
    n = char_count - 10
    assert n >= 0
    return document.Table(
        type_name=None,
        elements=[document.Element(document.Key("ident", "S"), document.String("x" * n))],
        inline=False,
        trailing_comma=False,
    )


def _forced_inline_length(table: document.Table) -> int:
    """テーブルを強制的にインライン化した場合の1行描画の文字数。

    format_compact の判定結果に関わらず、テスト側の入力(padded_table)が
    意図した文字数になっているかどうかを、判定から独立に確かめるために使う。
    """
    forced = document.Table(table.type_name, table.elements, True, table.trailing_comma)
    return len(document.dump(document.Document(root=forced, indent="\t", trailing_newline=False)))


def test_format_compact():
    """「入れ子がすべて1行に収まり、かつ自身も78文字に収まる場合に1行」を確認する。"""

    t78 = _padded_table(78)
    assert _forced_inline_length(t78) == 78, "test setup error: padded_table(78) is not 78 chars"
    doc78 = document.Document(root=t78, indent="\t", trailing_newline=False)
    out78 = document.format_compact(doc78)
    assert out78.root.inline is True, "a table whose own single-line form is exactly 78 chars must be inline"

    t79 = _padded_table(79)
    assert _forced_inline_length(t79) == 79, "test setup error: padded_table(79) is not 79 chars"
    doc79 = document.Document(root=t79, indent="\t", trailing_newline=False)
    out79 = document.format_compact(doc79)
    assert out79.root.inline is False, "a table whose own single-line form exceeds 78 chars must not be inline"

    # 入れ子に複数行の表があれば、自身が78文字に収まっても複数行にする
    deep_leaf = document.Table(
        type_name=None,
        elements=[document.Element(document.Key("ident", "S"), document.String("x" * 200))],
        inline=False,
        trailing_comma=False,
    )
    parent = document.Table(
        type_name=None,
        elements=[document.Element(document.Key("ident", "Child"), deep_leaf)],
        inline=False,
        trailing_comma=False,
    )
    nested_doc = document.Document(root=parent, indent="\t", trailing_newline=False)
    out_nested = document.format_compact(nested_doc)
    assert out_nested.root.inline is False
    assert out_nested.root.elements[0].value.inline is False, (
        "a child whose own single-line form exceeds 78 chars stays multi-line, "
        "and the parent must follow"
    )

    return PASS


# --------------------------------------------------------------------------
# 往復(バイト一致・トークン一致)
# --------------------------------------------------------------------------

# document.py の字句解析とは独立に書く。文字列はエスケープを解かず1トークンとして
# 扱う(構造の比較ではなく字句の比較にするため)。
_COMPARE_TOKEN_RE = re.compile(
    r'--[^\n]*'
    r'|"(?:[^"\\]|\\.)*"'
    r"|[A-Za-z_][A-Za-z0-9_]*"
    r"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"|[{}\[\]=,()]"
)


def _compare_tokens(text: str) -> list[str]:
    tokens = []
    pos = 0
    n = len(text)
    while pos < n:
        if text[pos].isspace():
            pos += 1
            continue
        match = _COMPARE_TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(f"cannot tokenize at position {pos}: {text[pos:pos + 20]!r}")
        token = match.group()
        if not token.startswith("--"):
            tokens.append(token)
        pos = match.end()
    return tokens


def _fusion_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion"


def _collect_corpus() -> list[pathlib.Path]:
    roots = []
    env_dir = os.environ.get("FUSION_MACRO_CLI_PRESETS")
    if env_dir:
        env_path = pathlib.Path(env_dir)
        if env_path.is_dir():
            roots.append(env_path)
        else:
            print(f"    FUSION_MACRO_CLI_PRESETS does not exist: {env_path}")
    else:
        print("    FUSION_MACRO_CLI_PRESETS is not set; checking the Fusion directory only")

    fusion_dir = _fusion_dir()
    if fusion_dir.is_dir():
        roots.append(fusion_dir)
    else:
        print(f"    Fusion directory not found: {fusion_dir}")

    files: list[pathlib.Path] = []
    for root in roots:
        for ext in ("*.setting", "*.comp"):
            files.extend(sorted(root.rglob(ext)))
    return files


def _count_tables(node: document.Node) -> int:
    """ノード以下に含まれるテーブルの総数(自身を含む)。"""
    if isinstance(node, document.Table):
        return 1 + sum(_count_tables(el.value) for el in node.elements)
    return 0


def _inline_agreement(original: document.Table, reformatted: document.Table) -> tuple[int, int]:
    """format_compact が解析時の inline と一致させたテーブル数と、比較したテーブルの総数。

    数値合わせが目的ではなく、78文字規則の計測方法(型名を含み、Key = の前置と
    字下げは含まない)による一致率を報告する。"""
    agree = 1 if original.inline == reformatted.inline else 0
    total = 1
    for o_el, r_el in zip(original.elements, reformatted.elements):
        if isinstance(o_el.value, document.Table):
            a, t = _inline_agreement(o_el.value, r_el.value)
            agree += a
            total += t
    return agree, total


def test_roundtrip_corpus():
    """既存の .setting / .comp を解析して書き出し、元と一致することを確認する。

    1層目はバイト単位の比較、2層目は1層目が一致しなかった文書に対する
    トークン単位の比較(違いが空白の入れ方だけに留まることの確認)。

    件数の表明は行わず、トークン単位でも一致しないファイルを列挙する。
    """
    files = _collect_corpus()
    if not files:
        print("    no corpus files found; skipping round-trip test")
        return SKIP

    byte_match = 0
    token_only_match = 0
    mismatches: list[str] = []
    table_count = 0
    inline_agree = 0
    inline_total = 0

    for path in files:
        original = path.read_bytes()
        text = original.decode("utf-8")
        doc = document.parse(text)
        written = document.dump(doc).encode("utf-8")

        table_count += _count_tables(doc.root)
        reformatted = document.format_compact(doc)
        agree, total = _inline_agreement(doc.root, reformatted.root)
        inline_agree += agree
        inline_total += total

        if written == original:
            byte_match += 1
            continue
        original_tokens = _compare_tokens(text)
        written_tokens = _compare_tokens(written.decode("utf-8"))
        if original_tokens == written_tokens:
            token_only_match += 1
        else:
            mismatches.append(str(path))

    total_files = len(files)
    token_match = byte_match + token_only_match
    print(f"    target files: {total_files}")
    print(f"    byte-identical: {byte_match}")
    print(f"    token-only match: {token_only_match} (token-identical total: {token_match})")
    print(f"    mismatched: {len(mismatches)}")
    for name in mismatches:
        mtime = pathlib.Path(name).stat().st_mtime
        print(f"      mismatch: {name} (mtime={mtime:.0f})")
    print(f"    table count: {table_count}")
    if inline_total:
        rate = 100 * inline_agree / inline_total
        print(
            f"    format_compact inline agreement with parsed inline "
            f": {rate:.1f}% ({inline_agree}/{inline_total})"
        )

    assert not mismatches, f"{len(mismatches)} file(s) failed even token-level comparison"
    return PASS


def test_format_compact_roundtrip():
    """.drt から取り出したような1行の文書に整形の規則を通して書き出し、
    トークン列が元の文書と一致することを確認する(構造ではなく字句の比較)。

    sample.comp を独立の字句解析で1行に潰した文字列から parse し、
    format_compact を通して dump した結果を、同じ独立の字句解析で比較する。
    """
    text = _sample_comp_path().read_text(encoding="utf-8")
    original_tokens = _compare_tokens(text)
    # 文字列トークンはエスケープを解かずそのまま連結すれば、空白で区切っても
    # 1行の妥当な文書になる(識別子・数値・記号はいずれも空白区切りで安全)。
    one_line = " ".join(original_tokens)

    doc = document.parse(one_line)
    reformatted = document.format_compact(doc)
    written = document.dump(reformatted)
    written_tokens = _compare_tokens(written)

    assert written_tokens == original_tokens, "format_compact must not change the token stream"
    return PASS


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------

TESTS = [
    ("test_escape_roundtrip", test_escape_roundtrip),
    ("test_parse_sample_comp", test_parse_sample_comp),
    ("test_parse_sample_setting", test_parse_sample_setting),
    ("test_number_literals_preserved", test_number_literals_preserved),
    ("test_format_compact", test_format_compact),
    ("test_format_compact_roundtrip", test_format_compact_roundtrip),
    ("test_roundtrip_corpus", test_roundtrip_corpus),
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
