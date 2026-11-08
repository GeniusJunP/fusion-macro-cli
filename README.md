# fusion-macro-cli

[日本語](README.ja.md)

A CLI and Agent Skill that enable AI agents to build DaVinci Resolve title presets (`.setting`) from Fusion compositions.

You design the look in Fusion, and the agent turns it into presets. The agent extracts the composition, declares which inputs to publish, assembles and validates the `.setting`, renders thumbnails in Resolve, and packages everything into a `.drfx`. Finally, you visually verify the rendering and inspector layout in Resolve.

A `.setting` can take one of two forms:

- **Macro form**: Displays only the declared inputs under their specified names in the inspector. Multiple tools can be combined into one.
- **Tool form**: Saves a single tool as-is and exposes that tool's full native inspector.

## Setup

Install both the Skill and the CLI for your agent.

### 1. Skill

In Claude Code, add this repository as a plugin marketplace and install the plugin:

```
/plugin marketplace add GeniusJunP/fusion-macro-cli
/plugin install fusion-macro-cli@fusion-macro-cli
```

Claude Code 2.1.275 or later can perform both in a single command: `/plugin install fusion-macro-cli --marketplace GeniusJunP/fusion-macro-cli`. From a shell, run `claude plugin marketplace add GeniusJunP/fusion-macro-cli` followed by `claude plugin install fusion-macro-cli@fusion-macro-cli`.

For other agents, copy `skills/fusion-macro-cli/` into the directory where your agent loads skills:

```
git clone https://github.com/GeniusJunP/fusion-macro-cli
cp -R fusion-macro-cli/skills/fusion-macro-cli <skills directory>/
```

### 2. CLI

Requires Python 3.11 or later with no external dependencies. Install using any of the following:

```
uv tool install git+https://github.com/GeniusJunP/fusion-macro-cli
```

```
pipx install git+https://github.com/GeniusJunP/fusion-macro-cli
```

```
pip install git+https://github.com/GeniusJunP/fusion-macro-cli
```

This installs the `fusion-macro-cli` command.

### 3. DaVinci Resolve

`schema dump` and `preview` use the DaVinci Resolve Studio scripting API. Make sure to enable external scripting in Resolve preferences. The Free edition does not support external scripting; on the Free edition, pass `--console` and run the output from the Fusion page console. All other commands work without Resolve installed.

## Usage

After designing the look in Fusion, prompt your agent. For example:

```
Turn the caption in this timeline into a title preset where only the color can be changed. Render a thumbnail and pack it into a .drfx.
```

The agent follows the workflow in the Skill and runs the CLI. `preview` creates a timeline in Resolve for verification; inspect the rendering and inspector layout there visually. After reinstalling the `.drfx` in Resolve, restart Resolve.

## Documentation

| Document | Contents |
|---|---|
| [skills/fusion-macro-cli/SKILL.md](skills/fusion-macro-cli/SKILL.md) | Workflow, terminology, and which document to read at each stage |
| [authoring/forms.md](skills/fusion-macro-cli/authoring/forms.md) | Choosing between the macro form and the tool form |
| [authoring/declaration.md](skills/fusion-macro-cli/authoring/declaration.md) | Declaration (TOML) format specification |
| [authoring/inspector.md](skills/fusion-macro-cli/authoring/inspector.md) | Conditions for published inputs to be shown and editable in the inspector, and expression evaluation |
| [reference/commands/README.md](skills/fusion-macro-cli/reference/commands/README.md) | Inputs, outputs, options, behavior, and exit codes for each command |
| [reference/rules.md](skills/fusion-macro-cli/reference/rules.md) | What each check rule detects, its impact in Resolve, and whether `fix` can repair it |
| [resolve/environment.md](skills/fusion-macro-cli/resolve/environment.md) | Resolve Free and Studio, external scripting, restarting after replacing a `.drfx`, and verified versions |
| [docs/development.md](docs/development.md) | Module architecture, wording conventions, the `.setting` / `.comp` format, and testing |
| [docs/data.md](docs/data.md) | Internal data: document kinds, schema, controls, and the reference graph |

## License

[MIT](LICENSE)
