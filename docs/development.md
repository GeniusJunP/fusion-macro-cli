# Developer documentation

This document describes the module structure, wording, document format, tests, and plugin layout of fusion-macro-cli. The data that the modules read and build is described in [data.md](data.md), and terms in [SKILL.md](../skills/fusion-macro-cli/SKILL.md#terms). The user-facing documents are in [skills/fusion-macro-cli/](../skills/fusion-macro-cli/).

- [Module structure](#module-structure)
- [Wording](#wording)
- [Document format](#document-format)
- [Tests](#tests)
- [Plugin and Skill](#plugin-and-skill)

## Module structure

```
src/fusion_macro_cli/
  document.py     parsing and writing of .setting / .comp
  form.py         determining the document kind (.comp, macro-form or tool-form .setting)
  schema.py       reading the schema, splitting controls, and determining elements of multi-element tools
  expression.py   extracting references from expressions and qualifying bare identifiers
  graph.py        building and querying the reference graph
  macro.py        reading published inputs and outputs of macros
  publish.py      loading publish declarations and assembling the .setting of the declared form
  rules.py        check rules
  sfnt.py         reading the family, style, and variable-font instance names of each face from sfnt (TTF/OTF/TTC/OTC) bytes
  fonts.py        listing the font directories of the OS, scanning them, and matching Text+ Font/Style pairs (used by font-available)
  show.py         displaying the input list
  pack.py         archiving the `Templates` directory into a .drfx
  sources/drt.py      extraction from .drt / .drp
  resolve/_common.py  script generation and connection to Resolve (used by dump and preview)
  resolve/dump.py     schema dump (the part run inside Fusion is dump_script.py3)
  resolve/preview.py  preview (the part run inside Fusion is preview_script.py3)
  cli.py
```

- Dependencies point in one direction. `document` and `expression` are the lowest layer, `form`, `schema`, `graph`, and `macro` sit above them, and `publish`, `rules`, `show`, and `pack` sit above those.
  `publish`, `rules`, `show`, and `pack` do not reference each other. `cli` is the entry point
- The "check, then write" processing of `apply` and `pack` is done by `cli` combining `rules` with `publish` and `rules` with `pack`. `publish` only assembles the document
- `declare` uses the same `publish.py` functions as `apply`
- Only `resolve/` references the Resolve scripting API
- `graph` exposes the traversal of tools that recurses into the inside of `MacroOperator` (`iter_tools`), the graph construction that returns `None` on duplicate names (`try_build_graph`),
  the union of schema inputs and `UserControls` (`Graph.known_inputs`), and the control splitting and kind (`Graph.control_of`, `Graph.control_type`), and `publish`, `rules`, and `show` use them.
  Sibling modules do not reimplement the same traversal or conversion
- `document` exposes the conversion from nodes to Python values (`as_native`, and the type-restricted `as_int`, `as_str`, `as_bool`)
- Because the scripts run inside Fusion have a different execution environment from the CLI side, they are included in the package as `.py3` files and not embedded as strings
- `schema.py` does the control splitting. It scans inputs in declaration order and splits at inputs without `INPI_IC_ControlID` as heads
- The functions of `show` do not search the schema. Without a schema passed, they operate as having no schema. `cli` does the loading from the default directory
- The text of the `--help` format is defined as constants by `publish.py` (declaration format), `show.py` (meaning of columns), and `pack.py` (structure of `.drfx`), and `cli` passes them to the `argparse` epilog
- Script generation, connection to Resolve, and assembly of the one-line console command are common to `schema dump` and `preview`, and `resolve/_common.py` does them
- The qualified expression used for comparison in `preview` is produced by `expression.qualify_same_tool_references`

## Wording

Strings output to users (logs, exception messages, CLI output) are written in English, because they are displayed on the same line as check rule identifiers and file paths.
Comments and docstrings are written in Japanese. Documentation is written in English. Only the README is also written in Japanese (`README.ja.md`), with the same content as `README.md`.

The reason for a failure is held in the data as a code, not as a message. `resolve/dump.py` attaches `not_a_tool`, `add_tool_returned_none`, `add_tool_raised`, and `read_inputs_raised`,
and `schema.Schema.missing_reason` converts them into messages. Changing the messages does not affect files that have already been written.

## Document format

`document.py` does parsing and writing in one module. String escaping is symmetric between the parsing side and the writing side.

- Formatting that cannot be derived from the document is kept as attributes: the width of one indentation level (a tab or 4 spaces; a tab for a document with no clue),
  the presence of a trailing newline, and, for each table, "whether it was written on one line" and "whether it had a trailing comma"
- Numbers are not parsed and are kept as text. When a key is a number (such as `[0.998749]` in keyframes), it is also kept as text
- Comments (from `--` to the end of the line) and blank lines are not kept. Lexical analysis skips them like whitespace. No comments remain in the output of `apply`
- The format of a table is two values per table (one line or multiple lines), and the format in which only the first half of a table is written on the same line as the opening brace is not represented.
  A document in this format does not match byte for byte after writing, but matches token for token
- Only the three string escapes `\n`, `\"`, and `\\` are interpreted. Any other escape sequence is a `ParseError`
- Writing uses the format recorded at parse time. The rule by which Fusion decides which tables to write on one line is not reproduced, because neither the type nor the length of a table determines it
- `apply` applies the formatting rule before writing. The rule is "one line when all nested tables fit on one line and the table itself also fits within 78 characters",
  and the 78 characters are measured by the length of the table itself written on one line (including the type-name prefix, and excluding the `Key = ` from the parent and the indentation)

## Tests

```
python3 tests/run.py
```

This runs `test_*.py` directly under `tests/` in name order. Exit code 0 means pass, 1 means failure, and 2 means the data for verification is absent.
`run.py` returns 1 if any test returned 1, and otherwise returns 0.

The test data in `tests/data/` is hand-made and minimal: a macro (`sample.comp`, `sample.toml`, and `sample.setting`, `TextPlus` and `Background` composited with `Merge`) and a tool-form `.setting` of one `TextPlus` (`sample_tool.setting`).

### FUSION_MACRO_CLI_PRESETS

Tests against existing presets run only when a directory is passed through the environment variable `FUSION_MACRO_CLI_PRESETS`. If it is not passed, those tests exit with exit code 2.

```
FUSION_MACRO_CLI_PRESETS=/path/to/presets python3 tests/run.py
```

### Test contents

**Round trip**: parses existing `.setting` and `.comp` files, writes them out, and confirms they match the originals.
The first layer is a byte-for-byte comparison, and the second layer is a token-for-token comparison for documents that did not match in the first layer, confirming that the only difference is how whitespace is placed.
No structural comparison is made. If string values are written back without escaping, rereading yields the same structure but a different string for Fusion,
and the token comparison treats a string as one token as text, so it detects this difference.
The lexical analysis used for the comparison is implemented independently of `document.py`.

`test_roundtrip_corpus` prints the total number of tables and the match rate of the formatting rule.

**Escaping**: confirms that `encode_string` and `decode_string` are inverses of each other, both for round trips from values and round trips from text.

**Extraction**: confirms that the composition decoded from a `.drt` matches the one written by `comp.Save()` in the same state in the contents of Tools.
`Prefs` and `CustomData` are not compared because they vary with the way of saving. Line wrapping and the presence of commas are aligned before comparison.

**Control splitting**: splits controls from the schema and confirms that `Font` and `Style` of `TextPlus`, the four color components, and `Start` and `End` each form one control,
and that the three controls of `LightDirectional` share the same `ControlGroup`. The splitting implementation is written independently on the test side.

When existing presets are available, it checks that the `Page` of published inputs is `Controls`, that display names are not duplicated within a macro,
and that, when multiple inputs of the same control are published, they have the same `ControlGroup` number. How inputs are published (all or part) and how keys are assigned are not checked, and only the counts are printed.

**Round trip (declaration)**: creates a declaration from an existing `.setting`, applies it, and compares the result with the original. The function that creates declarations is exposed by `publish.py`.
For the tool form, the result must equal the original document. For the macro form, key names and `ControlGroup` numbers are determined by the conventions, so a byte match is not required. What is compared is the set of published (tool, input) pairs, their order, display names, `Page`, `Expression`,
and the macro outputs (the sequence of (`SourceOp`, `Source`)). `Default` must match only for published inputs where the original has a `Default`, has no `Expression`, and the inner `.comp` has an explicit `Value`.

**False positives**: applies the check rules to the existing presets among `FUSION_MACRO_CLI_PRESETS` that are in the macro form or the tool form, and confirms that no violation is reported. `label-control-passive` only prints counts.

**Detection**: applies known transformations (deleting `Page`, renaming a referenced name, shifting keyframe times, changing the input name that a published input points to) to `tests/data/sample.comp` and `sample.setting` (the result of `apply` on `sample.toml`),
and confirms that the corresponding rules report violations.

## Plugin and Skill

The repository doubles as a Claude Code plugin marketplace.

```
.claude-plugin/marketplace.json   marketplace definition; the plugin source is "./" (the repository root)
.claude-plugin/plugin.json        plugin manifest
skills/fusion-macro-cli/SKILL.md  Agent Skill
skills/fusion-macro-cli/authoring/   what the Skill reads to write presets
skills/fusion-macro-cli/reference/   commands and check rules
skills/fusion-macro-cli/resolve/     Resolve environment facts
```

Validate the format with `claude plugin validate .`.
