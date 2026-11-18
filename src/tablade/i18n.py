"""畫面的語言：Python 產生、畫面上看得到的文字寫成 ``t("中文", "English")``（docs/i18n-plan.md）。

- 語言是模組層級的設定（桌面 App 只有一個使用者），由 ``Session.set_language`` 切換。
- 預設中文：CLI、AI skill、處理紀錄（manifest）不切換，維持中文。
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
    global _lang
    if lang not in LANGS:
        raise ValueError(f"不支援的語言：{lang}（可用：{'、'.join(LANGS)}）")
    _lang = lang


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
