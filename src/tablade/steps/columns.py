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


@register
class SelectColumns(Step):
    """保留和排序欄位：只留下列出的欄，依清單的順序排列；沒列到的欄會刪掉。"""

    type: Literal["select_columns"] = "select_columns"
    columns: list[NonEmpty] = Field(
        title="要保留的欄位（依順序）", min_length=1, json_schema_extra={"ui": "column_order"}
    )

    label = "保留和排序欄位"
    category = "欄"
    effect = {"kind": "select", "names": "$columns"}
    ai_notes = (
        "columns 依輸出的順序列出要保留的資料欄，沒列到的資料欄會刪掉"
        "（系統欄位 __source_file、__line_no 不受影響）。欄名不能重複。"
    )
    examples = (
        Example(
            title="只留三欄並調整順序",
            params={"columns": ["金額", "日期", "門市"]},
            input={
                "日期": ["115/01/05", "115/01/06"],
                "門市": ["北區門市", "中區門市"],
                "備註": ["網路", "門市"],
                "金額": ["1,280", "25"],
            },
            output={
                "金額": ["1,280", "25"],
                "日期": ["115/01/05", "115/01/06"],
                "門市": ["北區門市", "中區門市"],
            },
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        columns = [c for c in frame.columns if not c.startswith("__")]
        return {"columns": columns} if columns else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        # 依欄位效果（validator 的 select）排出輸出欄位；欄位不存在或重複時丟出 EffectError
        return lf.select(self.output_order(lf))

    def describe(self) -> str:
        return f"保留 {len(self.columns)} 欄：{quote_list(self.columns, limit=6)}"


@register
class RenameColumns(Step):
    """改欄名：在要改的欄位旁邊填上新名稱。"""

    type: Literal["rename_columns"] = "rename_columns"
    names: dict[NonEmpty, NonEmpty] = Field(
        title="新欄名",
        min_length=1,
        description="空白代表不改",
        json_schema_extra={"ui": "rename_map"},
    )

    label = "改欄名"
    category = "欄"
    effect = {"kind": "rename", "names": "$names"}
    ai_notes = (
        "names 是 {舊欄名: 新欄名}，只列要改的欄。改完後欄名不能重複（可以互換兩個欄名），"
        "新欄名不能以 __ 開頭。要依標題列一次改全部欄名時，用 promote_headers。"
    )
    examples = (
        Example(
            title="改兩個欄名",
            params={"names": {"欄1": "日期", "欄3": "金額"}},
            input={"欄1": ["115/01/05"], "欄2": ["北區門市"], "欄3": ["1,280"]},
            output={"日期": ["115/01/05"], "欄2": ["北區門市"], "金額": ["1,280"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        # 從欄標題右鍵進來時，先填上原本的名稱，直接改就好
        column = context.get("column")
        return {"names": {column: column}} if column in frame.columns else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        order = self.output_order(lf)  # 先用欄位效果檢查舊名存在、新名不重複
        return lf.rename(dict(self.names)).select(order)

    def describe(self) -> str:
        pairs = [f"{old}→{new}" for old, new in self.names.items()]
        return f"改欄名：{quote_list(pairs)}"
