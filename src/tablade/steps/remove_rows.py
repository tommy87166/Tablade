"""移除列：前 N 列、後 N 列、空白列。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from .base import ORDER, SOURCE_FILE, Example, Step, data_columns, is_blank, row_in_file
from .registry import register

_RN = "__rn"
_COUNT = "__rows_in_file"


@register
class RemoveTopRows(Step):
    """移除每個來源檔最前面的 N 列（以目前剩下的列計算）。"""

    type: Literal["remove_top_rows"] = "remove_top_rows"
    n: int = Field(title="列數", ge=0)

    label = "移除前 N 列"
    category = "列"
    ai_notes = (
        "用來去掉報表開頭的標題雜訊。多個檔案時每個檔案各自移除；"
        "如果前面已經移除過空白列，計算的是剩下的列。"
    )
    examples = (
        Example(
            title="去掉兩行標題雜訊",
            params={"n": 2},
            input={
                "line": ["門市銷售明細", "匯出時間：115/02/01", "日期,門市", "115/01/05,北區門市"]
            },
            output={"line": ["日期,門市", "115/01/05,北區門市"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        return {"n": 1}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        return (
            lf.sort(ORDER)
            .with_columns(row_in_file().alias(_RN))
            .filter(pl.col(_RN) >= self.n)
            .drop(_RN)
        )

    def describe(self) -> str:
        return f"移除每個檔案的前 {self.n} 列"


@register
class RemoveBottomRows(Step):
    """移除每個來源檔最後面的 N 列（以目前剩下的列計算）。"""

    type: Literal["remove_bottom_rows"] = "remove_bottom_rows"
    n: int = Field(title="列數", ge=0)

    label = "移除後 N 列"
    category = "列"
    ai_notes = "用來去掉報表結尾的合計、頁尾或「共 N 筆」。多個檔案時每個檔案各自移除。"
    examples = (
        Example(
            title="去掉結尾的筆數",
            params={"n": 1},
            input={"line": ["日期,門市", "115/01/05,北區門市", "共 1 筆"]},
            output={"line": ["日期,門市", "115/01/05,北區門市"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        return {"n": 1}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        return (
            lf.sort(ORDER)
            .with_columns(row_in_file().alias(_RN), pl.len().over(SOURCE_FILE).alias(_COUNT))
            .filter(pl.col(_RN) < pl.col(_COUNT) - self.n)
            .drop(_RN, _COUNT)
        )

    def describe(self) -> str:
        return f"移除每個檔案的後 {self.n} 列"


@register
class RemoveBlankRows(Step):
    """移除所有資料欄位都是空白（null 或只有空白字元）的列。"""

    type: Literal["remove_blank_rows"] = "remove_blank_rows"

    label = "移除空白列"
    category = "列"
    ai_notes = "只看資料欄位，系統欄位（__ 開頭）不算。只有空白字元的列也算空白。"
    examples = (
        Example(
            title="去掉空行",
            params={},
            input={"line": ["日期,門市", "", "   ", "115/01/05,北區門市"]},
            output={"line": ["日期,門市", "115/01/05,北區門市"]},
        ),
    )

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        columns = data_columns(lf)
        if not columns:
            return lf
        blank = pl.all_horizontal([is_blank(pl.col(c)) for c in columns])
        return lf.filter(~blank)

    def describe(self) -> str:
        return "移除空白列"
