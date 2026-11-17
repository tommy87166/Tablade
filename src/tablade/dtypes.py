"""型別的中文名稱（型別分頁、錯誤訊息共用）。"""

from __future__ import annotations

import polars as pl


def type_label(dtype: pl.DataType) -> str:
    """型別的中文名稱。"""
    if dtype == pl.String:
        return "文字"
    if isinstance(dtype, pl.Decimal):
        return f"小數（{dtype.scale} 位）"
    if dtype.is_integer():
        return "整數"
    if dtype.is_float():
        return "浮點數"
    if dtype == pl.Date:
        return "日期"
    if isinstance(dtype, pl.Datetime):
        return "日期時間"
    if dtype == pl.Boolean:
        return "是／否"
    if dtype == pl.Null:
        return "空"
    return str(dtype)
