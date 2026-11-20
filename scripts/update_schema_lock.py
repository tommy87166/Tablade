#!/usr/bin/env python3
"""更新 src/tablade/recipe/recipe-schema.lock.json（DESIGN.md §17.6）。

步驟定義有結構性變更時（新增步驟、新增或修改參數、改預設值），要先升
``tablade.recipe.schema.RECIPE_VERSION``，再執行：

    uv run python scripts/update_schema_lock.py

- minor（1.0 → 1.1）：相容的變更，例如新增步驟、新增選用參數
- major（1.x → 2.0）：不相容的變更，要附舊 recipe 的自動升級程式
只改標題、說明、選項文字不需要升版本，也不會改到 lock。
"""

from __future__ import annotations

import sys

from tablade.recipe.spec import LOCK_PATH, lock_content, read_lock, structural, write_lock


def main() -> int:
    new = lock_content()
    if LOCK_PATH.exists():
        old = read_lock()
        # 舊的 lock 也照現在的規則去掉說明用的欄位再比（說明用的欄位之後多了一種時不會被當成結構有變）
        if (
            structural(old["spec"]) != new["spec"]
            and old["recipe_version"] == new["recipe_version"]
        ):
            print(
                f"步驟定義有結構性變更，但 RECIPE_VERSION 仍是 {new['recipe_version']}。"
                "請先升版本（相容的變更升 minor，不相容的升 major）。",
                file=sys.stderr,
            )
            return 1
    path = write_lock()
    print(
        f"已更新 {path.name}（recipe 版本 {new['recipe_version']}，{len(structural(new['spec'])['steps'])} 種步驟）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
