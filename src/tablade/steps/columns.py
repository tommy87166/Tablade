"""刪除欄。"""

from __future__ import annotations

from typing import Annotated, Literal

import polars as pl
from pydantic import Field, ValidationInfo, field_validator

from .base import Example, NonEmpty, Step, pick_column, quote_list
from .registry import register

By = Literal["name", "position"]
BY_LABELS = {"name": "名稱", "position": "位置（第幾欄）"}
Position = Annotated[int, Field(ge=1)]


@register
class RemoveColumns(Step):
    """刪除欄位：依名稱或依位置（第幾個資料欄）選，可以一次選很多個。"""

    type: Literal["remove_columns"] = "remove_columns"
    by: By = Field(default="name", title="依", json_schema_extra={"ui_labels": BY_LABELS})
    columns: list[NonEmpty] = Field(
        default_factory=list,
        validate_default=True,
        title="要刪除的欄位",
        json_schema_extra={"ui": "columns", "ui_show_if": {"by": "name"}},
    )
    positions: list[Position] = Field(
        default_factory=list,
        validate_default=True,
        title="要刪除第幾欄",
        description="從 1 起算，不算系統欄位",
        json_schema_extra={"ui": "column_positions", "ui_show_if": {"by": "position"}},
    )

    label = "刪除欄"
    category = "欄"
    effect = {"kind": "drop", "by": "$by", "names": "$columns", "positions": "$positions"}
    ai_notes = (
        'by="name" 用 columns 列出欄名；by="position" 用 positions 列出第幾個資料欄'
        "（從 1 起，不算 __source_file 等系統欄位）。依名稱比較穩：前面的步驟改了欄位順序時，"
        "依位置會刪到別的欄。不能刪光所有資料欄。"
    )
    examples = (
        Example(
            title="依名稱刪除兩欄",
            params={"columns": ["備註", "單號"]},
            input={
                "日期": ["115/01/05", "115/01/06"],
                "備註": ["網路", "門市"],
                "單號": ["0001", "0002"],
                "金額": ["1,280", "25"],
            },
            output={"日期": ["115/01/05", "115/01/06"], "金額": ["1,280", "25"]},
        ),
        Example(
            title="依位置刪除第 2、3 欄",
            params={"by": "position", "positions": [2, 3]},
            input={
                "日期": ["115/01/05", "115/01/06"],
                "備註": ["網路", "門市"],
                "單號": ["0001", "0002"],
                "金額": ["1,280", "25"],
            },
            output={"日期": ["115/01/05", "115/01/06"], "金額": ["1,280", "25"]},
        ),
    )

    @field_validator("columns")
    @classmethod
    def _names_given(cls, value: list[str], info: ValidationInfo) -> list[str]:
        if info.data.get("by", "name") == "name" and not value:
            raise ValueError("要選至少一個要刪除的欄位")
        return value

    @field_validator("positions")
    @classmethod
    def _positions_given(cls, value: list[int], info: ValidationInfo) -> list[int]:
        if info.data.get("by") == "position" and not value:
            raise ValueError("要選至少一個要刪除的欄位")
        return value

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        return {"columns": [column]} if column else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        # 依欄位效果（validator 的 drop）算出剩下的欄；欄位不存在或位置超出範圍時丟出 EffectError
        return lf.select(self.output_order(lf))

    def describe(self) -> str:
        if self.by == "position":
            return f"刪除第 {'、'.join(str(p) for p in sorted(set(self.positions)))} 欄"
        return f"刪除欄：{quote_list(self.columns)}"
