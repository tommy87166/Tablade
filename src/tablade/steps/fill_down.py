"""向下填滿。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from .base import (
    ColumnRefs,
    Example,
    Step,
    StepError,
    data_columns,
    is_blank,
    pick_column,
    quote_list,
)
from .registry import register


@register
class FillDown(Step):
    """空白的儲存格填入上面最近一個有值的內容。"""

    type: Literal["fill_down"] = "fill_down"
    columns: ColumnRefs = Field(title="要填滿的欄位")
    blank_as_null: bool = Field(default=True, title="空字串也當成空白")

    label = "向下填滿"
    per_file = True
    category = "欄"
    ai_notes = (
        "報表或匯出檔常只在群組的第一列寫值（例如門市、科別），用這一步補齊。"
        "text_lines 拆出來的欄位是空字串而不是 null，所以預設把空字串也當成空白。"
    )
    examples = (
        Example(
            title="補齊門市",
            params={"columns": ["門市"]},
            input={
                "門市": ["北區門市", "", "", "南區門市", ""],
                "品名": ["原子筆", "折疊傘", "滑鼠", "筆記本", "後背包"],
            },
            output={
                "門市": ["北區門市", "北區門市", "北區門市", "南區門市", "南區門市"],
                "品名": ["原子筆", "折疊傘", "滑鼠", "筆記本", "後背包"],
            },
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        return {"columns": [column]} if column else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        missing = [c for c in self.columns if c not in data_columns(lf)]
        if missing:
            raise StepError(f"欄位不存在：{'、'.join(missing)}")
        if self.blank_as_null:
            lf = lf.with_columns(
                pl.when(is_blank(pl.col(c))).then(None).otherwise(pl.col(c)).alias(c)
                for c in self.columns
            )
        return lf.with_columns(pl.col(c).forward_fill() for c in self.columns)

    def describe(self) -> str:
        return f"向下填滿：{quote_list(self.columns)}"
