"""從 registry 產生步驟規格（spec）：給驗證器、AI skill 與 schema lock 使用（DESIGN.md §17）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tablade.i18n import strip_en
from tablade.steps import all_steps

from .schema import READERS, RECIPE_VERSION, Recipe
from .validator import SYSTEM_COLUMNS

LOCK_PATH = Path(__file__).with_name("recipe-schema.lock.json")
_HUMAN_KEYS = {
    "title",
    "description",
    "ui_labels",
    "ui_option_hints",
    "label",
    "category",
    "examples",
    "ai_notes",
    "doc",  # 自訂的語法表（ui_syntax）每一項的說明與例子
    "example",
}
"""純說明用的欄位：schema lock 不記（只改這些文字不用升 RECIPE_VERSION）。"""


def build_spec() -> dict[str, Any]:
    """AI skill、驗證器與 schema lock 用的規格：一律中文（拿掉欄位的英文 ``"en"``）。"""
    schema = strip_en(Recipe.model_json_schema())
    steps = {}
    for cls in all_steps():
        steps[cls.type_name()] = {
            "label": cls.label,
            "category": cls.category,
            "description": cls.summary(),
            "effect": cls.effect,
            "schema_ref": cls.__name__,
            **({"combines": True} if cls.combines else {}),  # 合併來源檔：之後多 __orig_* 系統欄位
        }
    return {
        "recipe_version": RECIPE_VERSION,
        "schema": schema,
        "steps": steps,
        "readers": READERS,
        "system_columns": list(SYSTEM_COLUMNS),
        "system_column_defs": condition_defs(),
    }


def condition_defs() -> list[str]:
    """條件（篩選、將列進行分類、依條件複製欄位共用）的 schema 名稱：只有這裡面可以引用系統欄位。"""
    from tablade.steps.filter import Condition

    return [member.__name__ for member in Condition.__origin__.__args__]  # type: ignore[attr-defined]


def structural(value: Any) -> Any:
    """去掉純說明用的欄位（標題、說明、選項文字），只留會影響 recipe 能否通過的部分。"""
    if isinstance(value, dict):
        return {k: structural(v) for k, v in value.items() if k not in _HUMAN_KEYS}
    if isinstance(value, list):
        return [structural(v) for v in value]
    return value


def lock_content() -> dict[str, Any]:
    spec = build_spec()
    return {"recipe_version": spec["recipe_version"], "spec": structural(spec)}


def read_lock() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def write_lock() -> Path:
    LOCK_PATH.write_text(
        json.dumps(lock_content(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return LOCK_PATH
