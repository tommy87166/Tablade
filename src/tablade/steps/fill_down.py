"""向下填滿。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from .base import (
    ROW_INDEX,
    SOURCE_FILE,
    ColumnRefs,
    Example,
    Step,
    StepError,
    by_file,
    collect_small,
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
        lf = lf.with_row_index(ROW_INDEX)
        dtype = lf.collect_schema()[ROW_INDEX]
        exprs = []
        for name in self.columns:
            # 整欄往下填（串流可執行），再把每個檔案第一個有值之前的列清回空白，避免上一個檔案的值填過來
            firsts = collect_small(
                lf.filter(pl.col(name).is_not_null())
                .group_by(SOURCE_FILE)
                .agg(pl.col(ROW_INDEX).min().alias("first"))
            )
            first = by_file(dict(zip(firsts[SOURCE_FILE], firsts["first"], strict=True)), dtype)
            before_first = (pl.col(ROW_INDEX) < first).fill_null(True)
            exprs.append(
                pl.when(before_first).then(None).otherwise(pl.col(name).forward_fill()).alias(name)
            )
        return lf.with_columns(exprs).drop(ROW_INDEX)

    def describe(self) -> str:
        return f"向下填滿：{quote_list(self.columns)}"
