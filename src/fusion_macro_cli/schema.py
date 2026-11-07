"""スキーマの読み出しと制御の切り出し。

スキーマは ``schema dump`` が書き出す gzip 圧縮 JSON であり、ツール種別ごとの入力定義と
出力ポート定義を持つ。
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import os
import pathlib
import re
from typing import Any, Final

#: スキーマのディレクトリを上書きする環境変数。
ENV_VAR: Final[str] = "FUSION_MACRO_CLI_SCHEMA"

_FILENAME_GLOB: Final[str] = "fusion-*.json.gz"

#: 多要素ツールの要素の入力名(`<族><n>`)。
_ELEMENT_SUFFIX: Final[re.Pattern[str]] = re.compile(r"^(.*\D)(\d+)$")

#: `failed` の符号から文言への対応。文言をファイルへ書かないため、言い回しの変更が既存のスキーマに影響しない。
_REASON_TEXT: Final[dict[str, str]] = {
    "not_a_tool": "not a tool (registry class type 1)",
    "add_tool_returned_none": "AddTool returned None",
    "add_tool_raised": "AddTool raised an exception",
    "read_inputs_raised": "GetInputList raised an exception",
}

#: 「`INPI_IC_ControlGroup` を持たない」ことを表す番兵。
_NO_GROUP: Final[object] = object()


def schema_dir() -> pathlib.Path:
    """スキーマのディレクトリを返す。"""
    override = os.environ.get(ENV_VAR)
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".local" / "share" / "fusion-macro-cli" / "schema"


def list_schemas(directory: pathlib.Path | None = None) -> list[pathlib.Path]:
    """スキーマファイルを更新日時の新しい順に列挙する。

    ファイル名のバージョン番号は区切りの数や桁数がバージョンごとに変わり、文字列の比較が新旧と一致しないため、更新日時で判定する。
    """
    target = directory if directory is not None else schema_dir()
    if not target.is_dir():
        return []
    return sorted(
        target.glob(_FILENAME_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


@dataclasses.dataclass(frozen=True)
class InputAttributes:
    """入力1つ分の属性。``default`` は JSON の値を解釈せずに保持する。"""

    data_type: str | None
    control_type: str | None
    external: bool | None
    default: Any
    page: str | None
    ic_name: str | None
    control_group: int | None
    has_control_id: bool


@dataclasses.dataclass(frozen=True)
class _FailedEntry:
    reason: str
    class_type: int | None
    detail: str | None


class Schema:
    """ツール種別ごとの入力定義と `GetRegList` の一覧を保持する。"""

    def __init__(
        self,
        version: str,
        registry_ids: frozenset[str],
        tools: dict[str, dict[str, dict[str, Any]]],
        outputs: dict[str, dict[str, dict[str, Any]]],
        failed: dict[str, _FailedEntry],
    ) -> None:
        self.version = version
        self._registry_ids = registry_ids
        self._tools = tools
        self._outputs = outputs
        self._failed = failed

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schema":
        """`schema dump` が書いた JSON 相当の辞書から組み立てる。`outputs` を持たない古いスキーマも読める。"""
        registry_ids = frozenset(entry["id"] for entry in data["registry"])
        failed = {
            entry["id"]: _FailedEntry(
                reason=entry["reason"],
                class_type=entry.get("class_type"),
                detail=entry.get("detail"),
            )
            for entry in data["failed"]
        }
        return cls(
            version=data["version"],
            registry_ids=registry_ids,
            tools=data["tools"],
            outputs=data.get("outputs", {}),
            failed=failed,
        )

    def exists(self, tool_id: str) -> bool:
        """種別が Fusion の `GetRegList`（`registry`）に存在するかを返す。"""
        return tool_id in self._registry_ids

    def has_inputs(self, tool_id: str) -> bool:
        """種別の入力定義を取得できたかを返す。`GetInputList` が空を返した種別も真となる。"""
        return tool_id in self._tools

    def missing_reason(self, tool_id: str) -> str | None:
        """入力定義を取得できなかった理由を返す。`failed` に記録が無い種別では `None` を返す。"""
        entry = self._failed.get(tool_id)
        if entry is None:
            return None
        text = _REASON_TEXT[entry.reason]
        if entry.detail:
            return f"{text}: {entry.detail}"
        return text

    def input_names(self, tool_id: str) -> list[str]:
        """種別が持つ入力名を、`GetInputList` が返した宣言順で返す。"""
        return list(self._require_tool(tool_id).keys())

    def input_attributes(self, tool_id: str, input_name: str) -> InputAttributes:
        """入力1つの属性を返す。"""
        inputs = self._require_tool(tool_id)
        raw = self._require_input(tool_id, input_name, inputs)
        return InputAttributes(
            data_type=raw.get("INPS_DataType"),
            control_type=raw.get("INPID_InputControl"),
            external=raw.get("INPB_External"),
            default=raw.get("INPN_Default"),
            page=raw.get("INPS_ICS_ControlPage"),
            ic_name=raw.get("INPS_IC_Name"),
            control_group=raw.get("INPI_IC_ControlGroup"),
            has_control_id="INPI_IC_ControlID" in raw,
        )

    def controls(self, tool_id: str) -> list[list[str]]:
        """種別の入力を、宣言順を保って制御ごとに区切る。

        `INPI_IC_ControlID` を持たない入力が制御の先頭となる。`INPI_IC_ControlGroup` の値は複数の制御で
        再利用されるため区切りに使わない。続きの形の入力は、直前の入力が同じ `ControlGroup` を持つ場合に限り
        合流させる(`Merge` の `ObjectID` のように、先頭が `GetInputList` に現れない入力があるため)。
        """
        inputs = self._require_tool(tool_id)
        result: list[list[str]] = []
        prev_group: Any = _NO_GROUP
        for name, raw in inputs.items():
            has_group = "INPI_IC_ControlGroup" in raw
            has_id = "INPI_IC_ControlID" in raw
            group = raw.get("INPI_IC_ControlGroup")
            continues = (
                has_group
                and has_id
                and prev_group is not _NO_GROUP
                and group == prev_group
            )
            if continues:
                result[-1].append(name)
            else:
                result.append([name])
            prev_group = group if has_group else _NO_GROUP
        return result

    def element_of(self, tool_id: str, input_name: str) -> int | None:
        """多要素ツールの入力が所属する要素の番号を返す。要素に所属しない入力では `None` を返す。

        所属を示す属性は存在しないため、`SelectElement` を持つ種別で `Enabled<n>` と同一ページにある `<族><n>` を要素 n とする。
        """
        inputs = self._require_tool(tool_id)
        self._require_input(tool_id, input_name, inputs)
        match = _ELEMENT_SUFFIX.match(input_name)
        if "SelectElement" not in inputs or match is None:
            return None
        enabled = inputs.get(f"Enabled{match.group(2)}")
        if enabled is None or inputs[input_name].get("INPS_ICS_ControlPage") != enabled.get("INPS_ICS_ControlPage"):
            return None
        return int(match.group(2))

    def visible_substitute(self, tool_id: str, input_name: str) -> str | None:
        """要素の選択に依らず表示される、同じ値の複製入力(`<入力名>Clone`)を返す。無ければ `None` を返す。

        `TextPlus` の `Red1Clone` などは `INPB_IgnoreVisible` を持ち、書き込みは `Red1` へ反映される。
        """
        inputs = self._require_tool(tool_id)
        clone = inputs.get(f"{input_name}Clone")
        if clone is None or not clone.get("INPB_IgnoreVisible"):
            return None
        return f"{input_name}Clone"

    def control_of(self, tool_id: str, input_name: str) -> list[str]:
        """入力名から、それが属する制御（入力名の列）を返す。"""
        inputs = self._require_tool(tool_id)
        self._require_input(tool_id, input_name, inputs)
        for control in self.controls(tool_id):
            if input_name in control:
                return control
        raise AssertionError(
            f"{input_name!r} of {tool_id!r} was not placed into any control"
        )

    def output_names(self, tool_id: str) -> list[str] | None:
        """種別の出力ポート名を宣言順で返す。定義が無い場合は、判定を飛ばせるよう例外ではなく `None` を返す。"""
        tool_outputs = self._outputs.get(tool_id)
        if tool_outputs is None:
            return None
        return list(tool_outputs.keys())

    def output_type(self, tool_id: str, output_name: str) -> str | None:
        """出力ポートの値の型（`OUTS_DataType`）を返す。定義が無い場合は `None` を返す。"""
        tool_outputs = self._outputs.get(tool_id)
        if tool_outputs is None:
            return None
        raw = tool_outputs.get(output_name)
        if raw is None:
            return None
        return raw.get("OUTS_DataType")

    def _require_tool(self, tool_id: str) -> dict[str, dict[str, Any]]:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(
                f"schema has no input definitions for {tool_id!r}"
            ) from None

    @staticmethod
    def _require_input(
        tool_id: str, input_name: str, inputs: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            return inputs[input_name]
        except KeyError:
            raise KeyError(f"{tool_id!r} has no input {input_name!r}") from None


def load(path: pathlib.Path | str | None = None) -> Schema:
    """スキーマを読む。`path` を省略した場合はスキーマのディレクトリの最新のファイルを読む。"""
    if path is None:
        candidates = list_schemas()
        if not candidates:
            raise FileNotFoundError(f"no schema files found in {schema_dir()}")
        target = candidates[0]
    else:
        target = pathlib.Path(path)
    with gzip.open(target, "rt", encoding="utf-8") as f:
        data = json.load(f)
    return Schema.from_dict(data)
