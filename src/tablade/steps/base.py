"""步驟的共同介面（DESIGN.md §7.3、§8、§17.5）。

每種步驟是一個 pydantic model：欄位就是參數，類別屬性是給 UI、驗證器與 AI skill 的中繼資料。

- ``apply(lf)``：純函式，LazyFrame → LazyFrame；不讀寫檔案、不 print、不碰 UI
- ``describe()``：給 UI 顯示的一句話
- ``warning_checks()``：要對輸出檢查的警告條件（可引用 ``__w_<id>_*`` 暫存欄位，engine 會在下一步前移除）
- ``effect``：欄位效果樣板，``$參數名`` 會換成參數值；Tablade 與驗證器都用它推算欄位
- ``examples``：輸入、參數、預期輸出；同時是單元測試，也會寫進 AI skill 的步驟目錄

**串流規則**（幾 GB 的檔案也要在固定記憶體內跑完，DESIGN.md §8）：
- 資料讀進來時已經依 ``__source_file, __line_no`` 排好，filter / with_columns 都會保持順序，
  所以步驟**不要再 sort**（排序會把整份資料放進記憶體）。
- 不要用 window 函式（``.over()``）與 ``replace_strict``：串流引擎不支援，會退回整份放進記憶體。
  每個來源檔的資訊（起點、列數、第一列）改用 ``file_positions()`` 先做一次小聚合，
  再用 ``by_file()`` 的 when 對照表套回每一列。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tablade.recipe.validator import apply_effect, resolve_effect

SOURCE_FILE = "__source_file"
LINE_NO = "__line_no"
ORDER = (SOURCE_FILE, LINE_NO)
SYSTEM_PREFIX = "__"
TEMP_PREFIX = "__w_"
ROW_INDEX = "__g"

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
ColumnRef = Annotated[str, Field(min_length=1, json_schema_extra={"ui": "column"})]
ColumnRefs = Annotated[list[NonEmpty], Field(min_length=1, json_schema_extra={"ui": "columns"})]
NewColumns = Annotated[list[NonEmpty], Field(min_length=1, json_schema_extra={"ui": "new_columns"})]


class StepError(ValueError):
    """步驟無法套用在目前的資料上（例如欄位數不符）。"""


@dataclass(frozen=True)
class Example:
    title: str
    params: dict[str, Any]
    input: dict[str, list[Any]]
    """不含系統欄位；測試時自動補上 ``__source_file`` / ``__line_no``。"""
    output: dict[str, list[Any]]


@dataclass(frozen=True)
class WarningCheck:
    condition: pl.Expr
    """在這一步的輸出上為 True 的列會產生警告。"""
    message: str
    """可含一個 ``{}``，會換成 ``detail`` 的值。"""
    detail: pl.Expr | None = None


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(title="步驟 id", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    enabled: bool = Field(default=True, title="啟用")

    label: ClassVar[str] = ""
    category: ClassVar[str] = ""
    effect: ClassVar[dict[str, Any]] = {"kind": "keep"}
    examples: ClassVar[tuple[Example, ...]] = ()
    ai_notes: ClassVar[str] = ""

    @classmethod
    def type_name(cls) -> str:
        return cls.model_fields["type"].default

    @classmethod
    def suggest(cls, frame: pl.DataFrame, context: dict[str, Any]) -> dict[str, Any]:
        """新增步驟時的建議參數。``frame`` 是這一步的輸入；``context`` 可能有使用者點的
        ``column`` / ``value``。建議值不一定合法（例如還沒填的值），使用者會在表單裡補齊。"""
        return {}

    def params(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"id", "type", "enabled"})

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def warning_checks(self) -> list[WarningCheck]:
        return []

    def temp(self, name: str) -> str:
        """這一步專用的暫存欄位名稱。"""
        return f"{TEMP_PREFIX}{self.id}_{name}"

    def output_order(self, lf: pl.LazyFrame) -> list[str]:
        """依欄位效果推算輸出欄位順序：系統欄位、資料欄位，再保留前面步驟留下的暫存欄位。"""
        names = lf.collect_schema().names()
        data = [c for c in names if not c.startswith(SYSTEM_PREFIX)]
        effect = resolve_effect(self.effect, self.model_dump(mode="json"))
        temps = [c for c in names if c.startswith(TEMP_PREFIX)]
        return system_columns(names) + apply_effect(data, effect) + temps


def system_columns(names: list[str]) -> list[str]:
    return [
        c
        for c in names
        if c.startswith(SYSTEM_PREFIX) and not c.startswith(TEMP_PREFIX) and c != ROW_INDEX
    ]


def data_columns(lf: pl.LazyFrame) -> list[str]:
    return [c for c in lf.collect_schema().names() if not c.startswith(SYSTEM_PREFIX)]


def by_file(values: dict[str, Any], dtype: pl.DataType, default: Any = None) -> pl.Expr:
    """每個來源檔對應一個值（when 對照表；串流引擎不支援 ``replace_strict``）。"""
    branches = [
        pl.when(pl.col(SOURCE_FILE) == name).then(pl.lit(v, dtype=dtype))
        for name, v in values.items()
    ]
    return pl.coalesce([*branches, pl.lit(default, dtype=dtype)])


def collect_small(lf: pl.LazyFrame) -> pl.DataFrame:
    """執行只會產生少量結果的查詢（每個來源檔一列之類）。"""
    return lf.collect(engine="streaming")


def file_positions(lf: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.Expr, pl.Expr]:
    """回傳（加上全域列號的 lf、每列在來源檔內的位置（0 起）、該檔目前的列數）。

    先用一次 min / len 聚合算出每個檔案的起點與列數，都是串流原生支援的操作。
    用完記得 ``.drop(ROW_INDEX)``。
    """
    lf = lf.with_row_index(ROW_INDEX)
    stats = collect_small(
        lf.group_by(SOURCE_FILE).agg(
            pl.col(ROW_INDEX).min().alias("start"), pl.len().alias("count")
        )
    )
    dtype = lf.collect_schema()[ROW_INDEX]
    start = by_file(dict(zip(stats[SOURCE_FILE], stats["start"], strict=True)), dtype)
    count = by_file(dict(zip(stats[SOURCE_FILE], stats["count"], strict=True)), dtype, 0)
    return lf, (pl.col(ROW_INDEX).cast(pl.Int64) - start.cast(pl.Int64)), count.cast(pl.Int64)


def is_blank(expr: pl.Expr) -> pl.Expr:
    return expr.cast(pl.String).str.strip_chars().fill_null("") == ""


def pick_column(
    frame: pl.DataFrame, context: dict[str, Any], prefer: str | None = None
) -> str | None:
    """建議要操作的欄位：使用者點的欄 → 指定的偏好欄 → 第一個資料欄。"""
    columns = [c for c in frame.columns if not c.startswith(SYSTEM_PREFIX)]
    for candidate in (context.get("column"), prefer):
        if candidate in columns:
            return candidate
    return columns[0] if columns else None


def sample_values(frame: pl.DataFrame, column: str, n: int = 200) -> list[str | None]:
    return frame.get_column(column).head(n).cast(pl.String).to_list()


def guess_delimiter(
    values: list[str | None], candidates: str = ",\t;|", quote: str | None = '"'
) -> str:
    """挑拆出來的欄數最一致（而且多於 1 欄）的分隔符號；``quote`` 不是 None 時會處理引號。"""
    lines = [v for v in values if v]
    best, best_score = candidates[0], 0.0
    for d in candidates:
        if quote:
            counts = [len(next(csv.reader([v], delimiter=d, quotechar=quote), [])) for v in lines]
        else:
            counts = [v.count(d) + 1 for v in lines]
        if not counts or max(counts) <= 1:
            continue
        common = max(set(counts), key=counts.count)
        if common <= 1:
            continue
        score = counts.count(common) / len(counts)
        if score > best_score:  # 分數相同時保留排在前面的候選
            best, best_score = d, score
    return best


def quote_list(names: list[str], limit: int = 4) -> str:
    shown = "、".join(names[:limit])
    return shown + ("…" if len(names) > limit else "")
