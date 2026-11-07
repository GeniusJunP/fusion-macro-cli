"""検査をまとめて走らせる。

終了コード 0 は合格、1 は失敗、2 は検証用のデータが無いことを表す。
tests/ 直下の test_*.py を名前順に実行し、最も悪い結果を返す。
2 は合否ではないため、1 があれば 1 を、無ければ 0 を返す。
"""

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
STATUS = {0: "passed", 1: "failed", 2: "skipped (FUSION_MACRO_CLI_PRESETS not set)"}


def main() -> int:
    worst = 0
    for path in sorted(HERE.glob("test_*.py")):
        print(f"--- {path.name}")
        result = subprocess.run([sys.executable, str(path)])
        code = result.returncode
        print(f"    {STATUS.get(code, f'exit code {code}')}")
        if code == 1:
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
