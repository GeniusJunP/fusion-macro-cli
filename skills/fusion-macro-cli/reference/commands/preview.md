# preview

The behavior common to all commands and the exit codes are in [commands.md](../commands.md#common-behavior).

Pastes a `.setting` into a Resolve composition, writes a PNG, and leaves the timeline.
Thumbnail generation and visual checking are done with one command.

**Input** Path of a `.setting` (one or more)

**Options**

| Option | Description |
|---|---|
| `--frame N` | Frame number to render. If omitted, the last frame of the inserted item |
| `--project NAME` | Project in which to create the timeline. If omitted, the open project |
| `--console` | Does not connect to Resolve; shows one line to paste into the Fusion page console |
| `--timeout SEC` | Seconds to wait for the result file. Defaults to 120 seconds per `.setting` × the number of `.setting` files |
| `--sample-text [TEXT]` | Renders with the text of the top-level tool replaced by `TEXT` (`Ag` if omitted) |
| `--sample-size SIZE` | `Size` used together with `--sample-text`. Defaults to 0.5. Has no effect without `--sample-text` |
| `--framing content\|frame` | `content` crops the thumbnail to the area of the frame whose alpha is not 0; `frame` scales the whole frame. Defaults to `content`, or `frame` with `--sample-text` |
| `--content-margin RATIO` | Margin added on each side of the content area with `--framing content`, as a fraction of the area's width and height. Defaults to 0.1 |

**Prerequisites** Run Resolve Studio and enable external scripting in the preferences. Use `--console` for Free ([environment.md](../../resolve/environment.md#free-and-studio)).

**Processing** Generates the script to run inside Fusion in a temporary directory.
In Studio it is run with `fusion.RunScript(path)`, and with `--console` the user pastes the following one line into the Fusion page console. Both run the same script.

```
exec(open("<path of the generated script>", encoding="utf-8").read())
```

The script does the following.

1. Creates a timeline with the name the CLI specified
2. For each `.setting`, just before insertion, moves the playhead to the start of the last existing item. It does not move for the first insertion, when there are no items
3. Inserts an empty composition with `tl.InsertFusionCompositionIntoTimeline()`, opens that composition on the Fusion page, and then pastes with `comp.Paste(bmd.readfile(path))`.
   The procedure to open it identifies the new item with `GetUniqueId()`, moves the playhead to `item.GetStart()`, calls `OpenPage("edit")` and then `OpenPage("fusion")`,
   and confirms that `GetToolList()` is only `MediaOut1`. The pasted macro is obtained with `comp.FindTool(<macro name>)`
4. Connects `BetterResize` (`Width` 103, `Height` 58) and a `Saver`, and writes the PNG with `comp.Render({Start, End, Wait})`.
   With `--framing content`, `PipeRouter` and `Crop` are placed between the macro's output and `BetterResize`; the first `Render` passes the whole frame,
   the content window is computed from the `DataWindow` of the image that `PipeRouter.Output.GetValue(frame)` returns, it is set on `Crop`, and the frame is rendered again.
   Only the call to `comp.Render()` is wrapped in `comp.Lock()` / `comp.Unlock()`. Right after `Render`, it sets `comp.CurrentTime = 0`
5. Deletes the added tools by specifying their names, and then connects the macro's output (`macro.GetOutputList()[1]`) to the input of `MediaOut1` obtained with `comp.FindTool("MediaOut1")`
6. Sets the name of the inserted item to the macro name (`item.SetName`)
7. Leaves the timeline. The user checks the rendering and the Inspector in this timeline
8. For expressions of inner tools that refer to an input name of that tool's `UserControls` by a bare identifier,
   it compares the evaluated value of the expression with the evaluated value of the expression with that identifier qualified as `<tool name>.<identifier>`, and restores the expression after the comparison.
   If the values do not match, the bare identifier is resolved not to the `UserControls` input but to a name inside the tool ([inspector.md](../../authoring/inspector.md#expressions)).
   This name does not appear in `GetInputList` or the schema, so `check` cannot detect it.
   The qualified expression is built by the CLI side from the `.setting`, and built-in names such as `time` and the inside of string literals are not qualified.
   The mismatch is recorded in the result file, and the CLI shows the expression and both values and returns exit code 1. The PNG is written

**Output** A PNG in the same directory as the `.setting`, and a timeline in Resolve

- The `.setting` may be in either form. A `.comp`, or a document that is neither form, is a one-line error
- The project in which the timeline is created is the project opened with `pm.LoadProject(NAME)` when `--project NAME` is given, and the open project when omitted.
  If the specified name is the same as the open project, `LoadProject` is not called. In either case, the project name used is written to standard output.
  If it cannot be opened with `--project NAME`, it exits with a one-line error and exit code 1
- The timecode to which the playhead is moved to the start of the item is built from the timeline's `timelineFrameRate` and `timelineDropFrameTimecode`, following the drop-frame format.
  After moving, it is compared with `timeline.GetCurrentTimecode()` and repeated up to 3 times until they match. If they do not match, that `.setting` is recorded as a failure
- The timeline name is decided by the CLI and is `fusion-macro-cli preview YYYY-MM-DD HHMMSS` (local time, down to seconds). No colons are put between hours, minutes, and seconds.
  A collision from running twice in the same second is not handled. If `CreateEmptyTimeline` returns `None`, it exits with a one-line error and exit code 1
- The internal script writes the result for each `.setting` (`setting`, `output`, `frame_requested`, `frame_used`, `status`, `reason`, `framing`, `content_window`) to a JSON result file.
  `frame_requested` is the value of `--frame`, or `null` if omitted, and `frame_used` is the frame number actually passed to `Render`.
  The result file is created in the same temporary directory as the internal script, and the CLI embeds its path in the script. The CLI deletes any existing result file before launching,
  waits for the result file to appear after launching, reads it, writes the PNGs written and the list of failures to standard output, and decides the exit code
- The upper limit of the wait is 120 seconds per `.setting` × the number of files and can be changed with `--timeout`. If exceeded, it exits with a one-line error and exit code 1,
  and if the result file was partly written, its contents are also shown
- With `--console`, it shows the path of the result file together with the one line to paste and exits with exit code 0
- When multiple `.setting` files are given, one failure (paste, playhead move, connection, `Render`) does not stop the rest.
  The failed `.setting` is recorded with its reason and processing continues, and after all are done the list of failures is shown and exit code 1 is returned. If none failed, 0 is returned
- If no frame is specified, the last frame of the inserted item (`item.GetEnd() - item.GetStart() - 1`) is rendered and written to `<name>.png`. This becomes the preset's thumbnail.
  Keyframe times are not used for the default frame. Animation that continues beyond the length of the item cannot be rendered for that range
- If `--frame N` is specified, that value is used and the output is written to `<name>_f<N>.png`. If `N` exceeds the last frame of the item, it is not corrected and that `.setting` is recorded as a failure
- The output of the `Saver` is the fixed name `render.png` in the temporary directory, and after writing it is moved to `<name>.png`
- Before `comp.Render()`, it confirms that `saver.Input.GetConnectedOutput()` is not `None`
- After connecting to `MediaOut1`, it confirms that `media_out.Input.GetConnectedOutput()` is not `None`. If it is `None`,
  or `comp.FindTool("MediaOut1")` returns `None`, that `.setting` is recorded as a failure and processing moves to the next
- Added tools are deleted by specifying their names, not with `comp.Undo()`
- With `--framing content`, the content window is the `DataWindow` of the rendered image, enlarged on each side by `--content-margin` × its width and height,
  then widened on its shorter side to the 103:58 ratio of the thumbnail. A window larger than the frame is capped at the frame size while keeping the ratio,
  and a window that extends past an edge of the frame is shifted inside the frame. The window is recorded in the result file as `content_window` (`[x, y, width, height]` in pixels, origin at the bottom left).
  If the rendered frame is fully transparent, the first rendering of the whole frame is kept, `content_window` is `null`, and the CLI shows a warning on standard error.
  If `GetValue` returns no image, that `.setting` is recorded as a failure with the reason `content_image`
- With `--sample-text`, the default framing is `frame`, so that the size set with `--sample-size` is kept in the thumbnail. `--framing content` can be given explicitly
- `comp.Lock()` is called only around `Render`
- With `--sample-text [TEXT]`, which needs a tool-form `.setting` of a `TextPlus` (a macro form is a one-line error, because its `Inputs` are `InstanceInput`s without the tool's values), a copy of the `.setting` in which `StyledText` of the top-level tool is `TEXT` (`Ag` if omitted), `Size` is the value of `--sample-size` (default 0.5),
  and `Center` is `{0.5, 0.5}` is written to `_preview_sample/` in the temporary directory, and that copy is pasted.
  The rewrite is done on the `.setting` document before pasting (because tools inside a macro cannot be obtained with `comp.FindTool`)

The method of placing a `.setting` in a `Templates` directory and inserting it as a title is not used, because Resolve caches the list of titles ([environment.md](../../resolve/environment.md#replacing-presets)).

