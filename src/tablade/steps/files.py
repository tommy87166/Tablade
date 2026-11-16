"""來源檔：合併來源檔。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from .base import (
    LINE_NO,
    ROW_INDEX,
    SOURCE_FILE,
    Example,
    NonEmpty,
    Step,
    collect_small,
)
from .registry import register


def file_order(lf: pl.LazyFrame) -> list[str]:
    """目前的來源檔順序（依每個檔第一列的位置；只做一次小聚合）。"""
    stats = collect_small(
        lf.with_row_index(ROW_INDEX)
        .group_by(SOURCE_FILE)
        .agg(pl.col(ROW_INDEX).min().alias("start"))
    )
    return stats.sort("start")[SOURCE_FILE].to_list()


@register
class MergeFiles(Step):
    """依指定的順序把所有來源檔接成一份，行號從 1 重新編。"""

    type: Literal["merge_files"] = "merge_files"
    order: list[NonEmpty] = Field(
        default_factory=list,
        title="來源檔順序",
        description="沒列到的來源檔依原本的順序接在後面",
        json_schema_extra={"ui": "source_files"},
    )
    name: NonEmpty = Field(default="合併", title="合併後的來源檔名稱")

    label = "合併來源檔"
    category = "列"
    per_file = True
    scope_note = "之後的步驟不再分來源檔"
    ai_notes = (
        "多個來源檔要當成一份連續的資料處理時使用（例如分成好幾個檔匯出的同一份報表）。"
        "之後的 __source_file 都是 name、__line_no 從 1 重新編號；"
        "「前 N 列」「後 N 列」「向下填滿」「提升標題列」也改成對整份資料計算。"
        "order 只列要調整順序的檔名即可，沒列到的檔依原本的順序接在後面。"
    )
    examples = (
        Example(
            title="接成一份",
            params={},
            input={"line": ["日期,門市", "115/01/05,北區門市"]},
            output={"line": ["日期,門市", "115/01/05,北區門市"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        return {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        schema = lf.collect_schema()
        current = file_order(lf)
        wanted = [f for f in dict.fromkeys(self.order) if f in current]
        order = wanted + [f for f in current if f not in wanted]
        if order != current:
            # 依檔案拆開再依序接起來（串流執行，不排序）
            lf = pl.concat([lf.filter(pl.col(SOURCE_FILE) == f) for f in order])
        return (
            lf.with_row_index(ROW_INDEX, offset=1)
            .with_columns(
                pl.lit(self.name, dtype=schema[SOURCE_FILE]).alias(SOURCE_FILE),
                pl.col(ROW_INDEX).cast(schema[LINE_NO]).alias(LINE_NO),
            )
            .drop(ROW_INDEX)
        )

    def describe(self) -> str:
        how = f"依「{'、'.join(self.order)}」的順序" if self.order else "依原本的順序"
        return f"{how}合併成「{self.name}」"
