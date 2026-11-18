"""分段報表用的步驟：將列進行分類、用正規表示式擷取、依固定位置拆欄（DESIGN.md §7.1）。

列印格式的報表把明細依門市之類的欄位分段，每段有段落標題、欄位標題、小計，跨頁還有頁首。
典型的攤平流程：將列進行分類 → 從段落標題擷取值 → 向下填滿 → 篩選只留明細 → 依固定位置拆欄。
全部用 Polars 原生運算（不呼叫 Python），可以串流處理大檔。
"""

from __future__ import annotations

import unicodedata
from itertools import pairwise
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from tablade.i18n import en, plural, t

from .base import (
    ColumnRef,
    Example,
    NewColumns,
    NonEmpty,
    Step,
    StepError,
    data_columns,
    pick_column,
    quote_list,
    sample_values,
)
from .filter import Condition
from .registry import register

MATCH_LABELS = {"all": "全部符合", "any": "任一符合"}
MATCH_LABELS_EN = {"all": "All conditions", "any": "Any condition"}


# ── 將列進行分類 ───────────────────────────────────────────────────


class Rule(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, title="分類規則", json_schema_extra=en("Classification rule")
    )

    label: str = Field(
        title="類別",
        min_length=1,
        description="符合這條規則的列標成這個類別",
        json_schema_extra=en("Category", description="Rows that match this rule get this category"),
    )
    conditions: list[Condition] = Field(
        title="條件", min_length=1, json_schema_extra=en("Conditions")
    )
    match: Literal["all", "any"] = Field(
        default="all",
        title="條件組合",
        json_schema_extra={"ui_labels": MATCH_LABELS, **en("Match", ui_labels=MATCH_LABELS_EN)},
    )

    def expr(self) -> pl.Expr:
        exprs = [c.expr().fill_null(False) for c in self.conditions]
        return pl.all_horizontal(exprs) if self.match == "all" else pl.any_horizontal(exprs)


@register
class ClassifyRows(Step):
    """將列進行分類：依規則替每一列標上類別（例如段落標題、明細、小計），結果放在新的一欄。"""

    type: Literal["classify_rows"] = "classify_rows"
    column: NonEmpty = Field(
        default="類型",  # i18n-ignore：預設的新欄名是資料（寫進 Recipe）
        title="新欄位名稱",
        json_schema_extra=en("New column name"),
    )
    rules: list[Rule] = Field(
        title="分類規則",
        min_length=1,
        description="由上往下比對，第一條符合的就是這一列的類別",
        json_schema_extra=en(
            "Rules",
            description="Checked from top to bottom; the first rule that matches sets the category",
        ),
    )
    otherwise: str = Field(
        default="其他",  # i18n-ignore：預設的類別是資料（寫進 Recipe）
        title="都不符合時的類別",
        description="空白代表空值",
        json_schema_extra=en("Category if no rule matches", description="Leave empty for null"),
    )

    label = "將列進行分類"
    label_en = "Classify rows"
    doc_en = (
        "Classify rows: label each row with a category by rules (for example section header, "
        "detail, subtotal), in a new column."
    )
    category = "列"
    effect = {"kind": "add", "names": "$column"}
    ai_notes = (
        "分段報表（列印格式）用：先分類，再用 filter 依類別欄只留明細，"
        "或用 extract_regex 從段落標題擷取值再 fill_down。rules 由上往下比對，第一條符合的就是"
        "那一列的類別；每條規則的 conditions 寫法和 filter 一樣，match 決定條件要全部符合或任一符合。"
        "otherwise 是都不符合時的類別（空字串代表 null）。"
    )
    examples = (
        Example(
            title="分出段落標題、小計與明細",
            params={
                "rules": [
                    {
                        "label": "段落",
                        "conditions": [{"column": "line", "op": "starts_with", "value": "門市："}],
                    },
                    {
                        "label": "小計",
                        "conditions": [{"column": "line", "op": "contains", "value": "小計："}],
                    },
                ],
                "otherwise": "明細",
            },
            input={"line": ["門市：北區門市", "115/01/05 A001 原子筆", "小計： 300"]},
            output={
                "line": ["門市：北區門市", "115/01/05 A001 原子筆", "小計： 300"],
                "類型": ["段落", "明細", "小計"],
            },
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context, prefer="line")
        if column is None:
            return {}
        return {
            "rules": [
                {"label": "", "conditions": [{"column": column, "op": "starts_with", "value": ""}]}
            ]
        }

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        result = pl.lit(self.otherwise or None, dtype=pl.String)
        for rule in reversed(self.rules):  # 倒過來包：最上面的規則最先比對
            result = pl.when(rule.expr()).then(pl.lit(rule.label)).otherwise(result)
        return lf.with_columns(result.alias(self.column)).select(self.output_order(lf))

    def describe(self) -> str:
        labels = quote_list([r.label for r in self.rules])
        other = f'"{self.otherwise}"' if self.otherwise else "null"
        return t(
            f"將列分類到「{self.column}」：{labels}，其他是「{self.otherwise or '空值'}」",
            f'Classify rows into "{self.column}": {labels}; the rest are {other}',
        )


# ── 用正規表示式擷取 ───────────────────────────────────────────


def regex_groups(pattern: str) -> int:
    """正規表示式有幾個群組（用 Polars 的引擎算，和執行時一樣）；語法錯誤丟出 ValueError。"""
    try:
        groups = pl.Series([""], dtype=pl.String).str.extract_groups(pattern)
    except pl.exceptions.PolarsError as err:
        raise ValueError(
            t(f"正規表示式有誤：{err}", f"Invalid regular expression: {err}")
        ) from None
    return len(groups.struct.fields)


@register
class ExtractRegex(Step):
    """用正規表示式擷取：從一欄抓出一或多個值（每個群組一欄），放到新的欄。"""

    type: Literal["extract_regex"] = "extract_regex"
    column: ColumnRef = Field(title="從哪一欄擷取", json_schema_extra=en("Extract from column"))
    pattern: str = Field(
        title="正規表示式",
        min_length=1,
        description="每個括號群組變成一個新欄，例如 門市：(.+)",
        json_schema_extra={
            "ui": "regex",
            **en(
                "Regular expression",
                description="Each group in parentheses becomes a new column, e.g. Store: (.+)",
            ),
        },
    )
    names: NewColumns = Field(
        title="新欄位名稱",
        description="依序對應每個群組",
        json_schema_extra=en("New column names", description="One for each group, in order"),
    )
    keep_source: bool = Field(
        default=True, title="保留原欄位", json_schema_extra=en("Keep original column")
    )

    label = "用正規表示式擷取"
    label_en = "Extract with regex"
    doc_en = (
        "Extract with a regular expression: capture one or more values from a column "
        "(one column per group) into new columns."
    )
    category = "欄"
    effect = {
        "kind": "split",
        "source": "$column",
        "names": "$names",
        "keep_source": "$keep_source",
    }
    ai_notes = (
        "Rust regex 語法；每個括號群組依序變成 names 裡的一欄，群組數量要和 names 一樣多，"
        "(?:…) 不算群組。不符合的列，新欄是 null（常接著用 fill_down 往下填）。"
    )
    examples = (
        Example(
            title="從段落標題擷取門市",
            params={"column": "line", "pattern": r"^門市：\s*(.+)$", "names": ["門市"]},
            input={"line": ["門市：北區門市", "115/01/05 A001 原子筆", "門市： 中區門市"]},
            output={
                "line": ["門市：北區門市", "115/01/05 A001 原子筆", "門市： 中區門市"],
                "門市": ["北區門市", None, "中區門市"],
            },
        ),
    )

    @field_validator("names")
    @classmethod
    def _groups_match_names(cls, value: list[str], info: ValidationInfo) -> list[str]:
        pattern = info.data.get("pattern")
        if pattern:
            groups = regex_groups(pattern)
            if groups == 0:
                raise ValueError(
                    t(
                        "正規表示式沒有括號群組：要擷取的部分請用 ( ) 括起來",
                        "The regular expression has no groups: put the part to extract in ( )",
                    )
                )
            if groups != len(value):
                raise ValueError(
                    t(
                        f"正規表示式有 {groups} 個群組，但列了 {len(value)} 個新欄名",
                        f"The regular expression has {plural(groups, 'group', 'groups')}, "
                        f"but {plural(len(value), 'new column name is', 'new column names are')} "
                        "given",
                    )
                )
        return value

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context, prefer="line")
        return {"column": column} if column else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        if self.column not in data_columns(lf):
            raise StepError(
                t(f"欄位「{self.column}」不存在", f'Column "{self.column}" does not exist')
            )
        source = pl.col(self.column).cast(pl.String)
        exprs = [source.str.extract(self.pattern, i + 1).alias(n) for i, n in enumerate(self.names)]
        return lf.with_columns(exprs).select(self.output_order(lf))

    def describe(self) -> str:
        return t(
            f"從「{self.column}」擷取 {quote_list(self.names)}（/{self.pattern}/）",
            f'Extract {quote_list(self.names)} from "{self.column}" (/{self.pattern}/)',
        )


# ── 依固定位置拆欄 ─────────────────────────────────────────────

WIDE = (
    r"[\p{Han}\x{1100}-\x{115F}\x{2E80}-\x{303F}\x{3040}-\x{30FF}\x{3100}-\x{31BF}"
    r"\x{31F0}-\x{33FF}\x{AC00}-\x{D7A3}\x{FE30}-\x{FE4F}\x{FF01}-\x{FF60}\x{FFE0}-\x{FFE6}]"
)
"""佔兩格寬的字：中日韓文字、注音、全形符號（和 cp950 / Big5 報表裡一個字佔兩個位元組相同）。"""
FILLER = ""
"""寬字後面補一個佔位字元，讓「一格寬＝一個字元」，切完再拿掉（私用區字元，資料裡不會有）。"""


def display_width(text: str) -> int:
    """佔幾格寬（建立步驟時猜欄位位置用，Python）：Unicode 的 East Asian Width 是 W、F 的算 2 格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def guess_starts(lines: list[str | None], blank_share: float = 0.98) -> list[int]:
    """從對齊的空白猜每一欄的開始位置（第幾格，從 1 起）。

    每一行先換成「一格一個字元」（中文字兩格），找出幾乎每一行（98%）都是空白的位置；
    每一段這種空白的第一格就是下一欄的開始。用空白的開頭而不是結尾：靠右對齊的數字
    （例如金額）會往左長，從空白的開頭切比較不會切到；多出來的空白之後會去掉。
    """
    rows = []
    for line in lines:
        if not line or not line.strip():
            continue
        cells: list[str] = []
        for ch in line:
            cells += [ch, FILLER] if display_width(ch) == 2 else [ch]
        rows.append(cells)
    if not rows:
        return [1]
    width = max(len(r) for r in rows)
    need = blank_share * len(rows)
    blank = [sum(1 for r in rows if i >= len(r) or r[i] == " ") >= need for i in range(width)]
    starts = [1]
    for i in range(1, width):
        if blank[i] and not blank[i - 1] and not all(blank[i:]):
            starts.append(i + 1)
    return starts


@register
class SplitFixedWidth(Step):
    """依固定位置拆欄：列印格式的報表用空白對齊欄位，依每一欄的開始位置拆開（中文字算 2 格寬）。"""

    type: Literal["split_fixed_width"] = "split_fixed_width"
    column: ColumnRef = Field(
        default="line", title="要拆的欄位", json_schema_extra=en("Column to split")
    )
    starts: list[int] = Field(
        title="每一欄的開始位置",
        min_length=1,
        description="第幾個字寬，從 1 起；中文字算 2 格寬。最後一欄到行尾",
        json_schema_extra={
            "ui": "int_list",
            **en(
                "Start position of each column",
                description="In character widths, starting at 1; a Chinese character is 2 wide. "
                "The last column runs to the end of the line",
            ),
        },
    )
    names: NewColumns = Field(title="拆出來的欄名", json_schema_extra=en("New column names"))
    trim: bool = Field(
        default=True,
        title="去掉每一欄前後的空白",
        json_schema_extra=en("Trim spaces from each column"),
    )
    keep_source: bool = Field(
        default=False, title="保留原欄位", json_schema_extra=en("Keep original column")
    )

    label = "依固定位置拆欄"
    label_en = "Split by fixed width"
    doc_en = (
        "Split by fixed width: printed reports line up columns with spaces; split at the start "
        "position of each column (a Chinese character is 2 wide)."
    )
    category = "欄"
    effect = {
        "kind": "split",
        "source": "$column",
        "names": "$names",
        "keep_source": "$keep_source",
    }
    ai_notes = (
        "列印格式（空白對齊）的報表用。starts 是每一欄的開始位置（第幾個字寬，從 1 起，遞增），"
        "數量要和 names 一樣；中日韓文字與全形符號算 2 格寬（和 cp950 報表裡一個字兩個位元組相同），"
        "最後一欄到行尾。通常先用 classify_rows ＋ filter 只留明細列再拆。"
    )
    examples = (
        Example(
            title="拆開對齊的明細",
            params={"starts": [1, 12, 17, 27], "names": ["日期", "品號", "品名", "金額"]},
            input={
                "line": ["115/01/05  A001 原子筆        300", "115/01/06  D300 後背包      2,940"]
            },
            output={
                "日期": ["115/01/05", "115/01/06"],
                "品號": ["A001", "D300"],
                "品名": ["原子筆", "後背包"],
                "金額": ["300", "2,940"],
            },
        ),
    )

    @field_validator("starts")
    @classmethod
    def _increasing(cls, value: list[int]) -> list[int]:
        if any(p < 1 for p in value):
            raise ValueError(t("開始位置從 1 起算", "Start positions begin at 1"))
        if any(b <= a for a, b in pairwise(value)):
            raise ValueError(t("開始位置要由小到大", "Start positions must be in increasing order"))
        return value

    @field_validator("names")
    @classmethod
    def _same_count(cls, value: list[str], info: ValidationInfo) -> list[str]:
        starts = info.data.get("starts")
        if starts is not None and len(starts) != len(value):
            raise ValueError(
                t(
                    f"有 {len(starts)} 個開始位置，但列了 {len(value)} 個欄名",
                    f"{plural(len(starts), 'start position', 'start positions')}, "
                    f"but {plural(len(value), 'column name', 'column names')} given",
                )
            )
        return value

    @classmethod
    def suggest(cls, frame, context):
        column = pick_column(frame, context, prefer="line")
        if column is None:
            return {}
        starts = guess_starts(sample_values(frame, column, 500))
        return {
            "column": column,
            "starts": starts,
            "names": [f"欄{i}" for i in range(1, len(starts) + 1)],  # i18n-ignore：暫時的欄名是資料
        }

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        if self.column not in data_columns(lf):
            raise StepError(
                t(f"欄位「{self.column}」不存在", f'Column "{self.column}" does not exist')
            )
        # 寬字後面補一個佔位字元：之後「第幾個字元」就是「第幾格寬」
        cells = pl.col(self.column).cast(pl.String).str.replace_all(f"({WIDE})", f"${{1}}{FILLER}")
        exprs = []
        bounds = [*self.starts, None]
        for name, start, end in zip(self.names, bounds, bounds[1:], strict=False):
            part = cells.str.slice(start - 1, None if end is None else end - start)
            part = part.str.replace_all(FILLER, "", literal=True)
            if self.trim:
                part = part.str.strip_chars()
            exprs.append(part.alias(name))
        return lf.with_columns(exprs).select(self.output_order(lf))

    def describe(self) -> str:
        where = t("、", ", ").join(str(p) for p in self.starts)
        return t(
            f"把「{self.column}」依位置 {where} 拆成 {quote_list(self.names)}",
            f'Split "{self.column}" at positions {where} into {quote_list(self.names)}',
        )
