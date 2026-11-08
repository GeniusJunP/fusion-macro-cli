# Resolve Environment

This document lists the facts about DaVinci Resolve that decide what to ask the user before and after running the commands that connect to Resolve (`schema dump` and `preview`).
The other commands do not use Resolve.

- [Free and Studio](#free-and-studio)
- [Before running a command](#before-running-a-command)
- [Schema and the Resolve version](#schema-and-the-resolve-version)
- [Replacing presets](#replacing-presets)
- [Verified versions](#verified-versions)

## Free and Studio

- Only Resolve Studio lets an external process connect to Resolve, and only when external scripting is enabled in Preferences (System > General > External scripting using).
  Ask the user to start Resolve Studio and enable it before running `schema dump` or `preview` without `--console`
- Resolve Free has no external scripting. Pass `--console`: the command prints one line, and the user opens the Fusion page console (Workspace > Console), switches its language to `Py3`, pastes the line, and presses Return.
  Pasted multi-line Python runs at once, and the line runs the same script as the Studio path
- With `--console` the command exits with 0 after printing the line. `preview --console` also prints the path of the result file; the command does not wait for it

## Before running a command

- A project must be open. While the Project Manager or a modal dialog (such as the software update notice) is shown, no page can be opened and the commands fail with an error that says so.
  Ask the user to open a project and close the dialog
- `schema dump` needs a composition with actual content open on the Fusion page
- `preview --project NAME` loads another project. The open project is not reloaded, because reloading a project with unsaved changes opens a save confirmation that blocks every later call
- `preview` creates a timeline named `fusion-macro-cli preview YYYY-MM-DD HHMMSS` in the project and leaves it there for the user to check

## Schema and the Resolve version

Run `schema dump` again after the user updates Resolve. The input definitions differ between versions (from 21.0.4 to 21.1.0, 115 of 602 types gained or renamed inputs, such as `Colorspace` → `ColorSpace`),
and control boundaries in `show` and `apply` depend on the input order. The newest schema in the schema directory is used, so the old one need not be deleted.

## Replacing presets

Resolve caches the list of titles and does not reread the `Templates` directory or a `.drfx` while running: a deleted `.setting` can still be inserted, and a new one cannot.
After the user replaces a `.drfx` or changes the `Templates` directory, the user must restart Resolve. `preview` pastes the `.setting` into a composition and does not depend on this cache.

When Resolve is quit from a script or a shell, wait until the process `Resolve` has exited (`pgrep -x Resolve` prints nothing) before starting it again.
Resolve runs as a single instance, and a start during shutdown connects to the exiting process, which then leaves no Resolve running.

## Verified versions

The behavior that the commands and these documents rely on was verified on macOS with DaVinci Resolve Studio 21.0.4.0005, 21.1.0.0014, 21.1.2, and 21.2. The Python inside Fusion was 3.13.7.
