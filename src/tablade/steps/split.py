"""拆欄：把整行依分隔符號拆成多欄、把某一欄再拆。"""

from __future__ import annotations

import csv
from typing import Literal

import polars as pl
from pydantic import Field, ValidationInfo, field_validator

from tablade.i18n import en, plural, t

from .base import (
    VALUE,
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

OnExtra = Literal["warn", "merge", "merge_into"]
ON_EXTRA_LABELS = {
    "warn": "產生警告並捨棄多的段",
    "merge": "多的段併入最後一欄",
    "merge_into": "多的段併回指定的欄",
}
ON_EXTRA_LABELS_EN = {
    "warn": "Warn and drop the extra parts",
    "merge": "Merge extra parts into the last column",
    "merge_into": "Merge extra parts into a chosen column",
}


MERGE_COLUMN_TITLE = "併回哪一欄"
MERGE_COLUMN_DESCRIPTION = (
    "段數比欄名多時選「多的段併回指定的欄」才用：多的段接回這一欄，"
    "它左邊的欄從左邊取、右邊的欄從右邊往回數"
)
"""欄位標題寫在模組層（畫面文字的檢查只放過類別、模組層的標題；英文在 ``en()`` 裡）。"""


def merge_column_field(names_field: str) -> object:
    """``on_extra`` 是 ``merge_into`` 時，多的段併回哪一欄（這一步自己的欄名之一，docs/split-merge-plan.md）。"""
    return Field(
        default="",
        validate_default=True,  # 選了 merge_into 卻沒填時也要檢查
        title=MERGE_COLUMN_TITLE,
        description=MERGE_COLUMN_DESCRIPTION,
        json_schema_extra={
            "ui": "own_name",
            "ui_from": names_field,
            "ui_show_if": {"on_extra": "merge_into"},
            **en(
                "Merge into column",
                description="Used only with “Merge extra parts into a chosen column”: the extra parts "
                "are joined back into this column; columns to its left are taken from the left, "
                "columns to its right counted back from the right",
            ),
        },
    )


def check_merge_column(value: str, on_extra: object, names: object) -> str:
    """``merge_column`` 的規則（``recipe/validator.py`` 的 ``step_rules`` 有同樣的檢查）。"""
    if on_extra == "merge_into":
        if not value:
            raise ValueError(
                t("要選多的段併回哪一欄", "Choose the column to merge the extra parts into")
            )
        if isinstance(names, list) and value not in names:
            raise ValueError(
                t(
                    f"「{value}」不是拆出來的欄名",
                    f'"{value}" is not one of the new column names',
                )
            )
    elif value:
        raise ValueError(
            t(
                "只有「段數比欄名多時」選「多的段併回指定的欄」才能填 merge_column",
                "merge_column can be set only when extra parts are merged into a chosen column",
            )
        )
    return value


def merged_fields(
    parts: pl.Expr, count: pl.Expr, names: list[str], into: int, delimiter: str
) -> list[pl.Expr]:
    """``merge_into``：每個新欄位的值。``parts`` 是拆好的清單，``count`` 是段數，``into`` 是併回的那一欄。

    段數比欄名多時：``into`` 左邊的欄照順序取，右邊的欄從右邊往回數（第 i 段往後移「多了幾段」），
    ``into`` 那一欄是中間那幾段用分隔符號接回去。段數剛好或比較少時和照順序取一樣（少的欄是空值）。
    """
    extra = count - len(names)
    shift = pl.when(extra > 0).then(extra).otherwise(0)
    out = []
    for i, name in enumerate(names):
        if i < into:
            value = parts.list.get(i, null_on_oob=True)
        elif i == into:
            value = (
                pl.when(extra > 0)
                .then(parts.list.slice(into, extra + 1).list.join(delimiter))
                .otherwise(parts.list.get(into, null_on_oob=True))
            )
        else:
            value = parts.list.get(pl.lit(i, pl.Int64) + shift, null_on_oob=True)
        out.append(value.alias(name))
    return out


def merged_warning(column: str, count: str, names: list[str], into: str) -> WarningCheck:
    """``merge_into``：被併過的列（多了幾段、併回哪一欄），讓使用者檢查有沒有併錯。"""
    return WarningCheck(
        condition=pl.col(count) > len(names),
        message=t(
            f"「{column}」拆出 {VALUE} 段，比 {len(names)} 個欄名多，多的段已併回「{into}」",
            f'"{column}" split into {VALUE} parts, more than '
            f"{plural(len(names), 'column name', 'column names')}; the extra parts were merged "
            f'into "{into}"',
        ),
        detail=pl.col(count),
    )


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
    merge_column: str = merge_column_field("columns")  # type: ignore[assignment]
    keep_source: bool = Field(
        default=False, title="保留原欄位", json_schema_extra=en("Keep original column")
    )

    @field_validator("merge_column")
    @classmethod
    def _merge_column(cls, value: str, info: ValidationInfo) -> str:
        return check_merge_column(value, info.data.get("on_extra"), info.data.get("columns"))

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
        "某個文字欄裡有沒加引號的分隔符號（那幾列段數變多、後面的欄位錯位），其他欄都很規矩時，"
        "on_extra 用 merge_into、merge_column 寫那個文字欄：多的段併回那一欄，它右邊的欄從右邊往回數。"
        "每一列被併過的會產生警告。好幾個欄裡都可能有分隔符號時不要用（沒有唯一的切法）。"
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
        Example(
            title="摘要裡有沒加引號的分隔符號：多的段併回摘要",
            params={
                "delimiter": "|",
                "columns": ["日期", "門市", "摘要", "金額"],
                "on_extra": "merge_into",
                "merge_column": "摘要",
            },
            input={"line": ["0105|北區門市|一般銷售|120", "0106|中區門市|退貨|重開|80"]},
            output={
                "日期": ["0105", "0106"],
                "門市": ["北區門市", "中區門市"],
                "摘要": ["一般銷售", "退貨|重開"],
                "金額": ["120", "80"],
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
        if self.on_extra == "merge_into":
            into = names.index(self.merge_column)
            exprs = merged_fields(parts, pl.col(count_name), names, into, self.delimiter)
        elif self.on_extra == "merge":
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
        if self.on_extra == "merge_into":
            return [
                merged_warning(
                    self.column, self.temp("fields"), list(self.columns), self.merge_column
                )
            ]
        return [
            WarningCheck(
                condition=pl.col(self.temp("fields")) > len(self.columns),
                message=t(
                    f"這一列拆出 {VALUE} 段，超過 {len(self.columns)} 個欄位，多的段已捨棄",
                    f"This row split into {VALUE} parts, more than "
                    f"{plural(len(self.columns), 'column', 'columns')}; the extra parts were dropped",
                ),
                detail=pl.col(self.temp("fields")),
            )
        ]

    def describe(self) -> str:
        text = t(
            f"把「{self.column}」依「{self.delimiter}」拆成 {len(self.columns)} 欄（{quote_list(self.columns)}）",
            f'Split "{self.column}" by "{self.delimiter}" into '
            f"{plural(len(self.columns), 'column', 'columns')} ({quote_list(self.columns)})",
        )
        return text + _merge_note(self.on_extra, self.merge_column)


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
    merge_column: str = merge_column_field("names")  # type: ignore[assignment]
    trim: bool = Field(
        default=True, title="去掉每段前後的空白", json_schema_extra=en("Trim spaces from each part")
    )
    keep_source: bool = Field(
        default=False, title="保留原欄位", json_schema_extra=en("Keep original column")
    )

    @field_validator("merge_column")
    @classmethod
    def _merge_column(cls, value: str, info: ValidationInfo) -> str:
        return check_merge_column(value, info.data.get("on_extra"), info.data.get("names"))

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
    ai_notes = (
        "分隔符號是一般文字（不是正規表示式）。段數不足的列，多出來的新欄位是 null。"
        "某一段的文字裡可能有分隔符號時，on_extra 用 merge_into、merge_column 寫那一欄（每一列被併過的會產生警告）。"
    )
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
        count_name = self.temp("parts")
        count = source.str.count_matches(self.delimiter, literal=True) + 1
        if self.on_extra == "merge_into":
            list_name = self.temp("list")
            out = lf.with_columns(
                source.str.split(self.delimiter).alias(list_name), count.alias(count_name)
            )
            into = list(self.names).index(self.merge_column)
            fields = [
                pl.when(source.is_null()).then(None).otherwise(f)
                for f in merged_fields(
                    pl.col(list_name), pl.col(count_name), list(self.names), into, self.delimiter
                )
            ]
            if self.trim:  # 和 merge 一樣：併回去的值整個去頭尾空白（裡面的空白留著）
                fields = [f.str.strip_chars() for f in fields]
            out = out.with_columns(
                f.alias(name) for f, name in zip(fields, self.names, strict=True)
            )
            return out.select([*self.output_order(lf), count_name])
        parts = (
            source.str.splitn(self.delimiter, n)
            if self.on_extra == "merge"
            else source.str.split_exact(self.delimiter, n - 1)
        )
        struct_name = self.temp("struct")
        out = lf.with_columns(parts.alias(struct_name), count.alias(count_name))
        # 一欄一個 struct.field()（不用 unnest）：新欄名和來源欄同名時直接取代來源欄
        fields = [pl.col(struct_name).struct.field(f"field_{i}") for i in range(n)]
        if self.trim:
            fields = [f.str.strip_chars() for f in fields]
        out = out.with_columns(f.alias(name) for f, name in zip(fields, self.names, strict=True))
        return out.select([*self.output_order(lf), count_name])

    def warning_checks(self) -> list[WarningCheck]:
        if self.on_extra == "merge":
            return []
        if self.on_extra == "merge_into":
            return [
                merged_warning(self.column, self.temp("parts"), list(self.names), self.merge_column)
            ]
        return [
            WarningCheck(
                condition=pl.col(self.temp("parts")) > len(self.names),
                message=t(
                    f"「{self.column}」拆出 {VALUE} 段，超過 {len(self.names)} 個新欄位，多的段已捨棄",
                    f'"{self.column}" split into {VALUE} parts, more than '
                    f"{plural(len(self.names), 'new column', 'new columns')}; "
                    "the extra parts were dropped",
                ),
                detail=pl.col(self.temp("parts")),
            )
        ]

    def describe(self) -> str:
        text = t(
            f"把「{self.column}」依「{self.delimiter}」拆成 {quote_list(self.names)}",
            f'Split "{self.column}" by "{self.delimiter}" into {quote_list(self.names)}',
        )
        return text + _merge_note(self.on_extra, self.merge_column)


def _merge_note(on_extra: str, column: str) -> str:
    if on_extra != "merge_into":
        return ""
    return t(f"；多的段併回「{column}」", f'; extra parts merged into "{column}"')
