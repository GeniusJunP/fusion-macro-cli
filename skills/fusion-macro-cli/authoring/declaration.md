# Publish Declaration

A publish declaration is a TOML file for creating one `.setting` from one `.comp`. `apply` reads it, and `declare` creates it from an existing `.setting`.

The declaration states the form of the `.setting` with `form`; how to choose it is in [forms.md](forms.md).

- [Example (macro form)](#example-macro-form)
- [Example (tool form)](#example-tool-form)
- [Top-level keys](#top-level-keys)
- [Tool form](#tool-form)
- [`[[output]]` (macro form)](#output-macro-form)
- [`[[publish]]` (macro form)](#publish-macro-form)
- [Conventions (macro form)](#conventions-macro-form)

## Example (macro form)

```toml
name = "title_blue"
source = "0003_title.comp"
form = "macro"

[[output]]
tool = "Merge1"

[[publish]]
tool = "Template"
input = "StyledText"
label = "テキスト"

[[publish]]
tool = "Template"
input = "Red1Clone"
label = "カラー"
expression = "iif(ColorSelect == 2, CustomColorR, iif(ColorSelect == 0, 0xE4, 0x41) / 255)"

[[publish]]
tool = "Template"
input = "Green1Clone"

[[publish]]
tool = "Template"
input = "Blue1Clone"
```

## Example (tool form)

```toml
name = "01 Blue Subtitle"
source = "0001_01 Blue Subtitle.comp"
form = "tool"
tool = "Template"
```

## Top-level keys

| Key | Form | Description |
|---|---|---|
| `name` | both | Required. The output file name without `.setting`. In the macro form it is also the macro name, and appears as the key of `Tools` and as `ActiveTool` |
| `source` | both | Required. Path of the input `.comp`, relative to the declaration |
| `form` | both | `"macro"` or `"tool"`. Defaults to `"macro"`. `declare` always writes it |
| `tool` | tool | Required in the tool form. Name of the top-level tool of the `.comp` to save. The tool keeps this name in the `.setting` |
| `[[output]]` | macro | Macro outputs. At least one is required |
| `[[publish]]` | macro | Inputs to publish. The order becomes the order in the Inspector |

Writing `[[output]]` or `[[publish]]` in the tool form, or `tool` in the macro form, is an error: the tool form has no `MacroOperator` to carry published inputs or outputs.

## Tool form

`apply` writes `{ Tools = ordered() { <tool> = <type> { ... } } }`: the named tool with its name, values, expressions, and `UserControls` exactly as in the `.comp`, and no `ActiveTool`.
The constraints of this form are in [forms.md](forms.md#constraints-of-the-tool-form).

## `[[output]]` (macro form)

| Key | Description |
|---|---|
| `tool` | Required. Tool name |
| `port` | Output port name. Defaults to `Output` |

Multiple `[[output]]` can be written. The second and later outputs are used to connect a value other than the rendered result, such as `Heading` of `PolyPath` or `StyledText` of `StyledTextCLS`, to another macro within Fusion. The same (`tool`, `port`) may be written more than once.
The keys of `InstanceOutput` are assigned in order as `MainOutput1` `MainOutput2`.

```toml
[[output]]
tool = "Template"

[[output]]
tool = "CharacterLevelStyling1"
port = "StyledText"
```

At least one output must be of image type ([rules.md](../reference/rules.md#output-reachable)).

## `[[publish]]` (macro form)

| Key | Description |
|---|---|
| `tool` | Required. Tool name |
| `input` | Required. Input name |
| `label` | Display name. If omitted, `Name` is not written to the `InstanceInput`, and the Inspector shows the control name on the schema side (`INPS_IC_Name`). If `label` is written only on the head of a control, the control is shown as one control with one name |
| `page` | Page. Defaults to `Controls`. Anything other than `Controls` is a `page-controls` violation |
| `expression` | An `Expression` written on the `InstanceInput` itself. It is separate from any expression held by the inner tool's input, and is used when an Inspector control changes its value in response to another published input ([inspector.md](inspector.md#switching-values-with-a-combo)) |

Connections do not appear in the declaration. The `Tools` of the `.comp` become the inside of the macro as they are, so the connections of the `.comp` enter the `.setting` as they are.
The declaration describes only information that does not exist in the `.comp`: the `Inputs` and `Outputs` of the `MacroOperator` and the macro name.
The declaration has no inheritance or include feature, and a single declaration alone determines the result.

## Conventions (macro form)

Values that do not exist in the `.comp` are determined from the declaration as follows.

| Item | Convention |
|---|---|
| Completing controls | Not completed. Only the inputs written in `[[publish]]` are published |
| `ControlGroup` | When multiple inputs belonging to the same control are published, they get the same number. A single input gets none. Numbers are assigned from 1 in the order of `[[publish]]` |
| `Page` | `Controls` |
| Key name | `<tool name>_<input name>` |
| `Default` | The value, if the `.comp` `Inputs` has an explicit `Value`. Not written for a published input that has an `expression`. Not written if neither applies |

Which inputs of a control are published is exactly as written in the declaration. `show <comp>` shows the structure of controls, so what to write can be checked before writing the declaration.

`Default` is not filled in from the schema or the default values of `UserControls`. Fusion fills in a default for a published input that has no `Default`,
and does not use `Default` for a published input that has an expression ([inspector.md](inspector.md#expressions)).
Writing it only when the `.comp` has an explicit `Value` makes the initial value when the macro is inserted equal to the current value in the `.comp`.
