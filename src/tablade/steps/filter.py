"""條件篩選。條件是結構化參數，不接受任意字串運算式（DESIGN.md §7.1）。"""

from __future__ import annotations

from typing import Annotated, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from .base import ColumnRef, Example, Step, is_blank, pick_column
from .registry import register

TEXT_OPS = {
    "equals": "等於",
    "not_equals": "不等於",
    "contains": "包含",
    "not_contains": "不包含",
    "starts_with": "開頭是",
    "ends_with": "結尾是",
}
REGEX_OPS = {"regex": "符合正規表示式", "not_regex": "不符合正規表示式"}
EMPTY_OPS = {"is_empty": "是空白", "is_not_empty": "不是空白"}
NUMBER_OPS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤", "num_equals": "="}


class _Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    column: ColumnRef = Field(title="欄位")


class TextCondition(_Condition):
    model_config = ConfigDict(title="文字")

    op: Literal["equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with"] = (
        Field(title="條件", json_schema_extra={"ui_labels": TEXT_OPS})
    )
    value: str = Field(title="值")
    ignore_case: bool = Field(default=False, title="不分大小寫")

    def expr(self) -> pl.Expr:
        col, value = pl.col(self.column).cast(pl.String), self.value
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
        return f"{self.column} {TEXT_OPS[self.op]}「{self.value}」"


class RegexCondition(_Condition):
    model_config = ConfigDict(title="正規表示式")

    op: Literal["regex", "not_regex"] = Field(
        title="條件", json_schema_extra={"ui_labels": REGEX_OPS}
    )
    pattern: str = Field(title="正規表示式", min_length=1, json_schema_extra={"ui": "regex"})

    def expr(self) -> pl.Expr:
        matched = pl.col(self.column).cast(pl.String).str.contains(self.pattern)
        return matched if self.op == "regex" else ~matched

    def describe(self) -> str:
        return f"{self.column} {REGEX_OPS[self.op]} /{self.pattern}/"


class EmptyCondition(_Condition):
    model_config = ConfigDict(title="空白")

    op: Literal["is_empty", "is_not_empty"] = Field(
        title="條件", json_schema_extra={"ui_labels": EMPTY_OPS}
    )

    def expr(self) -> pl.Expr:
        blank = is_blank(pl.col(self.column))
        return blank if self.op == "is_empty" else ~blank

    def describe(self) -> str:
        return f"{self.column} {EMPTY_OPS[self.op]}"


class NumberCondition(_Condition):
    model_config = ConfigDict(title="數值")

    op: Literal["gt", "ge", "lt", "le", "num_equals"] = Field(
        title="條件", json_schema_extra={"ui_labels": NUMBER_OPS}
    )
    value: float = Field(title="數值")

    def expr(self) -> pl.Expr:
        number = (
            pl.col(self.column)
            .cast(pl.String)
            .str.replace_all(",", "", literal=True)
            .str.strip_chars()
            .cast(pl.Float64, strict=False)
        )
        return {
            "gt": number > self.value,
            "ge": number >= self.value,
            "lt": number < self.value,
            "le": number <= self.value,
            "num_equals": number == self.value,
        }[self.op]

    def describe(self) -> str:
        return f"{self.column} {NUMBER_OPS[self.op]} {self.value:g}"


Condition = Annotated[
    TextCondition | RegexCondition | EmptyCondition | NumberCondition, Field(discriminator="op")
]


@register
class Filter(Step):
    """依條件保留（或排除）列。"""

    type: Literal["filter"] = "filter"
    conditions: list[Condition] = Field(title="條件", min_length=1)
    match: Literal["all", "any"] = Field(
        default="all",
        title="條件組合",
        json_schema_extra={"ui_labels": {"all": "全部符合", "any": "任一符合"}},
    )
    keep: Literal["matching", "non_matching"] = Field(
        default="matching",
        title="保留",
        json_schema_extra={
            "ui_labels": {"matching": "符合條件的列", "non_matching": "不符合條件的列"}
        },
    )

    label = "篩選"
    category = "列"
    ai_notes = (
        "條件的 op 決定需要哪些欄位：文字條件用 value（字串），正規表示式用 pattern，"
        "數值條件用 value（數字，比較前會去掉千分位逗號，轉不成數字的列視為不符合），空白條件不需要值。"
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
        exprs = [c.expr().fill_null(False) for c in self.conditions]
        combined = pl.all_horizontal(exprs) if self.match == "all" else pl.any_horizontal(exprs)
        return lf.filter(combined if self.keep == "matching" else ~combined)

    def describe(self) -> str:
        joiner = " 且 " if self.match == "all" else " 或 "
        text = joiner.join(c.describe() for c in self.conditions)
        return f"{'保留' if self.keep == 'matching' else '排除'}：{text}"
