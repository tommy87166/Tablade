"""把第一列作為欄位標題。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from tablade.i18n import en, plural, t

from .base import (
    ROW_INDEX,
    Example,
    NewColumns,
    Step,
    StepError,
    WarningCheck,
    collect_small,
    data_columns,
    quote_list,
)
from .registry import register


@register
class PromoteHeaders(Step):
    """用目前的第一列當欄名，並移除那一列。欄名在新增步驟時就決定好；之後資料的標題不同時會警告，但仍用原本的欄名。"""

    type: Literal["promote_headers"] = "promote_headers"
    names: NewColumns = Field(
        title="欄名",
        description="建立步驟時從第一列讀出，之後固定",
        json_schema_extra=en(
            "Column names",
            description="Read from the first row when the step is created, then fixed",
        ),
    )

    label = "把第一列作為欄位標題"
    label_en = "Promote headers"
    doc_en = (
        "Use the current first row as column names and remove that row. The names are set when the "
        "step is added; if a later file's header row is different, you get a warning but the "
        "original names are kept."
    )
    per_file = True
    category = "欄"
    effect = {"kind": "rename_data", "names": "$names"}
    ai_notes = (
        "names 的數量必須等於目前的資料欄數。每個檔案的第一列都會被移除（多檔時每個檔案都有標題列）。"
        "如果某個檔案的第一列和 names 不同，會產生警告，但仍照 names 命名。"
    )
    examples = (
        Example(
            title="第一列當欄名",
            params={"names": ["日期", "門市"]},
            input={"欄1": ["日期", "115/01/05"], "欄2": ["門市", "北區門市"]},
            output={"日期": ["115/01/05"], "門市": ["北區門市"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        columns = [c for c in frame.columns if not c.startswith("__")]
        if not columns or frame.height == 0:
            return {"names": columns}
        first = frame.select(columns).row(0)
        return {"names": cls.suggest_names(list(first))}

    @classmethod
    def suggest_names(cls, first_row: list[object]) -> list[str]:
        """建立步驟時從第一列產生欄名：空白補成「欄N」，重複的加上 _2、_3。"""
        names: list[str] = []
        for i, value in enumerate(first_row, start=1):
            base = str(value).strip() if value is not None else ""
            base = base or f"欄{i}"  # i18n-ignore：自動產生的欄名是資料（寫進 Recipe）
            if base.startswith("__"):
                base = base.lstrip("_") or f"欄{i}"  # i18n-ignore：自動產生的欄名是資料
            name, k = base, 2
            while name in names:
                name, k = f"{base}_{k}", k + 1
            names.append(name)
        return names

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        columns = data_columns(lf)
        if len(columns) != len(self.names):
            raise StepError(
                t(
                    f"目前有 {len(columns)} 個資料欄，但 names 有 {len(self.names)} 個",
                    f"The data has {plural(len(columns), 'column', 'columns')}, "
                    f"but {plural(len(self.names), 'name is', 'names are')} given",
                )
            )
        order = self.output_order(lf)
        flag = self.temp("mismatch")
        # 這個檔的第一列（只讀開頭）和 recipe 記錄的欄名不同時，在下一列標記警告
        first = collect_small(lf.select(columns).head(1))
        differs = first.height > 0 and [
            "" if v is None else str(v).strip() for v in first.row(0)
        ] != list(self.names)
        if differs:
            out = (
                lf.with_row_index(ROW_INDEX)
                .with_columns((pl.col(ROW_INDEX) == 1).alias(flag))
                .filter(pl.col(ROW_INDEX) > 0)
                .drop(ROW_INDEX)
            )
        else:
            out = lf.slice(1).with_columns(pl.lit(False).alias(flag))
        return out.rename(dict(zip(columns, self.names, strict=True))).select([*order, flag])

    def warning_checks(self) -> list[WarningCheck]:
        return [
            WarningCheck(
                condition=pl.col(self.temp("mismatch")),
                message=t(
                    "這個檔案的標題列和 recipe 記錄的欄名不同，仍照 recipe 命名",
                    "This file's header row differs from the column names in the Recipe; "
                    "the Recipe's names are used",
                ),
            )
        ]

    def describe(self) -> str:
        return t(
            f"用第一列當欄名（{quote_list(self.names)}）",
            f"Use the first row as column names ({quote_list(self.names)})",
        )
