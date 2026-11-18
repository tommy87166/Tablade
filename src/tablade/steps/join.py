"""合併：結合（join）。「合併來源檔」在 files.py。

右表是前面某個階段的結果（引擎在套用前用 ``bind()`` 交給步驟，步驟自己不讀檔）。左邊還沒合併時，
每個來源檔各自和整張右表 join：join 是一列一列查表，結果和怎麼分檔無關（``per_file = False``）。
右表整份放進記憶體（hash table），左邊串流；``maintain_order="left"`` 讓左邊的順序與行號不變。
"""

from __future__ import annotations

from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from tablade.dtypes import type_label
from tablade.i18n import en, t

from .base import (
    LINE_NO,
    SOURCE_FILE,
    ColumnRef,
    Example,
    NonEmpty,
    Step,
    StepError,
    WarningCheck,
    collect_small,
    data_columns,
    pick_column,
    quote_list,
)
from .registry import register

HOW_LABELS = {"left": "左連接", "inner": "內連接", "anti": "檢查", "semi": "篩選"}
HOW_LABELS_EN = {"left": "Left join", "inner": "Inner join", "anti": "Check", "semi": "Filter"}
HOW_HINTS = {
    "left": "以這一張表為準，保留這一張表每一列，加入另一張表的欄。",
    "inner": "取交集，只留下兩張表都有的列，加入另一張表的欄。",
    "anti": "以這一張表為準，只留沒對到另一張表的列。",
    "semi": "以這一張表為準，只留有對到另一張表的列。",
}
HOW_HINTS_EN = {
    "left": "Keeps every row of this table and adds columns from the other table.",
    "inner": "Keeps only rows found in both tables and adds columns from the other table.",
    "anti": "Keeps only rows of this table that have no match in the other table.",
    "semi": "Keeps only rows of this table that have a match in the other table.",
}
HOW_ICONS = {
    "left": "venn_left",
    "inner": "venn_inner",
    "anti": "venn_left_only",
    "semi": "venn_inner_filter",
}
"""四宮格的文氏圖（前端 OptionIcon）：左邊的圓是這一張表、右邊是另一張表，虛線代表不帶另一張表的欄。"""
BRINGS = ("left", "inner")
SEPARATOR = "\x1f"
"""好幾個鍵接成一個字串比對時的分隔字元（資料裡不會有）。"""
SHOWN_DUPLICATES = 5


class JoinKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, title="鍵", json_schema_extra=en("Key"))

    left: ColumnRef = Field(title="這一張表", json_schema_extra=en("This table"))
    right: NonEmpty = Field(
        title="另一張表", json_schema_extra={"ui": "right_column", **en("Other table")}
    )


@register
class Join(Step):
    """結合：依兩張表相同鍵值進行結合。"""

    type: Literal["join"] = "join"
    right: NonEmpty = Field(
        title="另一張表（來自前面階段）",
        json_schema_extra={"ui": "stage", **en("Other table (from an earlier stage)")},
    )
    on: list[JoinKey] = Field(
        min_length=1,
        title="鍵值",
        description="這一張表的欄＝另一張表的欄；可以有好幾組（全部相同才算對到）",
        json_schema_extra=en(
            "Keys",
            description="Column in this table = column in the other table; "
            "add several pairs to match on all of them",
        ),
    )
    how: Literal["left", "inner", "anti", "semi"] = Field(
        default="left",
        title="方式",
        json_schema_extra={
            "ui": "option_cards",
            "ui_labels": HOW_LABELS,
            "ui_option_hints": HOW_HINTS,
            "ui_option_icons": HOW_ICONS,
            **en("Join kind", ui_labels=HOW_LABELS_EN, ui_option_hints=HOW_HINTS_EN),
        },
    )
    columns: list[NonEmpty] = Field(
        default_factory=list,
        title="要帶進來的欄",
        json_schema_extra={
            "ui": "right_columns",
            "ui_show_if": {"how": list(BRINGS)},
            **en("Columns to bring in"),
        },
    )
    warn_unmatched: bool = Field(
        default=True,
        title="沒對到的列產生警告",
        description="當這一張表的鍵值在另一張表找不到時，產生警告",
        json_schema_extra={
            "ui_show_if": {"how": list(BRINGS)},
            **en(
                "Warn about unmatched rows",
                description="Warns when a key in this table isn't found in the other table",
            ),
        },
    )

    label = "結合"
    label_en = "Join"
    doc_en = "Join: combines two tables on matching key values."
    category = "合併"
    effect = {"kind": "join", "stage": "$right", "how": "$how", "names": "$columns", "on": "$on"}
    ai_notes = (
        "right 是前面某個階段的名稱（只能引用前面的階段），另一張表就是那個階段的結果。"
        "on 每一組是 {left: 這一張表的欄, right: 另一張表的欄}，兩邊型別要一樣（文字對文字、日期對日期）；"
        "鍵是空值的不算對到。how：left 保留每一列、inner 只留對得到的、anti 只留沒對到的、semi 只留有對到的；"
        "columns 列出要帶進來的另一張表的欄（不能是鍵值、不能和這一張表的欄同名；anti / semi 不帶欄）。"
        "另一張表的鍵值重複時直接報錯（不會默默多出列），要先在那個階段移除重複。"
        "warn_unmatched（預設 true）讓 left / inner 沒對到的列產生附行號的警告。"
        "合併來源檔之前每個來源檔各自和整張另一張表 join，順序與行號不變。"
    )
    examples = (
        Example(
            title="依門市代號補上門市名稱",
            params={
                "right": "門市對照",
                "on": [{"left": "門市代號", "right": "代號"}],
                "columns": ["門市名稱"],
            },
            input={"門市代號": ["A01", "B02", "A01", "C09"], "金額": [100, 50, 30, 80]},
            tables={"門市對照": {"代號": ["A01", "B02"], "門市名稱": ["北區門市", "中區門市"]}},
            output={
                "門市代號": ["A01", "B02", "A01", "C09"],
                "金額": [100, 50, 30, 80],
                "門市名稱": ["北區門市", "中區門市", "北區門市", None],
            },
        ),
    )

    _right_keys: pl.Series | None = PrivateAttr(default=None)

    @classmethod
    def suggest(cls, frame, context):
        """右表預設用最近的一個前面階段；鍵用使用者點的欄（或兩邊第一個同名的欄）。"""
        stages: dict[str, list[str] | None] = context.get("stages") or {}
        if not stages:
            return {}
        right = list(stages)[-1]
        right_columns = stages[right] or []
        left = pick_column(frame, context)
        same = [c for c in data_columns(frame.lazy()) if c in right_columns]
        if context.get("column") not in frame.columns and same:
            left = same[0]
        if left is None:
            return {"right": right}
        key = left if left in right_columns else (right_columns[0] if right_columns else left)
        bring = [c for c in right_columns if c != key and c not in frame.columns]
        return {"right": right, "on": [{"left": left, "right": key}], "columns": bring}

    def stage_refs(self) -> list[str]:
        return [self.right]

    def _bring(self) -> list[str]:
        return list(dict.fromkeys(self.columns)) if self.how in BRINGS else []

    def _check(self, lf: pl.LazyFrame, right: pl.LazyFrame) -> list[pl.DataType]:
        """欄位存在、鍵的型別一樣；回傳每一組鍵比對時要用的型別。"""
        lefts, rights = [k.left for k in self.on], [k.right for k in self.on]
        missing = [c for c in dict.fromkeys(lefts) if c not in data_columns(lf)]
        if missing:
            raise StepError(
                t(
                    f"欄位不存在：{'、'.join(missing)}",
                    f"{'Column' if len(missing) == 1 else 'Columns'} not found: {', '.join(missing)}",
                )
            )
        bring = self._bring()
        keyed = [c for c in bring if c in rights]
        if keyed:
            raise StepError(
                t(
                    f"「{'、'.join(keyed)}」是另一張表的鍵值，不用帶進來",
                    f"No need to bring in the other table's keys: {', '.join(keyed)}",
                )
            )
        right_names = data_columns(right)
        missing = [c for c in dict.fromkeys(rights + bring) if c not in right_names]
        if missing:
            raise StepError(
                t(
                    f"@{self.right} 沒有欄位：{'、'.join(missing)}（它的欄位：{quote_list(right_names, 12)}）",
                    f"Not found in @{self.right}: {', '.join(missing)} "
                    f"(its columns: {quote_list(right_names, 12)})",
                )
            )
        left_schema, right_schema = lf.collect_schema(), right.collect_schema()
        types = []
        for key in self.on:
            a, b = left_schema[key.left], right_schema[key.right]
            if a != b and pl.Null not in (a, b):
                raise StepError(
                    t(
                        f"鍵的型別不一樣：「{key.left}」是{type_label(a)}，@{self.right} 的「{key.right}」"
                        f"是{type_label(b)}；請先轉成一樣的型別",
                        f'Key types differ: "{key.left}" is {type_label(a)}, but "{key.right}" '
                        f"in @{self.right} is {type_label(b)}; convert them to the same type first",
                    )
                )
            types.append(b if a == pl.Null else a)
        return types

    def _check_duplicates(self, right: pl.LazyFrame) -> None:
        """右表的鍵重複就報錯（join 會默默多出列）；鍵有空值的列不會對到，不算。"""
        rights = list(dict.fromkeys(k.right for k in self.on))
        names = right.collect_schema().names()
        where = [pl.col(SOURCE_FILE).first().alias("file")] if SOURCE_FILE in names else []
        if LINE_NO in names:
            where.append(pl.col(LINE_NO).head(3).alias("lines"))
        found = collect_small(
            right.filter(pl.all_horizontal(pl.col(c).is_not_null() for c in rights))
            .group_by(rights)
            .agg(pl.len().alias("n"), *where)
            .filter(pl.col("n") > 1)
            .head(SHOWN_DUPLICATES)
        )
        if found.is_empty():
            return
        shown = []
        for row in found.iter_rows(named=True):
            value = t("、", ", ").join(str(row[c]) for c in rights)
            place = ""
            if "lines" in row:
                lines = t("、", ", ").join(str(n) for n in row["lines"])
                file = row.get("file") or ""
                line_word = "line" if len(row["lines"]) == 1 else "lines"
                place = t(
                    f"（{row.get('file') or ''} 第 {lines} 行）".replace("（ ", "（"),
                    f" ({file}, {line_word} {lines})" if file else f" ({line_word} {lines})",
                )
            shown.append(t(f"「{value}」{place}", f'"{value}"{place}'))
        raise StepError(
            t(
                f"@{self.right} 的鍵「{'、'.join(rights)}」重複：{'；'.join(shown)}。"
                f"請先在階段「{self.right}」移除重複或修正資料",
                f'Key "{", ".join(rights)}" has duplicates in @{self.right}: {"; ".join(shown)}. '
                f'Remove duplicates or fix the data in stage "{self.right}" first',
            )
        )

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        right = self.table(self.right)
        types = self._check(lf, right)
        self._check_duplicates(right)
        left_keys = [self.temp(f"l{i}") for i in range(len(self.on))]
        right_keys = [self.temp(f"r{i}") for i in range(len(self.on))]
        keyed = lf.with_columns(
            pl.col(k.left).cast(t).alias(name)
            for k, t, name in zip(self.on, types, left_keys, strict=True)
        )
        table = right.select(
            *[
                pl.col(k.right).cast(t).alias(name)
                for k, t, name in zip(self.on, types, right_keys, strict=True)
            ],
            *[pl.col(c) for c in self._bring()],
        )
        joined = keyed.join(
            table,
            left_on=left_keys,
            right_on=right_keys,
            how=self.how,
            maintain_order="left",
            coalesce=True,
        )
        return joined.select(self.output_order(lf))

    def _key(self, columns: list[str]) -> pl.Expr:
        parts = [pl.col(c).cast(pl.String) for c in columns]
        return parts[0] if len(parts) == 1 else pl.concat_str(parts, separator=SEPARATOR)

    def warning_checks(self) -> list[WarningCheck]:
        if self.how not in BRINGS or not self.warn_unmatched:
            return []
        if self._right_keys is None:
            rights = [k.right for k in self.on]
            right = self.table(self.right)
            missing = [c for c in rights if c not in data_columns(right)]
            if missing:
                return []  # 套用時會報「沒有欄位」
            self._right_keys = collect_small(
                right.select(self._key(rights).alias("key")).drop_nulls().unique()
            ).to_series()
        lefts = [k.left for k in self.on]
        key = self._key(lefts)
        shown = pl.concat_str(
            [pl.col(c).cast(pl.String).fill_null(t("（空值）", "(null)")) for c in lefts],
            separator=t("、", ", "),
        )
        return [
            WarningCheck(
                condition=key.is_null() | ~key.is_in(self._right_keys.implode()),
                message=t(
                    f"「{'、'.join(lefts)}」的「{{}}」在 @{self.right} 找不到",
                    f'"{", ".join(lefts)}" value "{{}}" not found in @{self.right}',
                ),
                detail=shown,
                on_input=True,
            )
        ]

    def describe(self) -> str:
        keys = t("、", ", ").join(
            k.left if k.left == k.right else t(f"{k.left}＝{k.right}", f"{k.left} = {k.right}")
            for k in self.on
        )
        if self.how == "anti":
            return t(
                f"只留在 @{self.right} 找不到的列（{keys}）",
                f"Keep rows not found in @{self.right} ({keys})",
            )
        if self.how == "semi":
            return t(
                f"只留在 @{self.right} 找得到的列（{keys}）",
                f"Keep rows found in @{self.right} ({keys})",
            )
        way = t("左連接", "left join") if self.how == "left" else t("內連接", "inner join")
        if self.columns:
            bring = t(
                f"：帶進 {quote_list(self.columns)}", f": bring in {quote_list(self.columns)}"
            )
        else:
            bring = ""
        return t(
            f"結合（{way} @{self.right}，{keys}）{bring}",
            f"Join ({way} with @{self.right} on {keys}){bring}",
        )
