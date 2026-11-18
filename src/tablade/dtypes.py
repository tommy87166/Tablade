"""型別的中文名稱（型別分頁、錯誤訊息共用）。"""

from __future__ import annotations

import polars as pl

from .i18n import plural, t


def type_label(dtype: pl.DataType) -> str:
    """型別的中文名稱。"""
    if dtype == pl.String:
        return t("文字", "Text")
    if isinstance(dtype, pl.Decimal):
        return t(f"小數（{dtype.scale} 位）", f"Decimal ({plural(dtype.scale, 'place', 'places')})")
    if dtype.is_integer():
        return t("整數", "Integer")
    if dtype.is_float():
        return t("浮點數", "Float")
    if dtype == pl.Date:
        return t("日期", "Date")
    if isinstance(dtype, pl.Datetime):
        return t("日期時間", "Date/Time")
    if dtype == pl.Boolean:
        return t("是／否", "True/False")
    if dtype == pl.Null:
        return t("空", "Null")
    return str(dtype)
