"""自訂：用 Polars 的寫法算出新的一欄（docs/custom-expression-plan.md）。

使用者寫的是一條運算式（一段文字），**不執行**：``recipe/validator.py`` 的 ``ExpressionScan`` 用 ``ast``
讀成語法樹、照語法表檢查，轉成中間的樹；這裡再一個節點一個節點轉成 Polars 的運算式。能呼叫的 Polars 函式
只來自語法表（不在語法表的寫法在 ``ExpressionScan`` 就擋下了）。

和原本的 Polars 不一樣的地方：
- 小數是精確小數：寫的 ``0.9`` 是 1 位的小數；兩個小數相乘時先轉成位數相加的小數（結果完全精確）；
  ``/`` 的被除數先轉成「兩邊的位數與 6 取最大」的小數；``round`` 與 ``cast`` 讓位數變少時四捨五入。
- 除以 0 是空值（``/``、``//``、``%`` 的除數是 0 時換成空值），不會讓整次執行失敗。
- 結果的型別只會是 DESIGN.md §7.2.1 的那幾種：小的整數轉成 Int64、Float32 轉成 Float64。
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

import polars as pl
from pydantic import Field, field_validator

from tablade.i18n import en, t
from tablade.recipe.validator import EXPR_MAX_CHARS, ExpressionScan, syntax_catalog

from .base import (
    Example,
    NonEmpty,
    Step,
    StepError,
    WarningCheck,
    clicked_column,
    missing_columns,
)
from .registry import register

DESCRIBE_CHARS = 60
"""步驟清單上的說明最多顯示運算式的幾個字。"""
DECIMAL_MAX_SCALE = 18
DIVIDE_SCALE = 6
"""相除的結果至少幾位小數。"""
ROUND_MODE = "half_away_from_zero"
"""``round`` 沒有寫 ``mode`` 時：四捨五入（和「文字轉數字」一樣）。Polars 預設是四捨六入五取偶數。"""
_SMALL_INTS = (pl.Int8, pl.Int16, pl.Int32, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
_ARITHMETIC = ("+", "-", "*", "/", "//", "%")


def quote(name: str) -> str:
    """欄名放進 ``pl.col("…")`` 時加的反斜線。"""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_decimal(dtype: pl.DataType) -> bool:
    return isinstance(dtype, pl.Decimal)


def _is_exact(dtype: pl.DataType) -> bool:
    """整數或精確小數（整數當成 0 位的小數）。"""
    return dtype.is_integer() or _is_decimal(dtype)


def _scale(dtype: pl.DataType) -> int:
    return dtype.scale if isinstance(dtype, pl.Decimal) else 0  # type: ignore[return-value]


def _polars_error(err: Exception) -> StepError:
    """Polars 的錯誤只留第一行（後面是一大段執行計畫）。"""
    first = (str(err).strip().splitlines() or [""])[0]
    return StepError(t(f"運算式算不出來：{first}", f"The expression can't be computed: {first}"))


def _nonzero(divisor: pl.Expr) -> pl.Expr:
    """除數是 0 時換成空值：任何數除以空值是空值。不能寫成 when(除數 == 0).then(None).otherwise(左 / 右)：兩邊都會算。"""
    return pl.when(divisor != 0).then(divisor)


class _Builder:
    """把 ``ExpressionScan.tree`` 轉成 Polars 的運算式。要看型別的地方用 ``lf`` 的 schema（不用跑資料）。"""

    def __init__(self, lf: pl.LazyFrame) -> None:
        self.lf = lf

    def dtype(self, expr: pl.Expr) -> pl.DataType:
        try:
            return self.lf.select(expr.alias("x")).collect_schema()["x"]
        except pl.exceptions.PolarsError as err:
            raise _polars_error(err) from None

    def build(self, node: Any) -> pl.Expr:
        kind = node[0]
        if kind == "col":
            return pl.col(node[1])
        if kind == "lit":
            return pl.lit(node[2])
        if kind == "op":
            return self.op(node[1], self.build(node[2]), self.build(node[3]))
        if kind == "unary":
            operand = self.build(node[2])
            return -operand if node[1] == "neg" else ~operand
        if kind == "when":
            chain: Any = pl
            for condition, value in node[1]:
                chain = chain.when(self.build(condition)).then(self.build(value))
            return chain.otherwise(self.build(node[2])) if node[2] is not None else chain
        if kind == "call":
            return self.call(node[1], node[2], node[3], node[4])
        raise StepError(t(f"不認得的運算式：{kind}", f"Unknown expression: {kind}"))

    def op(self, id: str, a: pl.Expr, b: pl.Expr) -> pl.Expr:
        if id in _ARITHMETIC:
            da, db = self.dtype(a), self.dtype(b)
            if pl.String in (da, db) and not (id == "+" and da == db == pl.String):
                raise StepError(
                    t(
                        f"文字不能用 {id} 計算：數字請先用「文字轉數字」轉成數字（兩邊都是文字時 + 是接起來）",
                        f'Text can\'t be used with {id}: convert numbers with "Parse numbers" first '
                        "(+ joins two texts)",
                    )
                )
            if id == "*" and _is_decimal(da) and _is_decimal(db):
                scale = _scale(da) + _scale(db)
                if scale > DECIMAL_MAX_SCALE:
                    raise StepError(
                        t(
                            f"相乘的結果有 {scale} 位小數，超過 {DECIMAL_MAX_SCALE} 位：請先用 .round(…) 減少位數",
                            f"The product has {scale} decimal places, more than {DECIMAL_MAX_SCALE}: "
                            "use .round(…) first",
                        )
                    )
                target = pl.Decimal(38, scale)
                return a.cast(target) * b.cast(target)
            if id == "/" and _is_exact(da) and _is_exact(db):
                scale = max(_scale(da), _scale(db), DIVIDE_SCALE)
                return a.cast(pl.Decimal(38, scale)) / _nonzero(b)
            if id in ("/", "//", "%"):
                b = _nonzero(b)
            return {
                "+": a.__add__,
                "-": a.__sub__,
                "*": a.__mul__,
                "/": a.__truediv__,
                "//": a.__floordiv__,
                "%": a.__mod__,
            }[id](b)
        return {
            "==": a.__eq__,
            "!=": a.__ne__,
            ">": a.__gt__,
            ">=": a.__ge__,
            "<": a.__lt__,
            "<=": a.__le__,
            "&": a.__and__,
            "|": a.__or__,
        }[id](b)

    def value(self, arg: Any) -> pl.Expr:
        return self.build(arg)

    def call(self, id: str, receiver: Any, args: list[Any], kwargs: dict[str, Any]) -> pl.Expr:
        if receiver is None:
            return self.function(id, args, kwargs)
        expr = self.build(receiver)
        if id == "round":
            return self.round(expr, args[0], kwargs.get("mode", ROUND_MODE))
        if id == "cast":
            return self.cast(expr, args[0])
        if id in ("abs", "floor", "ceil", "is_null", "is_not_null"):
            return getattr(expr, id)()
        if id == "clip":
            return expr.clip(self.value(args[0]), self.value(args[1]))
        if id == "is_between":
            return expr.is_between(self.value(args[0]), self.value(args[1]))
        if id == "fill_null":
            return expr.fill_null(self.value(args[0]))
        if id == "is_in":
            return expr.is_in([lit[2] for lit in args[0]])
        space, _, name = id.partition(".")
        if space == "str":
            return self.text(expr.str, name, args, kwargs)
        if space == "dt":
            if name == "to_string":
                self.check_format(args[0])
                return expr.dt.to_string(args[0])
            return getattr(expr.dt, name)()
        raise StepError(t(f"不認得的寫法：{id}", f"Unknown syntax: {id}"))

    @staticmethod
    def text(ns: Any, name: str, args: list[Any], kwargs: dict[str, Any]) -> pl.Expr:
        if name in ("contains", "replace", "replace_all"):
            return getattr(ns, name)(*args, literal=True)
        if name in ("strip_chars", "strip_chars_start", "strip_chars_end"):
            return getattr(ns, name)(args[0] if args else None)
        return getattr(ns, name)(*args)

    @staticmethod
    def check_format(fmt: str) -> None:
        """日期的格式先用一個固定的日期試一次：錯的格式（例如 %Q）執行時才錯，整次執行會失敗。"""
        try:
            pl.Series([datetime.date(2026, 1, 1)]).dt.to_string(fmt)
        except (pl.exceptions.PolarsError, ValueError):
            raise StepError(
                t(f"日期的格式「{fmt}」不對", f'The date format "{fmt}" is not valid')
            ) from None

    def function(self, id: str, args: list[Any], kwargs: dict[str, Any]) -> pl.Expr:
        if id == "pl.lit":
            return pl.lit(args[0][2])
        if id == "pl.date":
            return pl.date(*args)
        if id == "pl.concat_str":
            return pl.concat_str(
                [self.value(a) for a in args[0]], separator=kwargs.get("separator", "")
            )
        if id in ("pl.coalesce", "pl.max_horizontal", "pl.min_horizontal", "pl.sum_horizontal"):
            return getattr(pl, id[3:])(*[self.value(a) for a in args])
        raise StepError(t(f"不認得的寫法：{id}", f"Unknown syntax: {id}"))

    def round(self, expr: pl.Expr, decimals: int, mode: str) -> pl.Expr:
        dtype = self.dtype(expr)
        if _is_decimal(dtype):
            if decimals >= _scale(dtype):
                return expr  # 位數比原本的多：不動
            return expr.round(decimals, mode=mode).cast(pl.Decimal(38, decimals))  # type: ignore[arg-type]
        return expr.round(decimals, mode=mode)  # type: ignore[arg-type]

    def cast(self, expr: pl.Expr, target: Any) -> pl.Expr:
        _, id, scale = target
        if id == "pl.Decimal":
            dtype = self.dtype(expr)
            if (_is_decimal(dtype) and _scale(dtype) > scale) or dtype.is_float():
                expr = expr.round(scale, mode=ROUND_MODE)  # 位數變少時四捨五入
            return expr.cast(pl.Decimal(38, scale), strict=False)
        return expr.cast(
            {"pl.Int64": pl.Int64, "pl.Float64": pl.Float64, "pl.String": pl.String}[id],
            strict=False,
        )

    def finish(self, expr: pl.Expr) -> pl.Expr:
        """結果的型別收斂到 DESIGN.md §7.2.1 的那幾種。"""
        if expr.meta.has_multiple_outputs():
            raise StepError(t("運算式只能算出一欄", "The expression must compute a single column"))
        dtype = self.dtype(expr)
        if dtype in _SMALL_INTS:
            return expr.cast(pl.Int64)
        if dtype == pl.Float32:
            return expr.cast(pl.Float64)
        if isinstance(dtype, pl.Duration):
            raise StepError(
                t(
                    "結果是一段時間（兩個日期相減）：請在後面接 .dt.total_days() 算出差幾天",
                    "The result is a duration (two dates subtracted): add .dt.total_days() to get "
                    "the number of days",
                )
            )
        if dtype in (pl.Int64, pl.Float64, pl.String, pl.Boolean, pl.Date, pl.Null) or _is_decimal(
            dtype
        ):
            return expr
        raise StepError(
            t(f"結果的型別 {dtype} 不支援", f"The result type {dtype} is not supported")
        )


def build(tree: Any, lf: pl.LazyFrame) -> pl.Expr:
    """``ExpressionScan.tree`` → Polars 的運算式（型別已經收斂）。"""
    builder = _Builder(lf)
    return builder.finish(builder.build(tree))


EXPRESSION_HELP = (
    '一條 Polars 寫法的運算式，例如 pl.col("金額") - pl.col("折扣")。只能用「插入語法」列出的寫法；'
    '欄位寫成 pl.col("欄名")，文字在 then、otherwise 這些地方要寫成 pl.lit("…")。'
)
EXPRESSION_HELP_EN = (
    'A Polars expression, like pl.col("amount") - pl.col("discount"). Only the syntax listed under '
    '"Insert syntax" can be used; write columns as pl.col("name"), and text in then, otherwise and '
    'similar places as pl.lit("…").'
)


@register
class CustomExpression(Step):
    """用 Polars 的寫法算出新的一欄（一列一列計算）：兩欄相加減、乘上比例、依條件給值、把幾欄接起來。運算式只會被讀、不會被執行，只能用「插入語法」列出的寫法。"""

    type: Literal["custom_expression"] = "custom_expression"
    name: NonEmpty = Field(title="新欄位名稱", json_schema_extra=en("New column name"))
    expression: str = Field(
        title="運算式",
        min_length=1,
        max_length=EXPR_MAX_CHARS,
        description=EXPRESSION_HELP,
        json_schema_extra={
            "ui": "expression",
            "ui_syntax": syntax_catalog(),
            **en("Expression", description=EXPRESSION_HELP_EN),
        },
    )
    warn_null: bool = Field(
        default=True,
        title="算出空值時產生警告",
        json_schema_extra=en("Warn when the result is null"),
    )

    label = "自訂"
    label_en = "Custom"
    doc_en = (
        "Compute a new column with a Polars expression, row by row: add or subtract columns, "
        "multiply by a ratio, pick a value by condition, join columns together. The expression is "
        'only read, never run, and only the syntax listed under "Insert syntax" can be used.'
    )
    category = "欄"
    effect = {"kind": "add", "names": "$name"}
    ai_notes = (
        "expression 是一條 Polars 的 Python 寫法運算式（文字），Tablade 只讀不執行，只接受語法表裡的寫法"
        "（見 references/steps.md 的「自訂的運算式可以用的寫法」）；name 是新欄位名稱，不能和現有的欄重複"
        "（要取代原本的欄，後面接 remove_columns、rename_columns）。"
        "只能一列一列算：合計、平均、排名、上一列（sum、mean、over、shift…）都不行，要合計用 group_by 再 join 回來。"
        "讀進來的欄都是文字，數字要先用 parse_number、日期要先用 parse_date 轉好再算；文字直接加減會報錯。"
        '欄位只能用 pl.col("欄名")（可以讀 __line_no 這類系統欄位）；then、otherwise、pl.concat_str、pl.coalesce、'
        'clip、is_between 這些位置的文字要寫成 pl.lit("…")（直接寫字串 Polars 會當成欄名，會被擋下）；'
        "比較、fill_null、+ 的另一邊可以直接寫字串。條件用 &、|、~，每個條件加括號，不能用 and、or、not。"
        "和 Polars 不一樣的地方：寫的小數（0.9）是精確小數，小數相乘位數相加（2 位 × 0.9 → 3 位）；"
        '/ 的結果至少 6 位小數；round 是四捨五入（要 Polars 預設的進位寫 mode="half_to_even"），'
        "小數 round 之後位數跟著變；除以 0 是空值。"
        ".str.contains、.str.replace、.str.replace_all 一定要寫 literal=True；.cast 一定要寫 strict=False，"
        "而且不能轉成日期或是／否。算出空值（而用到的欄都有值）時有警告，故意的（例如 pl.when 沒寫 otherwise）就把 warn_null 設成 false。"
    )
    examples = (
        Example(
            title="兩欄相減",
            params={"name": "淨額", "expression": 'pl.col("金額") - pl.col("折扣")'},
            input={"金額": [120, 80, 45], "折扣": [10, 0, 5]},
            output={"金額": [120, 80, 45], "折扣": [10, 0, 5], "淨額": [110, 80, 40]},
        ),
        Example(
            title="依條件給值",
            params={
                "name": "級距",
                "expression": (
                    'pl.when(pl.col("數量") >= 10).then(pl.lit("大量")).otherwise(pl.lit("一般"))'
                ),
            },
            input={"數量": [12, 3, 10]},
            output={"數量": [12, 3, 10], "級距": ["大量", "一般", "大量"]},
        ),
        Example(
            title="把幾欄接起來",
            params={
                "name": "單號",
                "expression": 'pl.concat_str([pl.col("門市代號"), pl.lit("-"), pl.col("序號")])',
            },
            input={"門市代號": ["N01", "S02", "N01"], "序號": ["0001", "0002", "0003"]},
            output={
                "門市代號": ["N01", "S02", "N01"],
                "序號": ["0001", "0002", "0003"],
                "單號": ["N01-0001", "S02-0002", "N01-0003"],
            },
        ),
    )

    @field_validator("expression")
    @classmethod
    def _check_expression(cls, value: str) -> str:
        """``recipe/validator.py`` 的 ``step_rules`` 有同樣的檢查（同一個 ``ExpressionScan``）。"""
        scan = ExpressionScan(value)
        if scan.error:
            raise ValueError(scan.error)
        return value

    @classmethod
    def suggest(cls, frame, context):
        column = clicked_column(frame, context)
        return {"expression": f"pl.col({quote(column)})"} if column else {}

    def scan(self) -> ExpressionScan:
        return ExpressionScan(self.expression)

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        scan = self.scan()
        if scan.error:  # 已經由 field_validator 擋下；保險起見
            raise StepError(scan.error)
        names = lf.collect_schema().names()
        missing = [c for c in scan.columns if c not in names]
        if missing:
            raise missing_columns(missing)
        expr = build(scan.tree, lf)
        out = lf.with_columns(expr.alias(self.name))
        temps = []
        if self.warn_null and scan.columns:
            result = pl.col(self.name)
            bad = result.is_null()
            if self.dtype_of(out).is_float():
                bad = bad | result.is_nan() | result.is_infinite()
            inputs = pl.all_horizontal([pl.col(c).is_not_null() for c in scan.columns])
            temp = self.temp("null")
            temps.append(temp)
            out = out.with_columns((bad & inputs).alias(temp))
        return out.select([*self.output_order(lf), *temps])

    def dtype_of(self, lf: pl.LazyFrame) -> pl.DataType:
        return lf.collect_schema()[self.name]

    def warning_checks(self) -> list[WarningCheck]:
        if not self.warn_null or not self.scan().columns:
            return []
        return [
            WarningCheck(
                condition=pl.col(self.temp("null")).fill_null(False),
                message=t(
                    f"「{self.name}」算出來是空值（用到的欄都有值）",
                    f'"{self.name}" came out null (the columns it uses all have values)',
                ),
            )
        ]

    def describe(self) -> str:
        text = " ".join(self.expression.split())
        if len(text) > DESCRIBE_CHARS:
            text = text[: DESCRIBE_CHARS - 1] + "…"
        return t(f"新增「{self.name}」＝ {text}", f'Add "{self.name}" = {text}')
