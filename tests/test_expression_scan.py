"""自訂（custom_expression）的運算式：只讀不執行的檢查（recipe/validator.py 的 ExpressionScan）。

docs/custom-expression-plan.md 的「測試」第 3、4 節：擋下的寫法、可以用而容易誤擋的寫法。
步驟本身（轉成 Polars、和 Polars 不一樣的地方）在 tests/test_custom_expression.py。
"""

from __future__ import annotations

import pytest

from tablade.i18n import using
from tablade.recipe import validator
from tablade.recipe.validator import EXPR_MAX_CHARS, SYNTAX, ExpressionScan

# （運算式, 訊息裡要有的字）：每一個都要被擋下，訊息寫出是哪一個寫法；有建議的，訊息裡有那個建議
BLOCKED = [
    # 要看整份資料
    *[
        (f'pl.col("a").{m}()', f".{m}()")
        for m in ("sum", "mean", "min", "max", "count", "n_unique")
    ],
    *[(f'pl.col("a").{m}()', "群組彙總") for m in ("first", "last")],
    ("pl.len()", "群組彙總"),
    ('pl.col("a").over("b")', "群組彙總"),
    # 要看其他列
    *[(f'pl.col("a").{m}()', "向下填滿") for m in ("shift", "diff", "cum_sum", "rank", "sort")],
    ('pl.col("a").forward_fill()', "向下填滿"),
    ('pl.col("a").fill_null(strategy="forward")', "向下填滿"),
    # 列數會變
    *[(f'pl.col("a").{m}()', "篩選") for m in ("filter", "head", "unique", "explode")],
    # 會執行 Python
    ('pl.col("a").map_elements(lambda x: x)', "Python"),
    ('pl.col("a").map_batches(lambda x: x)', "Python"),
    ("(lambda: 1)()", "lambda"),
    ('pl.col("a").replace_strict({"a": "b"})', "取代文字"),
    ('pl.col("a").alias("b")', "新欄位名稱"),
    ('pl.col("a").name.suffix("_x")', "新欄位名稱"),
    ("pl.all()", "一步只算一欄"),
    ('pl.exclude("a")', "一步只算一欄"),
    ('pl.col("a", "b")', "一步只算一欄"),
    ("pl.col(pl.String)", "一步只算一欄"),
    ('pl.read_csv("x")', "pl.read_csv()"),
    ('pl.scan_csv("x")', "pl.scan_csv()"),
    ('pl.sql_expr("a")', "pl.sql_expr()"),
    ("pl.SQLContext()", "pl.SQLContext()"),
    ('(pl.col("a") > 1) and (pl.col("b") > 1)', "&"),
    ('(pl.col("a") > 1) or (pl.col("b") > 1)', "|"),
    ('not pl.col("a")', "~"),
    ('1 < pl.col("a") < 3', "is_between"),
    ('pl.col("a") if True else pl.col("b")', "pl.when"),
    ('pl.when(pl.col("a") > 1).then("大量")', "pl.lit"),
    ('pl.when(pl.col("a") > 1).then(1).otherwise("一般")', "pl.lit"),
    ('pl.col("a") ** 2', "**"),
    ('pl.col("a") in [1]', "is_in"),
    ('pl.col("a") is None', "is_null"),
    ('pl.col("a")[0]', "[ ]"),
    ('f"{1}"', 'f"…"'),
    ('[x for x in pl.col("a")]', "for"),
    ("(a := 1)", ":="),
    ('b"x"', 'b"…"'),
    ("1e-3", "科學記號"),
    # 執行程式的嘗試
    ('__import__("os").system("echo hi")', "system"),
    ('open("x").read()', "read"),
    ('pl.col("a").__class__', "__class__"),
    ('pl.col("a").__class__.__mro__', "__mro__"),
    ('getattr(pl, "read_csv")("x")', "getattr"),
    ('eval("1")', "eval"),
    ('exec("1")', "exec"),
    ("import os", "語法"),
    ('pl.col("a")\npl.col("b")', "語法"),
    # 名稱
    ('polars.col("a")', "polars"),
    ('col("a")', "col(…)"),
    ("x", "`x`"),
    # pl.col
    ('pl.col("*")', '"*"'),
    ('pl.col("^金.*$")', "正規表示式"),
    ('pl.col(["a"])', "一個欄名"),
    ("pl.col(1)", "文字"),
    ("pl.col()", "欄名"),
    # 參數
    ('pl.col("a").round()', "1 個參數"),
    ('pl.col("a").round(1, 2, 3)', "1 個參數"),
    ('pl.col("a").round("2")', "整數"),
    ('pl.col("a").str.head(pl.col("a"))', "整數"),
    ('pl.col("a").cast("Int64", strict=False)', "pl.Int64"),
    ('pl.col("a").round(19)', "0～18"),
    ('pl.col("a").round(-1)', "0～18"),
    ('pl.col("a").str.zfill(-1)', "0～1,000"),
    ('pl.col("a").str.zfill(1001)', "0～1,000"),
    ('pl.col("a").cast(pl.Decimal(38, 19), strict=False)', "0～18"),
    ('pl.col("a").cast(pl.Decimal(10, 2), strict=False)', "pl.Decimal(38"),
    ('pl.col("a").round(2, foo=1)', "foo="),
    ('pl.col("a").cast(pl.Int64)', "strict=False"),
    ('pl.col("a").cast(pl.Int64, strict=True)', "strict=False"),
    ('pl.col("a").str.contains("a")', "literal=True"),
    ('pl.col("a").str.contains("a", literal=False)', "literal=True"),
    ('pl.col("a").str.replace("a", "b")', "literal=True"),
    ('pl.col("a").round(2, mode="half_up")', "half_away_from_zero"),
    ('pl.col("a").cast(pl.Date, strict=False)', "文字轉日期"),
    ('pl.col("a").cast(pl.Boolean, strict=False)', "比較"),
    ('pl.col("a").str.pad_start(4, "ab")', "一個字"),
    # 運算式的位置直接寫字串
    ('pl.coalesce("a", "b")', "pl.lit"),
    ('pl.concat_str(["a", "b"])', "pl.lit"),
    ('pl.col("a").is_between("a", "c")', "pl.lit"),
    ('pl.col("a").clip("b", "y")', "pl.lit"),
    ('pl.max_horizontal("a", "b")', "pl.lit"),
    # pl.date
    ("pl.date(2026, 2, 30)", "沒有這個日期"),
    ("pl.date(2026, 13, 1)", "沒有這個日期"),
    ('pl.date(pl.col("年"), 1, 1)', "整數"),
    ("pl.date(2026, 1)", "3 個參數"),
    # 字面值
    (str(2**63), "int64"),
    ("1j", "1j"),
    ("...", "..."),
    # 打錯字的
    ("pl.col（“a”）", "半形"),
    ('pl.col("a"', "語法"),
    ("", "空的"),
    ("   \n  ", "空的"),
    ("# 只有註解", "空的"),
    # then 只能接在 pl.when 後面
    ('pl.col("a").then(1)', "pl.when"),
    ("pl.when(True)", ".then"),
    ('pl.col("a").otherwise(1)', ".then"),
]

TOO_BIG = [
    "x" * (EXPR_MAX_CHARS + 1),
    "(" * 300 + "1" + ")" * 300,
    "-" * 1999 + "1",
    "+".join(["1"] * 900),
    'pl.col("a")\0',
    " + ".join(['pl.col("a")'] * 100),  # 超過 500 個節點
    "(" * 60 + 'pl.col("a")' + ")" * 60 + ".abs()" * 30,  # 超過 50 層
]


@pytest.mark.parametrize(("text", "word"), BLOCKED)
def test_blocked(text, word):
    scan = ExpressionScan(text)
    assert scan.error, text
    assert word in scan.error, (text, scan.error)
    assert scan.tree is None


@pytest.mark.parametrize("text", TOO_BIG)
def test_too_big_is_an_ordinary_error(text):
    """很大的輸入：一般的錯誤訊息，不會有沒接住的例外（RecursionError、MemoryError）。"""
    scan = ExpressionScan(text)
    assert scan.error and ("太長或太複雜" in scan.error or "語法" in scan.error), scan.error


ALLOWED = [
    ('(pl.col("a")\n    + pl.col("b"))  # 註解', ["a", "b"]),
    ('pl.col("a")  # 行尾的註解', ["a"]),
    ("pl.col('a') + 'b'", ["a"]),
    ('pl.col("a") + """三引號"""', ["a"]),
    ('pl.col("a") + "有\\"引號\\"與\\\\反斜線"', ["a"]),
    ('pl.col("a") + "中文😀"', ["a"]),
    ('pl.col("有 空白") + pl.col("有.點") + pl.col("有\\"引號")', ["有 空白", "有.點", '有"引號']),
    ("-1", []),
    ('pl.col("a").str.slice(-2)', ["a"]),
    ('pl.col("a").clip(-5, 5)', ["a"]),
    ('((pl.col("a")))', ["a"]),
    ('pl.col("a") + pl.col("b") * pl.col("c")', ["a", "b", "c"]),
    ('~pl.col("a") & pl.col("b")', ["a", "b"]),
    (
        'pl.when(pl.when(pl.col("a") > 1).then(1).otherwise(2) > 1).then(pl.col("b")).otherwise(0)',
        ["a", "b"],
    ),
    ('pl.col("a").fill_null(pl.col("b") * 2)', ["a", "b"]),
    ('pl.concat_str([pl.col("a"), pl.lit("-"), pl.col("b").str.head(2)])', ["a", "b"]),
    ('pl.lit("固定")', []),
    ("1 + 1", []),
    ('pl.col("__line_no") * 10', ["__line_no"]),
    ('pl.col("a").cast(pl.Decimal(38, 0), strict=False)', ["a"]),
    ('  pl.col("a")  ', ["a"]),
    ('pl.col("a") + pl.col("a")', ["a"]),
]


@pytest.mark.parametrize(("text", "columns"), ALLOWED)
def test_allowed(text, columns):
    scan = ExpressionScan(text)
    assert scan.error is None, scan.error
    assert scan.columns == columns


def test_precedence_follows_python():
    """運算子的優先順序照 Python：a + b * c 與 (a + b) * c 不同；~a & b 與 ~(a & b) 不同。"""
    one = ExpressionScan('pl.col("a") + pl.col("b") * pl.col("c")').tree
    two = ExpressionScan('(pl.col("a") + pl.col("b")) * pl.col("c")').tree
    assert one[1] == "+" and two[1] == "*"
    three = ExpressionScan('~pl.col("a") & pl.col("b")').tree
    four = ExpressionScan('~(pl.col("a") & pl.col("b"))').tree
    assert three[0] == "op" and four[0] == "unary"


def test_decimal_literal_keeps_its_places():
    """寫的小數從原始的文字讀成精確小數：寫幾位就是幾位，不經過浮點數。"""
    from decimal import Decimal

    assert ExpressionScan("0.9").tree == ("lit", "decimal", Decimal("0.9"))
    assert ExpressionScan("0.333").tree[2].as_tuple().exponent == -3
    assert ExpressionScan("-0.10").tree == ("lit", "decimal", Decimal("-0.10"))
    assert ExpressionScan("0.1").tree[2] == Decimal("0.1")  # 不是 0.1000000000000000055…


def test_messages_in_english():
    """畫面的語言切成英文時，訊息是英文。"""
    texts = [t for t, _ in BLOCKED[::9]]
    assert len(texts) >= 10
    with using("en"):
        for text in texts:
            error = ExpressionScan(text).error
            assert error and not any("一" <= ch <= "鿿" for ch in error.replace("「", "")), (
                text,
                error,
            )


def test_removing_an_entry_blocks_it(monkeypatch):
    """能用的寫法只來自語法表：拿掉一項，原本用到它的運算式就被擋下。"""
    cases = {
        "pl.coalesce": 'pl.coalesce(pl.col("a"), 0)',
        "fill_null": 'pl.col("a").fill_null(0)',
        "str.strip_chars": 'pl.col("a").str.strip_chars()',
    }
    for id, text in cases.items():
        assert ExpressionScan(text).error is None
        monkeypatch.delitem(validator.SYNTAX_BY_ID, id)
        assert ExpressionScan(text).error, id
        monkeypatch.undo()


def test_syntax_table_shape():
    """語法表的每一項都有分類、插入的文字、說明、例子與英文；id 與 insert 不重複。"""
    categories = [zh for zh, _ in validator.SYNTAX_CATEGORIES]
    ids = [item["id"] for item in SYNTAX]
    inserts = [item["insert"] for item in SYNTAX]
    assert len(ids) == len(set(ids)) == 69
    assert len(inserts) == len(set(inserts))
    for item in SYNTAX:
        assert item["category"] in categories, item["id"]
        for key in ("insert", "doc", "example"):
            assert item[key].strip(), (item["id"], key)
        assert item["en"]["doc"].strip() and item["en"]["category"].strip(), item["id"]
        assert 0 <= item["caret"] <= len(item["insert"]), item["id"]
        assert validator.CARET not in item["insert"]
    # 分類照語法表的順序出現
    seen = list(dict.fromkeys(item["category"] for item in SYNTAX))
    assert seen == categories


def test_examples_use_their_own_entry():
    """每一項的 example 通過檢查，而且真的用到那一項。"""
    for item in SYNTAX:
        scan = ExpressionScan(item["example"])
        assert scan.error is None, (item["id"], scan.error)
        assert item["id"] in scan.ids, (item["id"], scan.ids)


def test_insert_only_names_entries():
    """語法選擇器插入的名稱（pl. 後面的、. 後面的）都在語法表裡，而且包含那一項自己的名稱。"""
    import re

    ids = set(validator.SYNTAX_BY_ID)
    allowed = ids | {"then", "otherwise"}  # pl.when 那一項的整串
    for item in SYNTAX:
        text = item["insert"]
        names = {f"pl.{n}" for n in re.findall(r"\bpl\.(\w+)", text)}
        names |= {f"str.{n}" for n in re.findall(r"\.str\.(\w+)", text)}
        names |= {f"dt.{n}" for n in re.findall(r"\.dt\.(\w+)", text)}
        stripped = re.sub(r"\.(str|dt)\.\w+|\bpl\.\w+", "", text)
        names |= set(re.findall(r"\.(\w+)\(", stripped))
        assert names <= allowed, (item["id"], names - allowed)
        if item["form"] in ("func", "method", "type", "column", "when", "date"):
            assert item["id"] in names, (item["id"], names)
