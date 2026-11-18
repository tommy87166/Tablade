"""條件篩選。條件是結構化參數，不接受任意字串運算式（DESIGN.md §7.1）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tablade.i18n import en, t

from .base import ColumnRef, Example, Step, check_regex, is_blank, missing_columns, pick_column
from .registry import register

TEXT_OPS = {
    "equals": "等於",
    "not_equals": "不等於",
    "contains": "包含",
    "not_contains": "不包含",
    "starts_with": "開頭是",
    "ends_with": "結尾是",
}
TEXT_OPS_EN = {
    "equals": "equals",
    "not_equals": "does not equal",
    "contains": "contains",
    "not_contains": "does not contain",
    "starts_with": "begins with",
    "ends_with": "ends with",
}
REGEX_OPS = {"regex": "符合正規表示式", "not_regex": "不符合正規表示式"}
REGEX_OPS_EN = {"regex": "matches regex", "not_regex": "does not match regex"}
EMPTY_OPS = {"is_empty": "是空白", "is_not_empty": "不是空白"}
EMPTY_OPS_EN = {"is_empty": "is blank", "is_not_empty": "is not blank"}
MATCH_LABELS_EN = {"all": "All conditions", "any": "Any condition"}
KEEP_LABELS_EN = {"matching": "Matching rows", "non_matching": "Non-matching rows"}
NUMBER_OPS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤", "num_equals": "="}
CONDITIONS_HELP = (
    "空值當成空白的文字：「不等於」「不包含」「不符合」會留下空值的列；"
    "數值條件裡轉不成數字的值（含空值）都不符合"
)
CONDITIONS_HELP_EN = (
    "Nulls count as empty text: “does not equal”, “does not contain” and “does not match” "
    "keep rows with nulls; values that aren't numbers (including nulls) never match a number "
    "condition"
)
"""條件清單的說明（篩選、將列進行分類、依條件複製欄位共用）。"""


def text_of(column: str) -> pl.Expr:
    """文字與正規表示式條件比對的值：空值當成空字串。

    否則否定的條件（不等於、不包含、不符合）遇到空值算出來是空值、被當成不符合，
    「保留：備註 不包含『退貨』」會默默拿掉備註是空值的列；csv 的空白格（空值）與
    逐行讀取拆出來的空白（空字串）結果也會不一樣。
    """
    return pl.col(column).cast(pl.String).fill_null("")


def number_text(value: float) -> str:
    """數值條件的門檻：照寫的值顯示（不用科學記號、不截掉位數），加千分位，例如 1,000,000、0.000001。"""
    return f"{Decimal(repr(value)).normalize():,f}"


def check_columns(lf: pl.LazyFrame, conditions: list) -> None:
    """條件引用的欄都要存在（否則是 Polars 的英文原文與一大段執行計畫）。"""
    names = lf.collect_schema().names()
    missing = [c for c in dict.fromkeys(x.column for x in conditions) if c not in names]
    if missing:
        raise missing_columns(missing)


class _Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    column: ColumnRef = Field(title="欄位", json_schema_extra=en("Column"))


class TextCondition(_Condition):
    model_config = ConfigDict(title="文字", json_schema_extra=en("Text"))

    op: Literal["equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with"] = (
        Field(
            title="條件",
            json_schema_extra={"ui_labels": TEXT_OPS, **en("Operator", ui_labels=TEXT_OPS_EN)},
        )
    )
    value: str = Field(title="值", json_schema_extra=en("Value"))
    ignore_case: bool = Field(
        default=False, title="不分大小寫", json_schema_extra=en("Ignore case")
    )

    def expr(self) -> pl.Expr:
        col, value = text_of(self.column), self.value
        if self.ignore_case:
            col, value = col.str.to_lowercase(), value.lower()
        base = {
            "equals": col == value,
            "not_equals": col != value,
            "contains": col.str.contains(value, literal=True),
            "not_contains": ~col.str.contains(value, literal=True),
            "starts_with": col.str.starts_with(value),
            "ends_with": col.str.ends_with(value),
        }[self.op]
        return base

    def describe(self) -> str:
        return t(
            f"{self.column} {TEXT_OPS[self.op]}「{self.value}」",
            f'{self.column} {TEXT_OPS_EN[self.op]} "{self.value}"',
        )


class RegexCondition(_Condition):
    model_config = ConfigDict(title="正規表示式", json_schema_extra=en("Regular expression"))

    op: Literal["regex", "not_regex"] = Field(
        title="條件",
        json_schema_extra={"ui_labels": REGEX_OPS, **en("Operator", ui_labels=REGEX_OPS_EN)},
    )
    pattern: str = Field(
        title="正規表示式",
        min_length=1,
        json_schema_extra={"ui": "regex", **en("Regular expression")},
    )

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, value: str) -> str:
        check_regex(value)
        return value

    def expr(self) -> pl.Expr:
        matched = text_of(self.column).str.contains(self.pattern)
        return matched if self.op == "regex" else ~matched

    def describe(self) -> str:
        return t(
            f"{self.column} {REGEX_OPS[self.op]} /{self.pattern}/",
            f"{self.column} {REGEX_OPS_EN[self.op]} /{self.pattern}/",
        )


class EmptyCondition(_Condition):
    model_config = ConfigDict(title="空白", json_schema_extra=en("Blank"))

    op: Literal["is_empty", "is_not_empty"] = Field(
        title="條件",
        json_schema_extra={"ui_labels": EMPTY_OPS, **en("Operator", ui_labels=EMPTY_OPS_EN)},
    )

    def expr(self) -> pl.Expr:
        blank = is_blank(pl.col(self.column))
        return blank if self.op == "is_empty" else ~blank

    def describe(self) -> str:
        return t(f"{self.column} {EMPTY_OPS[self.op]}", f"{self.column} {EMPTY_OPS_EN[self.op]}")


class NumberCondition(_Condition):
    model_config = ConfigDict(title="數值", json_schema_extra=en("Number"))

    op: Literal["gt", "ge", "lt", "le", "num_equals"] = Field(
        title="條件", json_schema_extra={"ui_labels": NUMBER_OPS, **en("Operator")}
    )
    value: float = Field(title="數值", json_schema_extra=en("Number"))

    def expr(self) -> pl.Expr:
        number = (
            pl.col(self.column)
            .cast(pl.String)
            .str.replace_all(",", "", literal=True)
            .str.strip_chars()
            .cast(pl.Float64, strict=False)
        )
        number = pl.when(number.is_finite()).then(number)  # 「inf」「NaN」這類字不算數字
        return {
            "gt": number > self.value,
            "ge": number >= self.value,
            "lt": number < self.value,
            "le": number <= self.value,
            "num_equals": number == self.value,
        }[self.op]

    def describe(self) -> str:
        return f"{self.column} {NUMBER_OPS[self.op]} {number_text(self.value)}"


Condition = Annotated[
    TextCondition | RegexCondition | EmptyCondition | NumberCondition, Field(discriminator="op")
]


@register
class Filter(Step):
    """依條件保留（或排除）列。"""

    type: Literal["filter"] = "filter"
    conditions: list[Condition] = Field(
        title="條件",
        min_length=1,
        description=CONDITIONS_HELP,
        json_schema_extra=en("Conditions", description=CONDITIONS_HELP_EN),
    )
    match: Literal["all", "any"] = Field(
        default="all",
        title="條件組合",
        json_schema_extra={
            "ui_labels": {"all": "全部符合", "any": "任一符合"},
            **en("Match", ui_labels=MATCH_LABELS_EN),
        },
    )
    keep: Literal["matching", "non_matching"] = Field(
        default="matching",
        title="保留",
        json_schema_extra={
            "ui_labels": {"matching": "符合條件的列", "non_matching": "不符合條件的列"},
            **en("Keep", ui_labels=KEEP_LABELS_EN),
        },
    )

    label = "篩選"
    label_en = "Filter rows"
    doc_en = "Keep (or exclude) rows that match conditions."
    category = "列"
    ai_notes = (
        "條件的 op 決定需要哪些欄位：文字條件用 value（字串），正規表示式用 pattern，"
        "數值條件用 value（數字，比較前會去掉千分位逗號，轉不成數字的列視為不符合），空白條件不需要值。"
        "文字與正規表示式條件把空值當成空字串：not_equals、not_contains、not_regex 會留下空值的列"
        "（和 keep=non_matching 搭配肯定的條件結果一樣）。"
        "正規表示式是 Rust regex 語法，不支援 lookaround 與反向參照。"
    )
    examples = (
        Example(
            title="排除退貨（金額有括號的列）",
            params={
                "conditions": [{"column": "金額", "op": "starts_with", "value": "("}],
                "keep": "non_matching",
            },
            input={"品名": ["原子筆", "折疊傘", "滑鼠"], "金額": ["125", "(320)", "1,170"]},
            output={"品名": ["原子筆", "滑鼠"], "金額": ["125", "1,170"]},
        ),
        Example(
            title="金額大於 500 的北區門市",
            params={
                "conditions": [
                    {"column": "門市", "op": "equals", "value": "北區門市"},
                    {"column": "金額", "op": "gt", "value": 500},
                ]
            },
            input={"門市": ["北區門市", "北區門市", "南區門市"], "金額": ["125", "1,280", "1,680"]},
            output={"門市": ["北區門市"], "金額": ["1,280"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        if column is None:
            return {}
        value = context.get("value")
        keep = {"keep": "non_matching"} if context.get("exclude") else {}
        if value is None or value == "":
            return {"conditions": [{"column": column, "op": "is_not_empty"}], **keep}
        return {"conditions": [{"column": column, "op": "equals", "value": str(value)}], **keep}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        check_columns(lf, self.conditions)
        exprs = [c.expr().fill_null(False) for c in self.conditions]
        combined = pl.all_horizontal(exprs) if self.match == "all" else pl.any_horizontal(exprs)
        return lf.filter(combined if self.keep == "matching" else ~combined)

    def describe(self) -> str:
        joiner = t(" 且 ", " and ") if self.match == "all" else t(" 或 ", " or ")
        text = joiner.join(c.describe() for c in self.conditions)
        if self.keep == "matching":
            return t(f"保留：{text}", f"Keep: {text}")
        return t(f"排除：{text}", f"Exclude: {text}")
