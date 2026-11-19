"""步驟（DESIGN.md §7）。匯入這個套件就會把所有步驟登錄到 registry。

匯入順序就是 UI 與 AI skill 步驟目錄的顯示順序。
"""

# ruff: noqa: I001
from . import remove_rows  # noqa: F401  移除前 N 列、後 N 列、空白列、移除重複
from . import filter  # noqa: F401  篩選（將列進行分類沿用它的條件，所以排在前面）
from . import split  # noqa: F401  依分隔符號拆成多欄、拆欄
from . import report  # noqa: F401  將列進行分類、用正規表示式擷取、依固定位置拆欄
from . import headers  # noqa: F401  把第一列作為欄位標題
from . import fill_down  # noqa: F401  依條件複製欄位、向下填滿
from . import columns  # noqa: F401  刪除欄、保留和排序欄位、改欄名
from . import text  # noqa: F401  清理文字、取代文字
from . import convert  # noqa: F401  文字轉數字、文字轉日期
from . import custom  # noqa: F401  自訂（「欄」分類的最後一個）
from . import aggregate  # noqa: F401  取消樞紐、群組彙總、樞紐
from . import files  # noqa: F401  合併來源檔
from . import join  # noqa: F401  結合
from .registry import all_steps, get

__all__ = ["all_steps", "get"]
