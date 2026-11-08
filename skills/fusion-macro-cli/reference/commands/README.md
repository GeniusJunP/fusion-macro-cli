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

Each command's input, output, options, and behavior are in the file for its purpose.

| Purpose | Commands | Document |
|---|---|---|
| Create a preset | `extract` `declare` `show` `apply` | [authoring.md](authoring.md) |
| Inspect | `check` `fix` `fonts` | [checking.md](checking.md) |
| Connect to Resolve | `preview` `schema dump` | [resolve.md](resolve.md) |
| Archive for distribution | `pack` | [pack.md](pack.md) |

## Common behavior

- Every command that reads a `.comp` or `.setting` determines its kind from the structure of the document, not from the extension:
  a root of type `Composition` is a `.comp`; a root with one tool at the top level of `Tools` is a `.setting`, in the **macro form** if that tool is a `MacroOperator` and in the **tool form** otherwise ([forms.md](../../authoring/forms.md)).
  A `.setting` with zero or several top-level tools is an error
- `show` `check` `fix` `fonts` take one or more paths. A directory is expanded recursively to the `.comp` and `.setting` files under it, skipping files and directories whose names start with `.`, in path order; the paths are processed in the order given.
  A path that does not exist, or a directory with no target, is an error and nothing is processed

- `show` `apply` `check` `fix` `pack` take `--schema <dir>` and can use a schema in a directory other than the default. The schema directory is described in [schema dump](resolve.md#schema-directory)
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
