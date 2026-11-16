"""提升標題列。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import Field

from .base import (
    ORDER,
    SOURCE_FILE,
    Example,
    NewColumns,
    Step,
    StepError,
    WarningCheck,
    data_columns,
    quote_list,
    row_in_file,
)
from .registry import register

_RN = "__rn"


@register
class PromoteHeaders(Step):
    """用每個來源檔目前的第一列當欄名，並移除那一列。欄名在建立步驟時寫死在 recipe。"""

    type: Literal["promote_headers"] = "promote_headers"
    names: NewColumns = Field(title="欄名", description="建立步驟時從第一列讀出，之後固定")

    label = "提升標題列"
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
    def suggest_names(cls, first_row: list[object]) -> list[str]:
        """建立步驟時從第一列產生欄名：空白補成「欄N」，重複的加上 _2、_3。"""
        names: list[str] = []
        for i, value in enumerate(first_row, start=1):
            base = str(value).strip() if value is not None else ""
            base = base or f"欄{i}"
            if base.startswith("__"):
                base = base.lstrip("_") or f"欄{i}"
            name, k = base, 2
            while name in names:
                name, k = f"{base}_{k}", k + 1
            names.append(name)
        return names

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        columns = data_columns(lf)
        if len(columns) != len(self.names):
            raise StepError(f"目前有 {len(columns)} 個資料欄，但 names 有 {len(self.names)} 個")
        flag = self.temp("mismatch")
        differs = pl.any_horizontal(
            [
                pl.col(c).first().over(SOURCE_FILE).cast(pl.String).str.strip_chars().fill_null("")
                != name
                for c, name in zip(columns, self.names, strict=True)
            ]
        )
        out = (
            lf.sort(ORDER)
            .with_columns(row_in_file().alias(_RN))
            .with_columns((differs & (pl.col(_RN) == 1)).alias(flag))
            .filter(pl.col(_RN) > 0)
            .drop(_RN)
            .rename(dict(zip(columns, self.names, strict=True)))
        )
        return out.select([*self.output_order(lf), flag])

    def warning_checks(self) -> list[WarningCheck]:
        return [
            WarningCheck(
                condition=pl.col(self.temp("mismatch")),
                message="這個檔案的標題列和 recipe 記錄的欄名不同，仍照 recipe 命名",
            )
        ]

    def describe(self) -> str:
        return f"用第一列當欄名（{quote_list(self.names)}）"
