"""彙總：取消樞紐、群組彙總、樞紐（DESIGN.md §7.2）。

和其他步驟一樣，合併來源檔之前每個來源檔各自處理（讀 Excel 全部工作表時每個工作表各自處理），
合併之後才是整份一起處理。輸出的列不再一對一對應輸入的列（``reshapes = True``）：
- 群組彙總、樞紐：每一組一列，照每組第一次出現的順序；``__line_no`` 是這一組第一筆的行號。
- 取消樞紐：每一列展開成連續的幾列，都保留原本的行號。
全部用 Polars 原生運算，可以串流（tests/test_review_steps.py 檢查串流的執行計畫沒有退回整份放進記憶體）；
排序只排彙總後的少量資料（列數＝組數）。
"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from tablade.i18n import en, t

from .base import (
    LINE_NO,
    POSITION,
    ROW_INDEX,
    VALUE,
    ColumnRef,
    ColumnRefs,
    Example,
    NonEmpty,
    Step,
    StepError,
    WarningCheck,
    clicked_column,
    data_columns,
    data_values,
    is_blank,
    pick_column,
    quote_list,
    system_columns,
    too_many_values,
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
OP_LABELS_EN = {
    "sum": "Sum",
    "mean": "Average",
    "max": "Max",
    "min": "Min",
    "count": "Count of values",
    "n_unique": "Count distinct",
    "first": "First",
    "last": "Last",
}
PivotOp = Literal["sum", "mean", "max", "min", "count", "first", "last"]
NUMERIC_OPS = {"sum", "mean"}
KEY_TYPES = (pl.String, pl.Date, pl.Null)
"""分組欄（群組彙總的 by、樞紐的 index）只能是文字或日期。"""


def _dtype_text(dtype: pl.DataType) -> str:
    if dtype.is_numeric():
        return t("數字", "number")
    if dtype.is_temporal():
        return t("日期", "date")
    return t("文字", "text") if dtype == pl.String else str(dtype)


def check_columns(lf: pl.LazyFrame, columns: list[str]) -> pl.Schema:
    missing = [c for c in dict.fromkeys(columns) if c not in data_columns(lf)]
    if missing:
        raise StepError(
            t(
                f"欄位不存在：{'、'.join(missing)}",
                f"{'Column' if len(missing) == 1 else 'Columns'} not found: {', '.join(missing)}",
            )
        )
    return lf.collect_schema()


def check_keys(schema: pl.Schema, keys: list[str], what: str) -> None:
    bad = [
        t(f"{k}（{_dtype_text(schema[k])}）", f"{k} ({_dtype_text(schema[k])})")
        for k in keys
        if schema[k] not in KEY_TYPES
    ]
    if bad:
        raise StepError(
            t(
                f"{what}只能是文字或日期欄：{'、'.join(bad)}",
                f"{what} must be text or date columns: {', '.join(bad)}",
            )
        )


def check_numeric(schema: pl.Schema, column: str, op: str) -> None:
    if op in NUMERIC_OPS and not schema[column].is_numeric():
        raise StepError(
            t(
                f"「{column}」是{_dtype_text(schema[column])}，{OP_LABELS[op]}要先用「文字轉數字」",
                f'"{column}" is a {_dtype_text(schema[column])} column; '
                f'to use {OP_LABELS_EN[op]}, first convert it with "Text to number"',
            )
        )


def aggregate(expr: pl.Expr, op: str) -> pl.Expr:
    return {
        "sum": expr.sum(),
        "mean": expr.mean(),
        "max": expr.max(),
        "min": expr.min(),
        "count": expr.count().cast(pl.Int64),
        # 和「有值的筆數」一樣不算空值（空值不是一個值）
        "n_unique": expr.drop_nulls().n_unique().cast(pl.Int64),
        "first": expr.first(),
        "last": expr.last(),
    }[op]


def group_rows(lf: pl.LazyFrame, keys: list[str], aggs: list[pl.Expr]) -> pl.LazyFrame:
    """依 keys 分組（沒有 keys 就整份一組），照每組第一次出現的順序。

    系統欄位：__source_file、__sheet、__stage 在同一個來源裡是固定值；__line_no 是這一組第一筆的行號，
    __orig_*（合併前的位置）也取這一組第一筆的；位置欄（串流時回報進度用）取這一組最後一筆的位置。
    """
    names = lf.collect_schema().names()
    system = []
    for c in system_columns(names):
        if c == LINE_NO:
            system.append(pl.col(LINE_NO).min())
        else:
            # __source_file、__sheet、__stage 在同一個來源裡是固定值；__orig_*（合併前的位置）取這一組
            # 第一筆的：每個來源已依行號排好，第一筆就是行號最小的那一筆（arg_min 串流引擎不支援）
            system.append(pl.col(c).first())
    if POSITION in names:
        system.append(pl.col(POSITION).max())
    if not keys:
        # 整份彙總成一列；沒有資料時（行號是空的）不產生彙總列
        return lf.select(*system, *aggs).filter(pl.col(LINE_NO).is_not_null())
    # 照每組第一筆的「位置」排序，不是行號：取消樞紐之後同一個行號有好幾列，
    # 用行號排分不出先後，每次執行的順序都可能不同
    return (
        lf.with_row_index(ROW_INDEX)
        .group_by(keys)
        .agg(pl.col(ROW_INDEX).min(), *system, *aggs)
        .sort(ROW_INDEX)
        .drop(ROW_INDEX)
    )


# ── 取消樞紐 ───────────────────────────────────────────────────


@register
class Unpivot(Step):
    """取消樞紐（寬轉長）：把好幾欄攤開成「名稱、值」兩欄，每一列展開成好幾列。"""

    type: Literal["unpivot"] = "unpivot"
    columns: ColumnRefs = Field(
        title="要攤開的欄位",
        description="例如 1月、2月、3月",
        json_schema_extra=en("Columns to unpivot", description="For example Jan, Feb, Mar"),
    )
    name_column: NonEmpty = Field(
        default="名稱",  # i18n-ignore：預設的新欄名會寫進 Recipe
        title="放原本欄名的新欄",
        json_schema_extra=en("New column for column names"),
    )
    value_column: NonEmpty = Field(
        default="值",  # i18n-ignore：預設的新欄名會寫進 Recipe
        title="放值的新欄",
        json_schema_extra=en("New column for values"),
    )
    skip_empty: bool = Field(
        default=False, title="值是空白的不展開", json_schema_extra=en("Skip blank values")
    )

    label = "取消樞紐"
    label_en = "Unpivot"
    doc_en = (
        'Unpivot (wide to long): turn several columns into two columns, "name" and "value"; '
        "each row expands into several rows."
    )
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
        column = clicked_column(frame, context)
        return {"columns": [column]} if column else {}

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
        return t(
            f"取消樞紐：{quote_list(self.columns)} → 「{self.name_column}」「{self.value_column}」",
            f'Unpivot: {quote_list(self.columns)} → "{self.name_column}", "{self.value_column}"',
        )


# ── 群組彙總 ───────────────────────────────────────────────────


class Aggregation(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, title="彙總", json_schema_extra=en("Aggregation")
    )

    column: ColumnRef = Field(title="欄位", json_schema_extra=en("Column"))
    op: Op = Field(
        default="sum",
        title="方式",
        json_schema_extra={
            "ui_labels": OP_LABELS,
            **en("Operation", ui_labels=OP_LABELS_EN),
        },
    )
    name: NonEmpty = Field(title="新欄名", json_schema_extra=en("New column name"))


@register
class GroupBy(Step):
    """群組彙總：依分組欄分組，算每一組的合計、平均、筆數…；不選分組欄就是整個來源檔彙總成一列。"""

    type: Literal["group_by"] = "group_by"
    by: list[NonEmpty] = Field(
        default_factory=list,
        title="分組欄",
        description="只能是文字或日期欄；不選代表整個來源檔彙總成一列",
        json_schema_extra={
            "ui": "columns",
            **en(
                "Group by",
                description="Text or date columns only; "
                "leave empty to summarize the whole source file into one row",
            ),
        },
    )
    aggregations: list[Aggregation] = Field(
        default_factory=list, title="彙總", json_schema_extra=en("Aggregations")
    )
    count_as: str = Field(
        default="筆數",  # i18n-ignore：預設的新欄名會寫進 Recipe
        title="另外加一欄筆數",
        description="這一組有幾列；空白代表不加",
        json_schema_extra=en(
            "Add a row count column",
            description="Number of rows in each group; leave blank to skip",
        ),
    )

    label = "群組彙總"
    label_en = "Group and summarize"
    doc_en = (
        "Group and summarize: group rows by the group-by columns and compute each group's "
        "sum, average, row count…; with no group-by columns, the whole source file "
        "is summarized into one row."
    )
    per_file = True
    whole_source = True
    category = "彙總"
    reshapes = True
    effect = {"kind": "group", "by": "$by", "aggregations": "$aggregations", "count": "$count_as"}
    ai_notes = (
        "合併來源檔之前每個來源檔各自分組；要整份一起彙總先用 merge_files。by 只能是文字或日期欄，"
        "空清單代表整個來源檔彙總成一列。op：sum、mean 只能用在數字欄（先用 parse_number），"
        "count 是有值的筆數、n_unique 是不同值個數（兩個都不算空值；空字串算一個值）；count_as 另外加一欄這一組有幾列（空字串代表不加）。"
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
        column = clicked_column(frame, context)
        by = [column] if column and frame.schema[column] in KEY_TYPES else []
        numbers = [c for c, t in frame.schema.items() if not c.startswith("__") and t.is_numeric()]
        aggregations = [  # i18n-ignore：建議的新欄名會寫進 Recipe
            {"column": c, "op": "sum", "name": f"{c}合計"} for c in numbers[:1]
        ]
        return {"by": by, "aggregations": aggregations}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        by = list(dict.fromkeys(self.by))
        schema = check_columns(lf, by + [a.column for a in self.aggregations])
        check_keys(schema, by, t("分組欄", "Group by columns"))
        aggs = []
        for a in self.aggregations:
            check_numeric(schema, a.column, a.op)
            aggs.append(aggregate(pl.col(a.column), a.op).alias(a.name))
        if self.count_as:
            aggs.append(pl.len().cast(pl.Int64).alias(self.count_as))
        return group_rows(lf, by, aggs).select(self.reshape_order(lf))

    def describe(self) -> str:
        what = [
            t(f"{OP_LABELS[a.op]}({a.column})", f"{OP_LABELS_EN[a.op]}({a.column})")
            for a in self.aggregations
        ]
        if self.count_as:
            what.append(t("筆數", "Row count"))
        if self.by:
            by = t(f"依 {quote_list(self.by)} 分組", f"by {quote_list(self.by)}")
        else:
            by = t("整個來源檔", "whole source file")
        return t(
            f"群組彙總（{by}）：{quote_list(what) or '沒有彙總'}",
            f"Group and summarize ({by}): {quote_list(what) or 'no aggregations'}",
        )


# ── 樞紐 ──────────────────────────────────────────────────────


@register
class Pivot(Step):
    """樞紐（長轉寬）：把某一欄的值變成新的欄名，每一組一列；欄名在建立步驟時從資料讀出。"""

    type: Literal["pivot"] = "pivot"
    index: list[NonEmpty] = Field(
        default_factory=list,
        title="樞紐表格列依據欄位",
        description="只能是文字或日期欄；不選代表整個來源檔一列",
        json_schema_extra={
            "ui": "columns",
            **en(
                "Row fields",
                description="Text or date columns only; leave empty for one row per source file",
            ),
        },
    )
    on: ColumnRef = Field(title="樞紐表格欄依據欄位", json_schema_extra=en("Column field"))
    values: ColumnRef = Field(title="樞紐表格值依據欄位", json_schema_extra=en("Value field"))
    op: PivotOp = Field(
        default="sum",
        title="彙總方式",
        json_schema_extra={
            "ui_labels": {k: v for k, v in OP_LABELS.items() if k != "n_unique"},
            **en(
                "Summarize by",
                ui_labels={k: v for k, v in OP_LABELS_EN.items() if k != "n_unique"},
            ),
        },
    )
    columns: list[NonEmpty] = Field(
        min_length=1,
        title="要變成欄的值",
        description="從「樞紐表格欄依據欄位」的資料讀出；資料裡多出來的值會產生警告",
        json_schema_extra={
            "ui": "data_values",
            "ui_from": "on",
            **en(
                "Values to turn into columns",
                description='Read from the data in "Column field"; '
                "values in the data that aren't listed produce a warning",
            ),
        },
    )

    label = "樞紐"
    label_en = "Pivot"
    doc_en = (
        "Pivot (long to wide): turn the values of one column into new column names, "
        "one row per group; the column names are read from the data when the step is created."
    )
    per_file = True
    whole_source = True
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
        "（空值與空白也算）產生警告、不轉成欄。合併來源檔之前每個來源檔各自處理。index 只能是文字或日期欄，空清單代表整個"
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
        found = data_values(frame, on)
        # 超過上限就不帶入（表單上會說明原因），不默默只取前面幾個
        return {"on": on, "columns": [] if too_many_values(on, len(found)) else found}

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        index = list(dict.fromkeys(self.index))
        schema = check_columns(lf, [*index, self.on, self.values])
        check_keys(schema, index, t("樞紐表格列依據欄位", "Row fields"))
        check_numeric(schema, self.values, self.op)
        on, values = pl.col(self.on).cast(pl.String), pl.col(self.values)
        aggs = []
        for name in self.columns:
            match = on == name
            if self.op in ("first", "last"):  # 這一組第一筆（最後一筆）是這個值的列
                value = aggregate(values.filter(match), self.op)
            else:
                # when/then 而不是 filter：串流時 filter 會把資料另外分給每一欄各算一次，記憶體多很多
                value = aggregate(pl.when(match).then(values), self.op)
            # 這一組沒有這個值的任何一筆：空值（合計不會變成 0）
            aggs.append(pl.when(match.any()).then(value).otherwise(None).alias(name))
        # 沒轉成欄的值（空值與空白也算：那些列的值不在任何一欄）：記這一組第一個、共幾種給警告用。
        # 不把全部的值接成一串（unique ＋ str.join）：串流引擎不支援，會整份放進記憶體
        extra, extra_n = self.temp("extra"), self.temp("extra_n")
        shown = pl.when(is_blank(on)).then(pl.lit(t("（空白）", "(blank)"))).otherwise(on)
        unknown = pl.when(on.is_null() | ~on.is_in(list(self.columns))).then(shown).drop_nulls()
        aggs.append(unknown.first().alias(extra))
        aggs.append(unknown.n_unique().cast(pl.Int64).alias(extra_n))
        return group_rows(lf, index, aggs).select(self.reshape_order(lf, [extra, extra_n]))

    def warning_checks(self):
        extra, count = pl.col(self.temp("extra")), pl.col(self.temp("extra_n"))
        more = pl.concat_str(
            [extra, pl.lit(t("（共 ", " (")), count.cast(pl.String), pl.lit(t(" 種）", " in all)"))]
        )
        return [
            WarningCheck(
                condition=extra.is_not_null(),
                message=t(
                    f"這一組有沒轉成欄的「{self.on}」：{VALUE}",
                    f'This group has "{self.on}" values not turned into columns: {VALUE}',
                ),
                detail=pl.when(count > 1).then(more).otherwise(extra),
            )
        ]

    def describe(self) -> str:
        if self.index:
            by = t(f"依 {quote_list(self.index)}", f"by {quote_list(self.index)}")
        else:
            by = t("整個來源檔", "whole source file")
        return t(
            f"樞紐（{by}）：「{self.on}」的 {quote_list(self.columns)} 變成欄，"
            f"值是「{self.values}」的{OP_LABELS[self.op]}",
            f'Pivot ({by}): {quote_list(self.columns)} of "{self.on}" become columns; '
            f'values: {OP_LABELS_EN[self.op]} of "{self.values}"',
        )
