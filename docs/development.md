# Developer documentation


This document describes the internal data, module structure, document format, and tests of fusion-macro-cli. Terms are described in [SKILL.md](../skills/fusion-macro-cli/SKILL.md#terms). The user-facing references (commands, declaration, rules, Resolve behavior) are in [skills/fusion-macro-cli/references/](../skills/fusion-macro-cli/references/).

## Data

### Document kinds

`form.classify` determines the kind of a document from its structure, and every command and check rule refers to this one function.

| Kind | Structure |
|---|---|
| `comp` | The root has the type `Composition` |
| `macro` | The root has no type, and `Tools` has one tool, a `MacroOperator`. The tools of the `.comp` are inside it, and it carries the published inputs and outputs |
| `tool` | The root has no type, and `Tools` has one tool that is not a `MacroOperator`. The tool is saved as it is, and the inspector shows all of its inputs |

`macro` and `tool` are the two forms of a `.setting` (`form.SettingForm`). Any other document is an error. `form.top_tool` returns the top-level tool of a `.setting`.

- A declaration has the form as a type: `publish.MacroDeclaration` (macro name, `[[publish]]`, `[[output]]`) or `publish.ToolDeclaration` (the tool to save). `publish.Declaration` is their union, and `build_setting`, `declare`, and `declaration_to_toml` handle both
- Each check rule has `applies_to`, the set of kinds it applies to. `check` and `fix` run only the rules whose `applies_to` contains the kind of the document
- `show`, `extract`, and `preview` branch on the kind returned by `form.classify`

### Schema

The schema holds a list of inputs for each tool type, and each input has the following attributes.

| Attribute | Content |
|---|---|
| `INPS_ID` | Input name |
| `INPS_DataType` | Value type (`Text` / `Number` / `Image` / `Mask`) |
| `INPB_External` | Whether it can be set from outside |
| `INPID_InputControl` | Control kind (`SliderControl`, `FontFileControl`, and so on) |
| `INPI_IC_ControlGroup` | The group the control belongs to. It is not the unit of a control |
| `INPI_IC_ControlID` | Position within the control. An input without this attribute is the head of a control |
| `INPS_IC_Name` | Display name of the control on the Fusion side |
| `INPS_ICS_ControlPage` | Page name defined by the tool |
| `INPN_Default` | Default value |

The order of inputs preserves the order returned by `GetInputList`. Because control splitting depends on this order, reading and writing the schema preserves the order.

Output ports also have definitions per type. The attributes are those returned by `GetOutputList`, and include at least the output name and the value type (`OUTS_DataType`).

`INPB_IC_Visible` is not used for judgment ([resolve.md](../skills/fusion-macro-cli/references/resolve.md#input-definitions)).

#### Schema file format

Gzip-compressed JSON. `schema dump` writes it and `schema.py` reads it.

```
{
  "version": "21.0.4.0005",
  "registry": [
    { "id": "TextPlus", "class_type": 11, "hidden": false, "modifier": false,
      "category": "Generator", "name": "Text+" },
    ...
  ],
  "tools": {
    "TextPlus": {
      "SettingsNest": { "INPS_ID": "SettingsNest", "INPS_DataType": "Number", ... },
      ...
    },
    ...
  },
  "outputs": {
    "TextPlus": {
      "Output": { "OUTS_ID": "Output", "OUTS_DataType": "Image", ... }
    },
    ...
  },
  "failed": [
    { "id": "OFX_Operator", "reason": "not_a_tool", "class_type": 1 },
    { "id": "Circle", "reason": "add_tool_returned_none", "class_type": null },
    ...
  ]
}
```

| Key | Content |
|---|---|
| `version` | The string of `fusion.GetAttrs()["FUSIONS_Version"]` |
| `registry` | All entries of `GetRegList(1)`. Each key is taken from the attributes of `GetRegAttrs`: `id` is `REGS_ID`, `class_type` is `REGI_ClassType`, `hidden` is `REGB_Hide`, `category` is `REGS_Category`, and `name` is `REGS_Name`. `modifier` is true when bit1 (value 2) of the type class is not set. The localized `REGS_UIName` is not used |
| `tools` | A mapping from type name to a mapping of input name to attribute dictionary. The input order is the order of `GetInputList`. The keys of the attribute dictionary are Fusion attribute names such as `INPS_ID`. Attributes whose value is `None`, `False`, `0`, `0.0`, or `''` are removed |
| `outputs` | A mapping from type name to a mapping of output port name to attribute dictionary. It holds the attributes returned by `GetOutputList` under the same rule as `tools`. It is a separate key from `tools` so that the structure of the input dictionary does not change |
| `failed` | Types whose input definitions could not be acquired. Each has `id`, `reason` (a code), `class_type`, and, for a failure caused by an exception, `detail` (the exception message) |

The `reason` codes are four: `not_a_tool` (type class 1), `add_tool_returned_none`, `add_tool_raised`, and `read_inputs_raised`.
`class_type` is present in every element, and is `null` when the type class cannot be acquired.

Attributes whose value is the default are removed because `Input.GetAttrs()` returns attributes filled with default values (such as `INPI_IC_ControlID: 0`),
and left as is, the head of a control cannot be determined from the presence of `INPI_IC_ControlID` ([resolve.md](../skills/fusion-macro-cli/references/resolve.md#input-definitions)).

A type that could be created with `AddTool` but for which `GetInputList` returned empty is recorded as an empty dictionary in `tools`, not in `failed`.
This distinguishes "exists in Fusion", "can be created", and "has input definitions" through `registry`, `failed`, and `tools`.

`schema/` in the repository is excluded by `.gitignore`.
Absolute paths are embedded in the script pasted into the console, and the package can be imported from elsewhere, so the schema directory is a fixed absolute path and not a repository-relative location.

### Control splitting

Controls are split by scanning the schema's input definitions in declaration order ([resolve.md](../skills/fusion-macro-cli/references/resolve.md#control-boundaries)).

- An input without `INPI_IC_ControlGroup` becomes a control by itself
- An input that has `INPI_IC_ControlGroup` and no `INPI_IC_ControlID` is the head of a control, and the control continues up to just before the next head appears
- An input that has `INPI_IC_ControlID` and no `INPI_IC_ControlGroup` is treated as a control by itself
- A continuation input (an input that has both `INPI_IC_ControlGroup` and `INPI_IC_ControlID`) belongs to the same control as the previous input only when the previous input has the same `INPI_IC_ControlGroup` value.
  If the previous value differs, it is treated as a control by itself
- Controls are not split by the value of `INPI_IC_ControlGroup`, because the same value is reused by multiple controls
- `INPI_PC_ControlGroup` and `INPI_PC_ControlID` are not used for control splitting. They will be added to the specification when the declaration of 3D transform tools needs them

The control of an input in `UserControls` is the set of inputs that share the value of `IC_ControlGroup` within the `UserControls` of the same tool,
sorted in ascending order of `IC_ControlID`. An input without `IC_ControlGroup` becomes a control by itself.
Because `IC_ControlGroup` in `UserControls` is a per-tool number, controls are split by the value.

### Reference graph

The graph is built from a composition. A node is a tool and an edge is a reference. There are two kinds of edges.

- **Connection**: the tool and output that `SourceOp` and `Source` of an input point to
- **Expression**: the tool names and input names that appear in the `Expression` of an input

The tool name namespace is one for the whole composition, including the inside of macros.
Because a tool outside a macro can directly reference a tool inside it with `SourceOp` ([resolve.md](../skills/fusion-macro-cli/references/resolve.md#setting--comp)),
the graph is built once, recursing into the inside of macros, and per-macro queries are narrowed by the set of target tools.
If two tools have the same name, it is an error.

Expressions are written in Lua. References are extracted by the following rules.

- Dot form (`Tool.Port`) is a reference to another tool
- A bare identifier (not a function call) is a reference to an input of the same tool
- Lua reserved words, standard library names (`comp`, `string`, `math`, and so on), the identifier immediately before a function call, and a dot chain that starts right after `)` are not treated as references

An edge has a type. The type is determined from `INPS_DataType` in the schema. If the input is not in the schema,
the `LINKID_DataType` of the tool's `UserControls` is used. If it is in neither, the type is treated as unknown.

Values read out (such as `Default`) are converted to Python types (`int`, `float`, `str`, `bool`) and returned.
`document.py` keeps numbers as text for round trips, while the graph is for queries only, so it converts them.

The graph answers the following queries.

- Edges whose target does not exist (dangling references)
- Tools reachable from a given tool by following connection edges backward (a type can be specified)
- The set of inputs driven by expressions: the (tool name, input name) of inputs that have an `Expression`, and the expression. Used by `no-driven-publish`
- The range of keyframe times: for all tables that have `KeyFrames`, the minimum and maximum of their keys (times). Used by `keyframe-origin`
- The set of inputs of a tool (`Graph.known_inputs`): the union of the schema inputs and `UserControls`
- The output ports of a tool (`Graph.output_ports`): a mapping from output name to type, obtained from the schema. `MacroOperator` is not treated specially
- The control that an input belongs to and its kind (`Graph.control_of`, `Graph.control_type`)

### Macro

When a composition contains a macro, the following is read for each macro.

- **Published inputs**: the `InstanceInput` entries of `Inputs`, in order. Each element has the key, `SourceOp`, `Source`, `Name`, `Page`, `ControlGroup`, `Expression`, and `Default`
- **Outputs**: the (`SourceOp`, `Source`) of the `InstanceOutput` entries of `Outputs`
- A reference graph built from the inner `Tools`

### UserControls

When a tool has `UserControls`, the inputs defined there are read.

| Attribute | Read as |
|---|---|
| `LINKID_DataType` | Value type |
| `LINKS_Name` | Display name |
| `INPID_InputControl` | Control kind |
| `INP_Passive` | Not editable (common in `LabelControl`) |
| `INP_External` | Whether it can be set from outside |
| `ICS_ControlPage` | Page name |
| `INP_Default` | Default value |
| `IC_ControlGroup` / `IC_ControlID` | Group and position of the control |
| `CCS_AddString` (positional argument) | Combo choices |
| Others (`INP_Integer`, `INP_MinScale`, `CLRC_*`, `MBTNC_*`, `TEC_*`, and so on) | All kept as a raw dictionary |

The attributes in the table above are read as named attributes, and all attributes, including the others, are also kept in the raw dictionary.

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
- Writing uses the format recorded at parse time. The rule by which Fusion decides which tables to write on one line is not reproduced ([resolve.md](../skills/fusion-macro-cli/references/resolve.md#setting--comp))
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
skills/fusion-macro-cli/references/  references loaded by the Skill when needed
```

Validate the format with `claude plugin validate .`.
