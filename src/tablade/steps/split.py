"""拆欄：把整行依分隔符號拆成多欄、把某一欄再拆。"""

from __future__ import annotations

import csv
from typing import Literal

import polars as pl
from pydantic import Field

from tablade.i18n import en, plural, t

from .base import (
    ColumnRef,
    Example,
    NewColumns,
    Step,
    StepError,
    WarningCheck,
    data_columns,
    guess_delimiter,
    pick_column,
    quote_list,
    sample_values,
)
from .registry import register

OnExtra = Literal["warn", "merge"]
ON_EXTRA_LABELS = {"warn": "產生警告並捨棄多的段", "merge": "多的段併入最後一欄"}
ON_EXTRA_LABELS_EN = {
    "warn": "Warn and drop the extra parts",
    "merge": "Merge extra parts into the last column",
}


_REGEX_META = set("\\.^$|?*+()[]{}-/#&~")


def _escape(ch: str) -> str:
    if ch == "\t":
        return "\\t"
    return "\\" + ch if ch in _REGEX_META else ch


def field_pattern(delimiter: str, quote: str) -> str:
    """分隔符號＋一個 CSV 欄位：引號欄位（引號後的多餘字元也算進來）或一般欄位。

    每一行前面會先補一個分隔符號，所以每個欄位都以分隔符號開頭，不會有長度為 0 的比對
    （行首的空欄位如果靠 ``^`` 比對成空字串，下一個比對會跳過第一個分隔符號，少一欄）。
    """
    d, q = _escape(delimiter), _escape(quote)
    return f"{d}({q}(?:[^{q}]|{q}{q})*{q}[^{d}]*|[^{d}]*)"


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
    """把逐行讀取（爛格式檔案）依分隔符號拆成多欄，會處理引號。"""

    type: Literal["split_delimited"] = "split_delimited"
    column: ColumnRef = Field(
        default="line", title="要拆的欄位", json_schema_extra=en("Column to split")
    )
    delimiter: str = Field(
        default=",", title="分隔符號", min_length=1, max_length=1, json_schema_extra=en("Delimiter")
    )
    quote: str = Field(
        default='"',
        title="引號字元",
        max_length=1,
        description="空字串代表不處理引號",
        json_schema_extra=en("Quote character", description="Leave empty to ignore quotes"),
    )
    columns: NewColumns = Field(title="拆出來的欄名", json_schema_extra=en("New column names"))
    on_extra: OnExtra = Field(
        default="warn",
        title="段數比欄名多時",
        json_schema_extra={
            "ui_labels": ON_EXTRA_LABELS,
            **en("When there are more parts than names", ui_labels=ON_EXTRA_LABELS_EN),
        },
    )
    keep_source: bool = Field(
        default=False, title="保留原欄位", json_schema_extra=en("Keep original column")
    )

    label = "依分隔符號拆成多欄"
    label_en = "Split into columns by delimiter"
    doc_en = (
        "Split lines read line by line (messy files) into columns by a delimiter; handles quotes."
    )
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
    def suggest(cls, frame, context):
        column = pick_column(frame, context, prefer="line")
        if column is None:
            return {}
        values = sample_values(frame, column)
        delimiter = guess_delimiter(values)
        return {
            "column": column,
            "delimiter": delimiter,
            "columns": cls.suggest_columns(values, delimiter),
        }

    @classmethod
    def suggest_columns(
        cls, lines: list[str | None], delimiter: str = ",", quote: str = '"'
    ) -> list[str]:
        """建立步驟時掃描資料，依最多的段數產生暫時欄名。"""
        width = max((len(_parse_line(v, delimiter, quote)) for v in lines), default=1)
        return [f"欄{i}" for i in range(1, max(width, 1) + 1)]  # i18n-ignore：暫時的欄名是資料

    def fields(self) -> pl.Expr:
        """每一行拆成欄位的 list（Polars 原生正規表示式，可以串流處理幾 GB 的檔案）。

        引號內的分隔符號不拆，兩個連續引號代表一個引號（和 CSV 相同）；空行沒有欄位。
        引號不成對時，引號會保留在值裡（Python 的 csv 會把後面整行併成一欄）。
        """
        col = pl.col(self.column).cast(pl.String)
        if self.quote:
            q, item = self.quote, pl.element()
            prefixed = pl.concat_str([pl.lit(self.delimiter), col])
            parts = prefixed.str.extract_all(field_pattern(self.delimiter, q)).list.eval(
                item.str.slice(1)
            )
            quoted = item.str.starts_with(q) & item.str.ends_with(q) & (item.str.len_chars() >= 2)
            inner = item.str.slice(1, item.str.len_chars() - 2).str.replace_all(
                q + q, q, literal=True
            )
            parts = parts.list.eval(pl.when(quoted).then(inner).otherwise(item))
        else:
            parts = col.str.split(self.delimiter)
        return pl.when(col == "").then(None).otherwise(parts)

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        names = list(self.columns)
        n = len(names)
        order = self.output_order(lf)
        parts_name, count_name = self.temp("parts"), self.temp("fields")
        out = lf.with_columns(self.fields().alias(parts_name)).with_columns(
            pl.col(parts_name).list.len().fill_null(0).cast(pl.Int64).alias(count_name)
        )
        parts = pl.col(parts_name)
        exprs = [parts.list.get(i, null_on_oob=True).alias(name) for i, name in enumerate(names)]
        if self.on_extra == "merge":
            rest = parts.list.slice(n - 1).list.join(self.delimiter)
            exprs[-1] = (
                pl.when(pl.col(count_name) > n)
                .then(rest)
                .otherwise(parts.list.get(n - 1, null_on_oob=True))
                .alias(names[-1])
            )
        return out.with_columns(exprs).select([*order, count_name])

    def warning_checks(self) -> list[WarningCheck]:
        if self.on_extra == "merge":
            return []
        return [
            WarningCheck(
                condition=pl.col(self.temp("fields")) > len(self.columns),
                message=t(
                    f"這一列拆出 {{}} 段，超過 {len(self.columns)} 個欄位，多的段已捨棄",
                    f"This row split into {{}} parts, more than "
                    f"{plural(len(self.columns), 'column', 'columns')}; the extra parts were dropped",
                ),
                detail=pl.col(self.temp("fields")),
            )
        ]

    def describe(self) -> str:
        return t(
            f"把「{self.column}」依「{self.delimiter}」拆成 {len(self.columns)} 欄（{quote_list(self.columns)}）",
            f'Split "{self.column}" by "{self.delimiter}" into '
            f"{plural(len(self.columns), 'column', 'columns')} ({quote_list(self.columns)})",
        )


@register
class SplitColumn(Step):
    """把某一欄依分隔符號再拆成多欄。"""

    type: Literal["split_column"] = "split_column"
    column: ColumnRef = Field(title="要拆的欄位", json_schema_extra=en("Column to split"))
    delimiter: str = Field(title="分隔符號", min_length=1, json_schema_extra=en("Delimiter"))
    names: NewColumns = Field(title="拆出來的欄名", json_schema_extra=en("New column names"))
    on_extra: OnExtra = Field(
        default="warn",
        title="段數比欄名多時",
        json_schema_extra={
            "ui_labels": ON_EXTRA_LABELS,
            **en("When there are more parts than names", ui_labels=ON_EXTRA_LABELS_EN),
        },
    )
    trim: bool = Field(
        default=True, title="去掉每段前後的空白", json_schema_extra=en("Trim spaces from each part")
    )
    keep_source: bool = Field(
        default=False, title="保留原欄位", json_schema_extra=en("Keep original column")
    )

    label = "拆欄"
    label_en = "Split column"
    doc_en = "Split a column into more columns by a delimiter."
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

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        if column is None:
            return {}
        values = [v for v in sample_values(frame, column) if v]
        delimiter = guess_delimiter(values, "|/;,-_ ", quote=None)
        parts = max((v.count(delimiter) + 1 for v in values), default=2)
        names = [f"{column}_{i}" for i in range(1, max(parts, 2) + 1)]
        return {"column": column, "delimiter": delimiter, "names": names}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        if self.column not in data_columns(lf):
            raise StepError(
                t(f"欄位「{self.column}」不存在", f'Column "{self.column}" does not exist')
            )
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
                message=t(
                    f"「{self.column}」拆出 {{}} 段，超過 {len(self.names)} 個新欄位，多的段已捨棄",
                    f'"{self.column}" split into {{}} parts, more than '
                    f"{plural(len(self.names), 'new column', 'new columns')}; "
                    "the extra parts were dropped",
                ),
                detail=pl.col(self.temp("parts")),
            )
        ]

    def describe(self) -> str:
        return t(
            f"把「{self.column}」依「{self.delimiter}」拆成 {quote_list(self.names)}",
            f'Split "{self.column}" by "{self.delimiter}" into {quote_list(self.names)}',
        )
