"""畫面的語言：Python 產生、畫面上看得到的文字寫成 ``t("中文", "English")``（docs/i18n-plan.md）。

- 語言是模組層級的設定（桌面 App 只有一個使用者），由 ``Session.set_language`` 切換。
- 預設中文：CLI、AI skill 不切換，維持中文。處理紀錄（manifest）的欄位名稱固定是英文，
  步驟說明與警告是匯出當時畫面的語言（DESIGN.md §9）。
- 兩種語言放在一起，好對照也不會漏翻；``tests/test_i18n.py``（``scripts/check_i18n.py``）檢查
  列在 ``PY_MODULES`` 的檔案裡，中文只出現在 ``t()`` 的第一個參數（docstring 與註解不算）。
- 不能在模組最上層用 ``t()`` 算好常數（會停在載入時的語言），要寫成函式或在用到時才算。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

LANGS = ("zh", "en")
"""可以切換的語言：繁體中文、英文。"""

_lang = "zh"


def set_lang(lang: str) -> None:
    """切換語言；recipe/validator.py（不能 import tablade，自己有 t()）也一起換。"""
    global _lang
    if lang not in LANGS:
        raise ValueError(f"不支援的語言：{lang}（可用：{'、'.join(LANGS)}）")
    _lang = lang
    from tablade.recipe import validator

    validator.LANG = lang


def lang() -> str:
    return _lang


def t(zh: str, en: str) -> str:
    """依目前的語言選一個。"""
    return en if _lang == "en" else zh


def plural(n: int, one: str, many: str) -> str:
    """英文的單複數：``plural(3, "row", "rows")`` → ``"3 rows"``（中文直接寫在 t 的第一個參數）。"""
    return f"{n:,} {one if n == 1 else many}"


@contextlib.contextmanager
def using(lang: str) -> Iterator[None]:
    """暫時換成某個語言（例如產生 AI skill 時一律用中文），結束後換回原本的語言。"""
    previous = _lang
    set_lang(lang)
    try:
        yield
    finally:
        set_lang(previous)


def en(
    title: str | None = None,
    description: str | None = None,
    ui_labels: dict[str, str] | None = None,
    ui_option_hints: dict[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    """步驟參數欄位的英文：``Field(title="中文", json_schema_extra=en("English"))``。

    放在 JSON schema 的 ``"en"`` 裡；送給前端時依語言換掉 title、description、ui_labels、ui_option_hints
    （選項下面的說明）（``localize_schema``）。
    已經有其他 json_schema_extra 時合在一起：``json_schema_extra={"ui_labels": LABELS, **en("…", ui_labels=…)}``。
    """
    text: dict[str, object] = {}
    if title is not None:
        text["title"] = title
    if description is not None:
        text["description"] = description
    if ui_labels is not None:
        text["ui_labels"] = ui_labels
    if ui_option_hints is not None:
        text["ui_option_hints"] = ui_option_hints
    return {"en": text}


def localize_schema(value: object) -> object:
    """JSON schema 依目前的語言換上英文（每個有 ``"en"`` 的地方），並拿掉 ``"en"``（回傳新的物件）。"""
    if isinstance(value, dict):
        out = {k: localize_schema(v) for k, v in value.items() if k != "en"}
        text = value.get("en")
        if _lang == "en" and isinstance(text, dict):
            out.update(text)
        return out
    if isinstance(value, list):
        return [localize_schema(v) for v in value]
    return value


def strip_en(value: object) -> object:
    """拿掉 JSON schema 裡的 ``"en"``（AI skill 與 schema lock 用：維持中文、不受英文影響）。"""
    if isinstance(value, dict):
        return {k: strip_en(v) for k, v in value.items() if k != "en"}
    if isinstance(value, list):
        return [strip_en(v) for v in value]
    return value
