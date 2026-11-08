# fonts

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
