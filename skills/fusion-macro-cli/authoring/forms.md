# Forms of a `.setting`

A `.setting` has one of two forms. The declaration states which one with `form` ([declaration.md](declaration.md)).

| Form | Top-level tool of the `.setting` | Inspector | The declaration holds |
|---|---|---|---|
| `macro` | A `MacroOperator` that contains the tools of the `.comp` | Only the published inputs, with the names given in the declaration | The macro name, the published inputs, and the macro outputs |
| `tool` | One tool of the `.comp`, saved as it is | Every input of the tool, as the tool's own Inspector | The tool to save and the file name |

Both forms have exactly one tool at the top level of `Tools`; `check`, `show`, `extract`, `declare`, and `preview` tell them apart by the type of that tool.

## Choosing the form

- **Macro form** when the Inspector should show only some inputs, with names you choose (Japanese labels, a color driven by a `ColorSelect` combo, ...), or when the preset combines several tools (`TextPlus` with `Background` and `Merge`, modifiers, ...).
  Which inputs to publish is decided by [inspector.md](inspector.md)
- **Tool form** when one tool renders the whole preset and its standard Inspector is what the user should edit, such as a text style that is only a `TextPlus` with fonts, colors, and shading set.
  `preview --sample-text` renders a type sample of such a preset

## Constraints of the tool form

- Only the named tool is written. Other tools of the `.comp` are not, so a connection from the saved tool to one of them becomes a dangling reference, which `apply` reports as `no-dangling`.
  Only a tool that renders on its own (such as a `TextPlus`) suits the tool form; use the macro form when the preset needs more than one tool
- A `MacroOperator` cannot be saved in the tool form; write a macro-form declaration for it
- The tool form has no published inputs or outputs. The rules for tools still apply, and the rules for published inputs do not ([rules.md](../reference/rules.md))
- The tool keeps its name (usually `Template`). The name of the preset in Resolve is the file name of the `.setting`
