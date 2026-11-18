"""依條件複製欄位、向下填滿：兩個常一起用（把段落標題的值帶到下面的明細）。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from tablade.i18n import en, t

from .base import (
    ColumnRef,
    ColumnRefs,
    Example,
    NonEmpty,
    Step,
    StepError,
    data_columns,
    is_blank,
    pick_column,
    quote_list,
)
from .filter import Condition
from .registry import register

MATCH_LABELS_EN = {"all": "All conditions", "any": "Any condition"}


@register
class ConditionalCopy(Step):
    """符合條件的列，把指定欄位的值複製到新欄位，其他列留空；常接著用「向下填滿」把段落標題帶到下面的明細。"""

    type: Literal["conditional_copy"] = "conditional_copy"
    conditions: list[Condition] = Field(
        title="條件", min_length=1, json_schema_extra=en("Conditions")
    )
    match: Literal["all", "any"] = Field(
        default="all",
        title="條件組合",
        json_schema_extra={
            "ui_labels": {"all": "全部符合", "any": "任一符合"},
            **en("Match", ui_labels=MATCH_LABELS_EN),
        },
    )
    column: ColumnRef = Field(title="要複製的欄位", json_schema_extra=en("Column to copy"))
    name: NonEmpty = Field(title="新欄位名稱", json_schema_extra=en("New column name"))

    label = "依條件複製欄位"
    label_en = "Copy column if"
    doc_en = (
        "For rows that match the conditions, copy a column's value into a new column; "
        'other rows are left empty. Often followed by "Fill down" to carry section headings '
        "down to the detail rows."
    )
    category = "欄"
    effect = {"kind": "add", "names": "$name"}
    ai_notes = (
        "不拿掉任何列：符合 conditions（寫法和 filter 一樣）的列，新欄 name 是 column 的值，其他列是 null。"
        "典型用法是攤平分段的報表：段落標題列（例如 A 欄是「門市：」、B 欄是門市名稱）用這一步把門市複製到新欄，"
        "接著 fill_down 新欄，把門市帶到下面的明細，最後用 filter 拿掉段落標題列。"
    )
    examples = (
        Example(
            title="把段落標題的門市複製到新欄",
            params={
                "conditions": [{"column": "A", "op": "starts_with", "value": "門市："}],
                "column": "B",
                "name": "門市",
            },
            input={
                "A": ["門市：", "115/01/05", "115/01/06", "門市："],
                "B": ["北區門市", "原子筆", "保溫瓶", "中區門市"],
            },
            output={
                "A": ["門市：", "115/01/05", "115/01/06", "門市："],
                "B": ["北區門市", "原子筆", "保溫瓶", "中區門市"],
                "門市": ["北區門市", None, None, "中區門市"],
            },
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        if column is None:
            return {}
        value = context.get("value")
        condition = (
            {"column": column, "op": "is_not_empty"}
            if value is None or value == ""
            else {"column": column, "op": "equals", "value": str(value)}
        )
        return {"conditions": [condition], "column": column}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        if self.column not in data_columns(lf):
            raise StepError(t(f"欄位不存在：{self.column}", f"Column not found: {self.column}"))
        exprs = [c.expr().fill_null(False) for c in self.conditions]
        matched = pl.all_horizontal(exprs) if self.match == "all" else pl.any_horizontal(exprs)
        copied = pl.when(matched).then(pl.col(self.column)).otherwise(None).alias(self.name)
        return lf.with_columns(copied).select(self.output_order(lf))

    def describe(self) -> str:
        joiner = t(" 且 ", " and ") if self.match == "all" else t(" 或 ", " or ")
        text = joiner.join(c.describe() for c in self.conditions)
        return t(
            f"符合「{text}」的列，把「{self.column}」複製到「{self.name}」",
            f'Copy "{self.column}" to "{self.name}" where {text}',
        )


@register
class FillDown(Step):
    """空白的儲存格填入上面最近一個有值的內容。"""

    type: Literal["fill_down"] = "fill_down"
    columns: ColumnRefs = Field(title="要填滿的欄位", json_schema_extra=en("Columns to fill"))
    blank_as_null: bool = Field(
        default=True,
        title="空字串也當成空白",
        json_schema_extra=en("Treat empty strings as blank"),
    )

    label = "向下填滿"
    label_en = "Fill down"
    doc_en = "Fill blank cells with the nearest value above."
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
            raise StepError(
                t(
                    f"欄位不存在：{'、'.join(missing)}",
                    f"{'Column' if len(missing) == 1 else 'Columns'} not found: {', '.join(missing)}",
                )
            )
        if self.blank_as_null:
            lf = lf.with_columns(
                pl.when(is_blank(pl.col(c))).then(None).otherwise(pl.col(c)).alias(c)
                for c in self.columns
            )
        return lf.with_columns(pl.col(c).forward_fill() for c in self.columns)

    def describe(self) -> str:
        columns = quote_list(self.columns)
        return t(f"向下填滿：{columns}", f"Fill down: {columns}")
