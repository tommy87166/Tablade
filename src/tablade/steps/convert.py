"""轉型別：文字轉數字、文字轉日期（DESIGN.md §7.1）。

讀檔時每一欄都是文字（不讓 Polars 猜型別），要數字或日期時用這兩步明確轉換。
原欄位直接換成新的型別；原本有值、轉不了的列變成空值並產生警告（附來源檔與行號），
所以壞資料不會悄悄消失。例外：串流匯出時，被後面的步驟拿掉的列（例如之後篩掉、移除重複）不警告，
警告只算最後還留著的列（DESIGN.md §8）。全部用 Polars 原生運算，可以串流處理大檔。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

import polars as pl
from pydantic import Field, ValidationInfo, field_validator

from tablade.i18n import en, plural, t

from .base import (
    VALUE,
    ColumnRefs,
    Example,
    Step,
    StepError,
    WarningCheck,
    data_columns,
    pick_column,
    quote_list,
)
from .registry import register

NumberKind = Literal["decimal", "integer"]
NUMBER_KIND_LABELS = {"decimal": "小數（精確，不會有浮點誤差）", "integer": "整數"}
NUMBER_KIND_LABELS_EN = {
    "decimal": "Decimal (exact, no floating-point error)",
    "integer": "Integer",
}
Calendar = Literal["roc", "ad"]
CALENDAR_LABELS = {"roc": "民國年（加 1911）", "ad": "西元年"}
CALENDAR_LABELS_EN = {"roc": "ROC year (adds 1911)", "ad": "Gregorian year"}

DatePattern = Literal["slash", "dash", "dot", "chinese", "compact", "custom"]
DATE_PATTERNS: dict[str, str] = {
    "slash": r"^(\d{1,4})/(\d{1,2})/(\d{1,2})$",
    "dash": r"^(\d{1,4})-(\d{1,2})-(\d{1,2})$",
    "dot": r"^(\d{1,4})\.(\d{1,2})\.(\d{1,2})$",
    "chinese": r"^(\d{1,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日$",
    "compact": r"^(\d{1,4})(\d{2})(\d{2})$",
}
"""每個樣式是一個獨立的正規表示式，三個群組依序是年、月、日。"""
DATE_PATTERN_LABELS = {
    "slash": "年/月/日（115/01/05、2026/1/5）",
    "dash": "年-月-日（115-01-05、2026-1-5）",
    "dot": "年.月.日（115.01.05、2026.1.5）",
    "chinese": "年月日（115年1月5日）",
    "compact": "連寫，月日各 2 碼（1150105、20260105）",
    "custom": "自訂正規表示式",
}
DATE_PATTERN_LABELS_EN = {
    "slash": "Y/M/D (115/01/05, 2026/1/5)",
    "dash": "Y-M-D (115-01-05, 2026-1-5)",
    "dot": "Y.M.D (115.01.05, 2026.1.5)",
    "chinese": "Chinese date (year, month, day characters)",
    "compact": "No separators, 2-digit month and day (1150105, 20260105)",
    "custom": "Custom regular expression",
}
DATE_PATTERN_HINTS = {key: f"正規表示式：{regex}" for key, regex in DATE_PATTERNS.items()} | {
    "custom": "在下面輸入正規表示式，要有三個群組，依序是年、月、日"
}
DATE_PATTERN_HINTS_EN = {
    key: f"Regular expression: {regex}" for key, regex in DATE_PATTERNS.items()
} | {"custom": "Enter a regular expression below with three groups: year, month, day"}
YEAR_DIGITS = {"roc": (1, 3), "ad": (4, 4)}
"""民國年 1～3 位數、西元年 4 位數；不符合的算轉換失敗，不會變成錯的日期（例如西元 115 年）。"""


def date_groups(regex: str) -> int:
    """正規表示式有幾個群組（用 Polars 的引擎算，和執行時一樣）；語法錯誤丟出 ValueError。"""
    try:
        groups = pl.Series([""], dtype=pl.String).str.extract_groups(regex)
    except pl.exceptions.PolarsError as err:
        raise ValueError(
            t(f"正規表示式有誤：{err}", f"Invalid regular expression: {err}")
        ) from None
    return len(groups.struct.fields)


PRECISION = 38
"""Decimal 的總位數（Polars 的上限）。"""
PARSE_SCALE = 18
"""先用這麼多位小數讀進來，再四捨五入到要的位數；整數部分最多 20 位。"""


def _text(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strip_chars()


class _Convert(Step):
    """兩種轉型步驟共用：逐欄轉換，記下轉不了的原始值。"""

    def noun(self) -> str:
        """警告訊息裡「不是＿＿」的那個詞。"""
        raise NotImplementedError

    def convert(self, text: pl.Expr) -> pl.Expr:
        """已去掉前後空白的文字 → 新型別；轉不了是 null。"""
        raise NotImplementedError

    def targets(self, lf: pl.LazyFrame) -> list[str]:
        columns = list(dict.fromkeys(getattr(self, "columns", [])))
        missing = [c for c in columns if c not in data_columns(lf)]
        if missing:
            raise StepError(
                t(
                    f"欄位不存在：{'、'.join(missing)}",
                    f"{'Column' if len(missing) == 1 else 'Columns'} not found: {', '.join(missing)}",
                )
            )
        return columns

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        columns = self.targets(lf)
        exprs, temps = [], []
        for i, column in enumerate(columns):
            text = _text(column)
            result = self.convert(text)
            temp = self.temp(f"bad{i}")
            temps.append(temp)
            # 同一個 with_columns 裡的運算都看原本的值：先記下失敗的原始值，再換成新型別
            exprs.append(
                pl.when(text.is_not_null() & (text != "") & result.is_null())
                .then(pl.col(column).cast(pl.String))
                .alias(temp)
            )
            exprs.append(result.alias(column))
        return lf.with_columns(exprs).select([*self.output_order(lf), *temps])

    def warning_checks(self) -> list[WarningCheck]:
        return [
            WarningCheck(
                condition=pl.col(self.temp(f"bad{i}")).is_not_null(),
                message=t(
                    f"「{column}」的「{VALUE}」不是{self.noun()}，已改成空值",
                    f'"{VALUE}" in "{column}" is not a valid {self.noun()}; replaced with null',
                ),
                detail=pl.col(self.temp(f"bad{i}")),
            )
            for i, column in enumerate(dict.fromkeys(getattr(self, "columns", [])))
        ]

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        return {"columns": [column]} if column else {}


@register
class ParseNumber(_Convert):
    """文字轉數字：去掉千分位，括號 (1,000) 與尾端負號 1,000- 當成負數。"""

    type: Literal["parse_number"] = "parse_number"
    columns: ColumnRefs = Field(
        title="要轉成數字的欄位", json_schema_extra=en("Columns to convert")
    )
    kind: NumberKind = Field(
        default="decimal",
        title="轉成",
        json_schema_extra={
            "ui_labels": NUMBER_KIND_LABELS,
            **en("Convert to", ui_labels=NUMBER_KIND_LABELS_EN),
        },
    )
    scale: int = Field(
        default=2,
        ge=0,
        le=9,
        title="小數位數",
        description="超過的位數四捨五入",
        json_schema_extra={
            "ui_show_if": {"kind": "decimal"},
            **en("Decimal places", description="Extra digits are rounded"),
        },
    )

    label = "文字轉數字"
    label_en = "Text to number"
    doc_en = (
        "Convert text to numbers: remove thousands separators; "
        "(1,000) and a trailing minus (1,000-) are read as negative."
    )
    category = "欄"
    ai_notes = (
        "原欄位直接換成數字（不新增欄位）。kind=decimal 是精確小數（Decimal，scale 位小數，"
        "超過的位數四捨五入）；kind=integer 是整數，有小數的值（例如 12.5）算轉換失敗。"
        "會先去掉前後空白、千分位逗號與中間的空白；(1,000) 與 1,000- 當成 -1000。"
        "空白的儲存格變成空值；有值但轉不了（例如 abc、$100）也變成空值，並產生附行號的警告。"
    )
    examples = (
        Example(
            title="千分位、括號負數、尾端負號",
            params={"columns": ["金額"]},
            input={"金額": ["1,234", "(1,000)", "1,000-", "12.345", "abc", ""]},
            output={
                "金額": [
                    Decimal("1234.00"),
                    Decimal("-1000.00"),
                    Decimal("-1000.00"),
                    Decimal("12.35"),
                    None,
                    None,
                ]
            },
        ),
    )

    def noun(self) -> str:
        return t("整數", "integer") if self.kind == "integer" else t("數字", "number")

    def convert(self, text: pl.Expr) -> pl.Expr:
        s = text.str.replace_all(r"[,\s]", "")
        s = (
            pl.when(s.str.contains(r"^\(.+\)$"))
            .then(pl.concat_str([pl.lit("-"), s.str.slice(1, s.str.len_chars() - 2)]))
            .when(s.str.contains(r"^[^-].*-$"))
            .then(pl.concat_str([pl.lit("-"), s.str.strip_suffix("-")]))
            .otherwise(s)
        )
        value = s.cast(pl.Decimal(PRECISION, PARSE_SCALE), strict=False)
        if self.kind == "integer":
            return pl.when(value == value.round(0)).then(value.cast(pl.Int64, strict=False))
        rounded = value.round(self.scale, mode="half_away_from_zero")
        return rounded.cast(pl.Decimal(PRECISION, self.scale), strict=False)

    def describe(self) -> str:
        if self.kind == "integer":
            to = t("整數", "integer")
        else:
            places = plural(self.scale, "decimal place", "decimal places")
            to = t(f"數字（{self.scale} 位小數）", f"number ({places})")
        columns = quote_list(self.columns)
        return t(f"轉成{to}：{columns}", f"Convert to {to}: {columns}")


@register
class ParseDate(_Convert):
    """文字轉日期：先選樣式（每個樣式是一個正規表示式，也可以自訂），再選民國年或西元年。"""

    type: Literal["parse_date"] = "parse_date"
    columns: ColumnRefs = Field(
        title="要轉成日期的欄位", json_schema_extra=en("Columns to convert")
    )
    pattern: DatePattern = Field(
        title="樣式",
        json_schema_extra={
            "ui_labels": DATE_PATTERN_LABELS,
            "ui_option_hints": DATE_PATTERN_HINTS,
            **en("Format", ui_labels=DATE_PATTERN_LABELS_EN, ui_option_hints=DATE_PATTERN_HINTS_EN),
        },
    )
    regex: str = Field(
        default="",
        validate_default=True,
        title="正規表示式",
        description="三個群組依序是年、月、日，例如 ^(\\d{3})年(\\d{2})月(\\d{2})日$",
        json_schema_extra={
            "ui": "regex",
            "ui_show_if": {"pattern": "custom"},
            **en(
                "Regular expression",
                description="Three groups in order: year, month, day, e.g. ^(\\d{4})_(\\d{2})_(\\d{2})$",
            ),
        },
    )
    calendar: Calendar = Field(
        title="年份",
        json_schema_extra={
            "ui_labels": CALENDAR_LABELS,
            **en("Year", ui_labels=CALENDAR_LABELS_EN),
        },
    )

    label = "文字轉日期"
    label_en = "Text to date"
    doc_en = (
        "Convert text to dates: pick a format (each one is a regular expression; "
        "you can also write your own), then choose ROC or Gregorian years."
    )
    category = "欄"
    ai_notes = (
        "原欄位直接換成日期（Date，不含時間）。pattern 一定要指定，不會自動判斷："
        + "；".join(f"{k} = {DATE_PATTERNS[k]}" for k in DATE_PATTERNS)
        + "；custom 時在 regex 寫正規表示式（Rust regex 語法），要剛好三個群組、依序是年、月、日，"
        "建議用 ^…$ 包住整個值。calendar 一定要指定：roc 是民國年（加 1911，年要 1～3 位數），"
        "ad 是西元年（年要 4 位數）。值會先去掉前後空白再比對。不符合樣式、年份位數不對、"
        "不存在的日期（例如 115/13/40、西元 0000 年）都會變成空值，並產生附行號的警告。"
    )
    examples = (
        Example(
            title="民國年 年/月/日",
            params={"columns": ["日期"], "pattern": "slash", "calendar": "roc"},
            input={"日期": ["115/01/05", "115/1/6", "2026/01/07", "115/13/40", ""]},
            output={"日期": [date(2026, 1, 5), date(2026, 1, 6), None, None, None]},
        ),
        Example(
            title="自訂正規表示式",
            params={
                "columns": ["日期"],
                "pattern": "custom",
                "regex": r"^民國(\d{1,3})年(\d{1,2})月(\d{1,2})日$",
                "calendar": "roc",
            },
            input={"日期": ["民國115年1月5日", "115年1月6日"]},
            output={"日期": [date(2026, 1, 5), None]},
        ),
    )

    @field_validator("regex")
    @classmethod
    def _custom_regex(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("pattern") != "custom":
            return value
        if not value.strip():
            raise ValueError(
                t(
                    "選「自訂正規表示式」時要輸入正規表示式",
                    'Enter a regular expression when "Custom regular expression" is selected',
                )
            )
        groups = date_groups(value)
        if groups != 3:
            raise ValueError(
                t(
                    f"要剛好三個群組（年、月、日），這個正規表示式有 {groups} 個",
                    f"Needs exactly three groups (year, month, day); this regular expression has {groups}",
                )
            )
        return value

    def noun(self) -> str:
        return t("日期", "date")

    def active_regex(self) -> str:
        return self.regex if self.pattern == "custom" else DATE_PATTERNS[self.pattern]

    def convert(self, text: pl.Expr) -> pl.Expr:
        regex = self.active_regex()
        y, m, d = (text.str.extract(regex, group) for group in (1, 2, 3))
        low, high = YEAR_DIGITS[self.calendar]
        digits, year = y.str.len_chars(), y.cast(pl.Int32, strict=False)
        if self.calendar == "roc":
            year = year + 1911
        # 西元 0 年不存在（Python 的日期沒有 0 年：預覽表格取這一列會失敗），當成轉換失敗
        year = pl.when((digits >= low) & (digits <= high) & (year >= 1)).then(year)
        # 不能用 pl.date()：遇到不存在的日期（13 月、2 月 30 日）會讓整個執行失敗
        iso = pl.concat_str([year.cast(pl.String), m, d], separator="-")
        return iso.str.to_date("%Y-%m-%d", strict=False)

    def describe(self) -> str:
        if self.pattern == "custom":
            shape = f"/{self.regex}/"
        else:
            shape = t(
                DATE_PATTERN_LABELS[self.pattern].split("（")[0],
                DATE_PATTERN_LABELS_EN[self.pattern].split(" (")[0],
            )
        year = t("民國年", "ROC year") if self.calendar == "roc" else t("西元年", "Gregorian year")
        columns = quote_list(self.columns)
        return t(
            f"轉成日期（{shape}，{year}）：{columns}",
            f"Convert to date ({shape}, {year}): {columns}",
        )
