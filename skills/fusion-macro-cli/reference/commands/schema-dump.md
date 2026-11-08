# schema dump

The behavior common to all commands and the exit codes are in [commands.md](../commands.md#common-behavior).

Obtains from Fusion the definitions of inputs and outputs for each tool type. Run it once per Resolve version.

**Input** Output directory (`-o`, defaults to the schema directory)

**Processing** Enumerates the registry and, for all types except the host-side internal operators (4 entries of type class 1),
adds the tool to the composition, obtains the definition with `GetInputList` and `GetOutputList`, and deletes it.
OFX plugins and hidden items are also included.

**Output** `fusion-<version>.json.gz`. The version is obtained from `fusion.GetAttrs()["FUSIONS_Version"]`.
The order of inputs is written as returned by `GetInputList`. The file format is described in [data.md](https://github.com/GeniusJunP/fusion-macro-cli/blob/main/docs/data.md#schema-file-format).

## Schema directory

The default directory is `~/.local/share/fusion-macro-cli/schema/`. It can be overridden with `--schema` and the environment variable `FUSION_MACRO_CLI_SCHEMA`.
If the schema directory contains multiple schemas, the one with the latest modification time is used.
After updating Resolve and running `schema dump` again, the new schema is used without deleting the old one.

The schema is Blackmagic's internal product data (including localized display names), so it is not included in the repository. Users generate it from their own environment with `schema dump`.

## Execution conditions

Dumping is done under the following conditions.

- During the dump, the composition is locked with `comp.Lock()`
- Run it with a composition that has actual content open on the Fusion page. First one tool is generated to check, and if `AddTool` does not accept it, it is an error
- A dedicated timeline is created for each dump and deleted afterwards. Existing compositions are not used
- It guarantees that the set of tools in the composition does not change before and after the dump. Tools that were added are deleted even if it exits by an exception
- `pm.SaveProject()` is not called for an unnamed project
- The registry is enumerated before any tool is generated
- For multi-element tools, `Enabled<n>` is set to 1 and this is repeated until no more inputs appear
- The script reads the version from Fusion, decides the file name, and writes it. The CLI side does not wait for the file to appear and does not rename it
- By default (Studio), the script to run inside Fusion is written to the schema directory as `_dump.py3` and run with `fusion.RunScript(path)`
- With `--console`, it does not connect to Resolve; it writes `_dump.py3` and writes to standard output one line to paste into the Fusion page console.
  The user switches the console language to `Py3`, pastes it, and runs it with Return

  ```
  exec(open("<schema directory>/_dump.py3", encoding="utf-8").read())
  ```

- If it cannot connect to Resolve, it shows an error and returns exit code 1. External scripting is not available in the Free edition, so in that case `--console` is suggested ([environment.md](../../resolve/environment.md#free-and-studio))

