# Commands

```
extract <drt|drp|setting> -o <dir>       Extract compositions
declare <setting> [-o <toml>] [--source <comp>]
                                         Create a declaration from an existing preset
show <comp|setting|dir>... [--all]       Show candidates to publish, the publish result, or the saved tool
apply <toml> [-o <dir>] [--dry-run]      Apply a declaration and write a preset
check <comp|setting|dir>...              Inspect
fonts <comp|setting|dir>... [--json]     List the Text+ fonts used and whether each is installed
fix <comp|setting|dir>... [-o <dir>]     Fix the violations that can be fixed
schema dump [-o <dir>] [--console]       Dump the schema
preview <setting>... [--frame N] [--project NAME] [--console] [--timeout SEC]
                     [--sample-text [TEXT]] [--sample-size SIZE]
                     [--framing content|frame] [--content-margin RATIO]
                                         Render in Resolve and write a PNG
pack <dir> -o <drfx>                     Inspect a `Templates` directory and archive it into a .drfx
```

- [Common behavior](#common-behavior)
- [extract](#extract)
- [declare](#declare)
- [show](#show)
- [apply](#apply)
- [check](#check)
- [fix](#fix)
- [fonts](#fonts)
- [pack](#pack)
- [schema dump](commands/schema-dump.md) and [preview](commands/preview.md), the two commands that connect to Resolve, are in their own files

## Common behavior

- Every command that reads a `.comp` or `.setting` determines its kind from the structure of the document, not from the extension:
  a root of type `Composition` is a `.comp`; a root with one tool at the top level of `Tools` is a `.setting`, in the **macro form** if that tool is a `MacroOperator` and in the **tool form** otherwise ([forms.md](../authoring/forms.md)).
  A `.setting` with zero or several top-level tools is an error
- `show` `check` `fix` `fonts` take one or more paths. A directory is expanded recursively to the `.comp` and `.setting` files under it, skipping files and directories whose names start with `.`, in path order; the paths are processed in the order given.
  A path that does not exist, or a directory with no target, is an error and nothing is processed

- `show` `apply` `check` `fix` `pack` take `--schema <dir>` and can use a schema in a directory other than the default. The schema directory is described in [schema dump](commands/schema-dump.md#schema-directory)
- Only `schema dump` and `preview` connect to Resolve; the other commands work in environments without Resolve
- Each command loads the schema internally. If the schema is not found, it runs as if there were no schema
- In addition to the list of arguments, `--help` shows the format the command requires. `apply --help` shows the declaration format (each key and whether it can be omitted),
  `show --help` shows the meaning of the columns (including `same` `diff` `unknown` of `DIFF`), `check --help` and `fix --help` show which rules apply to which kind of document, and `pack --help` shows the structure of the `.drfx`
- `extract` `declare` `show` `check` `fix` `apply` `pack` report a failure caused by the target file
  as a one-line error (`fusion-macro-cli: <path>: <reason>`) with exit code 1.
  A traceback is printed only for a bug in the tool itself
- Argument errors are reported by `argparse` with exit code 2
- Publish editing operations are not provided as separate commands. To try a single publish, pass a declaration containing it to `apply --dry-run`

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. For `check` `fix` `pack` `apply`, there are no violations |
| 1 | There are violations, a failure caused by the target file, or a failure in processing in Resolve |
| 2 | Argument error |

## extract

Extracts compositions from a `.drt`, `.drp`, or `.setting`. It does not connect to Resolve.
The kind is determined by content, not by extension (a ZIP is a `.drt` / `.drp`; otherwise it is a `.setting`).

**Input** Path of a `.drt`, `.drp`, or `.setting`, and the output directory (`-o`, required)

**Output** Writes `<sequence number>_<name>.comp` to the output directory. The sequence number has 4 digits and starts at 1. For each file written, it shows `fusion-macro-cli: wrote <path>`.

### `.drt` / `.drp`

Opens the ZIP, decodes the `<CompositionBA>` element of `SeqContainer/<uuid>.xml`, and extracts the `.comp` text.

It does not parse the XML. It searches the start tag and end tag of `<CompositionBA>` as text and takes the content between them.
If there is no end tag matching a start tag, that XML is recorded as one failure.

If the body contains `Compressed = true, }`, it decompresses the compressed data that follows and restores the text by inserting `Tools = <contents> }` at the position of `Compressed = true, }`.
The replacement includes the `}` at the end of the plaintext part. `ordered()` is not added to the top-level `Tools`.

Sequence numbers are assigned in order of appearance in the file, and when spanning multiple `SeqContainer/<uuid>.xml` files, in order of appearance in the archive.
The name is determined in this order.

1. The last segment of the composition's `CustomData.TEMPLATE_ID`
2. The BLOB's `<n>_name`

zstd is not in the standard library, so `FieldsBlob` is not read.

- If one `.drt` has multiple `<CompositionBA>`, all are extracted
- If `count` is 2 or more, one file is written for each `<n>`
- If a name contains characters that cannot be used in a file name (`/` `\` `:` `*` `?` `"` `<` `>` `|` and control characters), they are replaced with `_`
- If there are multiple identical names, they are distinguished by sequence number
- A composition that does not contain `Compressed = true` is treated as `.comp` text as is
- If `Compressed = true, }` appears anywhere other than the end of the plaintext part, it is an error, and the plaintext is not returned as is
- The trailing NUL of the decompressed `Tools` body is removed
- The 1-byte flag of the BLOB is skipped
- If the extracted text cannot be parsed with `document.parse`, that composition is treated as having failed to decode
- If the key structure of the BLOB (the order of keys under `count`) is broken, the whole `<CompositionBA>` element is recorded as one failure
- Each composition keeps the in-archive path of the XML it came from
- If any composition failed to decode, it shows the sequence number and name and returns exit code 1. Compositions that decoded are written

### `.setting`

Writes the tools of the `.setting` as a `.comp`. The output is one file.

| Form | Written to the `.comp` | File name |
|---|---|---|
| macro | The `Tools` inside the `MacroOperator`. The macro's `Inputs`, `Outputs`, and name are not included (they are read with `show <setting>` and `declare`) | `0001_<macro name>.comp` |
| tool | The saved tool, with its name | `0001_<.setting file name>.comp`. The tool name (usually `Template`) is not unique across presets |

A document that is neither form (for example, several tools at the top level) yields a one-line error and exit code 1.

## declare

Creates a publish declaration from an existing `.setting`. It is the inverse of `apply`. It does not connect to Resolve.

**Input** Path of the `.setting`, path of the `.toml` to write (`-o`), path of the `.comp` to write in `source` (`--source`)

**Processing** Writes a declaration of the same form as the `.setting`, with `form` always written.

For the tool form, it writes `name` (the `.setting` file name without the extension) and `tool` (the name of the top-level tool).

For the macro form, it writes `name` (the macro name) and reads `Inputs` and `Outputs` of the `MacroOperator` into `[[publish]]` and `[[output]]`.

- `label` is not written for an `InstanceInput` that has no `Name`
- `page` is not written when `Page` is `Controls`
- `ControlGroup` is not written (`apply` assigns it from the order)
- `Default` is not written (it is determined from the current value in the `.comp`)
- All `Outputs` are written to `[[output]]` in their order. Even if multiple `InstanceOutput`s point to the same (`SourceOp`, `Source`), they are not merged

**Output** The declaration TOML. If `-o` is omitted, it is written to standard output.

- `source` is specified with `--source`. If omitted, the name of the `.comp` that `extract` writes for the same `.setting` is written (`0001_<macro name>.comp` for the macro form, `0001_<.setting file name>.comp` for the tool form)
- If the document is neither form, or a macro form has empty `Outputs`, it returns a one-line error and exit code 1

## show

Reads compositions and shows a list of inputs on standard output. It does not write files.
It does not accept a declaration (`.toml`). To see the result of applying a declaration, use `show <setting>` after `apply`, or use `apply --dry-run`.

**Input** Paths of `.comp` or `.setting` files, or directories. The tables are written one after another. A file that cannot be read gives a one-line error on standard error, the rest are still shown, and the exit code is 1

**Options** `--all` also lists inputs whose value equals the default. `--schema <dir>` schema directory

Given a `.comp`, it shows **candidates to publish**. For each tool, it lists the inputs that can be set from outside in declaration order, and for each input shows the following.

- The input name, and whether it is the head of a control
- The control type (`INPID_InputControl`) and the inputs belonging to the same control
- The value type
- The current value in the `.comp` and its difference from the schema default
- Whether there is an expression or a modifier connection
- Whether it was added as `UserControls`

Given a macro-form `.setting`, it shows the **publish result**. It lists the `InstanceInput`s in order and shows
the key, display name, the tool and input it points to, the `ControlGroup` number, `Page`, `Default`, and `Expression`.

Given a tool-form `.setting`, it shows the **inputs of the saved tool** with the same columns and the same selection as for a `.comp`. The Inspector shows every input of the tool, so this is the list of what the preset changes from the tool's defaults.

**Output format** Tab-separated. The first line shows which table it is (`candidates for publishing (.comp)` / `published inputs (.setting)` / `inputs of the saved tool (tool-form .setting)`) and the target path,
followed by a header line and then a block for each tool. One line per input. Japanese display names are mixed in, so fixed width is not used.

```
candidates for publishing (.comp): tests/data/sample.comp
HEAD	INPUT	IC_NAME	CONTROL	SIBLINGS	TYPE	VALUE	DEFAULT	DIFF	EXPRESSION	CONNECTION	SOURCE
== Template (TextPlus) ==
head	Red1	カラー	ColorControl	Green1,Blue1,Alpha1	Number	0.254901960784314	1.0	diff	iif(ColorSelect == 0, 0xE4, 0x41) / 255	-	schema
-	Green1	-	ColorControl	Red1,Blue1,Alpha1	Number	0.243137254901961	1.0	diff	-	-	schema
head	StyledText	-	TextEditControl	-	Text	"サンプルテロップ"	-	diff	-	-	schema
head	Font	-	FontFileControl	Style	Text	"Helvetica"	-	diff	-	-	schema
... 547 further input(s) unchanged (pass --all to list)
head	ColorSelect	-	ComboControl	-	Number	-	-	same	-	-	user-controls
```

```
published inputs (.setting): tests/data/sample.setting
KEY	NAME	TOOL	INPUT	GROUP	PAGE	DEFAULT	EXPRESSION
== sample ==
Template_Red1Clone	カラー	Template	Red1Clone	-	Controls	-	iif(ColorSelect == 0, 0xE4, 0x41) / 255
```

| Column | Description |
|---|---|
| `HEAD` | Whether it is the head of a control |
| `INPUT` | Input name |
| `IC_NAME` | The control name on the schema side (`INPS_IC_Name`). It is the name shown in the Inspector when `label` is omitted, and is used to tell which of similarly named inputs such as `Red` `Red1` `Red1Clone` is the one that appears in the Inspector |
| `CONTROL` | Control type |
| `SIBLINGS` | Other inputs belonging to the same control |
| `TYPE` | Value type |
| `VALUE` / `DEFAULT` | Current value in the `.comp` and the schema default |
| `DIFF` | `same` / `diff` / `unknown` |
| `EXPRESSION` / `CONNECTION` | Expression and modifier connection |
| `SOURCE` | `schema` or `user-controls` |
| `KEY` | The key of the `InstanceInput`. Used to identify keys that Fusion generated automatically, such as `Input1` |

- For a `.comp`, inputs that have a value different from the default, inputs that have an expression or a connection, and inputs whose other inputs in the same control satisfy those conditions are listed first.
  The rest are shown only as a count, and `--all` lists all of them (the order stays the declaration order)
- Compound values (tables such as `Center = { 0.5, 0.119 }`) cannot be compared with the default, so they are `unknown` and always listed
- The block of a `MacroOperator` lists the macro's `Inputs` and `Outputs`. The columns of `Inputs` are the same as `published inputs (.setting)`
  (key, `Name`, `SourceOp`, `Source`, `ControlGroup`, `Page`, `Default`, `Expression`), followed by each port of `Outputs` shown with its key, `SourceOp`, and `Source`.
  When `fix` fixes `no-macro`, `Inputs` and `Outputs` are removed, so check the values needed for the declaration with `show` before `fix`
- Inputs of `UserControls` are always listed and marked `user-controls`. `SIBLINGS` and `HEAD` are shown with the same meaning as for schema-derived inputs
- If there is no schema, comparison with defaults and control splitting cannot be done, so only inputs that have a value, expression, or connection are listed, and this is stated.
  If the schema exists but has no input definition for that type, the same treatment applies to that type

## apply

Applies a declaration and writes a preset. The declaration format is described in [declaration.md](../authoring/declaration.md).

**Input** Path of the declaration, output directory (`-o`)

**Options** `--dry-run` shows the inspection result and the publish result without writing. `--schema <dir>` schema directory

**Processing** Reads the `.comp` that `source` of the declaration points to, assembles a `.setting` of the declared `form`, inspects it, and writes it.
It does not modify the `.comp`. Applying the same declaration any number of times gives the same result. Assembly does not query anything external.

For the tool form:

1. Read the `.comp` of `source`
2. Take the top-level tool named by `tool`, with its name and contents unchanged. Other tools are not written
3. Write it as the only tool of `Tools = ordered() { ... }`, without `ActiveTool`
4. Apply the inspection rules for the tool form to the assembled `.setting`

For the macro form:

1. Read the `.comp` of `source`
2. For each `[[publish]]` from the top, obtain the control from the schema and generate an `InstanceInput`.
   The key is `<tool name>_<input name>`, `SourceOp` and `Source` are the `tool` and `input` of the declaration, `Name` is `label` (not written if omitted), `Page` is the `page` of the declaration (default `Controls`),
   `ControlGroup` is a per-control sequence number only when multiple inputs of the same control are published, `Default` is the current value in the `.comp` ([declaration.md](../authoring/declaration.md#conventions-macro-form)), and `Expression` is the `expression` of the declaration.
   Control determination is the same as `show`: schema-derived inputs use the schema's splitting, and `UserControls`-derived inputs use shared `IC_ControlGroup`
3. Make each `[[output]]` into an `InstanceOutput` in order. The key is `MainOutput<n>`
4. Make a `MacroOperator` and put the `Tools` of the `.comp` inside as they are
5. Apply the inspection rules for the macro form to the assembled `.setting`

**Output** `<output directory>/<name>.setting`. A category directory of a `Templates` directory can be specified directly as the output directory.

- If the inspection finds violations, nothing is written, the violations are shown on standard error, and exit code 1 is returned
- With `--dry-run`, nothing is written, and the inspection result and output equivalent to `show <setting>` are shown on standard output. The exit code is the same as normal `apply`, and is 1 if there are violations.
  With `--dry-run`, `-o` is not required, and nothing is written even if `-o` is given. Without `--dry-run`, `-o` is required
- If a tool or input that the declaration points to does not exist, it is shown together with the index of the `[[publish]]` (or the `tool` of the tool form) and exit code 1 is returned
- In the tool form, a `tool` that is a `MacroOperator` is an error
- In the macro form, if there is no `[[output]]`, it is an error. Outputs are not inferred from unreferenced tools
- If the tool that an `[[output]]` points to is not in the `.comp`, it is shown together with the index and exit code 1 is returned
- `Prefs` of the `.comp` (the absolute paths of `Paths.Map`, `LastFile`), `Version`, and `CustomData.TEMPLATE_ID` are not included in the `.setting`
- The top level is written in the `.setting` format (`Tools = ordered() {`)
- The formatting rules ([development.md](https://github.com/GeniusJunP/fusion-macro-cli/blob/main/docs/development.md#document-format)) are applied before writing

## check

Inspects. The inspection rules are described in [rules.md](rules.md).

**Input** Paths of `.comp` or `.setting` files, or directories

**Processing** Applies the inspection rules defined for the kind of each target (`.comp`, macro form, or tool form). A rule that is not defined for a kind is not run on it and prints nothing; the table is in [rules.md](rules.md) and in `check --help`.

**Output** Shows the list of violations on standard output. For each file, it shows the rule name and the location in the format `<path>: <rule name> <location>: <message>`.

- If there is no schema, the rules that require the schema are not run, and the names of the rules that were not run are shown
- If there is even one violation, exit code 1 is returned. A file that cannot be parsed also gives exit code 1

## fix

Fixes the violations that can be fixed. The content of each rule's fix is described in [rules.md](rules.md).

**Input** Paths of `.comp` or `.setting` files, or directories, and the output destination (`-o`)

**Processing** Fixes violations for rules that have a fix procedure, and re-inspects.

**Output** The fixed document. If no output destination is specified, it is overwritten.

- Shows the names and locations of the rules fixed
- If violations that cannot be fixed remain, they are shown and exit code 1 is returned. What could be fixed is written
- With `-o`, every target is written to the output destination with its own file name. If two targets have the same file name, it is an error and nothing is written

## fonts

See [commands/fonts.md](commands/fonts.md).

## pack

Inspects a `Templates` directory and archives it into a `.drfx`. It does not connect to Resolve.

**Input** Path of the `Templates` directory, path of the `.drfx` to write (`-o`, required)

**Processing** Inspects all `.comp` and `.setting` files under `Edit/` (including subdirectories) and, if there are no violations, writes everything under `Edit/` as a zip.
A `.drfx` is a zip with the structure of Fusion's `Templates/`, differing only in extension.

**Output** The `.drfx` at the specified path

- If there is no `Edit/` directly under it, it returns a one-line error and exit code 1
- If the inspection finds violations, nothing is written, the violations are shown, and exit code 1 is returned
- All files under `Edit/` are included. Only files and directories whose names start with `.` (`.git` `.DS_Store` `.venv`, etc.) are excluded. No selection by extension is done
- Paths in the archive are relative to the `Templates` directory (`Edit/...`) and are sorted by name. The same `.drfx` is obtained from the same `Templates` directory
- If the output destination is inside the `Templates` directory, the output file itself is excluded
- It is written with `zipfile` and no external commands are used

After replacing the `.drfx` in Resolve, restart Resolve.
