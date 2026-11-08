# Inspection Rules

The list of inspection rules applied by `check`, `fix`, `apply`, and `pack`.
Each rule has an identifier, the kinds of document it applies to, the data it requires, and whether `fix` repairs it.
Each rule below lists what it detects, what happens in Resolve, and what `fix` does.

A rule applies to some of three kinds of document: `.comp`, macro-form `.setting`, and tool-form `.setting` ([declaration.md](declaration.md)).
Rules for tools apply to all three, because the tools inside a macro and the tool saved in the tool form are carried over from the `.comp`.
Rules for published inputs and outputs apply only to the macro form, the only form that has them.
A rule that does not apply to a kind is not run on it and prints nothing; `check --help` prints the same table.

A rule whose "Requires" column includes the schema is not run when there is no schema, and `skipped rule (no schema): <rule name>` is shown.
When a judgment cannot be made for a published input, `skipped input: ...` is shown.

A violation is shown in the format `<path>: <rule name> <location>: <message>`. A violation fixed by `fix` is shown in the format `<path>: fixed <rule name> <location>: <message>`.

## List

### Rules for tools

| Identifier | Applies to | Description | Requires | Fix |
|---|---|---|---|---|
| `ascii-name` | comp, macro, tool | Tool names consist only of ASCII. The macro name is excluded | — | Yes |
| `unique-name` | comp, macro, tool | Tool names are unique within the composition | — | Yes |
| `no-dangling` | comp, macro, tool | The referenced tools and inputs exist | Graph | Yes |
| `keyframe-origin` | comp, macro, tool | The minimum keyframe time across the whole composition is 0 | Graph | Yes |
| `no-absolute-path` | comp, macro, tool | File references are not absolute paths. `Prefs` of a `.comp` is excluded | — | No |
| `font-available` | comp, macro, tool | The pair of `Font` and `Style` exists on the system | — | No |
| `generator-follows-format` | comp, macro, tool | `UseFrameFormatSettings` of a generator is 1. A generator is a type that has `UseFrameFormatSettings` and `GlobalOut` | Schema | Yes |
| `loader-scaled` | comp, macro, tool | There is an expression referring to `comp.RenderHeight` downstream of a `Loader`. The tool type is not restricted | Schema, Graph | No |
| `no-macro` | comp, macro | `Tools` contains no `MacroOperator`. In the macro form, the inside of the top-level macro is the target. The tool form is excluded: its only tool is by definition not a `MacroOperator` | — | Yes (flatten) |
| `no-media-io` | comp, macro, tool | `Tools` contains no `MediaIn` or `MediaOut` | — | Yes (delete) |

### Rules for published inputs and outputs

| Identifier | Applies to | Description | Requires | Fix |
|---|---|---|---|---|
| `page-controls` | macro | `Page` of a published input is `Controls` | — | Yes |
| `input-exists` | macro | The published input exists in that tool | Schema | Yes |
| `label-unique` | macro | Display names of published inputs that have `Name` are not duplicated within the macro. Inputs without `Name` are excluded | — | Yes |
| `label-control-passive` | macro | A `LabelControl` from `UserControls` has `INP_External = false` | Schema | Yes |
| `no-driven-publish` | macro | No input driven by an expression is published | Graph | Yes |
| `no-connected-publish` | macro | No input connected to the output of another tool is published | Graph | No |
| `no-nest-publish` | macro | No input that is a `NestControl` in the schema is published | Schema, Graph | No |
| `publish-element-selected` | macro | When an input belonging to an element of a multi-element tool is published, that element is selected in the inner tool | Schema, Graph | No |
| `color-control-rgb` | macro | When an input of a `ColorControl` is published, the three inputs red, green, and blue are published from the head of the control | Schema, Graph | No |
| `output-reachable` | macro | The tool and output port that every `InstanceOutput` points to exist inside, and at least one output is of image type | Schema, Graph | No |
| `no-self-image` | macro | No image input refers to the macro's own name | Graph | No |

## Details of rules for tools

### `ascii-name`

**Detects** A tool name contains characters other than ASCII. The macro name is excluded because it is the file name of the `.setting` and can use Japanese as the preset's display name.

**Effect in Resolve** Expressions refer to tools as Lua identifiers in the form `Tool.Port`, so a name containing non-ASCII characters cannot be referenced.

**`fix`** Replaces characters that cannot be used in an identifier with `_` (consecutive `_` become one, and leading and trailing `_` are removed. If the result is empty it becomes `tool`, and if it starts with a digit `_` is prepended), and appends `_2`, `_3`, etc. if it collides with an existing name.
It also rewrites `SourceOp`, `ActiveTool`, and dotted references in expressions that point to that tool.

### `unique-name`

**Detects** Two or more tools have the same name. The namespace of tool names is one for the whole composition, including the inside of macros.

**Effect in Resolve** The reference graph cannot be built, and it is not determined which tool a reference points to. TODO: not verified in Resolve.

**`fix`** Appends a suffix such as `_2` to the tool that appears the second time or later. Existing references are not rewritten, because it cannot be determined which tool they point to.

### `no-dangling`

**Detects** A connection (`SourceOp` and `Source`) or an expression refers to a tool or input that does not exist.

**Effect in Resolve** The connection is broken, and the expression fails to evaluate and yields the previous value or 0 ([resolve.md](resolve.md#expression-evaluation)).

**`fix`** Deletes the connection (`SourceOp` and `Source`) or expression (`Expression`) with the broken reference.
When the fix of `no-media-io` deletes `MediaIn`, connections that referred to `MediaIn` become violations of this rule.

### `keyframe-origin`

**Detects** The minimum keyframe time across the whole composition is not 0. It is judged for the whole composition, not for each modifier.
This detects the state where a `.comp` extracted from a `.drt` becomes a preset while still holding keyframes at positions on the timeline, so that nothing happens for the first few dozen frames after insertion.
A modifier that correctly starts partway through time, such as the start point of a time remap, is not a violation.

**Effect in Resolve** The animation starts late right after the preset is inserted.

**`fix`** Shifts the keys of all `KeyFrames` in parallel by the overall minimum so that it becomes 0. The relative relationships among modifiers are kept, and handles (`RH`, `LH`) are relative values, so they are not moved.

### `no-absolute-path`

**Detects** A file reference is an absolute path. `Prefs` of a `.comp` is excluded, because `Prefs.Comp.LastFile` and `Paths.Map` always contain absolute paths of the environment, and `apply` removes `Prefs`.

**Effect in Resolve** The file cannot be read in other environments. TODO: not verified in Resolve.

**`fix`** Does not repair this rule.

### `font-available`

**Detects** No installed font face has the family in `Font` and the style in `Style` of the same table. Names are compared without case. When `Style` is absent, only the family is compared.

A face matches as the Typographic Family and Subfamily (name IDs 16 and 17), falling back to Family and Subfamily (1 and 2) when they are absent, and also as Family and Subfamily. For a variable font, each named instance is a style. Only the English names are compared. For example, `Font = "A P-OTF Ryumin Pr6N"` with `Style = "M-KL"`, and `Font = "Noto Sans JP"` with `Style = "Black"` (a named instance).

The fonts are read from these directories, including subdirectories.

| OS | Directories |
|---|---|
| macOS | `/System/Library/Fonts`, `/Library/Fonts`, `~/Library/Fonts` |
| Windows | `%WINDIR%\Fonts`, `%LOCALAPPDATA%\Microsoft\Windows\Fonts` |
| Other | `/usr/share/fonts`, `/usr/local/share/fonts`, `~/.local/share/fonts`, `~/.fonts` |

`check`, `fix`, and `apply` scan the directories once per run. A font file that cannot be read is reported as a warning on standard error, and the message of a violation includes the number of such files.

**Effect in Resolve** Resolve renders with a different font. TODO: not verified in Resolve.

**`fix`** Does not repair this rule.

### `generator-follows-format`

**Detects** `UseFrameFormatSettings` of a generator (a type that has `UseFrameFormatSettings` and `GlobalOut`) is not 1.

**Effect in Resolve** The generator does not follow the timeline resolution. TODO: not verified in Resolve.

**`fix`** Sets `UseFrameFormatSettings` to 1.

### `loader-scaled`

**Detects** There is no expression referring to `comp.RenderHeight` downstream of a `Loader`. The tool type is not restricted.

**Effect in Resolve** The loaded image is not scaled to match the timeline resolution. TODO: not verified in Resolve.

**`fix`** Does not repair this rule.

### `no-macro`

**Detects** `Tools` contains a `MacroOperator`. In the macro form, the inside of the top-level macro is the target.

**Effect in Resolve** It has not been verified whether Fusion accepts a `.setting` that has a macro inside a macro, or, if it does, in which layer `Default` and `Expression` take effect ([resolve.md](resolve.md#setting--comp)).

**`fix`** Flattens the macro. If there are further `MacroOperator`s inside, it flattens them recursively.

1. Replace the `MacroOperator` with the contents of its `Tools`. The namespace of tool names is one for the whole composition, so names are not changed
2. Rewrite connections that referred to the macro (`SourceOp` is the macro name, `Source` is a key of `Outputs`) to the inner tool and port that the `InstanceOutput` points to.
   References by expression (`<macro name>.<key>`) are rewritten in the same way
3. Delete the macro's `Inputs`, `Outputs`, and macro name. `Default` of an `InstanceInput` is the value at save time, and the `Value` of the inner tool holds the current value.
   An `InstanceInput` `Expression` has no place in a `.comp`, so write it in the `expression` of the declaration. `show <comp>` lists these before flattening

### `no-media-io`

**Detects** `Tools` contains `MediaIn` or `MediaOut`. These are tools that Resolve adds to each composition.

**Effect in Resolve** If included in the preset, they duplicate the `MediaOut` of the composition it is inserted into.

**`fix`** Deletes these tools. The tool that was connected to the input of `MediaOut` remains, and it becomes a candidate for `[[output]]`.
Connections that referred to `MediaIn` become broken references, which `no-dangling` reports. The fix of `no-media-io` does not repair broken references.

## Details of rules for published inputs and outputs

### `page-controls`

**Detects** `Page` of a published input is not `Controls`.

**Effect in Resolve** The published input is placed on a different page of the Inspector.

**`fix`** Sets `Page` to `Controls`.

### `input-exists`

**Detects** The input that a published input points to does not exist in that tool. The set of inputs checked is the union of the schema inputs and the tool's `UserControls` (`Graph.known_inputs`).

**Effect in Resolve** It is not shown in the Inspector. TODO: not verified in Resolve.

**`fix`** Deletes that publish.

### `label-unique`

**Detects** Display names of published inputs that have `Name` are duplicated within the macro. Inputs without `Name` are excluded.

**Effect in Resolve** The inputs cannot be distinguished in the Inspector. TODO: not verified in Resolve.

**`fix`** Changes the second and later display names to `<display name> (2)`, `<display name> (3)`, and so on.

### `label-control-passive`

**Detects** A `LabelControl` from `UserControls` does not have `INP_External = false`.

**`fix`** Writes `INP_External = false`.

### `no-driven-publish`

**Detects** Among the inputs of inner tools, an input driven by an expression is published. The `Expression` of the `InstanceInput` itself is not a target.
An input that has both a connection and an expression gets its value from the expression ([resolve.md](resolve.md#expression-evaluation)), so only `no-driven-publish` detects it, and it is not a target of `no-connected-publish`.

**Effect in Resolve** The value is supplied by the expression, so operations in the Inspector are not reflected in the rendering.

**`fix`** Deletes that publish.

### `no-connected-publish`

**Detects** An input that receives its value from a connection (`SourceOp` and `Source`) rather than an expression is published.

**Effect in Resolve** The Inspector shows this control with a red keyframe button ([resolve.md](resolve.md#published-inputs-in-the-macro-inspector)).

**`fix`** Does not repair this rule, because the input to publish instead depends on the structure of the source tool. When `TextPlus.StyledText` receives its value from `StyledTextCLS`, the input to publish is `Text` of the modifier.

### `no-nest-publish`

**Detects** A collapsible group (an input that is a `NestControl` in the schema) is published. `Softness<n>` of `TextPlus` is an example.

**Effect in Resolve** A heading with a "∨" and no contents is shown, and Fusion puts the control immediately after it inside that collapsible group.

**`fix`** Does not repair this rule. The inputs to publish are the child inputs (`SoftnessX<n>` `SoftnessY<n>`, etc.).

### `publish-element-selected`

**Detects** An input belonging to an element of a multi-element tool is published, and that element is not selected in the inner tool. The judgment is as follows.

- Only when the type's schema has `SelectElement`
- If the input name has the form `<family><n>` (ending in a decimal number), `Enabled<n>` exists in the schema, and the input's `INPS_ICS_ControlPage` matches `Enabled<n>`, the input belongs to element n
- The selected element is the number obtained by adding 1 to the `Value` of `SelectElement` of the inner tool. If `Value` does not exist, it is element 1.
  If `Value` has an expression or a connection, no judgment is made and that published input is recorded as undeterminable
- It is a violation if element n does not match the selected element. If `<input name>Clone` exists in the schema with `INPB_IgnoreVisible`, the message points to that input
- `Select` (the page tab) is not used for the judgment

**Effect in Resolve** An input belonging to an element of a multi-element tool is not shown in the Inspector unless that element is the one selected in the inner tool.

**`fix`** Does not repair this rule. Changing the selection would hide another element, and replacing the input with a `Clone` input would change the declaration.
`Red1Clone` `Green1Clone` `Blue1Clone` `Alpha1Clone` are shown regardless of the selection. For one `TextPlus`, publishing the element inputs directly shows only one element.

### `color-control-rgb`

**Detects** An input of a `ColorControl` is published, and any of the three inputs red, green, and blue from the head of the control is not published.

**Effect in Resolve** It is shown as a swatch that cannot be operated in the Inspector.

**`fix`** Does not repair this rule.

For controls other than colors, publishing only some of the inputs is not a violation. It is also not required that the head of the control have a display name.
For an input whose schema has `INPS_IC_Name` (such as "始点" for `Start` of `TextPlus`), the name is shown even if `Name` is omitted on the publish side.

### `output-reachable`

**Detects** The tool or output port that an `InstanceOutput` points to does not exist inside, or there is no output of image type.
It is not required that tracing image edges back from the output reaches anything. A macro whose output is a `TextPlus` alone has no upstream tools.
A `.setting` is placed on the timeline and rendered whether it is a title, an effect, or a transition, so one image output is required for all uses. Also having outputs of other types, such as text, is allowed.
Output ports are obtained from `Graph.output_ports`. If the type has no definition of output ports, no judgment is made for that output.
If no output could be judged, it is not reported that there is no image output.

**Effect in Resolve** The preset is not rendered even when placed on the timeline. TODO: not verified in Resolve.

**`fix`** Does not repair this rule.

### `no-self-image`

**Detects** An image input refers to the macro's own name.

**Effect in Resolve** The macro's output returns to its own input and forms a cycle. TODO: not verified in Resolve.

**`fix`** Does not repair this rule.
