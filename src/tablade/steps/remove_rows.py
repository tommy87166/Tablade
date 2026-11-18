"""移除列：前 N 列、後 N 列、空白列、重複的列。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from .base import (
    Example,
    NonEmpty,
    Step,
    StepError,
    collect_small,
    data_columns,
    is_blank,
    quote_list,
)
from .registry import register


@register
class RemoveTopRows(Step):
    """移除最前面的 N 列（以目前剩下的列計算）。"""

    type: Literal["remove_top_rows"] = "remove_top_rows"
    n: int = Field(title="列數", ge=0)

    label = "移除前 N 列"
    per_file = True
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
        return lf.slice(self.n)

    def describe(self) -> str:
        return f"移除每個檔案的前 {self.n} 列"


@register
class RemoveBottomRows(Step):
    """移除最後面的 N 列（以目前剩下的列計算）。"""

    type: Literal["remove_bottom_rows"] = "remove_bottom_rows"
    n: int = Field(title="列數", ge=0)

    label = "移除後 N 列"
    per_file = True
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
        # 串流到檔尾才知道哪一列是最後一列，所以先數一次列數
        count = collect_small(lf.select(pl.len())).item()
        return lf.head(max(count - self.n, 0))

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


@register
class Dedupe(Step):
    """移除重複：依指定的欄判斷重複的列，只留第一筆（或最後一筆）。每個來源檔各自找重複。"""

    type: Literal["dedupe"] = "dedupe"
    columns: list[NonEmpty] = Field(
        default_factory=list,
        title="依哪些欄判斷重複",
        description="不選代表整列（所有資料欄）都一樣才算重複",
        json_schema_extra={"ui": "columns"},
    )
    keep: Literal["first", "last"] = Field(
        default="first",
        title="保留",
        json_schema_extra={"ui_labels": {"first": "第一筆", "last": "最後一筆"}},
    )

    label = "移除重複"
    per_file = True
    whole_source = True
    category = "列"
    ai_notes = (
        "合併來源檔之前，每個來源檔各自找重複（同樣的值出現在不同檔時都會留著）；"
        "要跨檔找重複，先用 merge_files。columns 空清單代表所有資料欄都一樣才算重複。"
        "空字串和 null 是不同的值。留下來的列保留原本的行號與順序。"
    )
    examples = (
        Example(
            title="同一張單號只留第一筆",
            params={"columns": ["單號"]},
            input={"單號": ["0001", "0002", "0001", "0003"], "金額": ["100", "250", "100", "80"]},
            output={"單號": ["0001", "0002", "0003"], "金額": ["100", "250", "80"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = context.get("column")
        return {"columns": [column]} if column in frame.columns else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        available = data_columns(lf)
        missing = [c for c in self.columns if c not in available]
        if missing:
            raise StepError(f"欄位不存在：{'、'.join(missing)}")
        subset = list(dict.fromkeys(self.columns)) or available
        if not subset:
            return lf
        return lf.unique(subset, keep=self.keep, maintain_order=True)

    def describe(self) -> str:
        which = "第一筆" if self.keep == "first" else "最後一筆"
        by = f"依 {quote_list(self.columns)}" if self.columns else "整列相同"
        return f"移除重複（{by}，保留{which}）"
