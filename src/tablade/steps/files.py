"""來源檔：合併來源檔。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from tablade.ingest.sources import Part, concat

from .base import LINE_NO, ROW_INDEX, SHEET, SOURCE_FILE, Example, NonEmpty, Step
from .registry import register


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
    combines = True
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

    def combine(self, parts: list[Part]) -> list[Part]:
        names = [name for name, _ in parts]
        wanted = [f for f in dict.fromkeys(self.order) if f in names]
        order = wanted + [f for f in names if f not in wanted]
        frames = dict(parts)
        # 照指定的順序把每個檔接起來：每個檔只讀一次，不排序
        lf = concat([(f, frames[f]) for f in order])
        schema = lf.collect_schema()
        # 讀 Excel 全部工作表時：合併後就不再分工作表（__sheet 改成空值，之後整份當成一個來源）
        sheet = [pl.lit(None, dtype=pl.String).alias(SHEET)] if SHEET in schema else []
        merged = (
            lf.with_row_index(ROW_INDEX, offset=1)
            .with_columns(
                pl.lit(self.name, dtype=schema[SOURCE_FILE]).alias(SOURCE_FILE),
                *sheet,
                pl.col(ROW_INDEX).cast(schema[LINE_NO]).alias(LINE_NO),
            )
            .drop(ROW_INDEX)
        )
        return [(self.name, merged)]

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """單獨套在一份資料上：只改名稱、重編行號（引擎會用 ``combine``）。"""
        return self.combine([("", lf)])[0][1]

    def describe(self) -> str:
        how = f"依「{'、'.join(self.order)}」的順序" if self.order else "依原本的順序"
        return f"{how}合併成「{self.name}」"
