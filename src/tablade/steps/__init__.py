"""步驟（DESIGN.md §7）。匯入這個套件就會把所有步驟登錄到 registry。

匯入順序就是 UI 與 AI skill 步驟目錄的顯示順序。
"""

# ruff: noqa: I001
from . import remove_rows  # noqa: F401  移除前 N 列、後 N 列、空白列
from . import split  # noqa: F401  依分隔符號拆成多欄、拆欄
from . import headers  # noqa: F401  提升標題列
from . import fill_down  # noqa: F401  向下填滿
from . import columns  # noqa: F401  刪除欄
from . import convert  # noqa: F401  文字轉數字、文字轉日期
from . import filter  # noqa: F401  篩選
from . import files  # noqa: F401  合併來源檔
from .registry import all_steps, get

__all__ = ["all_steps", "get"]
