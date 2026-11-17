"""彙總：取消樞紐、群組彙總、樞紐（DESIGN.md §7.2）。

和其他步驟一樣，合併來源檔之前每個來源檔各自處理（讀 Excel 全部工作表時每個工作表各自處理），
合併之後才是整份一起處理。輸出的列不再一對一對應輸入的列（``reshapes = True``）：
- 群組彙總、樞紐：每一組一列，照每組第一次出現的順序；``__line_no`` 是這一組第一筆的行號。
- 取消樞紐：每一列展開成連續的幾列，都保留原本的行號。
全部用 Polars 原生運算，可以串流；排序只排彙總後的少量資料（列數＝組數）。
"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from .base import (
    LINE_NO,
    POSITION,
    SHEET,
    SOURCE_FILE,
    ColumnRef,
    ColumnRefs,
    Example,
    NewColumns,
    NonEmpty,
    Step,
    StepError,
    WarningCheck,
    data_columns,
    is_blank,
    pick_column,
    quote_list,
    sample_values,
)
from .registry import register

Op = Literal["sum", "mean", "max", "min", "count", "n_unique", "first", "last"]
OP_LABELS = {
    "sum": "合計",
    "mean": "平均",
    "max": "最大",
    "min": "最小",
    "count": "有值的筆數",
    "n_unique": "不同值個數",
    "first": "第一筆",
    "last": "最後一筆",
}
PivotOp = Literal["sum", "mean", "max", "min", "count", "first", "last"]
NUMERIC_OPS = {"sum", "mean"}
KEY_TYPES = (pl.String, pl.Date, pl.Null)
"""分組欄（群組彙總的 by、樞紐的 index）只能是文字或日期。"""


def _dtype_text(dtype: pl.DataType) -> str:
    if dtype.is_numeric():
        return "數字"
    if dtype.is_temporal():
        return "日期"
    return "文字" if dtype == pl.String else str(dtype)


def check_columns(lf: pl.LazyFrame, columns: list[str]) -> pl.Schema:
    missing = [c for c in dict.fromkeys(columns) if c not in data_columns(lf)]
    if missing:
        raise StepError(f"欄位不存在：{'、'.join(missing)}")
    return lf.collect_schema()


def check_keys(schema: pl.Schema, keys: list[str], what: str) -> None:
    bad = [f"{k}（{_dtype_text(schema[k])}）" for k in keys if schema[k] not in KEY_TYPES]
    if bad:
        raise StepError(f"{what}只能是文字或日期欄：{'、'.join(bad)}")


def check_numeric(schema: pl.Schema, column: str, op: str) -> None:
    if op in NUMERIC_OPS and not schema[column].is_numeric():
        raise StepError(
            f"「{column}」是{_dtype_text(schema[column])}，{OP_LABELS[op]}要先用「文字轉數字」"
        )


def aggregate(expr: pl.Expr, op: str) -> pl.Expr:
    return {
        "sum": expr.sum(),
        "mean": expr.mean(),
        "max": expr.max(),
        "min": expr.min(),
        "count": expr.count().cast(pl.Int64),
        "n_unique": expr.n_unique().cast(pl.Int64),
        "first": expr.first(),
        "last": expr.last(),
    }[op]


def group_rows(lf: pl.LazyFrame, keys: list[str], aggs: list[pl.Expr]) -> pl.LazyFrame:
    """依 keys 分組（沒有 keys 就整份一組），照每組第一次出現的順序。

    系統欄位：__source_file、__sheet 在同一個來源裡是固定值；__line_no 是這一組第一筆的行號；
    位置欄（串流時回報進度用）取這一組最後一筆的位置。
    """
    names = lf.collect_schema().names()
    system = [pl.col(c).first() for c in (SOURCE_FILE, SHEET) if c in names]
    system.append(pl.col(LINE_NO).min())
    if POSITION in names:
        system.append(pl.col(POSITION).max())
    if not keys:
        # 整份彙總成一列；沒有資料時（行號是空的）不產生彙總列
        return lf.select(*system, *aggs).filter(pl.col(LINE_NO).is_not_null())
    return lf.group_by(keys).agg(*system, *aggs).sort(LINE_NO)


# ── 取消樞紐 ───────────────────────────────────────────────────


@register
class Unpivot(Step):
    """取消樞紐（寬轉長）：把好幾欄攤開成「名稱、值」兩欄，每一列展開成好幾列。"""

    type: Literal["unpivot"] = "unpivot"
    columns: ColumnRefs = Field(title="要攤開的欄位", description="例如 1月、2月、3月")
    name_column: NonEmpty = Field(default="名稱", title="放原本欄名的新欄")
    value_column: NonEmpty = Field(default="值", title="放值的新欄")
    skip_empty: bool = Field(default=False, title="值是空白的不展開")

    label = "取消樞紐"
    category = "彙總"
    reshapes = True
    effect = {
        "kind": "unpivot",
        "columns": "$columns",
        "name": "$name_column",
        "value": "$value_column",
    }
    ai_notes = (
        "寬轉長：沒列在 columns 的資料欄保留不動，每一列依 columns 展開成連續的幾列"
        "（順序和原本一樣），每一列都保留原本的 __source_file、__line_no。"
        "要攤開的欄型別不一樣時（例如文字與數字）全部轉成文字。"
    )
    examples = (
        Example(
            title="每個月一欄攤開成每個月一列",
            params={"columns": ["1月", "2月"], "name_column": "月份", "value_column": "金額"},
            input={"門市": ["北區門市", "中區門市"], "1月": [300, 120], "2月": [450, 80]},
            output={
                "門市": ["北區門市", "北區門市", "中區門市", "中區門市"],
                "月份": ["1月", "2月", "1月", "2月"],
                "金額": [300, 450, 120, 80],
            },
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = context.get("column")
        return {"columns": [column]} if column in frame.columns else {}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        columns = list(dict.fromkeys(self.columns))
        schema = check_columns(lf, columns)
        as_text = len({schema[c] for c in columns}) > 1  # 型別不一樣時全部轉成文字
        items = pl.concat_list(
            [
                pl.struct(
                    pl.lit(c, dtype=pl.String).alias(self.name_column),
                    (pl.col(c).cast(pl.String) if as_text else pl.col(c)).alias(self.value_column),
                )
                for c in columns
            ]
        )
        order = self.reshape_order(lf)
        kept = [c for c in order if c not in (self.name_column, self.value_column)]
        packed = self.temp("items")
        # concat_list ＋ explode：一列原始資料展開成連續的幾列（Polars 的 unpivot 串流時會打亂順序）
        out = lf.select(*kept, items.alias(packed)).explode(packed).unnest(packed)
        if self.skip_empty:
            out = out.filter(~is_blank(pl.col(self.value_column)))
        return out.select(order)

    def describe(self) -> str:
        return (
            f"取消樞紐：{quote_list(self.columns)} → 「{self.name_column}」「{self.value_column}」"
        )


# ── 群組彙總 ───────────────────────────────────────────────────


class Aggregation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, title="彙總")

    column: ColumnRef = Field(title="欄位")
    op: Op = Field(default="sum", title="方式", json_schema_extra={"ui_labels": OP_LABELS})
    name: NonEmpty = Field(title="新欄名")


@register
class GroupBy(Step):
    """群組彙總：依分組欄分組，算每一組的合計、平均、筆數…；不選分組欄就是整個來源檔彙總成一列。"""

    type: Literal["group_by"] = "group_by"
    by: list[NonEmpty] = Field(
        default_factory=list,
        title="分組欄",
        description="只能是文字或日期欄；不選代表整個來源檔彙總成一列",
        json_schema_extra={"ui": "columns"},
    )
    aggregations: list[Aggregation] = Field(default_factory=list, title="彙總")
    count_as: str = Field(
        default="筆數", title="另外加一欄筆數", description="這一組有幾列；空白代表不加"
    )

    label = "群組彙總"
    per_file = True
    category = "彙總"
    reshapes = True
    effect = {"kind": "group", "by": "$by", "aggregations": "$aggregations", "count": "$count_as"}
    ai_notes = (
        "合併來源檔之前每個來源檔各自分組；要整份一起彙總先用 merge_files。by 只能是文字或日期欄，"
        "空清單代表整個來源檔彙總成一列。op：sum、mean 只能用在數字欄（先用 parse_number），"
        "count 是有值的筆數、n_unique 是不同值個數；count_as 另外加一欄這一組有幾列（空字串代表不加）。"
        "輸出照每組第一次出現的順序，__line_no 是這一組第一筆的行號。"
    )
    examples = (
        Example(
            title="每個門市的金額合計",
            params={
                "by": ["門市"],
                "aggregations": [{"column": "金額", "op": "sum", "name": "金額合計"}],
            },
            input={"門市": ["北區門市", "中區門市", "北區門市"], "金額": [100, 50, 30]},
            output={"門市": ["北區門市", "中區門市"], "金額合計": [130, 50], "筆數": [2, 1]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        column = context.get("column")
        by = [column] if column in frame.columns and frame.schema[column] in KEY_TYPES else []
        numbers = [c for c, t in frame.schema.items() if not c.startswith("__") and t.is_numeric()]
        aggregations = [{"column": c, "op": "sum", "name": f"{c}合計"} for c in numbers[:1]]
        return {"by": by, "aggregations": aggregations}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        by = list(dict.fromkeys(self.by))
        schema = check_columns(lf, by + [a.column for a in self.aggregations])
        check_keys(schema, by, "分組欄")
        aggs = []
        for a in self.aggregations:
            check_numeric(schema, a.column, a.op)
            aggs.append(aggregate(pl.col(a.column), a.op).alias(a.name))
        if self.count_as:
            aggs.append(pl.len().cast(pl.Int64).alias(self.count_as))
        return group_rows(lf, by, aggs).select(self.reshape_order(lf))

    def describe(self) -> str:
        what = [f"{OP_LABELS[a.op]}({a.column})" for a in self.aggregations]
        if self.count_as:
            what.append("筆數")
        by = f"依 {quote_list(self.by)} 分組" if self.by else "整個來源檔"
        return f"群組彙總（{by}）：{quote_list(what) or '沒有彙總'}"


# ── 樞紐 ──────────────────────────────────────────────────────


@register
class Pivot(Step):
    """樞紐（長轉寬）：把某一欄的值變成新的欄名，每一組一列；欄名在建立步驟時從資料讀出。"""

    type: Literal["pivot"] = "pivot"
    index: list[NonEmpty] = Field(
        default_factory=list,
        title="每一列依哪些欄",
        description="只能是文字或日期欄；不選代表整個來源檔一列",
        json_schema_extra={"ui": "columns"},
    )
    on: ColumnRef = Field(title="哪一欄的值變成欄名")
    values: ColumnRef = Field(title="值從哪一欄來")
    op: PivotOp = Field(
        default="sum",
        title="同一格有好幾筆時",
        json_schema_extra={"ui_labels": {k: v for k, v in OP_LABELS.items() if k != "n_unique"}},
    )
    columns: NewColumns = Field(
        title="要變成欄的值", description="建立步驟時從資料讀出；資料裡多出來的值會產生警告"
    )

    label = "樞紐"
    per_file = True
    category = "彙總"
    reshapes = True
    effect = {
        "kind": "pivot",
        "index": "$index",
        "on": "$on",
        "values": "$values",
        "names": "$columns",
    }
    ai_notes = (
        "長轉寬：on 欄的值變成欄名，欄名要寫死在 columns（依資料裡出現的值）；資料裡有不在 columns 的值時"
        "產生警告、不轉成欄。合併來源檔之前每個來源檔各自處理。index 只能是文字或日期欄，空清單代表整個"
        "來源檔一列。沒有資料的格子是 null（不是 0）。sum、mean 只能用在數字欄。"
        "輸出照每組第一次出現的順序，__line_no 是這一組第一筆的行號。"
    )
    examples = (
        Example(
            title="每個門市一列、每個月一欄",
            params={"index": ["門市"], "on": "月份", "values": "金額", "columns": ["1月", "2月"]},
            input={
                "門市": ["北區門市", "北區門市", "中區門市", "北區門市"],
                "月份": ["1月", "2月", "1月", "1月"],
                "金額": [100, 200, 50, 30],
            },
            output={"門市": ["北區門市", "中區門市"], "1月": [130, 50], "2月": [200, None]},
        ),
    )

    @classmethod
    def suggest(cls, frame, context):
        on = pick_column(frame, context)
        if on is None:
            return {}
        seen = [v for v in dict.fromkeys(sample_values(frame, on, 5_000)) if v not in (None, "")]
        return {"on": on, "columns": seen[:50]}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        index = list(dict.fromkeys(self.index))
        schema = check_columns(lf, [*index, self.on, self.values])
        check_keys(schema, index, "每一列依的欄")
        check_numeric(schema, self.values, self.op)
        on = pl.col(self.on).cast(pl.String)
        aggs = []
        for name in self.columns:
            match = on == name
            value = aggregate(pl.col(self.values).filter(match), self.op)
            # 這一組沒有這個值的任何一筆：空值（合計不會變成 0）
            aggs.append(pl.when(match.sum() > 0).then(value).otherwise(None).alias(name))
        extra = self.temp("extra")
        unknown = on.filter(on.is_not_null() & ~on.is_in(list(self.columns)))
        aggs.append(unknown.unique(maintain_order=True).str.join("、").alias(extra))
        return group_rows(lf, index, aggs).select(self.reshape_order(lf, [extra]))

    def warning_checks(self):
        extra = pl.col(self.temp("extra"))
        return [
            WarningCheck(
                condition=extra.is_not_null() & (extra != ""),
                message=f"這一組有沒轉成欄的「{self.on}」：{{}}",
                detail=extra,
            )
        ]

    def describe(self) -> str:
        by = f"依 {quote_list(self.index)}" if self.index else "整個來源檔"
        return (
            f"樞紐（{by}）：「{self.on}」的 {quote_list(self.columns)} 變成欄，"
            f"值是「{self.values}」的{OP_LABELS[self.op]}"
        )
