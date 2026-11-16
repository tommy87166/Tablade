"""拆欄：把整行依分隔符號拆成多欄、把某一欄再拆。"""

from __future__ import annotations

import csv
from typing import Literal

import polars as pl
from pydantic import Field

from .base import (
    ColumnRef,
    Example,
    NewColumns,
    Step,
    StepError,
    WarningCheck,
    data_columns,
    quote_list,
)
from .registry import register

OnExtra = Literal["warn", "merge"]
ON_EXTRA_LABELS = {"warn": "產生警告並捨棄多的段", "merge": "多的段併入最後一欄"}


def _parse_line(line: str | None, delimiter: str, quote: str) -> list[str]:
    if line is None:
        return []
    if quote:
        reader = csv.reader([line], delimiter=delimiter, quotechar=quote)
    else:
        reader = csv.reader([line], delimiter=delimiter, quoting=csv.QUOTE_NONE)
    return next(reader, [])


@register
class SplitDelimited(Step):
    """把一欄（通常是 text_lines 讀進來的 line）依分隔符號拆成多欄，會處理引號。"""

    type: Literal["split_delimited"] = "split_delimited"
    column: ColumnRef = Field(default="line", title="要拆的欄位")
    delimiter: str = Field(default=",", title="分隔符號", min_length=1, max_length=1)
    quote: str = Field(
        default='"', title="引號字元", max_length=1, description="空字串代表不處理引號"
    )
    columns: NewColumns = Field(title="拆出來的欄名")
    on_extra: OnExtra = Field(
        default="warn", title="段數比欄名多時", json_schema_extra={"ui_labels": ON_EXTRA_LABELS}
    )
    keep_source: bool = Field(default=False, title="保留原欄位")

    label = "依分隔符號拆成多欄"
    category = "欄"
    effect = {
        "kind": "split",
        "source": "$column",
        "names": "$columns",
        "keep_source": "$keep_source",
    }
    ai_notes = (
        "text_lines 讀進來的 CSV 用這一步拆欄。columns 的數量要在建立步驟時決定並寫死；"
        "通常先用暫時的欄名（欄1、欄2…），再用 promote_headers 換成標題列。"
        '引號內的分隔符號不會被拆開，例如 "1,280"。'
    )
    examples = (
        Example(
            title="拆開一行 CSV（含引號）",
            params={"columns": ["欄1", "欄2", "欄3"]},
            input={"line": ['115/01/05,北區門市,"1,280"', "115/01/06,中區門市,25"]},
            output={
                "欄1": ["115/01/05", "115/01/06"],
                "欄2": ["北區門市", "中區門市"],
                "欄3": ["1,280", "25"],
            },
        ),
    )

    @classmethod
    def suggest_columns(
        cls, lines: list[str | None], delimiter: str = ",", quote: str = '"'
    ) -> list[str]:
        """建立步驟時掃描資料，依最多的段數產生暫時欄名。"""
        width = max((len(_parse_line(v, delimiter, quote)) for v in lines), default=1)
        return [f"欄{i}" for i in range(1, max(width, 1) + 1)]

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        names = list(self.columns)
        count_name = self.temp("fields")
        dtype = pl.Struct(
            [pl.Field(n, pl.String) for n in names] + [pl.Field(count_name, pl.Int64)]
        )
        delimiter, quote, merge = self.delimiter, self.quote, self.on_extra == "merge"

        def parse(series: pl.Series) -> pl.Series:
            data: dict[str, list] = {n: [] for n in names}
            counts = []
            for value in series.to_list():
                fields = _parse_line(value, delimiter, quote)
                counts.append(len(fields))
                if merge and len(fields) > len(names):
                    fields = fields[: len(names) - 1] + [delimiter.join(fields[len(names) - 1 :])]
                for i, n in enumerate(names):
                    data[n].append(fields[i] if i < len(fields) else None)
            schema = {**dict.fromkeys(names, pl.String), count_name: pl.Int64}
            frame = pl.DataFrame({**data, count_name: counts}, schema=schema)
            return frame.to_struct(series.name)

        struct_name = self.temp("struct")
        out = lf.with_columns(
            pl.col(self.column).map_batches(parse, return_dtype=dtype).alias(struct_name)
        )
        out = out.unnest(struct_name)
        return out.select([*self.output_order(lf), count_name])

    def warning_checks(self) -> list[WarningCheck]:
        if self.on_extra == "merge":
            return []
        return [
            WarningCheck(
                condition=pl.col(self.temp("fields")) > len(self.columns),
                message=f"這一列拆出 {{}} 段，超過 {len(self.columns)} 個欄位，多的段已捨棄",
                detail=pl.col(self.temp("fields")),
            )
        ]

    def describe(self) -> str:
        return f"把「{self.column}」依「{self.delimiter}」拆成 {len(self.columns)} 欄（{quote_list(self.columns)}）"


@register
class SplitColumn(Step):
    """把某一欄依分隔符號再拆成多欄。"""

    type: Literal["split_column"] = "split_column"
    column: ColumnRef = Field(title="要拆的欄位")
    delimiter: str = Field(title="分隔符號", min_length=1)
    names: NewColumns = Field(title="新欄位名稱")
    on_extra: OnExtra = Field(
        default="warn", title="段數比欄名多時", json_schema_extra={"ui_labels": ON_EXTRA_LABELS}
    )
    trim: bool = Field(default=True, title="去掉每段前後的空白")
    keep_source: bool = Field(default=False, title="保留原欄位")

    label = "拆欄"
    category = "欄"
    effect = {
        "kind": "split",
        "source": "$column",
        "names": "$names",
        "keep_source": "$keep_source",
    }
    ai_notes = "分隔符號是一般文字（不是正規表示式）。段數不足的列，多出來的新欄位是 null。"
    examples = (
        Example(
            title="把備註拆成三欄",
            params={"column": "備註", "delimiter": "|", "names": ["通路", "付款方式", "單號"]},
            input={
                "門市": ["北區門市", "中區門市"],
                "備註": ["網路|信用卡|單號 0001", "門市 | 現金"],
            },
            output={
                "門市": ["北區門市", "中區門市"],
                "通路": ["網路", "門市"],
                "付款方式": ["信用卡", "現金"],
                "單號": ["單號 0001", None],
            },
        ),
    )

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        if self.column not in data_columns(lf):
            raise StepError(f"欄位「{self.column}」不存在")
        n = len(self.names)
        source = pl.col(self.column).cast(pl.String)
        parts = (
            source.str.splitn(self.delimiter, n)
            if self.on_extra == "merge"
            else source.str.split_exact(self.delimiter, n - 1)
        )
        struct_name = self.temp("struct")
        count_name = self.temp("parts")
        out = lf.with_columns(
            parts.struct.rename_fields(list(self.names)).alias(struct_name),
            (source.str.count_matches(self.delimiter, literal=True) + 1).alias(count_name),
        ).unnest(struct_name)
        if self.trim:
            out = out.with_columns(pl.col(name).str.strip_chars() for name in self.names)
        return out.select([*self.output_order(lf), count_name])

    def warning_checks(self) -> list[WarningCheck]:
        if self.on_extra == "merge":
            return []
        return [
            WarningCheck(
                condition=pl.col(self.temp("parts")) > len(self.names),
                message=f"「{self.column}」拆出 {{}} 段，超過 {len(self.names)} 個新欄位，多的段已捨棄",
                detail=pl.col(self.temp("parts")),
            )
        ]

    def describe(self) -> str:
        return f"把「{self.column}」依「{self.delimiter}」拆成 {quote_list(self.names)}"
