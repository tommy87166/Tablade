"""步驟 registry：recipe 只能使用這裡登錄的步驟類型（DESIGN.md §4、§17）。"""

from __future__ import annotations

from tablade.recipe.validator import EFFECT_KINDS

from .base import Step

_STEPS: dict[str, type[Step]] = {}


def register(cls: type[Step]) -> type[Step]:
    name = cls.type_name()
    if name in _STEPS:
        raise ValueError(f"步驟類型重複登錄：{name}")
    if not cls.label or not cls.category:
        raise ValueError(f"{name} 缺少 label 或 category")
    if not cls.__doc__:
        raise ValueError(f"{name} 缺少說明（docstring）")
    if not cls.label_en or not cls.doc_en:
        raise ValueError(f"{name} 缺少英文的名稱或說明（label_en、doc_en；docs/i18n-plan.md）")
    if cls.scope_note and not cls.scope_note_en:
        raise ValueError(f"{name} 缺少 scope_note_en")
    if not cls.examples:
        raise ValueError(f"{name} 至少要有一個範例（同時是單元測試）")
    if cls.effect.get("kind") not in EFFECT_KINDS:
        raise ValueError(f"{name} 的欄位效果不正確：{cls.effect}")
    _STEPS[name] = cls
    return cls


def get(name: str) -> type[Step]:
    return _STEPS[name]


def all_steps() -> list[type[Step]]:
    """依登錄順序回傳所有步驟類別。"""
    return list(_STEPS.values())
