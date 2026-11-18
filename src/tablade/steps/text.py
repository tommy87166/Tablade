"""文字：清理文字、取代文字。全部用 Polars 原生運算（不呼叫 Python），可以串流處理大檔。"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from tablade.i18n import en, plural, t

from .base import ColumnRefs, Example, Step, StepError, data_columns, pick_column, quote_list
from .registry import register

Width = Literal["none", "alnum", "nfkc"]
WIDTH_LABELS = {
    "none": "不轉換",
    "alnum": "全形英數字與全形空白轉半形（中文標點不動）",
    "nfkc": "全部轉半形（Unicode NFKC：標點、① 這類符號也會轉）",
}
WIDTH_LABELS_EN = {
    "none": "Don't convert",
    "alnum": "Full-width letters, digits and spaces to half-width (Chinese punctuation unchanged)",
    "nfkc": "Everything to half-width (Unicode NFKC: punctuation and symbols like ① too)",
}
FULLWIDTH = [chr(0xFF10 + i) for i in range(10)] + [
    chr(base + i) for base in (0xFF21, 0xFF41) for i in range(26)
]
HALFWIDTH = [chr(ord(c) - 0xFEE0) for c in FULLWIDTH]
FULLWIDTH.append("　")  # 全形空白
HALFWIDTH.append(" ")
INVISIBLE = r"[\p{Cf}[\p{Cc}--\t]]"
"""看不見的字元：零寬空白、BOM、軟連字號等格式字元，以及 Tab 以外的控制字元。"""

Match = Literal["contains", "whole", "regex"]
MATCH_LABELS = {
    "contains": "部分相符（取代找到的文字）",
    "whole": "整格完全相符才取代",
    "regex": "正規表示式（「換成」可以用 $1 引用群組）",
}
MATCH_LABELS_EN = {
    "contains": "Contains (replace the matched text)",
    "whole": "Replace only when the whole cell matches",
    "regex": 'Regular expression ("Replace with" can use $1 for groups)',
}


def text_columns(lf: pl.LazyFrame, columns: list[str]) -> list[str]:
    """檢查欄位存在而且是文字欄；回傳去掉重複的欄位清單。"""
    wanted = list(dict.fromkeys(columns))
    missing = [c for c in wanted if c not in data_columns(lf)]
    if missing:
        raise StepError(
            t(
                f"欄位不存在：{'、'.join(missing)}",
                f"{'Column' if len(missing) == 1 else 'Columns'} not found: {', '.join(missing)}",
            )
        )
    schema = lf.collect_schema()
    not_text = [
        c for c in wanted if schema[c] not in (pl.String, pl.Null)
    ]  # 全是 null 的欄是 Null 型別
    if not_text:
        verb = "is" if len(not_text) == 1 else "are"
        raise StepError(
            t(
                f"只能用在文字欄：{'、'.join(not_text)} 不是文字",
                f"Only works on text columns: {', '.join(not_text)} {verb} not text",
            )
        )
    return wanted


def _check_regex(pattern: str) -> None:
    """用 Polars 的引擎編譯一次（和執行時一樣）；語法錯誤丟出 ValueError。"""
    try:
        pl.Series([""], dtype=pl.String).str.contains(pattern)
    except pl.exceptions.PolarsError as err:
        raise ValueError(
            t(f"正規表示式有誤：{err}", f"Invalid regular expression: {err}")
        ) from None


@register
class TrimClean(Step):
    """清理文字：去掉前後空白、全形英數字轉半形、移除看不見的字元。"""

    type: Literal["trim_clean"] = "trim_clean"
    columns: ColumnRefs = Field(title="要清理的欄位", json_schema_extra=en("Columns to clean"))
    trim: bool = Field(
        default=True,
        title="去掉前後空白（含全形空白）",
        json_schema_extra=en("Trim leading and trailing spaces (including full-width)"),
    )
    width: Width = Field(
        default="alnum",
        title="全形轉半形",
        json_schema_extra={
            "ui_labels": WIDTH_LABELS,
            **en("Full-width to half-width", ui_labels=WIDTH_LABELS_EN),
        },
    )
    invisible: bool = Field(
        default=True,
        title="移除看不見的字元",
        description="零寬空白、BOM、控制字元（Tab 保留）",
        json_schema_extra=en(
            "Remove invisible characters",
            description="Zero-width spaces, BOM, control characters (tabs are kept)",
        ),
    )
    collapse_spaces: bool = Field(
        default=False,
        title="中間連續的空白合併成一個",
        json_schema_extra=en("Collapse repeated spaces into one"),
    )

    label = "清理文字"
    label_en = "Clean text"
    doc_en = (
        "Clean text: trim leading and trailing spaces, convert full-width letters and digits "
        "to half-width, remove invisible characters."
    )
    category = "欄"
    ai_notes = (
        "只能用在文字欄。處理順序：移除看不見的字元 → 全形轉半形 → 合併空白 → 去掉前後空白。"
        "width=alnum 只把全形英數字（０-９、Ａ-Ｚ、ａ-ｚ）與全形空白轉半形，中文標點（，：（））不動；"
        "width=nfkc 用 Unicode NFKC，全形標點、①、㈱ 這類符號也會轉。"
    )
    examples = (
        Example(
            title="全形英數字與前後空白",
            params={"columns": ["品號", "門市"]},
            input={"品號": ["　Ａ００１ ", "B002​"], "門市": [" 北區門市（總店）", "中區門市"]},
            output={"品號": ["A001", "B002"], "門市": ["北區門市（總店）", "中區門市"]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        if context.get("column"):
            column = pick_column(frame, context)
            return {"columns": [column]} if column else {}
        text = [c for c, t in frame.schema.items() if t == pl.String and not c.startswith("__")]
        return {"columns": text} if text else {}

    def clean(self, column: str) -> pl.Expr:
        expr = pl.col(column).cast(pl.String)  # 全是 null 的欄（Null 型別）也變成文字欄
        if self.invisible:
            expr = expr.str.replace_all(INVISIBLE, "")
        if self.width == "alnum":
            expr = expr.str.replace_many(FULLWIDTH, HALFWIDTH)
        elif self.width == "nfkc":
            expr = expr.str.normalize("NFKC")
        if self.collapse_spaces:
            expr = expr.str.replace_all(r"\s+", " ")
        if self.trim:
            expr = expr.str.strip_chars()
        return expr.alias(column)

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        return lf.with_columns(self.clean(c) for c in text_columns(lf, self.columns))

    def describe(self) -> str:
        what = []
        if self.trim:
            what.append(t("去掉前後空白", "trim spaces"))
        if self.width == "alnum":
            what.append(t("全形英數字轉半形", "full-width letters and digits to half-width"))
        elif self.width == "nfkc":
            what.append(t("全形轉半形（NFKC）", "full-width to half-width (NFKC)"))
        if self.invisible:
            what.append(t("移除看不見的字元", "remove invisible characters"))
        if self.collapse_spaces:
            what.append(t("合併空白", "collapse spaces"))
        done = t("、", ", ").join(what) or t("沒有選任何清理方式", "no cleaning options selected")
        columns = quote_list(self.columns)
        return t(f"清理文字（{done}）：{columns}", f"Clean text ({done}): {columns}")


class Replacement(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, title="取代", json_schema_extra=en("Replacement")
    )

    find: str = Field(title="找", min_length=1, json_schema_extra=en("Find"))
    replace: str = Field(
        default="",
        title="換成",
        description="空白代表刪掉找到的文字",
        json_schema_extra=en("Replace with", description="Leave empty to delete the matched text"),
    )


@register
class ReplaceValues(Step):
    """把格子裡找到的文字換掉（預設）；也可以只在整格相符時才換。"""

    type: Literal["replace_values"] = "replace_values"
    columns: ColumnRefs = Field(title="要取代的欄位", json_schema_extra=en("Columns to replace in"))
    match: Match = Field(
        default="contains",
        title="比對方式",
        json_schema_extra={"ui_labels": MATCH_LABELS, **en("Match", ui_labels=MATCH_LABELS_EN)},
    )
    replacements: list[Replacement] = Field(
        title="取代清單",
        min_length=1,
        description="由上往下依序取代",
        json_schema_extra=en("Replacements", description="Applied in order, top to bottom"),
    )

    label = "取代文字"
    label_en = "Replace text"
    doc_en = "Replace text found in cells (default); or replace only when the whole cell matches."
    category = "欄"
    ai_notes = (
        "只能用在文字欄；replacements 由上往下依序套用，null 維持 null。"
        "match=contains 取代值裡面找到的文字（一般文字，不是正規表示式）；"
        "match=whole 整格完全等於 find 才換成 replace；"
        "match=regex 時 find 是正規表示式（Rust regex 語法），replace 可以用 $1、${name} 引用群組。"
    )
    examples = (
        Example(
            title="把代表空白的寫法換掉",
            params={
                "columns": ["備註"],
                "match": "whole",
                "replacements": [{"find": "N/A", "replace": ""}, {"find": "-", "replace": ""}],
            },
            input={"備註": ["N/A", "-", "有折扣", "A-1"]},
            output={"備註": ["", "", "有折扣", "A-1"]},
        ),
        Example(
            title="正規表示式：把「單號 0001」改成「0001」",
            params={
                "columns": ["單號"],
                "match": "regex",
                "replacements": [{"find": r"^單號\s*(\d+)$", "replace": "$1"}],
            },
            input={"單號": ["單號 0001", "單號0002", "其他"]},
            output={"單號": ["0001", "0002", "其他"]},
        ),
    )

    @field_validator("replacements")
    @classmethod
    def _valid_regex(cls, value: list[Replacement], info: ValidationInfo) -> list[Replacement]:
        if info.data.get("match") == "regex":
            for item in value:
                _check_regex(item.find)
        return value

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context)
        if column is None:
            return {}
        value = context.get("value")
        find = "" if value is None else str(value)
        return {"columns": [column], "replacements": [{"find": find, "replace": ""}]}

    def replaced(self, column: str) -> pl.Expr:
        expr = pl.col(column).cast(pl.String)  # 全是 null 的欄（Null 型別）也變成文字欄
        for item in self.replacements:
            if self.match == "whole":
                expr = pl.when(expr == item.find).then(pl.lit(item.replace)).otherwise(expr)
            else:
                expr = expr.str.replace_all(
                    item.find, item.replace, literal=self.match == "contains"
                )
        return expr.alias(column)

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        return lf.with_columns(self.replaced(c) for c in text_columns(lf, self.columns))

    def describe(self) -> str:
        pairs = t("、", ", ").join(
            t(f"「{r.find}」→「{r.replace}」", f'"{r.find}" → "{r.replace}"')
            for r in self.replacements[:3]
        )
        n = len(self.replacements)
        more = t(f"…共 {n} 組", f"… {plural(n, 'pair', 'pairs')} in total") if n > 3 else ""
        how = {
            "contains": "",
            "whole": t("（整格相符）", " (whole cell)"),
            "regex": t("（正規表示式）", " (regular expression)"),
        }[self.match]
        columns = quote_list(self.columns)
        return t(
            f"取代文字{how}：{pairs}{more}，欄位 {columns}",
            f"Replace text{how} in {columns}: {pairs}{more}",
        )
