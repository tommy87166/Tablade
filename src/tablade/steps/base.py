"""步驟的共同介面（DESIGN.md §7.3、§8、§17.5）。

每種步驟是一個 pydantic model：欄位就是參數，類別屬性是給 UI、驗證器與 AI skill 的中繼資料。

- ``apply(lf)``：純函式，LazyFrame → LazyFrame；不讀寫檔案、不 print、不碰 UI
- ``describe()``：給 UI 顯示的一句話
- ``warning_checks()``：要對輸出檢查的警告條件（可引用 ``__w_<id>_*`` 暫存欄位，engine 會在下一步前移除）
- ``effect``：欄位效果樣板，``$參數名`` 會換成參數值；Tablade 與驗證器都用它推算欄位
- ``examples``：輸入、參數、預期輸出；同時是單元測試，也會寫進 AI skill 的步驟目錄

**各檔分開處理**（DESIGN.md §8）：引擎把資料當成一串 part（``Part``＝（名稱, LazyFrame）），
每個 part 是同一個 ``__source_file`` 的一段連續資料，名稱不重複。
- ``per_file = True`` 的步驟，``apply()`` 一次**只收到一個 part**（一個來源檔，或合併後的那一份），
  直接把整個 lf 當成一個檔處理；需要整檔資訊（例如列數、第一列）時用 ``collect_small()``。
- ``per_file = False`` 的步驟一列一列處理，可能收到任意切法的資料（預覽時是整張表）。
- ``combines = True`` 的步驟（合併來源檔）由引擎呼叫 ``combine(parts)``，不呼叫 ``apply()``；
  以後要接多個輸入的步驟（join、附加查詢）也要做成這種。要看整個來源檔的步驟（群組彙總、
  移除重複、樞紐）是 ``per_file = True``：合併來源檔之前每個檔各自處理，合併之後就是整份。

**串流規則**（幾 GB 的檔案也要在固定記憶體內跑完）：
- 每個 part 已經依 ``__line_no`` 排好，filter / with_columns 都會保持順序，
  所以步驟**不要再 sort**（排序會把整份資料放進記憶體）；只有彙總後的少量結果可以排序。
- 不要用 window 函式（``.over()``）、``replace_strict``、呼叫 Python 的 ``map_batches``：
  串流引擎不支援，會退回整份放進記憶體。
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints

from tablade.ingest.sources import Part
from tablade.recipe.validator import apply_effect, resolve_effect

SOURCE_FILE = "__source_file"
LINE_NO = "__line_no"
SHEET = "__sheet"
"""讀 Excel 全部工作表時才有：這一列來自哪個工作表（每個工作表各自是一個來源）。"""
STAGE = "__stage"
"""讀前面階段的輸出時才有：這一列是從哪個階段讀進來的（來源名稱變成「@階段／檔名」）。"""
ORIG_SOURCE_FILE = "__orig_source_file"
ORIG_LINE_NO = "__orig_line_no"
ORIG_SHEET = "__orig_sheet"
ORIG_STAGE = "__orig_stage"
ORIGINAL = {
    SOURCE_FILE: ORIG_SOURCE_FILE,
    LINE_NO: ORIG_LINE_NO,
    SHEET: ORIG_SHEET,
    STAGE: ORIG_STAGE,
}
"""合併來源檔時，原本的來源檔、行號、工作表、階段存到這些系統欄位（再合併一次時保留最早的）。"""
ORDER = (SOURCE_FILE, LINE_NO)
SYSTEM_PREFIX = "__"
TEMP_PREFIX = "__w_"
ROW_INDEX = "__g"
POSITION = f"{TEMP_PREFIX}position"
"""串流執行時每一列在所有輸入裡的位置（回報進度用，engine/progress.py）；彙總類的步驟要保留它。"""

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
    tables: dict[str, dict[str, list[Any]]] | None = None
    """前面階段的結果（結合的右表）：{階段名稱: 資料}，同樣自動補上系統欄位。"""


@dataclass(frozen=True)
class WarningCheck:
    condition: pl.Expr
    """在這一步的輸出上為 True 的列會產生警告。"""
    message: str
    """可含一個 ``{}``，會換成 ``detail`` 的值。"""
    detail: pl.Expr | None = None
    on_input: bool = False
    """True：對這一步的**輸入**檢查（例如結合沒對到的列：內連接之後那些列就不在了）。"""


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(title="步驟 id", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    enabled: bool = Field(default=True, title="啟用")

    _tables: dict[str, pl.LazyFrame] = PrivateAttr(default_factory=dict)
    """引擎套用前交給步驟的其他階段的結果（``bind()``）；步驟自己不讀檔。"""

    label: ClassVar[str] = ""
    category: ClassVar[str] = ""
    effect: ClassVar[dict[str, Any]] = {"kind": "keep"}
    examples: ClassVar[tuple[Example, ...]] = ()
    ai_notes: ClassVar[str] = ""
    per_file: ClassVar[bool] = False
    """True：每個來源檔分別處理（看得到檔案的開頭結尾，例如前 N 列、向下填滿）；
    False：一列一列處理，結果與來源檔怎麼分檔無關（有測試檢查）。"""
    scope_note: ClassVar[str] = ""
    """不是單純「分別處理／無關」時，畫面上改顯示這句（例如合併來源檔）。"""
    combines: ClassVar[bool] = False
    """True：把所有 part 接成新的 part（引擎呼叫 ``combine()``，不呼叫 ``apply()``）。"""
    reshapes: ClassVar[bool] = False
    """True：輸出的列不再一對一對應輸入的列（群組彙總、樞紐、取消樞紐）。

    之前步驟的暫存欄位（警告）不會帶過去；串流執行時引擎會在這一步之前先算好之前步驟的警告
    （合併來源檔也是：合併之後來源檔與行號都變了）。
    這種步驟用 ``reshape_order()`` 排輸出欄位：系統欄位、資料欄、位置欄（回報進度用）、自己的暫存欄。
    """

    @classmethod
    def type_name(cls) -> str:
        return cls.model_fields["type"].default

    @classmethod
    def scope(cls) -> str:
        """是否每個來源檔分別處理（畫面上接在步驟名稱後面）。"""
        if cls.scope_note:
            return cls.scope_note
        return "每個來源檔分別處理" if cls.per_file else "與來源檔分檔無關"

    @classmethod
    def summary(cls) -> str:
        """純文字的步驟說明＋[scope]：給 spec 與 AI skill。"""
        return f"{(cls.__doc__ or '').strip()}[{cls.scope()}]"

    @classmethod
    def suggest(cls, frame: pl.DataFrame, context: dict[str, Any]) -> dict[str, Any]:
        """新增步驟時的建議參數。``frame`` 是這一步的輸入；``context`` 可能有使用者點的
        ``column`` / ``value``。建議值不一定合法（例如還沒填的值），使用者會在表單裡補齊。"""
        return {}

    def params(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"id", "type", "enabled"})

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        raise NotImplementedError

    def combine(self, parts: list[Part]) -> list[Part]:
        """``combines = True`` 的步驟：把所有 part 接成新的 part。"""
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def warning_checks(self) -> list[WarningCheck]:
        return []

    def temp(self, name: str) -> str:
        """這一步專用的暫存欄位名稱。"""
        return f"{TEMP_PREFIX}{self.id}_{name}"

    def stage_refs(self) -> list[str]:
        """這一步要用到哪些前面階段的結果（例如結合的右表）。"""
        return []

    def bind(self, tables: Mapping[str, pl.LazyFrame | pl.DataFrame] | None) -> Step:
        """交給步驟它要用的其他階段的結果（回傳新的物件，不改原本的）。"""
        if not self.stage_refs():
            return self
        bound = self.model_copy()
        bound._tables = {n: tables[n].lazy() for n in self.stage_refs() if tables and n in tables}
        return bound

    def table(self, name: str) -> pl.LazyFrame:
        """其他階段的結果（含系統欄位）。"""
        if name not in self._tables:
            raise StepError(f"沒有階段「{name}」的結果")
        return self._tables[name]

    def output_order(self, lf: pl.LazyFrame) -> list[str]:
        """依欄位效果推算輸出欄位順序：系統欄位、資料欄位，再保留前面步驟留下的暫存欄位。"""
        names = lf.collect_schema().names()
        data = [c for c in names if not c.startswith(SYSTEM_PREFIX)]
        effect = resolve_effect(self.effect, self.model_dump(mode="json"))
        temps = [c for c in names if c.startswith(TEMP_PREFIX)]
        return system_columns(names) + apply_effect(data, effect) + temps

    def reshape_order(self, lf: pl.LazyFrame, own_temps: list[str] | None = None) -> list[str]:
        """彙總類步驟的輸出欄位：不保留之前步驟的暫存欄位，只留位置欄與這一步自己的暫存欄。"""
        names = lf.collect_schema().names()
        data = [c for c in names if not c.startswith(SYSTEM_PREFIX)]
        effect = resolve_effect(self.effect, self.model_dump(mode="json"))
        position = [POSITION] if POSITION in names else []
        return system_columns(names) + apply_effect(data, effect) + position + (own_temps or [])


def system_columns(names: list[str]) -> list[str]:
    return [
        c
        for c in names
        if c.startswith(SYSTEM_PREFIX) and not c.startswith(TEMP_PREFIX) and c != ROW_INDEX
    ]


def source_name(columns: list[str]) -> pl.Expr:
    """每一列屬於哪個來源（和讀檔時的 part 名稱相同）：檔名；讀 Excel 全部工作表時是「檔名［工作表］」；
    從前面的階段讀進來時前面加「@階段／」。"""
    name = pl.col(SOURCE_FILE)
    if SHEET in columns:
        labeled = pl.concat_str([name, pl.lit("［"), pl.col(SHEET), pl.lit("］")])
        name = pl.when(pl.col(SHEET).is_null()).then(name).otherwise(labeled)
    if STAGE in columns:
        staged = pl.concat_str([pl.lit("@"), pl.col(STAGE), pl.lit("／"), name])
        name = pl.when(pl.col(STAGE).is_null()).then(name).otherwise(staged)
    return name


def data_columns(lf: pl.LazyFrame) -> list[str]:
    return [c for c in lf.collect_schema().names() if not c.startswith(SYSTEM_PREFIX)]


def collect_small(lf: pl.LazyFrame) -> pl.DataFrame:
    """執行只會產生少量結果的查詢（列數、第一列之類）。"""
    return lf.collect(engine="streaming")


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


MAX_DATA_VALUES = 100
"""「從資料讀出」的值最多幾個（例如樞紐要變成欄的值）；超過時要使用者先篩選或換一欄。"""


def data_values(frame: pl.DataFrame, column: str) -> list[str]:
    """某一欄有哪些不同的值（依第一次出現的順序，轉成文字；空值與空白不算）。"""
    series = frame.get_column(column).cast(pl.String).drop_nulls()
    return series.filter(series.str.strip_chars() != "").unique(maintain_order=True).to_list()


def too_many_values(column: str, count: int) -> str | None:
    """不同的值超過上限時的說明（不默默截掉）；沒超過時是 None。"""
    limit = MAX_DATA_VALUES
    if count <= limit:
        return None
    return f"「{column}」有 {count:,} 個不同的值，超過 {limit} 個；請先篩選，或換一欄"


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
