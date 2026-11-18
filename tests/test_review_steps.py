"""審查發現（docs/review-findings.md）的回歸測試：步驟的部分。

每個測試的 docstring 開頭寫是哪一個發現。資料都是在測試裡產生的假資料，或用 ``tablade.fixtures``。
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from tablade import i18n
from tablade.engine.run import (
    RunError,
    apply_parts,
    apply_step,
    concat_parts,
    execute,
    split_parts,
)
from tablade.fixtures import write_all
from tablade.recipe.schema import load_json, parse_recipe
from tablade.steps import all_steps, get
from tablade.steps.base import LINE_NO, SOURCE_FILE, SYSTEM_PREFIX
from tablade.steps.report import CP950_SYMBOLS, WIDE, guess_starts

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "examples" / "sales_detail.recipe.json"
COMBINED = ROOT / "examples" / "sales_combined.recipe.json"


def frame(data: dict, files: list[str] | None = None) -> pl.DataFrame:
    n = len(next(iter(data.values())))
    files = files or ["a.csv"] * n
    line_no, counter = [], {}
    for f in files:
        counter[f] = counter.get(f, 0) + 1
        line_no.append(counter[f] + 1)  # csv：第 1 行是標題
    return pl.DataFrame({SOURCE_FILE: files, LINE_NO: line_no, **data}).with_columns(
        pl.col(LINE_NO).cast(pl.Int64)
    )


def make(type_name: str, **params):
    return get(type_name).model_validate({"id": "t", "type": type_name, **params})


def data_of(df: pl.DataFrame) -> dict:
    return {c: df[c].to_list() for c in df.columns if not c.startswith(SYSTEM_PREFIX)}


def kept(step, df: pl.DataFrame, column: str = "品名") -> list:
    return apply_step(step, df)[0][column].to_list()


# ── F-004：否定的條件遇到空值 ──────────────────────────────────────

REMARKS = {"品名": ["原子筆", "折疊傘", "滑鼠", "筆記本"], "備註": [None, "退貨", "一般", ""]}
PAIRS = [
    ({"op": "equals", "value": "退貨"}, {"op": "not_equals", "value": "退貨"}),
    ({"op": "contains", "value": "退"}, {"op": "not_contains", "value": "退"}),
    ({"op": "regex", "pattern": "^退"}, {"op": "not_regex", "pattern": "^退"}),
]


@pytest.mark.parametrize(("positive", "negative"), PAIRS)
def test_negated_conditions_keep_rows_with_nulls(positive, negative):
    """F-004：「保留：備註 不包含『退貨』」不會拿掉備註是空值的列，和「排除：備註 包含『退貨』」一樣。"""
    df = frame(REMARKS)
    keep_not = kept(make("filter", conditions=[{"column": "備註", **negative}]), df)
    drop_yes = kept(
        make("filter", conditions=[{"column": "備註", **positive}], keep="non_matching"), df
    )
    assert keep_not == drop_yes == ["原子筆", "滑鼠", "筆記本"]


TEXT_CONDITIONS = [
    {"op": "equals", "value": ""},
    {"op": "not_equals", "value": "x"},
    {"op": "contains", "value": "x"},
    {"op": "not_contains", "value": "x"},
    {"op": "starts_with", "value": ""},
    {"op": "ends_with", "value": ""},
    {"op": "regex", "pattern": "^$"},
    {"op": "not_regex", "pattern": "^$"},
]


@pytest.mark.parametrize("condition", TEXT_CONDITIONS, ids=lambda c: c["op"])
def test_null_and_empty_text_give_the_same_result(condition):
    """F-004：csv 的空白格是空值、逐行讀取拆出來的是空字串：換一種讀取方式，條件的結果不變。"""
    step = make("filter", conditions=[{"column": "備註", **condition}])
    from_csv = kept(step, frame({"品名": ["筆"], "備註": [None]}))
    from_lines = kept(step, frame({"品名": ["筆"], "備註": [""]}))
    assert from_csv == from_lines


def test_classify_and_conditional_copy_use_the_same_null_rule():
    """F-004：將列進行分類、依條件複製欄位用同一組條件，空值也一樣當成空字串。"""
    df = frame(REMARKS)
    rule = {
        "label": "一般",
        "conditions": [{"column": "備註", "op": "not_contains", "value": "退"}],
    }
    classified = apply_step(make("classify_rows", rules=[rule], otherwise="退貨"), df)[0]
    assert classified["類型"].to_list() == ["一般", "退貨", "一般", "一般"]
    copy = make(
        "conditional_copy",
        conditions=[{"column": "備註", "op": "not_equals", "value": "退貨"}],
        column="品名",
        name="一般品名",
    )
    assert apply_step(copy, df)[0]["一般品名"].to_list() == ["原子筆", None, "滑鼠", "筆記本"]


# ── F-005：依固定位置拆欄與 Big5 的符號 ───────────────────────────────


def pad(text: str, width: int) -> str:
    """列印格式的報表：依 cp950 的位元組數補空白對齊。"""
    return text + " " * (width - len(text.encode("cp950")))


SYMBOL_NAMES = ["保溫瓶", "20℃保溫瓶", "3×4收納盒", "※特價雨傘", "α型①號─"]


def test_split_fixed_width_counts_big5_symbols_as_two_cells():
    """F-005：℃、×、※ 這類字在 Big5 報表裡佔兩個位元組：後面的欄位不會往左偏一格。"""
    lines = [
        pad(f"A{i:03d}", 5) + pad(name, 12) + "12,280"
        for i, name in enumerate(SYMBOL_NAMES, start=1)
    ]
    step = make("split_fixed_width", starts=[1, 6, 18], names=["品號", "品名", "金額"])
    out, total, _ = apply_step(step, frame({"line": lines}))
    assert out["品名"].to_list() == SYMBOL_NAMES
    assert out["金額"].to_list() == ["12,280"] * len(SYMBOL_NAMES)
    assert total == 0


def test_every_cp950_double_byte_character_is_two_cells():
    """F-005：cp950 裡每個佔兩個位元組的字都算 2 格（``CP950_SYMBOLS`` 和 Python 的 cp950 一致）。"""

    def double(ch: str) -> bool:
        try:
            return len(ch.encode("cp950")) == 2
        except UnicodeEncodeError:
            return False

    chars = [chr(c) for c in range(0x80, 0x10000) if not 0xD800 <= c <= 0xDFFF]
    two = pl.Series([c for c in chars if double(c)], dtype=pl.String)
    assert len(two) > 13_000 and two.str.contains(f"^{WIDE}$").all()
    ascii_ = pl.Series([chr(c) for c in range(0x20, 0x7F)], dtype=pl.String)
    assert not ascii_.str.contains(WIDE).any()
    # 清單裡每一個碼位都真的是 cp950 的雙位元組字（沒有多列）
    for item in CP950_SYMBOLS.split():
        low, _, high = item.partition("-")
        for code in range(int(low, 16), int(high or low, 16) + 1):
            assert double(chr(code)), f"{code:04X}"


def test_guess_starts_uses_the_same_widths_as_the_split():
    """F-005：建立步驟時猜位置（``guess_starts``）和執行時同一套算法，猜出來的位置拆得對。"""
    lines = [
        pad("A001", 5) + pad("20℃保溫瓶", 12) + "12,280",
        pad("A002", 5) + pad("保溫瓶", 12) + "   300",
        pad("A003", 5) + pad("※特價雨傘", 12) + " 1,050",
    ]
    starts = guess_starts(lines)
    assert starts == [1, 5, 16]  # ℃、※ 算 1 格的話，第 3 欄會猜成從第 15 格開始
    step = make("split_fixed_width", starts=starts, names=["品號", "品名", "金額"])
    out = apply_step(step, frame({"line": lines}))[0]
    assert out["金額"].to_list() == ["12,280", "300", "1,050"]
    assert out["品名"].to_list() == ["20℃保溫瓶", "保溫瓶", "※特價雨傘"]


# ── F-013：串流執行計畫不能退回 in-memory ─────────────────────────────

FALLBACK = 'fillcolor="0.0 0.3 1.0"'
"""``show_graph`` 的圖例：「in-memory engine fallback」的節點用這個顏色。"""


def fallbacks(lf: pl.LazyFrame) -> list[str]:
    graph = lf.show_graph(engine="streaming", plan_stage="physical", raw_output=True, show=False)
    return [line for line in graph.splitlines() if "label=" in line and FALLBACK in line]


def _merged(df: pl.DataFrame) -> pl.LazyFrame:
    return make("merge_files").combine([("a.csv", df.lazy())])[0][1]


PLAN_DATA = {
    "門市": ["北", "北", None, "中"],
    "月份": ["1月", "", "2月", None],
    "金額": [1, 2, None, 4],
    "line": ["a,b", "c", None, '"d,e",f'],
}
AGG_OPS = ["sum", "mean", "max", "min", "count", "n_unique", "first", "last"]
PLAN_CASES = {
    **{
        f"群組彙總（合併來源檔之後，{op}）": lambda lf, op=op: make(
            "group_by", by=["門市"], aggregations=[{"column": "金額", "op": op, "name": "x"}]
        ).apply(_merged(lf.collect()))
        for op in AGG_OPS
    },
    "群組彙總（整份，合併來源檔之後）": lambda lf: make(
        "group_by", aggregations=[{"column": "金額", "op": "first", "name": "x"}]
    ).apply(_merged(lf.collect())),
    **{
        f"樞紐（{op}）": lambda lf, op=op: make(
            "pivot", index=["門市"], on="月份", values="金額", op=op, columns=["1月"]
        ).apply(lf)
        for op in AGG_OPS
        if op != "n_unique"
    },
    "樞紐（合併來源檔之後）": lambda lf: make(
        "pivot", index=["門市"], on="月份", values="金額", columns=["1月"]
    ).apply(_merged(lf.collect())),
    "樞紐（整份）": lambda lf: make("pivot", on="月份", values="金額", columns=["1月"]).apply(lf),
    "移除重複（保留最後一筆）": lambda lf: make("dedupe", columns=["門市"], keep="last").apply(lf),
    "移除重複（整列，保留最後一筆，合併之後）": lambda lf: make("dedupe", keep="last").apply(
        _merged(lf.collect())
    ),
    "取消樞紐": lambda lf: make("unpivot", columns=["門市", "月份"], skip_empty=True).apply(lf),
    "依分隔符號拆成多欄": lambda lf: make("split_delimited", columns=["a", "b"]).apply(lf),
    "拆欄（多的段併入最後一欄）": lambda lf: make(
        "split_column", column="line", delimiter=",", names=["a"], on_extra="merge"
    ).apply(lf),
}


def _example_plans():
    for cls in all_steps():
        for example in cls.examples:
            yield pytest.param(cls, example, id=f"{cls.type_name()}:{example.title}")


@pytest.mark.parametrize(("cls", "example"), list(_example_plans()))
def test_step_examples_stream_without_in_memory_fallback(cls, example):
    """F-013：每種步驟（registry 的範例參數）的串流執行計畫都沒有退回 in-memory 的節點。"""
    step = cls.model_validate({"id": "ex", "type": cls.type_name(), **example.params})
    tables = {
        name: frame(data, [f"{name}.csv"] * len(next(iter(data.values()))))
        for name, data in (example.tables or {}).items()
    }
    step = step.bind(tables)
    lf = frame(example.input).lazy()
    result = step.combine([("a.csv", lf)])[0][1] if cls.combines else step.apply(lf)
    assert fallbacks(result) == []


@pytest.mark.parametrize("name", list(PLAN_CASES))
def test_aggregations_stream_without_in_memory_fallback(name):
    """F-013：樞紐、合併來源檔之後的群組彙總、移除重複（保留最後一筆）也可以串流。"""
    assert fallbacks(PLAN_CASES[name](frame(PLAN_DATA).lazy())) == []


def test_group_by_after_merge_keeps_the_first_row_position():
    """F-013：合併來源檔之後的群組彙總，``__orig_*`` 仍是這一組第一筆合併前的位置。"""
    df = frame({"門市": ["北", "中", "北", "中"], "金額": [1, 2, 3, 4]}, files=["a", "a", "b", "b"])
    merged = concat_parts(apply_parts(make("merge_files"), split_parts(df))).collect()
    step = make(
        "group_by", by=["門市"], aggregations=[{"column": "金額", "op": "sum", "name": "合計"}]
    )
    out = apply_step(step, merged)[0]
    assert out.select("門市", "__orig_source_file", "__orig_line_no", "合計").rows() == [
        ("北", "a", 2, 4),
        ("中", "a", 3, 6),
    ]


def test_dedupe_keep_last_matches_unique():
    """F-013：保留最後一筆換了做法：結果和 Polars 的 ``unique(keep="last")`` 一樣（含空值與空字串）。"""
    df = frame(
        {
            "單號": ["1", "2", "1", None, "", None, "2", ""],
            "金額": ["10", "20", "30", "40", "50", "60", "70", "80"],
        },
        files=["a"] * 5 + ["b"] * 3,
    )
    for columns in (["單號"], []):
        out = apply_step(make("dedupe", columns=columns, keep="last"), df)[0]
        expected = pl.concat(
            [
                part.unique(columns or ["單號", "金額"], keep="last", maintain_order=True)
                for part in df.partition_by(SOURCE_FILE, maintain_order=True)
            ]
        )
        assert out.equals(expected)


# ── F-014：取消樞紐之後的群組彙總，組的順序 ──────────────────────────────

MONTHS = [f"{m}月" for m in range(1, 13)]


def _long() -> pl.DataFrame:
    wide = frame({"門市": ["北", "中", "南"], **{m: [1, 2, 3] for m in MONTHS}})
    step = make("unpivot", columns=MONTHS, name_column="月份", value_column="金額")
    return apply_step(step, wide)[0]


def test_group_by_after_unpivot_has_one_order():
    """F-014：取消樞紐之後同一個行號有好幾列：群組彙總照每組第一次出現的順序，每次都一樣。"""
    long = _long()
    group = make(
        "group_by", by=["月份"], aggregations=[{"column": "金額", "op": "sum", "name": "x"}]
    )
    orders = {tuple(apply_step(group, long)[0]["月份"].to_list()) for _ in range(20)}
    assert orders == {tuple(MONTHS)}
    pivot = make("pivot", index=["月份"], on="門市", values="金額", columns=["北", "中", "南"])
    orders = {tuple(apply_step(pivot, long)[0]["月份"].to_list()) for _ in range(20)}
    assert orders == {tuple(MONTHS)}


# ── F-015：樞紐依據的欄是空值或空白 ───────────────────────────────────

PIVOT_BLANKS = {
    "門市": ["北", "北", "北", "南", "南", "東"],
    "月份": ["1月", "2月", "3月", None, "", " "],
    "金額": [100, 200, 300, 1000, 2000, 5],
}


def test_pivot_warns_when_the_column_field_is_blank():
    """F-015：「欄依據欄位」是空值或空白的列，金額不在任何一欄：那一組也要警告（寫成「（空白）」）。"""
    step = make("pivot", index=["門市"], on="月份", values="金額", columns=["1月", "2月"])
    out, total, warnings = apply_step(step, frame(PIVOT_BLANKS))
    assert data_of(out) == {
        "門市": ["北", "南", "東"],
        "1月": [100, None, None],
        "2月": [200, None, None],
    }
    assert total == 3
    assert [(w.line_no, w.message) for w in warnings] == [
        (2, "這一組有沒轉成欄的「月份」：3月"),
        (5, "這一組有沒轉成欄的「月份」：（空白）"),  # 空值與空字串都是「（空白）」
        (7, "這一組有沒轉成欄的「月份」：（空白）"),
    ]
    i18n.set_lang("en")
    _, _, warnings = apply_step(step, frame(PIVOT_BLANKS))
    assert warnings[1].message == 'This group has "月份" values not turned into columns: (blank)'


def test_pivot_warning_counts_the_values_not_turned_into_columns():
    """F-013、F-015：只記第一個沒轉成欄的值與共幾種（串流時不能把全部的值接成一串）。"""
    df = frame({"門市": ["北"] * 4, "月份": ["1月", "3月", "4月", "3月"], "金額": [1, 2, 3, 4]})
    step = make("pivot", index=["門市"], on="月份", values="金額", columns=["1月"])
    _, total, warnings = apply_step(step, df)
    assert total == 1 and warnings[0].message == "這一組有沒轉成欄的「月份」：3月（共 2 種）"


# ── F-016：結合時這一邊的鍵不存在 ─────────────────────────────────────


@pytest.mark.parametrize("warn", [True, False])
def test_join_with_a_missing_key_is_a_run_error(warn):
    """F-016：這一邊的鍵不存在時，``warn_unmatched`` 開著也是「欄位不存在」（不是 Polars 的例外）。"""
    left = frame({"品號": ["P1"], "金額": ["1"]})
    right = frame({"代號": ["P1"], "類別": ["文具"]}, files=["b.csv"])
    step = make(
        "join",
        right="對照",
        on=[{"left": "不存在", "right": "代號"}],
        columns=["類別"],
        warn_unmatched=warn,
    )
    with pytest.raises(RunError, match="欄位不存在：不存在"):
        apply_step(step, left, tables={"對照": right})


def test_join_with_a_missing_key_in_stream_mode(tmp_path):
    """F-016：串流執行時也一樣。"""
    shutil.copy(COMBINED, tmp_path / COMBINED.name)
    write_all(tmp_path / "data")
    data = json.loads(COMBINED.read_text(encoding="utf-8"))
    join = next(s for s in data["stages"][-1]["steps"] if s["type"] == "join")
    join["on"] = [{"left": "不存在", "right": "品號"}]
    path = tmp_path / "bad.recipe.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    with pytest.raises(RunError, match="欄位不存在：不存在"):
        execute(
            parse_recipe(load_json(path)), tmp_path, output=tmp_path / "o.parquet", mode="stream"
        )


# ── F-017：把第一列作為欄位標題 ───────────────────────────────────────


def test_promote_headers_does_not_warn_on_the_file_it_was_made_from():
    """F-017：標題有空白格、重複或 __ 開頭時，建立步驟的那個檔不警告；真的不同的檔才警告。"""
    df = frame(
        {
            "欄1": ["日期", "115/01/05", "日期", "115/01/06"],
            "欄2": ["", "北區門市", "門市", "中區門市"],
            "欄3": ["金額", "100", "金額", "200"],
            "欄4": ["金額", "200", "金額", "300"],
            "欄5": ["__備註", "x", "__備註", "y"],
        },
        files=["a.csv", "a.csv", "b.csv", "b.csv"],
    )
    cls = get("promote_headers")
    params = cls.suggest(df.head(2), {})
    assert params == {"names": ["日期", "欄2", "金額", "金額_2", "備註"]}
    _, total, warnings = apply_step(
        cls.model_validate({"id": "h", "type": "promote_headers", **params}), df
    )
    assert total == 1 and warnings[0].source_file == "b.csv"  # b.csv 的第 2 欄有標題「門市」


# ── F-018：數值條件的說明文字 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "zh"),
    [
        (500, "500"),
        (999999, "999,999"),
        (1000000, "1,000,000"),
        (1234567, "1,234,567"),
        (100000.5, "100,000.5"),
        (0.000001, "0.000001"),
        (-3, "-3"),
        (12.25, "12.25"),
    ],
)
def test_number_condition_describes_the_exact_threshold(value, zh):
    """F-018：門檻照寫的值顯示：不用科學記號、不只留 6 位有效數字。"""
    condition = [{"column": "金額", "op": "gt", "value": value}]
    assert make("filter", conditions=condition).describe() == f"保留：金額 > {zh}"
    copy = make("conditional_copy", conditions=condition, column="品名", name="x")
    assert f"金額 > {zh}" in copy.describe()
    i18n.set_lang("en")
    assert make("filter", conditions=condition).describe() == f"Keep: 金額 > {zh}"


# ── F-044：拆欄的新欄名和來源欄同名 ───────────────────────────────────


@pytest.mark.parametrize("on_extra", ["warn", "merge"])
def test_split_column_can_reuse_the_source_name(on_extra):
    """F-044：把「備註」拆成「備註」「付款」（不保留來源欄）可以執行，結果和欄位效果推算的一樣。"""
    df = frame({"門市": ["北", "中"], "備註": ["門市 | 現金", "網路|信用卡|多"]})
    step = make(
        "split_column", column="備註", delimiter="|", names=["備註", "付款"], on_extra=on_extra
    )
    out, total, _ = apply_step(step, df)
    last = "信用卡" if on_extra == "warn" else "信用卡|多"
    assert data_of(out) == {"門市": ["北", "中"], "備註": ["門市", "網路"], "付款": ["現金", last]}
    assert total == (1 if on_extra == "warn" else 0)


# ── F-045：條件的正規表示式、條件的欄不存在 ─────────────────────────────

BAD_REGEX = [{"column": "品名", "op": "regex", "pattern": "("}]


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("filter", {"conditions": BAD_REGEX}),
        ("classify_rows", {"rules": [{"label": "x", "conditions": BAD_REGEX}]}),
        ("conditional_copy", {"conditions": BAD_REGEX, "column": "品名", "name": "x"}),
    ],
)
def test_condition_regex_is_checked_when_validating(name, params):
    """F-045：條件裡寫錯的正規表示式在驗證時就擋下（和取代文字一樣）。"""
    with pytest.raises(ValidationError, match="正規表示式有誤"):
        make(name, **params)


MISSING = [{"column": "zz", "op": "equals", "value": "x"}]


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("filter", {"conditions": MISSING}),
        ("classify_rows", {"rules": [{"label": "x", "conditions": MISSING}]}),
        ("conditional_copy", {"conditions": MISSING, "column": "品名", "name": "x"}),
    ],
)
def test_condition_on_a_missing_column(name, params):
    """F-045：條件引用的欄不存在時是「欄位不存在：…」，不是 Polars 的原文（中英文畫面都一樣）。"""
    df = frame({"品名": ["筆"]})
    with pytest.raises(RunError, match=r"欄位不存在：zz$"):
        apply_step(make(name, **params), df)
    i18n.set_lang("en")
    with pytest.raises(RunError, match=r"Column not found: zz$"):
        apply_step(make(name, **params), df)


# ── F-046：步驟的幾個小問題 ──────────────────────────────────────────


def test_fill_down_with_a_column_listed_twice():
    """F-046：向下填滿：同一欄列兩次時只填一次（不是 Polars 的錯誤）。"""
    out = apply_step(make("fill_down", columns=["門市", "門市"]), frame({"門市": ["北", None]}))[0]
    assert out["門市"].to_list() == ["北", "北"]


def test_trim_clean_turns_line_breaks_into_spaces():
    """F-046：清理文字的「移除看不見的字元」把格子裡的換行換成空白（前後的字不會黏在一起），說明也寫出來。"""
    field = get("trim_clean").model_json_schema()["properties"]["invisible"]
    assert "換行" in field["description"] and "line breaks" in field["en"]["description"]
    step = make("trim_clean", columns=["備註"])
    out = apply_step(step, frame({"備註": ["a\nb\tc", "x\r\ny\rz", "\u200b末\n"]}))[0]
    assert out["備註"].to_list() == ["a b\tc", "x y z", "末"]


@pytest.mark.parametrize(
    ("replace", "expected"),
    [("$1x", "Ax"), ("${1}x", "Ax"), ("$2$1", "1A"), ("$$1", "$1"), ("[$1]", "[A]")],
)
def test_replace_values_group_followed_by_letters(replace, expected):
    """F-046：取代文字的正規表示式模式：「換成 $1x」是第 1 個群組後面接 x（不是名叫 1x 的群組）。"""
    step = make(
        "replace_values",
        columns=["備註"],
        match="regex",
        replacements=[{"find": r"^(\w)-(\d)$", "replace": replace}],
    )
    assert apply_step(step, frame({"備註": ["A-1"]}))[0]["備註"].to_list() == [expected]


def test_replace_values_contains_keeps_dollar_signs():
    """F-046：部分相符（一般文字）時「換成」裡的 $ 就是 $。"""
    step = make("replace_values", columns=["備註"], replacements=[{"find": "-", "replace": "$1"}])
    assert apply_step(step, frame({"備註": ["A-1"]}))[0]["備註"].to_list() == ["A$11"]


def test_parse_date_year_zero_is_a_failed_conversion():
    """F-046：西元 0000 年轉不成日期（產生警告），不會讓預覽表格取這一列時失敗。"""
    ad = make("parse_date", columns=["日期"], pattern="dash", calendar="ad")
    out, total, warnings = apply_step(ad, frame({"日期": ["0000-01-01", "2026-01-05"]}))
    assert out["日期"].to_list() == [None, date(2026, 1, 5)]
    assert total == 1 and warnings[0].line_no == 2
    list(out.iter_rows())  # 不會丟出 ValueError
    roc = make("parse_date", columns=["日期"], pattern="slash", calendar="roc")
    assert str(apply_step(roc, frame({"日期": ["0/01/01"]}))[0]["日期"][0]) == "1911-01-01"


@pytest.mark.parametrize("name", ["dedupe", "unpivot", "group_by", "rename_columns"])
def test_suggestions_skip_system_columns(name):
    """F-046：在系統欄位上新增步驟時，建議的參數不會是那個系統欄位（一套用就是「欄位不存在」）。"""
    df = frame({"門市": ["北", "中"], "金額": [1, 2]})
    for column in (SOURCE_FILE, LINE_NO):
        params = get(name).suggest(df, {"column": column})
        assert SYSTEM_PREFIX not in json.dumps(params, ensure_ascii=False), params
    assert get(name).suggest(df, {"column": "門市"})  # 一般的欄照樣建議


def test_join_duplicate_keys_point_to_each_file():
    """F-046：另一張表的鍵重複時，錯誤訊息裡每個行號寫在它自己的檔底下。"""
    right = frame(
        {"代號": ["P1", "P2", "P3", "P1"], "類別": ["甲", "乙", "丙", "丁"]},
        ["lk/a.csv", "lk/a.csv", "lk/b.csv", "lk/b.csv"],
    )
    step = make("join", right="對照", on=[{"left": "品號", "right": "代號"}], columns=["類別"])
    with pytest.raises(RunError, match=r"「P1」（lk/a\.csv 第 2 行；lk/b\.csv 第 3 行）"):
        apply_step(step, frame({"品號": ["P1"]}), tables={"對照": right})
    same = frame({"代號": ["P1", "P1"], "類別": ["甲", "乙"]}, ["lk/a.csv"] * 2)
    with pytest.raises(RunError, match=r"「P1」（lk/a\.csv 第 2、3 行）"):
        apply_step(step, frame({"品號": ["P1"]}), tables={"對照": same})
    i18n.set_lang("en")
    with pytest.raises(RunError, match=r'"P1" \(lk/a\.csv, line 2; lk/b\.csv, line 3\)'):
        apply_step(step, frame({"品號": ["P1"]}), tables={"對照": right})


def test_join_checks_the_other_table_for_duplicates_once(monkeypatch):
    """F-046：合併來源檔之前每個來源檔各自結合：另一張表的重複檢查只算一次。"""
    from tablade.steps import join as join_module

    calls = []
    original = join_module.collect_small
    monkeypatch.setattr(join_module, "collect_small", lambda lf: calls.append(1) or original(lf))
    right = frame({"代號": ["P1", "P2"], "類別": ["甲", "乙"]}, ["lk.csv"] * 2)
    step = make("join", right="對照", on=[{"left": "品號", "right": "代號"}], columns=["類別"])
    bound = step.bind({"對照": right})
    left = frame({"品號": ["P1", "P2", "P1"]}, ["a.csv", "b.csv", "c.csv"])
    out = concat_parts(apply_parts(bound, split_parts(left))).collect()
    assert out["類別"].to_list() == ["甲", "乙", "甲"] and len(calls) == 1
    # 換了另一張表（重新 bind）就重新檢查
    dup = frame({"代號": ["P1", "P1"], "類別": ["甲", "乙"]}, ["lk.csv"] * 2)
    with pytest.raises(RunError, match="重複"):
        apply_step(step, left, tables={"對照": dup})


def test_number_conditions_do_not_treat_inf_or_nan_as_numbers():
    """F-046（另一位審查者回報）：「inf」「NaN」這類字不算數字，數值條件不符合。"""
    df = frame({"品名": ["a", "b", "c", "d"], "金額": ["inf", "NaN", "-inf", "1,200"]})
    assert kept(make("filter", conditions=[{"column": "金額", "op": "gt", "value": 500}]), df) == [
        "d"
    ]
    assert kept(make("filter", conditions=[{"column": "金額", "op": "lt", "value": 500}]), df) == []


# ── F-073：每個運算子、每種彙總方式各跑一次 ──────────────────────────────

OP_VALUES = ["10", "2.5", "Abc", None, "", "1,000", "abc-1"]
"""條件比對的值：數字、小數、大小寫、空值、空字串、千分位、文字與數字混在一起。"""

OP_CASES = [
    ({"op": "equals", "value": "abc"}, []),
    ({"op": "equals", "value": "abc", "ignore_case": True}, [2]),
    ({"op": "not_equals", "value": "abc"}, [0, 1, 2, 3, 4, 5, 6]),  # 空值當成空字串，也「不等於」
    ({"op": "contains", "value": "bc"}, [2, 6]),
    ({"op": "contains", "value": "BC", "ignore_case": True}, [2, 6]),
    ({"op": "not_contains", "value": "bc"}, [0, 1, 3, 4, 5]),
    ({"op": "starts_with", "value": "1"}, [0, 5]),
    ({"op": "starts_with", "value": "a", "ignore_case": True}, [2, 6]),
    ({"op": "ends_with", "value": "1"}, [6]),
    ({"op": "regex", "pattern": r"^\d"}, [0, 1, 5]),
    ({"op": "not_regex", "pattern": r"^\d"}, [2, 3, 4, 6]),
    ({"op": "is_empty"}, [3, 4]),
    ({"op": "is_not_empty"}, [0, 1, 2, 5, 6]),
    ({"op": "gt", "value": 2.5}, [0, 5]),  # 千分位也算數字
    ({"op": "ge", "value": 2.5}, [0, 1, 5]),
    ({"op": "lt", "value": 10.0}, [1]),
    ({"op": "le", "value": 10.0}, [0, 1]),
    ({"op": "num_equals", "value": 1000.0}, [5]),
]


def _op_id(case) -> str:
    condition, _ = case
    return condition["op"] + ("（不分大小寫）" if condition.get("ignore_case") else "")


@pytest.fixture(params=OP_CASES, ids=[_op_id(c) for c in OP_CASES])
def op_case(request):
    condition, matched = request.param
    return {"column": "值", **condition}, matched


def test_filter_with_every_operator(op_case):
    """F-073：篩選的每個運算子（含「≥」「<」與不分大小寫）各跑一次，保留的列正確。"""
    condition, matched = op_case
    df = frame({"值": OP_VALUES, "序": list(range(len(OP_VALUES)))})
    assert kept(make("filter", conditions=[condition]), df, "序") == matched
    excluded = make("filter", conditions=[condition], keep="non_matching")
    assert kept(excluded, df, "序") == [i for i in range(len(OP_VALUES)) if i not in matched]


def test_classify_rows_with_every_operator(op_case):
    """F-073：將列進行分類帶著每一種條件：符合的列是「是」，其他是「其他」。"""
    condition, matched = op_case
    step = make("classify_rows", rules=[{"label": "是", "conditions": [condition]}])
    out = apply_step(step, frame({"值": OP_VALUES}))[0]
    assert out["類型"].to_list() == [
        "是" if i in matched else "其他" for i in range(len(OP_VALUES))
    ]


def test_conditional_copy_with_every_operator(op_case):
    """F-073：依條件複製欄位帶著每一種條件：符合的列複製「序」，其他留空。"""
    condition, matched = op_case
    step = make("conditional_copy", conditions=[condition], column="序", name="複製")
    out = apply_step(step, frame({"值": OP_VALUES, "序": [str(i) for i in range(len(OP_VALUES))]}))[
        0
    ]
    assert out["複製"].to_list() == [
        str(i) if i in matched else None for i in range(len(OP_VALUES))
    ]


GROUPS = {"門市": ["北", "北", "北", "中"], "金額": [1, 5, None, 3]}


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ("sum", [6, 3]),
        ("mean", [3.0, 3.0]),
        ("max", [5, 3]),
        ("min", [1, 3]),
        ("count", [2, 1]),  # 有值的筆數：不算空值
        ("n_unique", [2, 1]),  # 不同值個數：也不算空值（F-046）
        ("first", [1, 3]),
        ("last", [None, 3]),
    ],
)
def test_group_by_with_every_aggregation(op, expected):
    """F-073：群組彙總的每一種彙總方式（含「最小」）各跑一次。"""
    step = make("group_by", by=["門市"], aggregations=[{"column": "金額", "op": op, "name": "x"}])
    out = apply_step(step, frame(GROUPS))[0]
    assert out["門市"].to_list() == ["北", "中"] and out["x"].to_list() == expected


def test_group_by_count_and_distinct_agree_on_nulls():
    """F-046：「有值的筆數」與「不同值個數」都不算空值，兩個數字對得起來；空字串算一個值。"""
    step = make(
        "group_by",
        by=["門市"],
        aggregations=[
            {"column": "類別", "op": "count", "name": "有值"},
            {"column": "類別", "op": "n_unique", "name": "不同"},
        ],
        count_as="",
    )
    df = frame({"門市": ["北", "北", "北", "中"], "類別": ["x", None, "", None]})
    out = apply_step(step, df)[0]
    assert out.select("有值", "不同").rows() == [(2, 2), (0, 0)]


PIVOT = {
    "門市": ["北", "北", "北", "中"],
    "月份": ["1月", "1月", "2月", "1月"],
    "金額": [1, 5, 2, 3],
}


@pytest.mark.parametrize(
    ("op", "north", "middle"),
    [
        ("sum", [6, 2], [3, None]),
        ("mean", [3.0, 2.0], [3.0, None]),
        ("max", [5, 2], [3, None]),
        ("min", [1, 2], [3, None]),
        ("count", [2, 1], [1, None]),  # 沒有任何一列的格子是空值（和 Excel 的樞紐分析表一樣是空白）
        ("first", [1, 2], [3, None]),
        ("last", [5, 2], [3, None]),
    ],
)
def test_pivot_with_every_aggregation(op, north, middle):
    """F-073：樞紐的每一種彙總方式（含「最大」「平均」「最小」）各跑一次。"""
    step = make("pivot", index=["門市"], on="月份", values="金額", op=op, columns=["1月", "2月"])
    out = apply_step(step, frame(PIVOT))[0]
    assert out.select("1月", "2月").rows() == [tuple(north), tuple(middle)]


def test_split_column_can_keep_the_source():
    """F-073：拆欄的「保留來源欄」：來源欄留著，拆出的欄接在它後面。"""
    step = make(
        "split_column", column="備註", delimiter="|", names=["通路", "付款"], keep_source=True
    )
    out = apply_step(step, frame({"備註": ["門市|現金", "網路|刷卡"], "金額": [1, 2]}))[0]
    assert data_of(out) == {
        "備註": ["門市|現金", "網路|刷卡"],
        "通路": ["門市", "網路"],
        "付款": ["現金", "刷卡"],
        "金額": [1, 2],
    }


# ── F-074：新增步驟的建議值、結合的說明與警告 ────────────────────────────

SUGGEST_CONTEXTS = [
    {},
    {"column": "門市"},
    {"column": SOURCE_FILE},
    {"column": LINE_NO},
    {"column": "門市", "value": "北區門市"},
    {"column": "門市", "value": None},
    {"column": "品名", "value": ""},
]
"""點在一般欄、系統欄、有值、空值、空字串的格子上新增步驟（表格的右鍵選單、步驟清單的「新增」）。"""

BROKEN = ("不存在", "找不到", "系統欄位", "只能用在文字欄", "not found", "does not exist")
"""建議的參數一套用就出現的錯誤（欄位不對）；要使用者填的「必填」、資料本身的問題（鍵重複）不算。"""


@pytest.fixture(scope="module")
def combined(tmp_path_factory):
    """多階段的範例，載入資料，停在最後一個階段（前面有可以結合的階段）。"""
    from tablade.session import Session

    work = tmp_path_factory.mktemp("suggest")
    write_all(work / "data")
    shutil.copy(COMBINED, work / COMBINED.name)
    s = Session()
    s.load_recipe(work / COMBINED.name)
    s.load_data()
    return s


@pytest.mark.parametrize("name", [cls.type_name() for cls in all_steps()])
def test_suggested_parameters_work_on_real_data(combined, name):
    """F-074：每種步驟、每種點法：建議的參數不含系統欄位，套用後沒有「欄位不存在」這類錯誤。"""
    s = combined
    for context in SUGGEST_CONTEXTS:
        s.select(len(s.steps) - 1)
        index = s.add_step(name, context)
        try:
            step = s.state()["steps"][index]
            params = {k: v for k, v in step["params"].items() if k not in ("id", "type")}
            assert SYSTEM_PREFIX not in json.dumps(params, ensure_ascii=False), (context, params)
            problems = [e["error"] for e in step["errors"]] + [step["run_error"] or ""]
            assert not [p for p in problems if any(b in p for b in BROKEN)], (context, problems)
        finally:
            s.undo()


@pytest.mark.parametrize("name", [cls.type_name() for cls in all_steps()])
def test_suggestions_without_data(tmp_path, name):
    """F-074：還沒載入資料時也可以新增每種步驟（建議值用空的表，不會出錯）。"""
    from tablade.session import Session

    s = Session()
    s.new(tmp_path / "x.recipe.json")
    for context in ({}, {"column": "門市", "value": "北"}):
        index = s.add_step(name, context)
        assert s.steps[index]["type"] == name


def test_trim_clean_suggests_text_columns_only():
    """F-074：清理文字點在系統欄位、日期或數字欄上時，建議全部的文字欄（不建議套用就出錯的欄）。"""
    df = frame({"日期": [date(2026, 1, 1)], "品名": ["筆"], "金額": [1]})
    for column in (SOURCE_FILE, "日期", "金額"):
        assert get("trim_clean").suggest(df, {"column": column}) == {"columns": ["品名"]}
    assert get("trim_clean").suggest(df, {"column": "品名"}) == {"columns": ["品名"]}
    assert get("replace_values").suggest(df, {"column": LINE_NO})["columns"] == ["品名"]


@pytest.mark.parametrize(
    ("params", "zh", "english"),
    [
        (
            {"how": "semi"},
            "只留在 @對照 找得到的列（品號＝代號）",
            "Keep rows found in @對照 (品號 = 代號)",
        ),
        (
            {"how": "anti"},
            "只留在 @對照 找不到的列（品號＝代號）",
            "Keep rows not found in @對照 (品號 = 代號)",
        ),
        (
            {"how": "left", "columns": []},
            "結合（左連接 @對照，品號＝代號）",
            "Join (left join with @對照 on 品號 = 代號)",
        ),
        (
            {"how": "inner", "columns": ["類別"]},
            "結合（內連接 @對照，品號＝代號）：帶進 類別",
            "Join (inner join with @對照 on 品號 = 代號): bring in 類別",
        ),
    ],
)
def test_join_describes_every_kind(params, zh, english):
    """F-074：結合的說明：只留有對到的、只留沒對到的、沒有要帶的欄、內連接。"""
    step = make("join", right="對照", on=[{"left": "品號", "right": "代號"}], **params)
    assert step.describe() == zh
    i18n.set_lang("en")
    assert step.describe() == english


def test_join_without_the_key_in_the_other_table():
    """F-074：另一張表沒有鍵的欄：不產生警告的檢查，套用時是「欄位不存在」。"""
    right = frame({"編號": ["P1"], "類別": ["甲"]}, ["lk.csv"])
    step = make("join", right="對照", on=[{"left": "品號", "right": "代號"}], columns=["類別"])
    assert step.bind({"對照": right}).warning_checks() == []
    with pytest.raises(RunError, match="代號"):
        apply_step(step, frame({"品號": ["P1"]}), tables={"對照": right})


# ── F-075：每種步驟都用難搞的資料跑一次 ───────────────────────────────────

HARD_VALUES = [None, "", "   ", "　", "20℃×3※", "ＡＢＣ１２３", "a{b}c"]
"""空值、空字串、只有空白、全形空白、cp950 的符號、全形英數字、含大括號的值。"""


def hardened(data: dict[str, list]) -> pl.DataFrame:
    """範例的輸入後面接上難搞的列（文字欄放 HARD_VALUES、其他欄空值）與一列重複的第一列，分在兩個檔。"""
    base = pl.DataFrame(data)
    strings = [c for c, dtype in base.schema.items() if dtype in (pl.String, pl.Null)]
    rows = base.to_dicts()
    extra = [{c: (v if c in strings else None) for c in base.columns} for v in HARD_VALUES]
    combined = pl.DataFrame(rows + extra + rows[:1], schema=base.schema, strict=False)
    n = combined.height
    files = ["a.csv"] * ((n + 1) // 2) + ["b.csv"] * (n // 2)
    return frame(combined.to_dict(as_series=False), files)


@pytest.mark.parametrize(("cls", "example"), list(_example_plans()))
def test_every_step_handles_hard_values(cls, example):
    """F-075：每種步驟的範例參數，輸入加上空值、空白、符號、重複的列（分在兩個檔）：執行不出錯（Polars 的原文、
    沒有包裝的例外都不行），列數合理（一列一列的步驟不會無故少列），中英文的警告都能產生。"""
    step = cls.model_validate({"id": "ex", "type": cls.type_name(), **example.params})
    tables = {
        name: frame(data, [f"{name}.csv"] * len(next(iter(data.values()))))
        for name, data in (example.tables or {}).items()
    }
    df = hardened(example.input)
    out, total, warnings = apply_step(step, df, tables=tables)
    assert total >= len(warnings)
    if not cls.reshapes and cls.type_name() not in {
        "remove_top_rows",
        "remove_bottom_rows",
        "remove_blank_rows",
        "dedupe",
        "filter",
        "promote_headers",
        "join",
    }:
        assert out.height == df.height  # 一列一列的步驟：每一列都還在
    i18n.set_lang("en")
    assert apply_step(step, df, tables=tables)[1] == total
