# Data

This document describes the data that fusion-macro-cli reads and builds: document kinds, the schema, controls, the reference graph, macros, and `UserControls`. Terms are described in [SKILL.md](../skills/fusion-macro-cli/SKILL.md#terms).

- [Document kinds](#document-kinds)
- [Schema](#schema)
- [Control splitting](#control-splitting)
- [Reference graph](#reference-graph)
- [Macro](#macro)
- [UserControls](#usercontrols)

## Document kinds

`form.classify` determines the kind of a document from its structure, and every command and check rule refers to this one function.

| Kind | Structure |
|---|---|
| `comp` | The root has the type `Composition` |
| `macro` | The root has no type, and `Tools` has one tool, a `MacroOperator`. The tools of the `.comp` are inside it, and it carries the published inputs and outputs |
| `tool` | The root has no type, and `Tools` has one tool that is not a `MacroOperator`. The tool is saved as it is, and the inspector shows all of its inputs |

`macro` and `tool` are the two forms of a `.setting` (`form.SettingForm`). Any other document is an error. `form.top_tool` returns the top-level tool of a `.setting`.

- A declaration has the form as a type: `publish.MacroDeclaration` (macro name, `[[publish]]`, `[[output]]`) or `publish.ToolDeclaration` (the tool to save). `publish.Declaration` is their union, and `build_setting`, `declare`, and `declaration_to_toml` handle both
- Each check rule has `applies_to`, the set of kinds it applies to. `check` and `fix` run only the rules whose `applies_to` contains the kind of the document
- `show`, `extract`, and `preview` branch on the kind returned by `form.classify`

## Schema

The schema holds a list of inputs for each tool type, and each input has the following attributes.

| Attribute | Content |
|---|---|
| `INPS_ID` | Input name |
| `INPS_DataType` | Value type (`Text` / `Number` / `Image` / `Mask`) |
| `INPB_External` | Whether it can be set from outside |
| `INPID_InputControl` | Control kind (`SliderControl`, `FontFileControl`, and so on) |
| `INPI_IC_ControlGroup` | The group the control belongs to. It is not the unit of a control |
| `INPI_IC_ControlID` | Position within the control. An input without this attribute is the head of a control |
| `INPS_IC_Name` | Display name of the control on the Fusion side |
| `INPS_ICS_ControlPage` | Page name defined by the tool |
| `INPN_Default` | Default value |

The order of inputs preserves the order returned by `GetInputList`. Because control splitting depends on this order, reading and writing the schema preserves the order.

Output ports also have definitions per type. The attributes are those returned by `GetOutputList`, and include at least the output name and the value type (`OUTS_DataType`).

`INPB_IC_Visible` is not used for judgment. It is the display state at the time of acquisition, not part of the input definition: in `TextPlus`, only the inputs of element 1 have it.

### Schema file format

Gzip-compressed JSON. `schema dump` writes it and `schema.py` reads it.

```
{
  "version": "21.0.4.0005",
  "registry": [
    { "id": "TextPlus", "class_type": 11, "hidden": false, "modifier": false,
      "category": "Generator", "name": "Text+" },
    ...
  ],
  "tools": {
    "TextPlus": {
      "SettingsNest": { "INPS_ID": "SettingsNest", "INPS_DataType": "Number", ... },
      ...
    },
    ...
  },
  "outputs": {
    "TextPlus": {
      "Output": { "OUTS_ID": "Output", "OUTS_DataType": "Image", ... }
    },
    ...
  },
  "failed": [
    { "id": "OFX_Operator", "reason": "not_a_tool", "class_type": 1 },
    { "id": "Circle", "reason": "add_tool_returned_none", "class_type": null },
    ...
  ]
}
```

| Key | Content |
|---|---|
| `version` | The string of `fusion.GetAttrs()["FUSIONS_Version"]` |
| `registry` | All entries of `GetRegList(1)`. Each key is taken from the attributes of `GetRegAttrs`: `id` is `REGS_ID`, `class_type` is `REGI_ClassType`, `hidden` is `REGB_Hide`, `category` is `REGS_Category`, and `name` is `REGS_Name`. `modifier` is true when bit1 (value 2) of the type class is not set. The localized `REGS_UIName` is not used |
| `tools` | A mapping from type name to a mapping of input name to attribute dictionary. The input order is the order of `GetInputList`. The keys of the attribute dictionary are Fusion attribute names such as `INPS_ID`. Attributes whose value is `None`, `False`, `0`, `0.0`, or `''` are removed |
| `outputs` | A mapping from type name to a mapping of output port name to attribute dictionary. It holds the attributes returned by `GetOutputList` under the same rule as `tools`. It is a separate key from `tools` so that the structure of the input dictionary does not change |
| `failed` | Types whose input definitions could not be acquired. Each has `id`, `reason` (a code), `class_type`, and, for a failure caused by an exception, `detail` (the exception message) |

The `reason` codes are four: `not_a_tool` (type class 1), `add_tool_returned_none`, `add_tool_raised`, and `read_inputs_raised`.
`class_type` is present in every element, and is `null` when the type class cannot be acquired.

Attributes whose value is the default are removed because `Input.GetAttrs()` returns attributes filled with default values (such as `INPI_IC_ControlID: 0`),
and left as is, the head of a control cannot be determined from the presence of `INPI_IC_ControlID`.

A type that could be created with `AddTool` but for which `GetInputList` returned empty is recorded as an empty dictionary in `tools`, not in `failed`.
This distinguishes "exists in Fusion", "can be created", and "has input definitions" through `registry`, `failed`, and `tools`.

`schema/` in the repository is excluded by `.gitignore`.
Absolute paths are embedded in the script pasted into the console, and the package can be imported from elsewhere, so the schema directory is a fixed absolute path and not a repository-relative location.

## Control splitting

Controls are split by scanning the schema's input definitions in declaration order.

- An input without `INPI_IC_ControlGroup` becomes a control by itself
- An input that has `INPI_IC_ControlGroup` and no `INPI_IC_ControlID` is the head of a control, and the control continues up to just before the next head appears
- An input that has `INPI_IC_ControlID` and no `INPI_IC_ControlGroup` is treated as a control by itself
- A continuation input (an input that has both `INPI_IC_ControlGroup` and `INPI_IC_ControlID`) belongs to the same control as the previous input only when the previous input has the same `INPI_IC_ControlGroup` value.
  If the previous value differs, it is treated as a control by itself
- Controls are not split by the value of `INPI_IC_ControlGroup`, because the same value is reused by multiple controls
- `INPI_PC_ControlGroup` and `INPI_PC_ControlID` are not used for control splitting. They will be added to the specification when the declaration of 3D transform tools needs them

The control of an input in `UserControls` is the set of inputs that share the value of `IC_ControlGroup` within the `UserControls` of the same tool,
sorted in ascending order of `IC_ControlID`. An input without `IC_ControlGroup` becomes a control by itself.
Because `IC_ControlGroup` in `UserControls` is a per-tool number, controls are split by the value.

## Reference graph

The graph is built from a composition. A node is a tool and an edge is a reference. There are two kinds of edges.

- **Connection**: the tool and output that `SourceOp` and `Source` of an input point to
- **Expression**: the tool names and input names that appear in the `Expression` of an input

The tool name namespace is one for the whole composition, including the inside of macros.
Because a tool outside a macro can directly reference a tool inside it with `SourceOp` (`MediaOut1` pointing to `Merge1` inside the macro),
the graph is built once, recursing into the inside of macros, and per-macro queries are narrowed by the set of target tools.
If two tools have the same name, it is an error.

Expressions are written in Lua. References are extracted by the following rules.

- Dot form (`Tool.Port`) is a reference to another tool
- A bare identifier (not a function call) is a reference to an input of the same tool
- Lua reserved words, standard library names (`comp`, `string`, `math`, and so on), the identifier immediately before a function call, and a dot chain that starts right after `)` are not treated as references

An edge has a type. The type is determined from `INPS_DataType` in the schema. If the input is not in the schema,
the `LINKID_DataType` of the tool's `UserControls` is used. If it is in neither, the type is treated as unknown.

Values read out (such as `Default`) are converted to Python types (`int`, `float`, `str`, `bool`) and returned.
`document.py` keeps numbers as text for round trips, while the graph is for queries only, so it converts them.

The graph answers the following queries.

- Edges whose target does not exist (dangling references)
- Tools reachable from a given tool by following connection edges backward (a type can be specified)
- The set of inputs driven by expressions: the (tool name, input name) of inputs that have an `Expression`, and the expression. Used by `no-driven-publish`
- The range of keyframe times: for all tables that have `KeyFrames`, the minimum and maximum of their keys (times). Used by `keyframe-origin`
- The set of inputs of a tool (`Graph.known_inputs`): the union of the schema inputs and `UserControls`
- The output ports of a tool (`Graph.output_ports`): a mapping from output name to type, obtained from the schema. `MacroOperator` is not treated specially
- The control that an input belongs to and its kind (`Graph.control_of`, `Graph.control_type`)

## Macro

When a composition contains a macro, the following is read for each macro.

- **Published inputs**: the `InstanceInput` entries of `Inputs`, in order. Each element has the key, `SourceOp`, `Source`, `Name`, `Page`, `ControlGroup`, `Expression`, and `Default`
- **Outputs**: the (`SourceOp`, `Source`) of the `InstanceOutput` entries of `Outputs`
- A reference graph built from the inner `Tools`

## UserControls

When a tool has `UserControls`, the inputs defined there are read.

| Attribute | Read as |
|---|---|
| `LINKID_DataType` | Value type |
| `LINKS_Name` | Display name |
| `INPID_InputControl` | Control kind |
| `INP_Passive` | Not editable (common in `LabelControl`) |
| `INP_External` | Whether it can be set from outside |
| `ICS_ControlPage` | Page name |
| `INP_Default` | Default value |
| `IC_ControlGroup` / `IC_ControlID` | Group and position of the control |
| `CCS_AddString` (positional argument) | Combo choices |
| Others (`INP_Integer`, `INP_MinScale`, `CLRC_*`, `MBTNC_*`, `TEC_*`, and so on) | All kept as a raw dictionary |

The attributes in the table above are read as named attributes, and all attributes, including the others, are also kept in the raw dictionary.

