# pack

The behavior common to all commands and the exit codes are in [README.md](README.md#common-behavior).

Inspects a `Templates` directory and archives it into a `.drfx`. It does not connect to Resolve.

**Input** Path of the `Templates` directory, path of the `.drfx` to write (`-o`, required)

**Processing** Inspects all `.comp` and `.setting` files under `Edit/` (including subdirectories) and, if there are no violations, writes everything under `Edit/` as a zip.
A `.drfx` is a zip with the structure of Fusion's `Templates/`, differing only in extension.

**Output** The `.drfx` at the specified path

- If there is no `Edit/` directly under it, it returns a one-line error and exit code 1
- If the inspection finds violations, nothing is written, the violations are shown, and exit code 1 is returned
- All files under `Edit/` are included. Only files and directories whose names start with `.` (`.git` `.DS_Store` `.venv`, etc.) are excluded. No selection by extension is done
- Paths in the archive are relative to the `Templates` directory (`Edit/...`) and are sorted by name. The same `.drfx` is obtained from the same `Templates` directory
- If the output destination is inside the `Templates` directory, the output file itself is excluded
- It is written with `zipfile` and no external commands are used

After replacing the `.drfx` in Resolve, restart Resolve.
