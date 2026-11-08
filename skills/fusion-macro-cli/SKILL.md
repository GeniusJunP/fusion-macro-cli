---
name: fusion-macro-cli
description: Build, check, preview, and package DaVinci Resolve title presets (Fusion .setting files, as a macro or as one saved tool) with the fusion-macro-cli command. Use when the user wants to create or rebuild a Resolve title preset from a .drt/.drp/.setting/.comp, decide which inputs to publish to the inspector, write a publish declaration, interpret `check` violations, render preset thumbnails (PNG) in Resolve, or archive a Templates directory into a .drfx.
---

# fusion-macro-cli

`fusion-macro-cli` makes DaVinci Resolve title presets (`.setting`) from Fusion compositions.
It extracts compositions from `.drt` / `.drp` / `.setting`, assembles a `.setting` from a TOML declaration,
checks it, renders it in Resolve to a PNG, and archives a `Templates` directory into a `.drfx`.

What the tool does not do: reproduce Fusion rendering (PNGs come from Resolve through `preview`); decide what to publish or how to name it (that goes in the declaration);
decide how many presets to make (one declaration makes one `.setting`); classify many compositions (aggregate `show` output yourself);
register the `.drfx` in Resolve (the user replaces it and restarts Resolve).

## Terms

- **Tool**: a node in a composition. It has a type (`TextPlus`, `Background`, ...) and a name. Names are unique across the whole composition, including inside macros.
- **Input**: a parameter of a tool. It holds a value, an expression, or a modifier connection.
- **Control**: a set of inputs shown as one widget in the inspector, for example `Red1` `Green1` `Blue1` `Alpha1` of `TextPlus`.
- **Macro**: a `MacroOperator`. It contains tools and exposes selected inputs as `InstanceInput`.
- **Publish**: expose an input outside the macro as an `InstanceInput`.
- **Form**: the shape of a `.setting`. Both forms have exactly one tool at the top level.
  - **Macro form**: the tool is a `MacroOperator` that wraps the tools of the `.comp`. The inspector shows only the published inputs, with the names from the declaration.
  - **Tool form**: the tool is one tool of the `.comp` (typically a `TextPlus`), saved as it is. The inspector is that tool's own inspector, with every input.
- **Schema**: the input and output definitions of each tool type, obtained from Fusion with `schema dump`.
- **Declaration**: the TOML that states the form and, for the macro form, what to publish.
- **`Templates` directory**: a directory with `Edit/` directly under it and the structure `Edit/Titles/<category>/...`, the same structure as Fusion's `Templates/`.

## Check the installation

```
fusion-macro-cli --help
```

If the command is missing, install it with whichever of `uv`, `pipx` or `pip` is available (Python 3.11 or later, no dependencies):

```
uv tool install git+https://github.com/GeniusJunP/fusion-macro-cli
pipx install git+https://github.com/GeniusJunP/fusion-macro-cli
pip install git+https://github.com/GeniusJunP/fusion-macro-cli
```

`schema dump` and `preview` use the scripting API of DaVinci Resolve Studio; all other commands work without Resolve.
Before running them, read [resolve/environment.md](resolve/environment.md) for what to ask the user (Studio and external scripting, or `--console` with Free).

Get the schema once per Resolve version, with a real composition open on the Fusion page:

```
fusion-macro-cli schema dump
```

Without a schema, `show` `check` `fix` `apply` `pack` still run, but rules that need the schema are skipped (`skipped rule (no schema): ...`) and `show` cannot compare with defaults or split controls.

## Flow

```
work/
  0003_title.comp   output of extract; edit this file
  0003_title.toml   the declaration

<Templates>/Edit/Titles/<category>/
  title_blue.setting   output of apply -o
  title_blue.png       output of preview
```

| Stage | Input | Output | Notes |
|---|---|---|---|
| `schema dump` | — | schema | Once per Resolve version |
| `extract` | `.drt` / `.drp` / `.setting` | `.comp` | |
| `declare` | `.setting` | `.toml` | Makes a declaration of the same form from an existing preset when rebuilding it |
| Edit the `.comp` | `.comp` | `.comp` | Values, expressions, `UserControls`, and tool names are edited in the `.comp` |
| `check <comp>` / `fix <comp>` | `.comp` | violations / `.comp` | Repair violations before making the macro |
| `show <comp>` | `.comp` | stdout | Publishing candidates, with the schema |
| Write the declaration | — | `.toml` | Choose the form first |
| `apply` | `.comp` + `.toml` | `.setting` | Runs `check` internally and writes nothing if there are violations |
| `show <setting>` | `.setting` | stdout | The published result (macro form) or the saved tool's inputs (tool form) |
| `preview` | `.setting` | PNG and a timeline | The user checks rendering and inspector by eye in Resolve |
| `pack` | `Templates` directory | `.drfx` | Archives only what passes the checks |

### 1. Extract the composition

```
fusion-macro-cli extract project.drt -o work/        # → work/0003_title.comp
```

To rebuild an existing preset, make the `.comp` and the declaration from the `.setting`. Both work for either form.
From a macro form, the extracted `.comp` does not contain the macro's `Inputs`, `Outputs`, or name; `declare` writes them into the declaration.
From a tool form, the `.comp` holds the saved tool with its name and is named after the `.setting` file; `declare` writes `form = "tool"` and the tool name.

```
fusion-macro-cli extract old.setting -o work/
fusion-macro-cli declare old.setting -o work/old.toml --source 0001_old.comp
```

### 2. Check, repair, and list candidates

```
fusion-macro-cli check work/0003_title.comp
fusion-macro-cli fix work/0003_title.comp            # overwrites; -o <dir> writes elsewhere
fusion-macro-cli show work/0003_title.comp
```

`check` `fix` `show` `fonts` take several paths and directories at once (`fusion-macro-cli check work/ other.setting`). `fonts` reports which of the Text+ fonts used by a preset are installed on this machine.

A `.comp` extracted from a `.drt` usually contains the `MediaIn` and `MediaOut` that Resolve adds and keyframes at the timeline position.
`fix` repairs the violations that have a repair procedure (`no-media-io`, `keyframe-origin`, and others). Violations that remain are handled by editing the `.comp`.
After `fix` removes `MediaIn`, connections from it become `no-dangling` violations; reconnect them in the `.comp` if they had a real source.

If the `.comp` contains a `MacroOperator`, run `show` before `fix`: `fix` flattens the macro (`no-macro`) and deletes its `Inputs` and `Outputs`.
An `InstanceInput` `Expression` has no place in the `.comp`; carry it over to the declaration's `expression`.

`show <comp>` lists, per tool, the inputs that can be set from outside: `HEAD` marks the head of a control, `SIBLINGS` lists the other inputs of the same control,
and `IC_NAME` is the name the inspector shows when `label` is omitted. Inputs equal to their defaults are only counted; `--all` lists them.

### 3. Edit the `.comp`

Values, expressions, `UserControls`, and tool names are edited in the `.comp`. The declaration only holds the form and the information the `.comp` lacks: for the macro form, the published inputs, the macro outputs, and the macro name; for the tool form, the tool to save.
For presets that differ only in values, such as color variants, copy the `.comp`, change the values, and write a declaration for each copy.

### 4. Choose the form and write the declaration

1. Choose the form with [authoring/forms.md](authoring/forms.md): the macro form to show only chosen inputs with chosen names or to combine tools, the tool form to save one self-rendering tool with its own Inspector
2. For the macro form, choose the inputs to publish with [authoring/inspector.md](authoring/inspector.md). A published input is shown and editable only under the conditions there
   (colors, elements of `TextPlus`, collapsible groups, connected and expression-driven inputs). Add `UserControls` inputs to the `.comp` when the input to publish does not exist yet
3. Write the declaration as described in [authoring/declaration.md](authoring/declaration.md). The order of `[[publish]]` is the order in the Inspector; put `label` only on the head of a control

### 5. Apply

```
fusion-macro-cli apply work/0003_title.toml --dry-run
fusion-macro-cli apply work/0003_title.toml -o "<Templates>/Edit/Titles/<category>"
```

`--dry-run` shows the check result and the `show` output of the result without writing. With violations, `apply` writes nothing and exits with 1.
Violations print as `<path>: <rule> <location>: <message>`; look up the rule in [reference/rules.md](reference/rules.md). Fix the cause in the `.comp` or the declaration and apply again.

### 6. Show and preview

```
fusion-macro-cli show "<Templates>/Edit/Titles/<category>/title_blue.setting"
fusion-macro-cli preview "<Templates>/Edit/Titles/<category>/title_blue.setting"
```

`preview` renders the last frame of the inserted item at 103×58 and writes `<name>.png` next to the `.setting`; this PNG is the preset thumbnail.
By default the thumbnail is cropped to the rendered content plus a 10% margin (`--framing content`), so a caption near the edge of the frame still fills the thumbnail. Use `--framing frame` when the position within the frame matters.
For text-style presets (tool form, one Text+), pass `--sample-text Ag --sample-size 0.5` to render a type sample at a fixed size instead of the preset's own text; framing then defaults to `frame` so samples stay comparable.
`--frame N` writes `<name>_f<N>.png`. `--sample-text [TEXT]` renders a type sample of a tool-form `TextPlus` with the text replaced by `Ag` or `TEXT`.
It leaves a timeline named `fusion-macro-cli preview YYYY-MM-DD HHMMSS`.

A passing `check` and a written PNG do not prove the preset is right: whether inputs are shown and editable cannot be confirmed by `check` alone.
Ask the user to look at the rendering and the Inspector on the timeline that `preview` left, and do not report the preset as correct before the user confirms it.
`preview` pastes the preset into a composition, where the Inspector order follows the tool's input list; ask the user to check the order on the preset inserted as a title ([authoring/inspector.md](authoring/inspector.md#order)).

### 7. Pack

```
fusion-macro-cli pack "<Templates>" -o titles.drfx
```

`pack` checks every `.comp` and `.setting` under `Edit/` and writes the archive only if there are no violations.
After replacing a `.drfx`, the user must restart Resolve ([resolve/environment.md](resolve/environment.md#replacing-presets)).

## Exit codes

0 success; 1 violations, a file error, or a failure in Resolve; 2 invalid arguments.

## Documents

Read these when the stage needs them.

| Stage | Document | Contents |
|---|---|---|
| Before `schema dump` / `preview`, after `pack` | [resolve/environment.md](resolve/environment.md) | What to ask the user: Studio or Free, external scripting, open project, restart after replacing a `.drfx` |
| 4 | [authoring/forms.md](authoring/forms.md) | The two forms, how to choose, and the constraints of the tool form |
| 4 | [authoring/inspector.md](authoring/inspector.md) | When a published input is shown and editable, and how expressions are evaluated |
| 4 | [authoring/declaration.md](authoring/declaration.md) | The declaration format and how keys, `ControlGroup`, `Page`, and `Default` are derived |
| 2, 5, 7 | [reference/rules.md](reference/rules.md) | Each check rule: what it detects, what happens in Resolve, and whether `fix` repairs it |
| Any | [reference/commands.md](reference/commands.md) | Every command's input, output, options, behavior, and exit codes ([schema dump](reference/commands/schema-dump.md) and [preview](reference/commands/preview.md) in their own files) |
