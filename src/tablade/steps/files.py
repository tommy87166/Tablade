"""合併：合併來源檔（結合在 join.py）。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from tablade.ingest.sources import Part, concat

from .base import (
    LINE_NO,
    ORIGINAL,
    ROW_INDEX,
    SHEET,
    SOURCE_FILE,
    STAGE,
    TEMP_PREFIX,
    Example,
    NonEmpty,
    Step,
    system_columns,
)
from .registry import register


@register
class MergeFiles(Step):
    """依指定的順序把所有來源檔接成一份，行號從 1 重新編（原本的來源檔與行號另外留在系統欄位）。"""

    type: Literal["merge_files"] = "merge_files"
    order: list[NonEmpty] = Field(
        default_factory=list,
        title="來源檔順序",
        description="沒列到的來源檔依原本的順序接在後面",
        json_schema_extra={"ui": "source_files"},
    )
    name: NonEmpty = Field(default="合併", title="合併後的來源檔名稱")

    label = "合併來源檔"
    category = "合併"
    per_file = True
    combines = True
    scope_note = "之後的步驟不再分來源檔"
    ai_notes = (
        "多個來源檔要當成一份連續的資料處理時使用（例如分成好幾個檔匯出的同一份報表）。"
        "之後的 __source_file 都是 name、__line_no 從 1 重新編號，原本的來源檔、行號（工作表、階段）"
        "留在系統欄位 __orig_source_file、__orig_line_no（__orig_sheet、__orig_stage），警告會同時寫出原始位置；"
        "「前 N 列」「後 N 列」「向下填滿」「把第一列作為欄位標題」也改成對整份資料計算。"
        "讀好幾個前面階段的輸出（附加查詢）時，也是用它把各階段的資料接起來。"
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
        names = schema.names()
        # 原本的來源檔、行號（工作表、階段）存到 __orig_*；已經合併過的保留最早的
        original = [
            pl.col(col).alias(orig)
            for col, orig in ORIGINAL.items()
            if col in names and orig not in names
        ]
        # 合併後就不再分工作表、階段（改成空值，之後整份當成一個來源）
        cleared = [pl.lit(None, dtype=pl.String).alias(c) for c in (SHEET, STAGE) if c in names]
        merged = (
            lf.with_columns(original)
            .with_row_index(ROW_INDEX, offset=1)
            .with_columns(
                pl.lit(self.name, dtype=schema[SOURCE_FILE]).alias(SOURCE_FILE),
                *cleared,
                pl.col(ROW_INDEX).cast(schema[LINE_NO]).alias(LINE_NO),
            )
            .drop(ROW_INDEX)
        )
        after = merged.collect_schema().names()
        data = [c for c in after if not c.startswith("__")]
        temps = [c for c in after if c.startswith(TEMP_PREFIX)]  # 之前步驟的警告、進度用的位置欄
        return [(self.name, merged.select(system_columns(after) + data + temps))]

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """單獨套在一份資料上：只改名稱、重編行號（引擎會用 ``combine``）。"""
        return self.combine([("", lf)])[0][1]

    def describe(self) -> str:
        how = f"依「{'、'.join(self.order)}」的順序" if self.order else "依原本的順序"
        return f"{how}合併成「{self.name}」"
