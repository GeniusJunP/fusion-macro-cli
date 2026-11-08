# Commands for inspecting

The behavior common to all commands and the exit codes are in [README.md](README.md#common-behavior).

## check

Inspects. The inspection rules are described in [rules.md](../rules.md).

**Input** Paths of `.comp` or `.setting` files, or directories

**Processing** Applies the inspection rules defined for the kind of each target (`.comp`, macro form, or tool form). A rule that is not defined for a kind is not run on it and prints nothing; the table is in [rules.md](../rules.md) and in `check --help`.

**Output** Shows the list of violations on standard output. For each file, it shows the rule name and the location in the format `<path>: <rule name> <location>: <message>`.

- If there is no schema, the rules that require the schema are not run, and the names of the rules that were not run are shown
- If there is even one violation, exit code 1 is returned. A file that cannot be parsed also gives exit code 1

## fix

Fixes the violations that can be fixed. The content of each rule's fix is described in [rules.md](../rules.md).

**Input** Paths of `.comp` or `.setting` files, or directories, and the output destination (`-o`)

**Processing** Fixes violations for rules that have a fix procedure, and re-inspects.

**Output** The fixed document. If no output destination is specified, it is overwritten.

- Shows the names and locations of the rules fixed
- If violations that cannot be fixed remain, they are shown and exit code 1 is returned. What could be fixed is written
- With `-o`, every target is written to the output destination with its own file name. If two targets have the same file name, it is an error and nothing is written

## fonts

Lists the `Font` and `Style` pairs that the Text+ tools in the targets use, and whether each is installed. It uses the same collection and lookup as the `font-available` rule of `check`. It does not connect to Resolve.

**Input** Paths of `.comp` or `.setting` files, or directories

**Output** A table on standard output with the columns `FONT` `STYLE` `STATUS` `FILES`. `STATUS` is `installed` or `missing`, `STYLE` is `-` when the tool has no `Style`, and `FILES` lists the targets that use the pair. Font files that cannot be read are reported on standard error as `fusion-macro-cli: warning: cannot read font <path>: <reason>`.

With `--json`, standard output is JSON instead of the table:

```json
{"fonts": [{"font": "Hiragino Sans", "style": "W8", "installed": true, "files": ["a.setting"]}],
 "unreadable": [{"path": "/Library/Fonts/broken.ttf", "reason": "malformed sfnt"}]}
```

`style` is `null` when the tool has no `Style`.

- Exit code 0 if every pair is installed (or the targets use no font), 1 if any pair is missing or a target cannot be parsed
