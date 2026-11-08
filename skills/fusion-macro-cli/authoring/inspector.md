# Published Inputs in the Inspector

This document describes when a published input of a macro-form `.setting` is shown and editable in the Inspector, and how expressions on inputs are evaluated.
The tool form has no published inputs; its Inspector is the tool's own ([forms.md](forms.md)).
The behavior was verified in Resolve Studio 21.1, both with a `.setting` pasted into a composition on the Fusion page and with the preset inserted as a title on the Edit page.

- [Order](#order)
- [Colors](#colors)
- [Elements of a multi-element tool](#elements-of-a-multi-element-tool)
- [Collapsible groups](#collapsible-groups)
- [Connected inputs](#connected-inputs)
- [Expressions](#expressions)
- [Switching values with a combo](#switching-values-with-a-combo)

## Order

Inserted as a title, the Inspector shows the published inputs in the order of `Inputs`, which is the order of `[[publish]]`.
Pasted into a composition (as `preview` does) and selected, the macro shows them in the order of `GetInputList`.
Whether an input is shown, and how it is drawn, is the same in both cases. Check the order on a title inserted on the Edit page.

Without `Name` (no `label`), an input is shown with the control name on the schema side (`IC_NAME` of `show`, such as "始点" for `Start` of `TextPlus`).
By Fusion's convention only the head of a control has a `Name`.

## Colors

A `ColorControl` is editable only when red, green, and blue are published, starting at the head of the control. Alpha may be left out.
An editable color shows a swatch with a slider for each component; `>` opens and closes the color picker and does not hide the sliders.

| Published | Inspector |
|---|---|
| Red, green, and blue (with or without alpha) | Swatch and sliders, editable |
| Only the head (`Red1`) | A swatch of the red component only |
| Only alpha (`Alpha1Clone`, `TopLeftAlpha`) | A swatch that cannot be edited |
| An `InstanceInput` with an `expression` | Only the swatch (read-only) |
| `Red1Clone` family | Only the swatch, shown regardless of the selected element |

To publish only opacity, add a `SliderControl` input to `UserControls` and drive alpha with an expression that reads it.
For controls other than colors, publishing only some of the inputs is allowed.

## Elements of a multi-element tool

`TextPlus` has `Red<n>`, `Thickness<n>`, and other inputs for each shading element (fill, outline, shadow, ...), and shows only the inputs of the element selected by `SelectElement`.
The selected element is the value of `SelectElement` plus 1, and element 1 when `SelectElement` is absent. Published inputs follow the same switching:
with `SelectElement = 1` in the inner tool, a published `Red2` is shown and a published `Red1` disappears. `Select` (the page tab) does not affect what is shown.

- For the colors of element 1, publish `Red1Clone` `Green1Clone` `Blue1Clone` (and `Alpha1Clone` if needed). They are shown regardless of the selection, and a value set on them also reaches `Red1` and the others
- To show inputs of element 2 or later together with element 1, add inputs to the tool's `UserControls` in the `.comp`, write expressions on the element inputs that read them, and publish the `UserControls` inputs.
  Publishing element inputs directly shows only one element per `TextPlus`

`check` reports an element input of an unselected element as `publish-element-selected`.

## Collapsible groups

An input whose control is a `NestControl` (such as `Softness<n>` of `TextPlus`) is a heading that bundles other inputs, not a value.
Published, it shows an empty heading with "∨", and Fusion puts the control that follows inside it.
Publish the child inputs (`SoftnessX<n>` `SoftnessY<n>`) instead. `check` reports a published group as `no-nest-publish`.

## Connected inputs

An input that receives its value from another tool's output is shown with a red keyframe button when published. Editing it still works:
text typed into `TextPlus.StyledText` connected to `StyledTextCLS` changes the modifier's `Text` and keeps the connection.
Publish the input of the source instead (the modifier's `Text` in this case); the button is then gray. `check` reports this as `no-connected-publish`.

## Expressions

- Expressions are Lua. An input of another tool is referenced as `Tool.Input`, and an input of the same tool by a bare identifier. Tool names are Lua identifiers, so they must be ASCII (`ascii-name`)
- An input driven by an expression takes its value from the expression; edits in the Inspector do not reach the rendering. Do not publish it (`no-driven-publish`):
  add a `UserControls` input, publish that, and reference it from the expression
- When an input has both a connection (`SourceOp`) and an `Expression`, the expression supplies the value and the connection is ignored.
  This happens when an expression is written in Fusion on an input that already had a modifier
- The `Value` of an expression-driven input and the `Default` of its `InstanceInput` are results cached at save time and are not used when the preset is loaded
- A bare identifier can resolve to a name that the tool holds internally instead of a `UserControls` input: a bare `Gap` in a `TextPlus` expression evaluates to 157 even after `Gap` is added to `UserControls`.
  Refer to `UserControls` inputs as `<tool>.<input>` (or `self.<input>`). `preview` reports such mismatches with exit code 1; `check` cannot detect them
- A name that does not exist is not an error. The expression fails to evaluate: a point input keeps its previous value, and a number input becomes 0
- `<tool>.Output.DataWindow[1]` to `[4]` give the pixel bounds of the rendered region (left, bottom, right, top) and follow changes to the text
- An animation driven by an expression on `time`, such as `min(time/comp:GetPrefs().Comp.FrameFormat.Rate, 1)`, has no keyframes

## Switching values with a combo

To switch a color (or another value) inside one preset, add a `ComboControl` input such as `ColorSelect` to `UserControls`, publish it,
and compute the value from the selection in the `expression` of the published color, for example
`iif(ColorSelect == 2, CustomColorR, iif(ColorSelect == 0, 0xE4, 0x41) / 255)`.
The color is then shown as a read-only swatch that follows the combo.
For presets that differ only in values, copy the `.comp` instead and write one declaration per copy.
