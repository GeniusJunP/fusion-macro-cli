# DaVinci Resolve / Fusion behavior

This document summarizes the DaVinci Resolve and Fusion behavior that fusion-macro-cli relies on.
All of it was verified on a real installation. For the procedure, see [SKILL.md](../SKILL.md). For command specifications, see [commands.md](commands.md).

## Verified environments

- DaVinci Resolve Studio 21.0.4.0005 (macOS)
- DaVinci Resolve Studio 21.2 (macOS). The `DataWindow` and `Crop` items under "Pasting into a composition and rendering" were verified in this environment
- DaVinci Resolve Studio 21.1.0.0014 (macOS). The sections on the `schema dump` implementation, `preview`,
  the inspector display, and expression evaluation were verified in this environment
- The Python inside Fusion is 3.13.7

When a section was verified in a different environment, the section states it.

## Free and Studio

- External scripting is not available in Free. Only Studio lets an external process connect to Resolve
- To use `fusion.RunScript` from outside in Studio, external scripting must be enabled in the preferences
- Even in Free, Python 3 scripts can be run from the Fusion page console ("Script execution inside Fusion")

## Differences between versions

Between 21.0.4 and 21.1.0, the registry gains 8 entries and loses 12 (626 → 622).
Of the 602 types that have input definitions in both, 487 have an identical input order,
and 115 have inputs added or renamed (for example `Colorspace` → `ColorSpace`).
In 7 of these types (`OGrafLoader`, `KD_ShapeSpiralCreate`, `KD_Connect3D`, and others),
the relative order of inputs present in both versions also changes.
Because control splitting depends on the input order, the control boundaries can change when the version changes.

The counts of attributes also differ by version.

| Item | 21.0.4.0005 | 21.1.0.0014 |
|---|---|---|
| Inputs that have `INPI_IC_ControlID` and no `INPI_IC_ControlGroup` | 605 inputs, 55 types | 641 inputs, 58 types |
| Inputs that have `INPI_PC_ControlGroup` / `INPI_PC_ControlID` | 1,122 inputs, 97 types | 1,342 inputs, 98 types |

`fusion.GetAttrs()["FUSIONS_Version"]` returns a full version string such as `21.0.4.0005`.
`fusion.Version` returns a rounded number such as `21.04`.

## File formats

### `.drt` / `.drp`

- `.drt` and `.drp` are ZIP archives. Each timeline item has a `SeqContainer/<uuid>.xml`,
  and the `<CompositionBA>` element inside it stores the composition. One `.drt` can contain multiple `<CompositionBA>` elements
- The XML contains tags that are invalid as XML names, such as `<ListMgt::LmVersionTable>` (two colons).
  A standard XML parser rejects the whole document
- The content of `<CompositionBA>` is a hex string and contains no nesting or entity references

The content of `<CompositionBA>` has the following format.

```
hex string → bytes
  [BE uint32 uncompressed size][zlib-compressed data]
    → [BE uint32 version][BE uint32 element count]
       {[BE uint32 key length][UTF-16BE key][BE uint32 type][1-byte flag][value]}

Three value types appear.
  2  … a BE uint32 value follows directly
  10 … a BE uint32 length (in bytes) followed by UTF-16BE
  11 / 12 … a BE uint32 length followed by raw bytes

The keys are count, <n>_name, <n>_mediaIdList, and <n>_data.
count is the number of contained compositions, and <n>_data (type 12) is the body of the .comp.
```

- When `count` is 2 or more, one `<CompositionBA>` holds multiple compositions
- The 1-byte flag was 0 in all the data examined, and its meaning is unknown
- `<n>_data` is NUL-terminated
- When the body contains `Compressed = true, }`, the plain-text part ends in the form `Composition { <settings...>, Compressed = true, }`.
  Immediately after the NUL that follows it, `[LE uint32 uncompressed size][zlib-compressed data]` continues, and decompressing it yields the contents of the Tools table (`{ ... }`).
  The decompressed data also has one trailing NUL byte. The `}` at the end of the plain-text part is the closing brace of `Composition`
- In a `.comp` written by `comp.Save()`, the top-level `Tools` has no `ordered()`
- The `Tools` of a `.comp` decoded from `<CompositionBA>` matches the one written by `comp.Save()` in the same state over 12,033 characters
  (compared after aligning line wrapping and the presence of commas). `Prefs` and `CustomData` vary with the way of saving
- For a composition inserted into the timeline as a title, `<n>_name` is always `Composition 1` regardless of the insertion method.
  The `<Name>` on the timeline item side is also `Fusion Title`. The name of the source template appears only inside the zstd-compressed `FieldsBlob`
  and in the last segment of the composition's `CustomData.TEMPLATE_ID`

### `.setting` / `.comp`

- The top level of a `.setting` is `Tools = ordered() {`, and the top level of a `.comp` is `Tools = {`
- When Fusion saves a `.setting`, the value at save time is written to the `Default` of `InstanceInput`.
  For an input that has an expression, this value is a cache of the expression's evaluation result
- The `Prefs` of a `.comp` always contain absolute paths of the environment in `Prefs.Comp.LastFile` and `Paths.Map`
- In documents saved by Fusion, one indentation level is a tab. Some older documents use 4 spaces
- Neither the type nor the length determines which tables Fusion writes on one line.
  "A table that contains a multi-line table is itself multi-line" almost always holds, but a threshold on the length of a single line alone cannot reproduce Fusion's decision.
  The rule "one line when all nested tables fit on one line and the table itself also fits within 78 characters" comes closest to Fusion's decision
- A format appears in which only the first half of a table is opened on the same line as the opening brace and the second half spans multiple lines
- Only three string escapes appear: `\n`, `\"`, and `\\`
- Fusion reads the tools in `Tools` as they are, including those inside a macro. A tool outside the macro
  can directly reference a tool inside it with `SourceOp` (`MediaOut1` points to `Merge1` inside the macro)
- `InstanceOutput` has only `SourceOp` and `Source`, and has no display name
- `InstanceInput` can have an `Expression`. It is separate from any expression on the input of the inner tool, and the `InstanceInput` itself computes the value of the published input
- When the `Name` of an `InstanceInput` is omitted, the inspector shows the control name on the schema side (`INPS_IC_Name`).
  By Fusion's convention, an input other than the first of a control has no `Name`
- Fusion also accepts an `InstanceInput` with `Page` and `Default` omitted, and fills in the default values

Whether a `.setting` accepts a double macro (an `InstanceInput` that points to an `InstanceInput` of an inner macro),
and, if it does, in which layer `Default` and `Expression` take effect, has not been verified.

`Project.db` is locked while Resolve has the project open. A project operated on PostgreSQL has no `Project.db`.

## Schema acquisition

### Registry

- `GetRegList` returns 626 entries (21.0.4). Enumeration does not involve creating tools
- `GetRegList(1)` returns the entries whose type class has bit0 set
- `GetRegAttrs` returns `REGS_ID`, `REGI_ClassType`, `REGB_Hide`, `REGS_Category`, `REGS_Name`, and `REGS_UIName`.
  `REGS_UIName` is the localized display name
- `REGI_ClassType` is a bit field. An entry with bit1 (value 2) set is a tool, and an entry without it is a modifier.
  The 4 entries with type class 1 (`OFX_Operator`, `CustomDialog`, `ViewLUTOp`, `ViewOperator`) are internal host-side operators,
  and `AddTool` returns `None` for them
- Some modifiers also return input definitions (`StyledTextFollower` returns 475, `StyledTextCLS` returns 141).
  Whether an entry is a tool or a modifier does not determine whether it has input definitions
- There are 103 OFX plugins and 117 hidden entries (such as `KD_*`)
- Calling `GetRegList` after creating and deleting many tools crashes Resolve

### Input definitions

- `GetInputList` and `GetOutputList` return a dictionary keyed by integers starting at 1
- Through scripting, `Input.GetAttrs()` returns 55 to 60 keys for every input,
  including attributes filled with default values such as `INPI_IC_ControlID: 0`. After removing attributes whose value is `None`, `False`, `0`, `0.0`, or `''`,
  all 117 inputs of `Background` match the acquired schema byte for byte
- The `GetAttrs()` of an output has `OUTS_ID`, `OUTS_DataType`, and `OUTS_Name`. 610 types have output definitions, with 682 output ports in total
- Examples of output ports: `TextPlus`, `Merge`, and `Background` have one port, `Output` (`Image`);
  `PolyPath` has `Position` (`Point`) and `Heading` (`Number`) and no image output; `StyledTextCLS` has `StyledText` (`Text`)
- Seven spline-type entries and modifiers (`BezierSpline`, `BSpline`, `NURBSpline`, `CubicSpline`, `NaturalCubicSpline`, `AudioDisplay`, `CompName`)
  can be created with `AddTool`, but `GetInputList` returns empty for them. They are modifiers on the side that supplies values
- For 8 entries (`Circle`, `Rectangle`, `Fill`, `CopyPolyLine`, `CopyEllipse`, `CopyRectangle`, `USDLight`, `USDMaterialNode`),
  `AddTool` returns `None`. They are meant to be attached to a parent tool and cannot be instantiated alone
- In a multi-element tool, the inputs of an element appear only after that element is enabled. No attribute indicates which input enables it, and the name `Enabled<n>` is Fusion's convention.
  In `TextPlus`, setting `Enabled1` through `Enabled8` to 1 raises the inputs from 309 to 722. The increase is in the shading elements on the `Shading` page,
  where 62 families (`Red`, `Green`, `Blue`, `Alpha`, `AngleX`, `Level`, `ElementShape`, `JoinStyle`, and others) appear for elements 2 through 8
- `INPB_IC_Visible` in the schema is not part of the input definition but the display state at the time of acquisition.
  In `TextPlus`, only the inputs of element 1 have it, and those of element 2 and later do not

### Acquisition conditions

- With the composition locked by `comp.Lock()`, a single run can acquire 614 types and 44,076 inputs from 622 of the 626 registry entries.
  No crash or dialog occurs. All 103 OFX plugins and 113 of the 117 hidden entries can be acquired. The result file is gzip-compressed JSON of 1.1MB
- When not locked, Fusion treats the composition as one in ordinary editing and updates the UI. As a result, the following two things happen
  - `Loader`, `OGrafLoader`, `VolumeLoader`, and others open a file selection dialog ("Modal dialogs")
  - Fusion crashes when creating paint strokes (`Multistroke` and others, 11 entries of type class 65541).
    With the composition locked, `Multistroke` returns 63 inputs
- When not locked, Fusion crashes on the `AddTool` that immediately follows an `AddTool` of `OFX_Operator` that returned `None`
- Calling `AddTool` on a composition obtained with `GetFusionCompByIndex` while on the Edit page crashes Resolve.
  No macOS crash report is generated, and Resolve's own log has no record of the termination
- In a project with no timeline, `GetCurrentComp` returns a value but `AddTool` returns `None`
- Calling `pm.SaveProject()` on an unnamed project opens a modal dialog that asks for the save location
- `pm.GetProjectListInCurrentFolder()` does not include the open project in the list
- When a diagnostic script that repeatedly creates 30 tools was run after acquisition finished, the methods on the return value of `AddTool` started returning `None` partway through,
  and Resolve crashed. There is no crash report. Acquisition with the composition locked got through 612 types, and the cause has not been identified

## Control boundaries

The boundary of a control (the set of inputs that appear as one part in the inspector) is determined by the presence of `INPI_IC_ControlID`.

- Among the inputs that have `INPI_IC_ControlGroup`, an input without `INPI_IC_ControlID` is the head of a control,
  and the control continues up to just before the next head appears. Examples are `Font` and `Style` of `TextPlus`,
  and `TopLeftRed`, `TopLeftGreen`, `TopLeftBlue`, and `TopLeftAlpha` of `Background`
- The value of `INPI_IC_ControlGroup` is not unique per control, and the same value is reused by multiple controls.
  `ControlGroup` 1 of `LightDirectional` holds three controls: the light color (`Red`, `Green`, `Blue`),
  the shadow color (`ShadowLightInputs3D.ShadowColorRed`, `ShadowColorGreen`, `ShadowColorBlue`), and `NearClip` with `FarClip`,
  and the head of each lacks `INPI_IC_ControlID`
- In the distribution of the number of inputs per `ControlGroup`, groups of 2 (878) and 4 (1,068) make up most, and 20 have 8 or more. The larger ones hold multiple controls together
- Some inputs have `INPI_IC_ControlID` and no `INPI_IC_ControlGroup` (such as `Transform3DOp.Translate.X` of `LightDirectional`; for counts, see "Differences between versions")
- 397 inputs (21.0.4) have both `ControlGroup` and `ControlID` but are not preceded by an input with the same `ControlGroup` value.
  Most are `ObjectID`, which appears at the end of many types. In `Merge`, `ObjectID` (preceded by `CorrectEdges`, which has no `ControlGroup`) and
  `ForegroundZOffset` (the preceding `PerformDepthMerge` has a different value) are such inputs, and the input that would be the head does not appear in `GetInputList`
- In a multi-element tool, `ControlGroup` differs for each element. In `TextPlus`, `Red1`, `Green1`, `Blue1`, and `Alpha1` are 10, and `Red2` and later are 11
- `INPI_PC_ControlGroup` and `INPI_PC_ControlID` are attributes of a different system, and link a `NestControl` (a collapsible section) with its children (such as `Transform3DOp.Translate.X` of `Merge3D`)
- The inputs of `UserControls` have `IC_ControlGroup` and `IC_ControlID` (the prefix is `IC_`, not `INPI_`).
  `IC_ControlGroup` is a number that the tool assigned on its own

## Expression evaluation

The verified environment is Resolve Studio 21.1.

- Expressions are written in Lua. An input of another tool is referenced in the form `Tool.Port`, and an input of the same tool is referenced by a bare identifier. Tool names are treated as Lua identifiers
- When one input has both a `SourceOp` (a connection to a modifier) and an `Expression`, the value is supplied from the expression, and the modifier connection is not used.
  In a `.setting` where `AnimCurves1.Input` has `SourceOp = "AnimCurves1_Mod"` (keyframes that change linearly from 0 to 1 over frames 0 to 120) and
  `Expression = "min(time/comp:GetPrefs().Comp.FrameFormat.Rate, 1)"`,
  a copy with `AnimCurves1_Mod` deleted (except for `SourceOp` and `Source`) was rendered at frame 60 and compared with the original output, and the PNGs matched byte for byte
  (the value would be 0.5 for the modifier and 1.0 for the expression). The same comparison also matched for `TimeStretcher1.SourceTime` (`TimeStretcher1_Mod` has one keyframe at frame 10, and the expression is `Template.GlobalOut`)
  and for `TimeStretcher1.SourceTime` in another `.setting` (one keyframe, and the expression is `comp.RenderEnd`).
  This form arises when a modifier remains after an expression is written later in Fusion
- For an input driven by an expression, the `Value` in the file and the `Default` of `InstanceInput` are the calculation results at save time, and are not used on load.
  In a `.setting` where `Red1` has `Expression = "iif(ColorSelect == 0, 0xE4, 0x41) / 255"`, only the two places related to `ColorSelect`
  (the `Default` of `Template_ColorSelect` and the `Value` of the inner `Template.ColorSelect`) were changed, with the color `Value` and `Default` left as they were, and rendered.
  The PNG matched byte for byte the output of another `.setting` in which `ColorSelect` had been changed
- A bare identifier can resolve to a name that the tool holds internally, instead of an input of `UserControls`.
  A bare `Gap` in a `TextPlus` expression evaluates to 157. `Gap` exists in neither `GetInputList` nor the schema, and in a `Transform` expression it evaluates to 0.
  Even after adding `Gap` to `UserControls`, a bare `Gap` stays 157, and qualifying it as `self.Gap` or `<tool name>.Gap` returns the value from `UserControls`
- A nonexistent name does not cause an error. A `Center` set to `Point(Foo, 0)` fails to evaluate and keeps its previous value, and a numeric input evaluates to 0.
  The value on failure differs by input type
- `<tool name>.Output.DataWindow[1]` through `[4]` return the pixel coordinates of the rendered region and follow changes to the text
- An animation driven by an expression that references `time`, such as `min(time/comp:GetPrefs().Comp.FrameFormat.Rate, 1)`, has no keyframes

## Script execution inside Fusion

- `fusion.RunScript(path)` chooses the interpreter by the extension. `.py` follows the "Default Python Version" preference.
  In an environment where this setting is not Python 3, it executes nothing and returns `None` without raising an exception. `.py3` specifies Python 3 explicitly and does not depend on the setting.
  A file with the same content did not work as `.py` and worked as `.py3`
- `fusion.RunScript` returns after only launching the script. The `print` of the internal script is output neither to the caller nor to Resolve's log,
  and only uncaught exceptions remain in the log
- The Fusion page console (Workspace > Console) has a `Lua` / `Py2` / `Py3` switch.
  When multi-line Python is pasted in `Py3` and Return is pressed, it runs all at once and not line by line.
  When `exec(open("<path>", encoding="utf-8").read())` is pasted, the code itself runs in that console, so it does not depend on the extension or the preference
- The executed script can reference `fusion`, `resolve`, `app`, and `fu` as globals, and can use `bmd` as the built-in `fusionscript` module.
  Because everything from `resolve.GetProjectManager()` through `GetCurrentProject()` can be obtained, timeline operations and title insertion can be done without connecting from outside
  (verified via `fusion.RunScript`; not verified in the console)
- From an internal script, `GetRegList` returns 626 entries, and the input definitions can be acquired by creating tools and written to a file

## Modal dialogs

When Fusion or Resolve opens a modal dialog, write-type calls all start returning `None` or `False`.
Read-type calls keep working, so the project appears to be broken. The dialogs verified and their effects are as follows.

| Trigger | Dialog | Calls that start returning `None` / `False` |
|---|---|---|
| `AddTool` of `Loader` or similar on an unlocked composition | File selection | `OpenPage`, `CreateEmptyTimeline`, `SaveProject`, `LoadProject`, `DeleteProject` |
| `pm.SaveProject()` on an unnamed project | Save location selection | Same as above |
| A failure of `comp.Render()` | `WARNING! Render did not complete!` | `OpenPage`, `CreateEmptyTimeline`, `CloseProject`, `InsertFusionCompositionIntoTimeline` |
| A success of `comp.Render()` | `Render completed!` | Same as above |
| Reopening a project with unsaved changes by `LoadProject` | Save confirmation | Subsequent calls do not return |

- `comp.Lock()` does not suppress `WARNING! Render did not complete!`
- Wrapping only the `comp.Render()` call in `comp.Lock()` / `comp.Unlock()` prevents `Render completed!` from opening
- Calling `comp.Render()` with the input of a `Saver` unconnected fails

## Timelines and titles

- The `titleName` of `InsertFusionTitleIntoTimeline(titleName)` is the file name without the category and without the extension.
  An ASCII name, a name containing Japanese, and a built-in template name such as `Text+` are all accepted
- The name of a timeline item inserted as a title is the file name of the `.setting`, not the inner macro name
- Resolve caches the title list and does not reload changes to the `Templates` directory.
  A `.setting` whose actual file was deleted could still be inserted, and a newly added `.setting` could not be inserted even after waiting 18 seconds.
  No way to trigger a reload from the API has been found. After replacing a `.drfx`, Resolve must be restarted
- Right after startup, Resolve has no page open (`GetCurrentPage()` is `None`), and in that state `CreateEmptyTimeline` returns `None`. After `OpenPage("edit")` it succeeds (verified in 21.1.2).
  While the Project Manager or a modal dialog (such as the software update notice) is shown, `GetCurrentProject()` returns `Untitled Project`, `OpenPage("edit")` returns `None`, the page stays `None`, and `CreateEmptyTimeline` returns `None` (verified in 21.1.2)
- `CreateEmptyTimeline` does not accept a name containing a colon and returns `None`. It also returns `None` when the name collides with an existing timeline
- `InsertFusionCompositionIntoTimeline()` takes no arguments and inserts an empty composition that has only `MediaOut`.
  The name of the inserted item is always `Fusion Composition`, and it can be changed with `item.SetName(name)`
- The length of an inserted composition is fixed to the timeline's standard duration (120 frames in the verified environment).
  `comp.Render` with a frame outside the range fails. There is no way to change the item's length from the API.
  The key `timelineStandardGeneratorDuration` does not exist among the 158 keys returned by either `project.GetSetting()` or `project.GetCurrentTimeline().GetSetting()`,
  `project.GetSetting("timelineStandardGeneratorDuration")` and `timeline.GetSetting("timelineStandardGeneratorDuration")` return an empty string,
  and `project.SetSetting("timelineStandardGeneratorDuration", "150")` returns `False` (Resolve Studio 21)
- `tl.GetItemListInTrack` returns a new wrapper object on every call. The identity of an item cannot be determined by comparing objects, and is determined by `GetUniqueId()`
- The format of the timecode that represents the playhead position changes with the timeline's `timelineFrameRate` and `timelineDropFrameTimecode`.
  In a drop-frame (29.97 / 59.94 family) timeline, the separator is a semicolon only before the frames, as in `01:00:00;00`,
  and at the start of each minute the numbers for 2 frames (4 frames in the 59.94 family) are skipped.
  In a 29.97 drop-frame timeline, `item.GetStart()` written as non-drop-frame is `01:00:02:00`,
  while `timeline.GetCurrentTimecode()` returns `01:00:01;12`
- Moving the playhead with `timeline.SetCurrentTimecode` may not take effect depending on the display state of the Fusion / Edit page.
  Even when it returns success, `GetCurrentTimecode()` may not match. Repeating the move to the same position makes it take effect
- Calling `InsertFusionCompositionIntoTimeline` while the playhead is in the middle of a timeline item splits the item at that position and inserts there.
  When the playhead is at the start of an item, it appends to the end of the timeline without splitting the preceding item

## Pasting into a composition and rendering

- `comp.Paste(bmd.readfile(path))` pastes a `.setting` into a composition. `bmd.readfile` reads a `.setting` as a `{Tools, ActiveTool}` dictionary.
  `bmd` can be referenced only inside Fusion. This method does not depend on the cache of the `Templates` directory
- `comp.Paste()` returns `False` unless the composition is actually open on the Fusion page. A valid object reference alone is not enough.
  To open an inserted item, move the playhead to `item.GetStart()`, then call `OpenPage("edit")` followed by `OpenPage("fusion")`
- A pasted macro can be obtained with `comp.FindTool(name)`. `comp.ActiveTool` depends on the selection state.
  Tools inside the macro cannot be obtained with `comp.FindTool`
- The output name of a pasted `MacroOperator` is localized to Fusion's display language (`出力` in a Japanese environment; "Output" in English), and `macro.Output` returns `None`.
  It can be obtained with `macro.GetOutputList()[1]`
- `ConnectTo(None)` does nothing and raises no exception
- `Saver` can also be used in a Resolve composition. `comp.Render({Start, End, Wait})` returns `True` and writes a PNG
- Setting `Width` and `Height` of `BetterResize` to 103 and 58 and placing it before the `Saver` yields a 103×58 PNG, the same size as Resolve's title thumbnails
- `output.GetValue(frame)` on the output of a pasted `MacroOperator` returns `None` even after `Render`. On the output of a built-in tool that the rendering passed through
  (such as `PipeRouter` connected to the macro's output), it returns an `Image` after `Render`, and `None` before it
- `Image.DataWindow` is an attribute (not a method) holding `{1: left, 2: bottom, 3: right, 4: top}` in pixels, with the origin at the bottom left.
  For Text+ based presets it is the area actually drawn, not the whole frame (a 3840×2160 timeline gave, for example, `{1: 115, 2: 1907, 3: 400, 4: 2129}` for a caption at the top left).
  `Image.Width` and `Image.Height` are the frame size as floats
- `XOffset`, `YOffset`, `XSize`, and `YSize` of `Crop` are in pixels, with `YOffset` measured from the bottom, the same coordinates as `DataWindow`.
  Setting them after a `Render` and rendering the same frame again writes the cropped image
- `Saver` strips the trailing digits from the file name it writes and then appends a 4-digit frame number
  (`thumb.png` becomes `thumb0000.png`, and `Text2.png` becomes `Text0000.png`)
- Even after `comp.Undo()`, the added tool remained as one extra tool
- `comp.Render({Start = F, End = F})` moves `comp.CurrentTime` (the local time on the Fusion page) to `F`,
  and moves the playhead to the absolute position of the rendered frame (item start + frame number). It does not return with `timeline.SetCurrentTimecode`,
  and is reset by `comp.CurrentTime = 0`, but this reset may not take effect

## Published inputs in the macro inspector

In Resolve Studio 21.1, the inspector was checked in two cases: when a `.setting` was pasted into a composition on the Fusion page and the macro was selected,
and when it was inserted as a title on the Edit page.

- When pasted, the inspector order follows the order of `GetInputList`, not the order of `Inputs`.
  When inserted as a title, it follows the order of `Inputs`. Whether an input is shown and its rendering form are identical in both
- An editable `ColorControl` shows a slider for each component under the swatch.
  The same holds for Blackmagic's bundled `Rise Fade` (which publishes `Red1` through `Alpha1` with `ControlGroup = 6`),
  and `>` only opens and closes the color picker; the sliders are not collapsed
- When the `InstanceInput` has an `Expression` (read-only display), or when the published target is a `Red1Clone`-family input with `INPB_IgnoreVisible`, only the swatch is shown
- When only the first input, `Red1`, is published, the swatch becomes a color with only the red component
- A `ColorControl` cannot be operated unless red, green, and blue are published. Publishing only `Alpha1Clone`, or only `TopLeftAlpha`, shows an inoperable swatch.
  Publishing `Red1Clone` through `Blue1Clone` without alpha displays normally
- `TextPlus` has `Red<n>`, `Thickness<n>`, and so on for each shading element (fill, outline, shadow, and so on),
  and shows only the inputs of the one element selected by `SelectElement`. This switching applies to the macro's published inputs as is.
  When `SelectElement` of the inner `Template` was `1`, `Red2` and `Thickness2` were shown and `Red1` disappeared;
  deleting `SelectElement` showed `Red1` and hid the inputs of element 2; setting it to `2` hid both.
  In another `.setting` with `SelectElement = 2` (element 3), only the inputs of element 3 were shown.
  The selected element is the number `SelectElement` plus 1, and element 1 when `SelectElement` is absent
- Deleting `Select` (the page tab) does not change the display of inputs on different pages, such as `Center`, `Size`, and `Start`
- `Red1Clone`, `Green1Clone`, `Blue1Clone`, and `Alpha1Clone` exist in the schema as built-in inputs and have `INPB_IgnoreVisible`.
  When published, they are shown regardless of the selected element. Replacing the `Red1`-family publication with the `Red1Clone` family showed the text color and the outline color at the same time with `SelectElement = 1` unchanged.
  Setting 0.25 on the macro's `Template_Red1Clone` also sets the inner `Red1` to 0.25
- Publishing a `NestControl` yields an empty heading with "∨", and Fusion places the controls that follow inside that collapsible section.
  `Softness<n>` of `TextPlus` is not a numeric input but a heading that bundles `SoftnessX<n>`, `SoftnessY<n>`, and others;
  publishing `Softness2` showed "∨ Outline ぼかし" (Outline blur), and in another `.setting` the `VerticalJustificationTop` that followed was shown inside it
- An input connected to the output of another tool is shown with a red keyframe button when published.
  Typing text into `TextPlus.StyledText` connected to `StyledTextCLS` changes the modifier's `Text`, is reflected in the rendering, and keeps the connection.
  Changing the published target to the modifier's `Text` turns the button gray. The `StyledText` of `Rise Fade` (connected to `StyledTextFollower`) is also shown in red
- The attributes returned by `GetInputList` of a pasted macro reflect the `ControlGroup` value, `INPID_InputControl`, and the `INPI_IC_ControlID` of non-head inputs as described in `Inputs`.
  Assigning `ControlGroup` as sequential numbers from 1 does not break the display

Whether the inspector display stops following when the `InstanceInput`'s `Expression` is removed has not been verified.

## Process and connection

- The Resolve process name is `Resolve` (`pgrep -x Resolve`)
- Resolve runs as a single instance. `pkill` only sends SIGTERM and the shutdown processing continues, and calling `open -a "DaVinci Resolve"` during that time does not start a new process.
  Connecting to the API right afterward connects to the process that is shutting down, `GetCurrentProject()` returns `Untitled Project`, and a few seconds later that process exits and no Resolve is left.
  Waiting until `pgrep -x Resolve` returns nothing after `pkill` before launching avoids this (3 of 3 times without waiting, 0 of 6 times with waiting)
- A Python process that used the Resolve API may exit with SIGSEGV (exit code 139) at interpreter shutdown after finishing its work normally.
  Resolve keeps running, and the result of the work is not affected
- `comp.Save(path)` saves a composition from the Resolve API as a `.comp`
