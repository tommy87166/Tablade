"""自訂（custom_expression）：轉成 Polars 的運算式（steps/custom.py）。

docs/custom-expression-plan.md 的「測試」第 1、2、5、6、7 節。只讀不執行的檢查在 tests/test_expression_scan.py。
"""

from __future__ import annotations

from decimal import Decimal

import polars as pl
import pytest
from pydantic import ValidationError

from tablade.engine.run import apply_step
from tablade.fixtures import EXPRESSION_TYPES, expression_rows
from tablade.i18n import localize_schema, strip_en, using
from tablade.recipe import validator
from tablade.recipe.spec import build_spec
from tablade.recipe.validator import SYNTAX, ExpressionScan
from tablade.steps import get
from tablade.steps.base import LINE_NO, SOURCE_FILE, StepError
from tablade.steps.custom import CustomExpression, build

DTYPES = {
    "文字": pl.String,
    "整數": pl.Int64,
    "小數 2 位": pl.Decimal(38, 2),
    "日期": pl.Date,
    "浮點數": pl.Float64,
}


def data() -> pl.DataFrame:
    rows = expression_rows()
    schema = {name: DTYPES[EXPRESSION_TYPES[name]] for name in EXPRESSION_TYPES}
    df = pl.DataFrame(rows, schema={**schema, LINE_NO: pl.Int64})
    return df.with_columns(pl.lit("a.txt").alias(SOURCE_FILE)).select(
        SOURCE_FILE, LINE_NO, *EXPRESSION_TYPES
    )


def make(expression: str, **params) -> CustomExpression:
    return get("custom_expression").model_validate(
        {"id": "t", "type": "custom_expression", "name": "x", "expression": expression, **params}
    )


def run(expression: str, df: pl.DataFrame | None = None, **params):
    out, _, warnings = apply_step(make(expression, **params), data() if df is None else df)
    return out, warnings


def D(text: str) -> Decimal:
    return Decimal(text)


N = None
DEC = lambda s: pl.Decimal(38, s)  # noqa: E731
STR, INT, BOOL, FLOAT, NULL = pl.String, pl.Int64, pl.Boolean, pl.Float64, pl.Null

# （測的 id, 運算式, 預期的四列, 型別）：照計畫的規則、用 Polars 2.1.0 算出來的
CASES = [
    # 運算子
    (["+"], 'pl.col("單價") + pl.col("折扣")', [D("12.50"), D("130.05"), D("20.54"), N], DEC(2)),
    (["+", "lit.str"], 'pl.col("代號") + "-x"', ["a1-x", "B22-x", "c-x", N], STR),
    (["-"], 'pl.col("單價") - pl.col("折扣")', [D("12.50"), D("109.95"), D("19.44"), N], DEC(2)),
    (["*"], 'pl.col("單價") * pl.col("數量")', [D("37.50"), D("1200.00"), D("0.00"), N], DEC(2)),
    (
        ["*", "lit.decimal"],
        'pl.col("單價") * 0.9',
        [D("11.250"), D("108.000"), D("17.991"), N],
        DEC(3),
    ),
    (
        ["*"],
        'pl.col("單價") * pl.col("折扣")',
        [D("0.0000"), D("1206.0000"), D("10.9945"), N],
        DEC(4),
    ),
    (["*", "lit.int"], 'pl.col("評分") * 2', [0.25, 5.0, -5.0, N], FLOAT),
    (["/"], 'pl.col("單價") / pl.col("數量")', [D("4.166667"), D("12.000000"), N, N], DEC(6)),
    (["/"], 'pl.col("數量") / 4', [D("0.750000"), D("2.500000"), D("0.000000"), N], DEC(6)),
    (["//"], 'pl.col("數量") // 4', [0, 2, 0, N], INT),
    (["%"], 'pl.col("數量") % 4', [3, 2, 0, N], INT),
    (["%"], '10 % pl.col("數量")', [1, 0, N, N], INT),
    (["neg"], '-pl.col("折扣")', [D("0.00"), D("-10.05"), D("-0.55"), N], DEC(2)),
    (["=="], 'pl.col("數量") == 3', [True, False, False, N], BOOL),
    (["!="], 'pl.col("數量") != 3', [False, True, True, N], BOOL),
    ([">"], 'pl.col("數量") > 3', [False, True, False, N], BOOL),
    ([">="], 'pl.col("數量") >= 3', [True, True, False, N], BOOL),
    (["<"], 'pl.col("數量") < 3', [False, False, True, N], BOOL),
    (["<="], 'pl.col("數量") <= 3', [True, False, True, N], BOOL),
    (["&"], '(pl.col("數量") > 1) & (pl.col("單價") > 15)', [False, True, False, N], BOOL),
    (["|"], '(pl.col("數量") > 5) | pl.col("門市").is_null()', [False, True, False, True], BOOL),
    (["~"], '~(pl.col("數量") > 1)', [False, False, True, N], BOOL),
    # 欄位與字面值
    (["pl.col"], 'pl.col("數量")', [3, 10, 0, N], INT),
    (["pl.col"], 'pl.col("__line_no") * 10', [20, 30, 40, 50], INT),
    (["pl.lit", "lit.str"], 'pl.lit("固定")', ["固定"] * 4, STR),
    (["pl.lit", "lit.decimal"], "pl.lit(1.5)", [D("1.5")] * 4, DEC(1)),
    (["lit.int"], 'pl.col("數量") + 1', [4, 11, 1, N], INT),
    (["False"], 'pl.col("門市").is_null() == False', [True, True, True, False], BOOL),
    (["True"], "pl.lit(True)", [True] * 4, BOOL),
    (["None"], "pl.lit(None)", [N] * 4, NULL),
    (["pl.date"], 'pl.col("日期") >= pl.date(2026, 2, 1)', [False, True, True, N], BOOL),
    # pl. 的函式
    (
        ["pl.when"],
        'pl.when(pl.col("數量") >= 10).then(pl.lit("大量"))'
        '.when(pl.col("數量") >= 1).then(pl.lit("一般")).otherwise(pl.lit("無"))',
        ["一般", "大量", "無", "無"],
        STR,
    ),
    (
        ["pl.when"],
        'pl.when(pl.col("數量") >= 10).then(pl.col("單價"))',
        [N, D("120.00"), N, N],
        DEC(2),
    ),
    (
        ["pl.concat_str"],
        'pl.concat_str([pl.col("代號"), pl.lit("-"), pl.col("數量")])',
        ["a1-3", "B22-10", "c-0", N],
        STR,
    ),
    (
        ["pl.concat_str"],
        'pl.concat_str([pl.col("代號"), pl.col("數量")], separator="/")',
        ["a1/3", "B22/10", "c/0", N],
        STR,
    ),
    (
        ["pl.coalesce"],
        'pl.coalesce(pl.col("折扣"), 0)',
        [D("0.00"), D("10.05"), D("0.55"), D("0.00")],
        DEC(2),
    ),
    (
        ["pl.max_horizontal"],
        'pl.max_horizontal(pl.col("單價"), 20)',
        [D("20.00"), D("120.00"), D("20.00"), D("20.00")],
        DEC(2),
    ),
    (
        ["pl.min_horizontal"],
        'pl.min_horizontal(pl.col("單價"), 20)',
        [D("12.50"), D("20.00"), D("19.99"), D("20.00")],
        DEC(2),
    ),
    (
        ["pl.sum_horizontal"],
        'pl.sum_horizontal(pl.col("單價"), pl.col("折扣"))',
        [D("12.50"), D("130.05"), D("20.54"), D("0.00")],
        DEC(2),
    ),
    # 一般的方法
    (["fill_null"], 'pl.col("數量").fill_null(0)', [3, 10, 0, 0], INT),
    (["is_null"], 'pl.col("門市").is_null()', [False, False, False, True], BOOL),
    (["is_not_null"], 'pl.col("門市").is_not_null()', [True, True, True, False], BOOL),
    (["is_in"], 'pl.col("代號").is_in(["a1", "c"])', [True, False, True, N], BOOL),
    (["is_between"], 'pl.col("數量").is_between(1, 5)', [True, False, False, N], BOOL),
    (["abs"], '(pl.col("折扣") - 1).abs()', [D("1.00"), D("9.05"), D("0.45"), N], DEC(2)),
    (["round"], '(pl.col("單價") * 0.333).round(2)', [D("4.16"), D("39.96"), D("6.66"), N], DEC(2)),
    (["round"], 'pl.col("評分").round(2)', [0.13, 2.5, -2.5, N], FLOAT),
    (["round"], 'pl.col("評分").round(2, mode="half_to_even")', [0.12, 2.5, -2.5, N], FLOAT),
    (["floor"], 'pl.col("單價").floor()', [D("12.00"), D("120.00"), D("19.00"), N], DEC(2)),
    (["ceil"], 'pl.col("單價").ceil()', [D("13.00"), D("120.00"), D("20.00"), N], DEC(2)),
    (["clip"], 'pl.col("數量").clip(1, 5)', [3, 5, 1, N], INT),
    (["cast", "pl.Int64"], 'pl.col("單價").cast(pl.Int64, strict=False)', [12, 120, 19, N], INT),
    (["cast", "pl.Int64"], 'pl.col("代號").cast(pl.Int64, strict=False)', [N] * 4, INT),
    (
        ["cast", "pl.Float64"],
        'pl.col("數量").cast(pl.Float64, strict=False)',
        [3.0, 10.0, 0.0, N],
        FLOAT,
    ),
    (
        ["cast", "pl.String"],
        'pl.col("單價").cast(pl.String, strict=False)',
        ["12.50", "120.00", "19.99", N],
        STR,
    ),
    (
        ["cast", "pl.Decimal"],
        'pl.col("單價").cast(pl.Decimal(38, 1), strict=False)',
        [D("12.5"), D("120.0"), D("20.0"), N],
        DEC(1),
    ),
    (
        ["cast", "pl.Decimal"],
        'pl.col("數量").cast(pl.Decimal(38, 4), strict=False)',
        [D("3.0000"), D("10.0000"), D("0.0000"), N],
        DEC(4),
    ),
    # .str.
    (["str.strip_chars"], 'pl.col("門市").str.strip_chars()', ["北區門市", "南區", "東區", N], STR),
    (
        ["str.strip_chars_start"],
        'pl.col("門市").str.strip_chars_start()',
        ["北區門市", "南區 ", "東區", N],
        STR,
    ),
    (
        ["str.strip_chars_end"],
        'pl.col("門市").str.strip_chars_end()',
        ["北區門市", " 南區", "東區", N],
        STR,
    ),
    (["str.to_uppercase"], 'pl.col("代號").str.to_uppercase()', ["A1", "B22", "C", N], STR),
    (["str.to_lowercase"], 'pl.col("代號").str.to_lowercase()', ["a1", "b22", "c", N], STR),
    (["str.len_chars"], 'pl.col("門市").str.len_chars()', [4, 4, 2, N], INT),
    (["str.slice"], 'pl.col("門市").str.slice(0, 2)', ["北區", " 南", "東區", N], STR),
    (["str.slice"], 'pl.col("門市").str.slice(2)', ["門市", "區 ", "", N], STR),
    (["str.head"], 'pl.col("代號").str.head(1)', ["a", "B", "c", N], STR),
    (["str.tail"], 'pl.col("代號").str.tail(1)', ["1", "2", "c", N], STR),
    (["str.zfill"], 'pl.col("代號").str.zfill(4)', ["00a1", "0B22", "000c", N], STR),
    (["str.pad_start"], 'pl.col("代號").str.pad_start(4, "*")', ["**a1", "*B22", "***c", N], STR),
    (["str.pad_end"], 'pl.col("代號").str.pad_end(4, "*")', ["a1**", "B22*", "c***", N], STR),
    (["str.starts_with"], 'pl.col("門市").str.starts_with("北")', [True, False, False, N], BOOL),
    (["str.ends_with"], 'pl.col("門市").str.ends_with("門市")', [True, False, False, N], BOOL),
    (
        ["str.contains"],
        'pl.col("門市").str.contains("區", literal=True)',
        [True, True, True, N],
        BOOL,
    ),
    (
        ["str.replace"],
        'pl.col("門市").str.replace(" ", "", literal=True)',
        ["北區門市", "南區 ", "東區", N],
        STR,
    ),
    (
        ["str.replace_all"],
        'pl.col("門市").str.replace_all(" ", "", literal=True)',
        ["北區門市", "南區", "東區", N],
        STR,
    ),
    # .dt.
    (["dt.year"], 'pl.col("日期").dt.year()', [2026, 2026, 2026, N], INT),
    (["dt.month"], 'pl.col("日期").dt.month()', [1, 2, 12, N], INT),
    (["dt.day"], 'pl.col("日期").dt.day()', [5, 28, 31, N], INT),
    (["dt.weekday"], 'pl.col("日期").dt.weekday()', [1, 6, 4, N], INT),
    (
        ["dt.to_string"],
        'pl.col("日期").dt.to_string("%Y/%m")',
        ["2026/01", "2026/02", "2026/12", N],
        STR,
    ),
    (
        ["dt.total_days", "-"],
        '(pl.col("日期") - pl.date(2026, 1, 1)).dt.total_days()',
        [4, 58, 364, N],
        INT,
    ),
]


@pytest.mark.parametrize(("ids", "text", "expected", "dtype"), CASES, ids=[c[1] for c in CASES])
def test_every_entry(ids, text, expected, dtype):
    """語法表的每一項：結果的值與型別；一列一列分別算、串流引擎，結果都和整份一起算相同。"""
    assert set(ids) <= ExpressionScan(text).ids
    out, _ = run(text, warn_null=False)
    assert out.schema["x"] == dtype
    values = out["x"].to_list()
    assert values == expected
    if dtype == pl.Decimal(38, 2) or isinstance(dtype, pl.Decimal):
        assert [v.as_tuple().exponent for v in values if v is not None] == [-dtype.scale] * len(
            [v for v in expected if v is not None]
        )
    df = data()
    rows = pl.concat([run(text, df[i : i + 1], warn_null=False)[0] for i in range(df.height)])
    assert rows["x"].to_list() == values
    step = make(text, warn_null=False)
    streamed = step.apply(df.lazy()).collect(engine="streaming")
    assert streamed["x"].to_list() == values


def test_every_entry_is_tested():
    """語法表的每一個 id 都有測試標到（之後加一項而沒有補測試，這裡會失敗）。"""
    tested = {id for ids, *_ in CASES for id in ids}
    assert tested == {item["id"] for item in SYNTAX}


def test_cast_failures_warn():
    """文字轉整數全部失敗：前三列各有一則警告，第四列（本來就是空值）沒有。"""
    _, warnings = run('pl.col("代號").cast(pl.Int64, strict=False)')
    assert [w.line_no for w in warnings] == [2, 3, 4]


# ── 語法選擇器 ──────────────────────────────────────────────────────


def test_schema_has_the_whole_syntax_table():
    """schema 裡 expression 的 ui_syntax 和語法表的 id 完全一樣（一項不多、一項不少），順序也一樣。"""
    prop = CustomExpression.model_json_schema()["properties"]["expression"]
    assert prop["ui"] == "expression"
    assert [item["id"] for item in prop["ui_syntax"]] == [item["id"] for item in SYNTAX]
    for item in prop["ui_syntax"]:
        assert set(item) == {"id", "category", "insert", "caret", "doc", "example", "en"}


def test_syntax_catalog_languages():
    prop = CustomExpression.model_json_schema()["properties"]["expression"]
    with using("en"):
        english = localize_schema(prop)
    for item in english["ui_syntax"]:
        assert "en" not in item
        assert not any("一" <= ch <= "鿿" for ch in item["doc"] + item["category"]), item
    chinese = strip_en(prop)
    assert chinese["ui_syntax"][0]["category"] == "欄位與值"
    assert "en" not in chinese["ui_syntax"][0]


def test_examples_compute_on_test_data():
    for item in SYNTAX:
        run(item["example"], warn_null=False)


# ── 和 Polars 不一樣的地方 ───────────────────────────────────────────


def money(values: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {SOURCE_FILE: ["a"] * len(values), LINE_NO: list(range(1, len(values) + 1)), "金額": values}
    ).with_columns(pl.col("金額").cast(pl.Decimal(38, 2)), pl.col(LINE_NO).cast(pl.Int64))


def test_decimal_times_decimal_is_exact():
    df = money(["1.15", "2.35", "0.07", "19.99", "33.33"])
    out, _ = run('pl.col("金額") * 0.9', df)
    assert out["x"].to_list() == [D("1.035"), D("2.115"), D("0.063"), D("17.991"), D("29.997")]
    assert out.schema["x"] == pl.Decimal(38, 3)


def test_million_rows_sum_is_exact():
    df = money(["19.99"] * 1_000_000)
    out, _ = run('pl.col("金額") * 0.9', df, warn_null=False)
    assert out["x"].sum() == D("17991000.000")


@pytest.mark.parametrize(
    ("text", "dtype", "first"),
    [
        ('pl.col("金額") + 0.005', pl.Decimal(38, 3), D("1.155")),
        ('pl.col("金額") * pl.col("金額")', pl.Decimal(38, 4), D("1.3225")),
        ('pl.col("金額") * 2', pl.Decimal(38, 2), D("2.30")),
        ('pl.col("金額") / 3', pl.Decimal(38, 6), D("0.383333")),
        ("pl.lit(1) / 8", pl.Decimal(38, 6), D("0.125000")),
        ("pl.lit(2) / 3", pl.Decimal(38, 6), D("0.666667")),
        ("pl.lit(3) * 4", pl.Int64, 12),
        ('(pl.col("金額") * 0.9) // 1', pl.Decimal(38, 3), D("1.000")),
        ('(pl.col("金額") * 0.9) % 1', pl.Decimal(38, 3), D("0.035")),
        ('pl.col("金額").round(4)', pl.Decimal(38, 2), D("1.15")),
        ('pl.col("金額").cast(pl.Float64, strict=False) * 2', pl.Float64, 2.3),
    ],
)
def test_decimal_rules(text, dtype, first):
    out, _ = run(text, money(["1.15"]))
    assert out.schema["x"] == dtype
    assert out["x"][0] == first


@pytest.mark.parametrize(
    ("value", "rounded", "even"),
    [
        ("1.045", "1.05", "1.04"),
        ("2.125", "2.13", "2.12"),
        ("0.005", "0.01", "0.00"),
        ("-1.035", "-1.04", "-1.04"),
    ],
)
def test_rounding_half_away_from_zero(value, rounded, even):
    df = money(["0"]).with_columns(pl.lit(D(value)).cast(pl.Decimal(38, 3)).alias("金額"))
    for text in ('pl.col("金額").round(2)', 'pl.col("金額").cast(pl.Decimal(38, 2), strict=False)'):
        out, _ = run(text, df)
        assert out["x"].to_list() == [D(rounded)], text
        assert out.schema["x"] == pl.Decimal(38, 2)
    out, _ = run('pl.col("金額").round(2, mode="half_to_even")', df)
    assert out["x"].to_list() == [D(even)]


def test_product_over_18_places_is_an_error():
    df = money(["1.15"]).with_columns(pl.col("金額").cast(pl.Decimal(38, 10)))
    with pytest.raises(Exception, match="18"):
        run('pl.col("金額") * pl.col("金額")', df)


@pytest.mark.parametrize(
    "text",
    [
        'pl.col("單價") / 0',
        'pl.col("數量") / 0',
        'pl.col("評分") / 0',
        'pl.col("單價") // 0',
        'pl.col("數量") // 0',
        'pl.col("評分") // 0',
        'pl.col("單價") % 0',
        'pl.col("數量") % 0',
        'pl.col("評分") % 0',
        'pl.col("數量") / (pl.col("數量") - pl.col("數量"))',
        'pl.col("數量") % (pl.col("數量") - pl.col("數量"))',
    ],
)
def test_divide_by_zero_is_null(text):
    out, warnings = run(text)
    assert out["x"].to_list() == [None] * 4
    assert [w.line_no for w in warnings] == [2, 3, 4]
    streamed = make(text).apply(data().lazy()).collect(engine="streaming")
    assert streamed["x"].to_list() == [None] * 4


def test_small_types_become_int64():
    for text in (
        'pl.col("門市").str.len_chars()',
        'pl.col("日期").dt.year()',
        'pl.col("日期").dt.weekday()',
    ):
        assert run(text)[0].schema["x"] == pl.Int64


def test_duration_is_an_error():
    with pytest.raises(Exception, match="total_days"):
        run('pl.col("日期") - pl.date(2026, 1, 1)')


def test_same_as_polars_where_nothing_changes():
    """沒有被小數、除以 0、型別收斂改到的寫法，轉出來的運算式和直接用 Polars 寫的相同。"""
    lf = data().lazy()
    same = {
        'pl.col("數量") + 1': pl.col("數量") + 1,
        'pl.col("門市").str.to_uppercase()': pl.col("門市").str.to_uppercase(),
        'pl.when(pl.col("數量") >= 10).then(pl.lit("大量")).otherwise(pl.lit("一般"))': pl.when(
            pl.col("數量") >= 10
        )
        .then(pl.lit("大量"))
        .otherwise(pl.lit("一般")),
        'pl.col("單價") - pl.col("折扣")': pl.col("單價") - pl.col("折扣"),
        'pl.col("代號").is_in(["a1", "c"])': pl.col("代號").is_in(["a1", "c"]),
    }
    for text, expected in same.items():
        assert build(ExpressionScan(text).tree, lf).meta.eq(expected), text


# ── 錯誤與警告 ──────────────────────────────────────────────────────


def test_text_arithmetic_says_parse_numbers_first():
    with pytest.raises(StepError, match="文字轉數字"):
        make('pl.col("代號") - 1').apply(data().lazy())


def test_bad_date_format():
    with pytest.raises(StepError, match="%Q"):
        make('pl.col("日期").dt.to_string("%Q")').apply(data().lazy())


def test_missing_column():
    with pytest.raises(StepError, match="不存在"):
        make('pl.col("沒有這欄") + 1').apply(data().lazy())


def test_warn_null():
    # pl.when 沒有 otherwise 的列有警告；用到的欄本來就是空值時不警告
    _, warnings = run('pl.when(pl.col("數量") >= 10).then(pl.col("單價"))')
    assert [w.line_no for w in warnings] == [2, 4]
    assert "x" in warnings[0].message
    _, warnings = run('pl.when(pl.col("數量") >= 10).then(pl.col("單價"))', warn_null=False)
    assert warnings == []
    # 沒有引用任何欄：不檢查
    _, warnings = run("pl.lit(None)")
    assert warnings == []


def test_output_order_and_no_temps():
    out, _ = run('pl.col("數量") + 1')
    assert out.columns == [SOURCE_FILE, LINE_NO, *EXPRESSION_TYPES, "x"]


def test_constant_fills_every_row():
    """不引用欄的運算式：整欄同一個值（with_columns，不是一列）。"""
    out, _ = run('pl.lit("固定")')
    assert out.height == 4


def test_describe_and_suggest():
    step = make('pl.col("金額") - pl.col("成本")')
    assert step.describe() == '新增「x」＝ pl.col("金額") - pl.col("成本")'
    with using("en"):
        assert step.describe() == 'Add "x" = pl.col("金額") - pl.col("成本")'
    long = make(" + ".join(['pl.col("金額")'] * 10))
    assert long.describe().endswith("…") and len(long.describe()) < 80
    frame = pl.DataFrame({'有"引號': ["a"], "__line_no": [1]})
    assert CustomExpression.suggest(frame, {"column": '有"引號'}) == {
        "expression": 'pl.col("有\\"引號")'
    }
    assert ExpressionScan(
        CustomExpression.suggest(frame, {"column": '有"引號'})["expression"]
    ).columns == ['有"引號']
    assert CustomExpression.suggest(frame, {"column": "__line_no"}) == {}


def test_pydantic_and_validator_agree():
    """擋下的寫法：pydantic 與驗證器的訊息相同。"""
    from test_expression_scan import BLOCKED

    for text, _ in BLOCKED[::3]:
        if not text.strip():
            continue
        with pytest.raises(ValidationError) as err:
            make(text)
        assert ExpressionScan(text).error in str(err.value), text


# ── 驗證器 ──────────────────────────────────────────────────────────

SPEC = build_spec()


def recipe(*steps, reader="text_lines", **input) -> dict:
    return {
        "tablade_recipe": "1.3",
        "name": "x",
        "stages": [
            {
                "name": "主要",
                "input": {"files": ["a.txt"], "reader": reader, **input},
                "steps": list(steps),
                "output_format": "parquet",
            }
        ],
        "output": {"keep_system_columns": True, "manifest": False},
    }


def custom(expression: str, name: str = "新欄", id: str = "c") -> dict:
    return {"id": id, "type": "custom_expression", "name": name, "expression": expression}


def errors(data: dict) -> list[dict]:
    return [i for i in validator.validate(data, SPEC, {}) if i["severity"] == "error"]


def test_validator_column_references():
    path = "stages[0].steps[0].expression"
    found = errors(recipe(custom('pl.col("lin") + "x"')))
    assert found and found[0]["path"] == path and "line" in found[0].get("hint", "")
    assert errors(recipe(custom('pl.col("line") + "x"'))) == []
    # 前一步新增的欄可以；後面才新增的欄不行
    two = [
        custom('pl.col("line") + "x"', name="a", id="c1"),
        custom('pl.col("a") + "y"', name="b", id="c2"),
    ]
    assert errors(recipe(*two)) == []
    assert errors(recipe(*reversed(two)))
    # 系統欄位可以讀；那個位置沒有的系統欄位不行
    assert errors(recipe(custom('pl.col("__line_no") * 10'))) == []
    found = errors(recipe(custom('pl.col("__sheet") + "x"')))
    assert found and found[0]["path"] == path


def test_validator_new_name():
    found = errors(recipe(custom('pl.col("line") + "x"', name="line")))
    assert found and "重複" in found[0]["error"]
    found = errors(recipe(custom('pl.col("line") + "x"', name="__x")))
    assert found and "__" in found[0]["error"]


def test_validator_syntax_error_path():
    found = errors(recipe(custom('pl.col("line").sum()')))
    assert found[0]["path"] == "stages[0].steps[0].expression"
    assert found[0]["error"] == ExpressionScan('pl.col("line").sum()').error


def test_old_recipes_still_open():
    from tablade.recipe.schema import parse_recipe

    old = recipe({"id": "s", "type": "remove_blank_rows"})
    old["tablade_recipe"] = "1.2"
    parse_recipe(old)


# ── 匯出 ──────────────────────────────────────────────────────────


def test_export_bool_float_and_decimal(tmp_path):
    """結果是「是／否」、浮點數、小數 3 位的欄：寫成 CSV 與 Parquet 再讀回來是對的。"""
    from pathlib import Path

    from tablade.engine.run import execute
    from tablade.fixtures import write_all
    from tablade.recipe.schema import load_json, parse_recipe

    write_all(tmp_path / "data")
    root = Path(__file__).resolve().parents[1]
    data = load_json(root / "examples" / "sales_detail.recipe.json")
    data["tablade_recipe"] = "1.3"
    data["stages"][0]["steps"] += [
        {"id": "n", "type": "parse_number", "columns": ["金額"]},
        {
            "id": "b",
            "type": "custom_expression",
            "name": "大額",
            "expression": 'pl.col("金額") > 1000',
        },
        {
            "id": "f",
            "type": "custom_expression",
            "name": "浮點",
            "expression": 'pl.col("金額").cast(pl.Float64, strict=False) / 8',
        },
        {
            "id": "d",
            "type": "custom_expression",
            "name": "九折",
            "expression": 'pl.col("金額") * 0.9',
        },
    ]
    outputs = {}
    for fmt in ("parquet", "csv"):
        data["stages"][0]["output_format"] = fmt
        out = tmp_path / f"明細.{fmt}"
        execute(parse_recipe(data), tmp_path, output=out, mode="stream")
        outputs[fmt] = out
    parquet = pl.read_parquet(outputs["parquet"])
    assert parquet.schema["大額"] == pl.Boolean
    assert parquet.schema["浮點"] == pl.Float64
    assert parquet.schema["九折"] == pl.Decimal(38, 3)
    csv = pl.read_csv(outputs["csv"], infer_schema=False)
    assert csv["大額"].to_list() == parquet["大額"].cast(pl.String).to_list()
    assert csv["九折"].to_list() == parquet["九折"].cast(pl.String).to_list()
    assert [float(v) for v in csv["浮點"].drop_nulls()] == parquet["浮點"].drop_nulls().to_list()


def test_skill_lists_every_entry():
    """AI skill 的 references/steps.md 有語法表每一項的寫法與例子；skill 的限制照樣過。"""
    from tablade.skill import check_limits, skill_files

    files = skill_files()
    steps = files["references/steps.md"]
    section = steps[steps.index("### 自訂的運算式可以用的寫法") :]
    for item in SYNTAX:
        assert item["example"].replace("|", "\\|") in section, item["id"]
        assert item["doc"] in section, item["id"]
    assert check_limits(files) == []
