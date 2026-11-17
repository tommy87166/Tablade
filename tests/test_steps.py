"""步驟：registry 的範例（同時是文件）＋各步驟的邊界情況。"""

import re

import polars as pl
import pytest

from tablade.engine.run import RunError, apply_step
from tablade.recipe.validator import EffectError, apply_effect, resolve_effect
from tablade.steps import all_steps, get
from tablade.steps.base import LINE_NO, SOURCE_FILE, TEMP_PREFIX
from tablade.steps.headers import PromoteHeaders
from tablade.steps.split import SplitDelimited


def frame(data: dict, files: list[str] | None = None) -> pl.DataFrame:
    n = len(next(iter(data.values())))
    files = files or ["a.txt"] * n
    line_no, counter = [], {}
    for f in files:
        counter[f] = counter.get(f, 0) + 1
        line_no.append(counter[f])
    return pl.DataFrame({SOURCE_FILE: files, LINE_NO: line_no, **data}).with_columns(
        pl.col(LINE_NO).cast(pl.Int64)
    )


def make(type_name: str, **params):
    return get(type_name).model_validate({"id": "t", "type": type_name, **params})


def run(step, df):
    out, total, warnings = apply_step(step, df)
    return out, total, warnings


def data_of(df: pl.DataFrame) -> dict:
    return {c: df[c].to_list() for c in df.columns if not c.startswith("__")}


# ── registry 與範例 ───────────────────────────────────────────────

EXAMPLES = [
    pytest.param(cls, example, id=f"{cls.type_name()}:{example.title}")
    for cls in all_steps()
    for example in cls.examples
]


@pytest.mark.parametrize(("cls", "example"), EXAMPLES)
def test_registry_examples(cls, example):
    step = cls.model_validate({"id": "ex", "type": cls.type_name(), **example.params})
    out, _, _ = run(step, frame(example.input))
    assert data_of(out) == example.output
    assert not [c for c in out.columns if c.startswith(TEMP_PREFIX)]


def test_per_file_steps():
    """說明裡標「每個來源檔分別處理」的步驟。"""
    per_file = {cls.type_name() for cls in all_steps() if cls.per_file}
    assert per_file == {
        "remove_top_rows",
        "remove_bottom_rows",
        "promote_headers",
        "fill_down",
        "merge_files",
    }
    assert get("fill_down").summary().endswith("[每個來源檔分別處理]")
    assert get("filter").summary().endswith("[與來源檔分檔無關]")


@pytest.mark.parametrize(("cls", "example"), [p for p in EXAMPLES if not p.values[0].per_file])
def test_row_steps_ignore_file_boundaries(cls, example):
    """標「與來源檔分檔無關」的步驟：同樣的列切成兩個檔，結果不變。"""
    step = cls.model_validate({"id": "ex", "type": cls.type_name(), **example.params})
    n = len(next(iter(example.input.values())))
    split = ["a.txt"] * (n // 2) + ["b.txt"] * (n - n // 2)
    one, _, _ = run(step, frame(example.input))
    two, _, _ = run(step, frame(example.input, split))
    assert data_of(one) == data_of(two)


@pytest.mark.parametrize(("cls", "example"), EXAMPLES)
def test_effect_predicts_output_columns(cls, example):
    """驗證器用的欄位效果，必須和實際輸出的欄位一致。"""
    step = cls.model_validate({"id": "ex", "type": cls.type_name(), **example.params})
    predicted = apply_effect(
        list(example.input), resolve_effect(cls.effect, step.model_dump(mode="json"))
    )
    assert predicted == list(example.output)


def test_registry_metadata_is_complete():
    names = [cls.type_name() for cls in all_steps()]
    assert names == [
        "remove_top_rows",
        "remove_bottom_rows",
        "remove_blank_rows",
        "split_delimited",
        "split_column",
        "promote_headers",
        "fill_down",
        "remove_columns",
        "select_columns",
        "rename_columns",
        "trim_clean",
        "replace_values",
        "parse_number",
        "parse_date",
        "filter",
        "merge_files",
    ]
    for cls in all_steps():
        assert cls.label and cls.category and cls.__doc__ and cls.ai_notes, cls
        example = cls.examples[0]
        step = cls.model_validate({"id": "ex", "type": cls.type_name(), **example.params})
        assert step.describe()


# ── 移除列 ─────────────────────────────────────────────────────


def test_remove_top_and_bottom_rows_count_per_file():
    df = frame({"line": ["a1", "a2", "a3", "b1", "b2"]}, files=["a", "a", "a", "b", "b"])
    top, _, _ = run(make("remove_top_rows", n=1), df)
    assert top["line"].to_list() == ["a2", "a3", "b2"]
    bottom, _, _ = run(make("remove_bottom_rows", n=2), df)
    assert bottom["line"].to_list() == ["a1"]
    assert run(make("remove_top_rows", n=0), df)[0].height == 5


def test_steps_keep_the_input_order():
    """讀檔保證依來源檔與行號排好；步驟不排序（排序在串流下會把整份資料放進記憶體）。"""
    df = frame({"line": ["a1", "", "a3", "b1", "b2"]}, files=["a", "a", "a", "b", "b"])
    for step in (
        make("remove_top_rows", n=0),
        make("remove_blank_rows"),
        make("fill_down", columns=["line"]),
    ):
        out, _, _ = run(step, df)
        assert out["__line_no"].to_list() == sorted(out["__line_no"].to_list(), key=lambda _: 0)
        assert out["__source_file"].to_list() == sorted(out["__source_file"].to_list())


def test_remove_blank_rows_looks_at_data_columns_only():
    df = frame({"x": ["1", " ", None, ""], "y": ["", "\t", None, "2"]})
    out, _, _ = run(make("remove_blank_rows"), df)
    assert data_of(out) == {"x": ["1", ""], "y": ["", "2"]}


# ── 拆欄 ──────────────────────────────────────────────────────


def test_split_delimited_warns_with_line_numbers_on_extra_fields():
    df = frame({"line": ["a,b", "a,b,c,d", "", 'x,"y,z"']})
    step = make("split_delimited", columns=["c1", "c2"])
    out, total, warnings = run(step, df)
    assert data_of(out) == {"c1": ["a", "a", None, "x"], "c2": ["b", "b", None, "y,z"]}
    assert total == 1
    assert (warnings[0].line_no, warnings[0].message) == (
        2,
        "這一列拆出 4 段，超過 2 個欄位，多的段已捨棄",
    )


def test_split_delimited_merge_and_no_quote_and_keep_source():
    df = frame({"line": ["a;b;c", '"q";r']})
    merged, total, _ = run(
        make("split_delimited", delimiter=";", columns=["c1", "c2"], on_extra="merge"), df
    )
    assert merged["c2"].to_list() == ["b;c", "r"] and total == 0
    raw, _, _ = run(
        make("split_delimited", delimiter=";", quote="", columns=["c1", "c2"], keep_source=True), df
    )
    assert list(data_of(raw)) == ["line", "c1", "c2"]
    assert raw["c1"].to_list() == ["a", '"q"']


def test_split_delimited_suggest_columns():
    assert SplitDelimited.suggest_columns(["a,b", 'x,"y,z",w', None]) == ["欄1", "欄2", "欄3"]
    assert SplitDelimited.suggest_columns([]) == ["欄1"]


def test_split_column_warn_merge_trim_and_nulls():
    df = frame({"k": ["1", "2", "3"], "m": ["a | b | c", "a|b", None]})
    out, total, warnings = run(
        make("split_column", column="m", delimiter="|", names=["x", "y"]), df
    )
    assert data_of(out) == {"k": ["1", "2", "3"], "x": ["a", "a", None], "y": ["b", "b", None]}
    assert total == 1 and warnings[0].line_no == 1
    merged, total, _ = run(
        make(
            "split_column",
            column="m",
            delimiter="|",
            names=["x", "y"],
            on_extra="merge",
            trim=False,
        ),
        df,
    )
    assert merged["y"].to_list() == [" b | c", "b", None] and total == 0


def test_split_column_missing_column_is_a_step_error():
    with pytest.raises(RunError, match="欄位「nope」不存在"):
        run(make("split_column", column="nope", delimiter="|", names=["x"]), frame({"m": ["a"]}))


# ── 標題列 ─────────────────────────────────────────────────────


def test_promote_headers_per_file_and_warns_on_different_header():
    df = frame(
        {
            "c1": ["日期", "1", "日期", "2", "Date", "3"],
            "c2": ["門市", "A", "門市", "B", "Store", "C"],
        },
        files=["a", "a", "b", "b", "c", "c"],
    )
    out, total, warnings = run(PromoteHeaders(id="h", names=["日期", "門市"]), df)
    assert data_of(out) == {"日期": ["1", "2", "3"], "門市": ["A", "B", "C"]}
    assert total == 1 and (warnings[0].source_file, warnings[0].line_no) == ("c", 2)


def test_promote_headers_column_count_must_match():
    with pytest.raises(RunError, match="目前有 2 個資料欄"):
        run(PromoteHeaders(id="h", names=["only"]), frame({"a": ["1"], "b": ["2"]}))


def test_promote_headers_suggest_names():
    assert PromoteHeaders.suggest_names(["日期", " ", "金額", "金額", None, "__x"]) == [
        "日期",
        "欄2",
        "金額",
        "金額_2",
        "欄5",
        "x",
    ]


# ── 向下填滿 ───────────────────────────────────────────────────


def test_fill_down_does_not_cross_files_and_respects_blank_option():
    df = frame({"g": ["A", "", None, "", "B"]}, files=["a", "a", "a", "b", "b"])
    out, _, _ = run(make("fill_down", columns=["g"]), df)
    assert out["g"].to_list() == ["A", "A", "A", None, "B"]
    strict, _, _ = run(make("fill_down", columns=["g"], blank_as_null=False), df)
    assert strict["g"].to_list() == ["A", "", "", "", "B"]


# ── 刪除欄 ─────────────────────────────────────────────────────

FOUR = {"a": ["1"], "b": ["2"], "c": ["3"], "d": ["4"]}


def test_remove_columns_by_name_and_position_keep_system_columns():
    out, _, _ = run(make("remove_columns", columns=["d", "b"]), frame(FOUR))
    assert out.columns == [SOURCE_FILE, LINE_NO, "a", "c"]
    out, _, _ = run(make("remove_columns", by="position", positions=[4, 1, 4]), frame(FOUR))
    assert out.columns == [SOURCE_FILE, LINE_NO, "b", "c"]
    # 依位置時不用填 columns（反之亦然）
    assert make("remove_columns", by="position", positions=[2]).columns == []
    assert make("remove_columns", by="position", positions=[2], columns=["x"]).describe() == (
        "刪除第 2 欄"
    )


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"columns": ["nope"]}, "欄位不存在：nope"),
        ({"by": "position", "positions": [5]}, "目前只有 4 個資料欄，沒有第 5 欄"),
        ({"columns": ["a", "b", "c", "d"]}, "不能刪除所有資料欄"),
    ],
)
def test_remove_columns_errors(params, message):
    with pytest.raises(RunError, match=message):
        run(make("remove_columns", **params), frame(FOUR))


def test_remove_columns_needs_something_picked():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="要選至少一個") as by_name:
        make("remove_columns")
    assert by_name.value.errors()[0]["loc"] == ("columns",)
    with pytest.raises(ValidationError, match="要選至少一個") as by_position:
        make("remove_columns", by="position")
    assert by_position.value.errors()[0]["loc"] == ("positions",)
    with pytest.raises(ValidationError):
        make("remove_columns", by="position", positions=[0])
    effect = {"kind": "drop", "by": "position", "names": [], "positions": []}
    with pytest.raises(EffectError, match="要選至少一個"):
        apply_effect(["a"], effect)
    assert apply_effect(None, {**effect, "positions": [3]}) is None  # 還不知道有哪些欄


def test_select_columns_order_and_errors():
    out, _, _ = run(make("select_columns", columns=["c", "a"]), frame(FOUR))
    assert out.columns == [SOURCE_FILE, LINE_NO, "c", "a"]
    with pytest.raises(RunError, match="欄位不存在：x"):
        run(make("select_columns", columns=["a", "x"]), frame(FOUR))
    with pytest.raises(RunError, match="欄位重複：a"):
        run(make("select_columns", columns=["a", "b", "a"]), frame(FOUR))
    assert make("select_columns", columns=["a", "b"]).describe() == "保留 2 欄：a、b"


def test_rename_columns_swap_and_errors():
    out, _, _ = run(make("rename_columns", names={"a": "b", "b": "a"}), frame(FOUR))
    assert out.columns == [SOURCE_FILE, LINE_NO, "b", "a", "c", "d"]
    assert out["b"].to_list() == ["1"]  # 原本的 a
    for names, message in [
        ({"x": "y"}, "欄位不存在：x"),
        ({"a": "b"}, "改完後欄名重複：b"),
        ({"a": "__x"}, "不能以 __ 開頭"),
    ]:
        with pytest.raises(RunError, match=message):
            run(make("rename_columns", names=names), frame(FOUR))
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make("rename_columns", names={})
    with pytest.raises(ValidationError):
        make("rename_columns", names={"a": ""})
    assert make("rename_columns", names={"a": "甲"}).describe() == "改欄名：a→甲"


# ── 文字 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("params", "value", "expected"),
    [
        ({}, "\u3000ＡＢ１２３，（總店）\u200b ", "AB123，（總店）"),
        ({"width": "nfkc"}, "ＡＢ１２３，（總店）①", "AB123,(總店)1"),
        ({"width": "none"}, "ＡＢ１ ", "ＡＢ１"),
        ({"trim": False}, " a ", " a "),
        ({"invisible": False}, "a\u200bb", "a\u200bb"),
        ({}, "a\tb\x07c\ufeff", "a\tbc"),  # Tab 保留，控制字元與 BOM 移除
        ({"collapse_spaces": True}, "北區　 門市  總店", "北區 門市 總店"),
        ({}, None, None),
    ],
)
def test_trim_clean_options(params, value, expected):
    out, _, _ = run(make("trim_clean", columns=["t"], **params), frame({"t": [value]}))
    assert out["t"].to_list() == [expected]


def test_text_steps_need_text_columns():
    df = frame({"n": ["1"]}).with_columns(pl.col("n").cast(pl.Int64))
    for step in (
        make("trim_clean", columns=["n"]),
        make("replace_values", columns=["n"], replacements=[{"find": "1", "replace": "2"}]),
    ):
        with pytest.raises(RunError, match="只能用在文字欄：n 不是文字"):
            run(step, df)
    with pytest.raises(RunError, match="欄位不存在：x"):
        run(make("trim_clean", columns=["x"]), df)


def test_replace_values_modes_in_order():
    df = frame({"t": ["aab", "ab", None, "b"]})
    pairs = [{"find": "a", "replace": "b"}, {"find": "bb", "replace": "c"}]
    out, _, _ = run(make("replace_values", columns=["t"], replacements=pairs), df)
    assert out["t"].to_list() == ["cb", "c", None, "b"]  # 依序：先 a→b，再 bb→c
    whole, _, _ = run(
        make(
            "replace_values",
            columns=["t"],
            match="whole",
            replacements=[{"find": "ab", "replace": "X"}],
        ),
        df,
    )
    assert whole["t"].to_list() == ["aab", "X", None, "b"]
    regex, _, _ = run(
        make(
            "replace_values",
            columns=["t"],
            match="regex",
            replacements=[{"find": "^(a+)(b)$", "replace": "$2-$1"}],
        ),
        df,
    )
    assert regex["t"].to_list() == ["b-aa", "b-a", None, "b"]
    # 一般文字模式不把 $ 當成群組，也不把 . 當成任意字元
    literal, _, _ = run(
        make("replace_values", columns=["t"], replacements=[{"find": ".", "replace": "$1"}]),
        frame({"t": ["a.b", "ab"]}),
    )
    assert literal["t"].to_list() == ["a$1b", "ab"]


def test_replace_values_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="正規表示式有誤"):
        make(
            "replace_values",
            columns=["t"],
            match="regex",
            replacements=[{"find": "(", "replace": ""}],
        )
    with pytest.raises(ValidationError):
        make("replace_values", columns=["t"], replacements=[])
    with pytest.raises(ValidationError):
        make("replace_values", columns=["t"], replacements=[{"find": "", "replace": "x"}])
    # 一般文字模式的 ( 不是正規表示式，不會擋
    assert make("replace_values", columns=["t"], replacements=[{"find": "(", "replace": ""}])
    step = make(
        "replace_values",
        columns=["t"],
        match="whole",
        replacements=[{"find": str(i), "replace": ""} for i in range(5)],
    )
    assert step.describe() == "取代（整格相符）：「0」→「」、「1」→「」、「2」→「」…共 5 組，欄位 t"


def test_validator_checks_new_effects_and_maps():
    from tablade.recipe.validator import EffectError, apply_effect

    assert apply_effect(["a", "b"], {"kind": "rename", "names": {"a": "b", "b": "a"}}) == ["b", "a"]
    assert apply_effect(None, {"kind": "select", "names": ["x"]}) is None
    with pytest.raises(EffectError, match="要填至少一個新欄名"):
        apply_effect(["a"], {"kind": "rename", "names": {}})
    with pytest.raises(EffectError, match="要選至少一個要保留的欄位"):
        apply_effect(["a"], {"kind": "select", "names": []})


# ── 轉型別 ─────────────────────────────────────────────────────


def test_parse_number_formats_rounding_and_warnings():
    from decimal import Decimal

    values = ["1,234.5", " (1,000) ", "1,000-", "+5", "1e3", "12.345", "-12.345", "1 234", "", None]
    df = frame({"金額": values + ["abc", "$100", "--5"]})
    out, total, warnings = run(make("parse_number", columns=["金額"]), df)
    assert out.schema["金額"] == pl.Decimal(38, 2)
    assert out["金額"].to_list() == [
        Decimal("1234.50"),
        Decimal("-1000.00"),
        Decimal("-1000.00"),
        Decimal("5.00"),
        Decimal("1000.00"),
        Decimal("12.35"),  # 四捨五入，不是銀行家捨入
        Decimal("-12.35"),
        Decimal("1234.00"),
        None,
        None,
        None,
        None,
        None,
    ]
    # 空白不算失敗；有值但轉不了的才警告，訊息帶原始值與行號
    assert total == 3 and [w.line_no for w in warnings] == [11, 12, 13]
    assert warnings[0].message == "「金額」的「abc」不是數字，已改成空值"
    zero, _, _ = run(make("parse_number", columns=["金額"], scale=0), frame({"金額": ["2.5"]}))
    assert zero["金額"].to_list() == [Decimal("3")]


def test_parse_number_integers_and_several_columns():
    df = frame({"a": ["12", "12.0", "12.5", "-3"], "b": ["1", "2", "x", "4"], "c": ["k"] * 4})
    out, total, warnings = run(make("parse_number", columns=["a", "b"], kind="integer"), df)
    assert out.schema["a"] == pl.Int64 and out.schema["b"] == pl.Int64
    assert out["a"].to_list() == [12, 12, None, -3] and out["b"].to_list() == [1, 2, None, 4]
    assert out["c"].to_list() == ["k"] * 4
    assert total == 2
    assert {w.message for w in warnings} == {
        "「a」的「12.5」不是整數，已改成空值",
        "「b」的「x」不是整數，已改成空值",
    }
    # 原本就不是文字的欄也可以轉；欄位不存在是步驟錯誤
    again, total, _ = run(make("parse_number", columns=["a"]), out)
    assert again["a"].to_list()[0] == 12 and total == 0
    with pytest.raises(RunError, match="欄位不存在：nope"):
        run(make("parse_number", columns=["nope"]), df)


def parse_date(pattern: str, calendar: str, **extra):
    return make("parse_date", columns=["d"], pattern=pattern, calendar=calendar, **extra)


@pytest.mark.parametrize(
    ("pattern", "calendar", "value", "expected"),
    [
        ("slash", "roc", "115/01/05", "2026-01-05"),
        ("slash", "roc", " 115/1/5 ", "2026-01-05"),  # 先去掉前後空白
        ("slash", "roc", "99/12/31", "2010-12-31"),
        ("slash", "roc", "2026/01/05", None),  # 民國年最多 3 位數
        ("slash", "ad", "2026/01/05", "2026-01-05"),
        ("slash", "ad", "115/01/05", None),  # 西元年要 4 位數：不會變成西元 115 年
        ("slash", "roc", "115-01-05", None),  # 每個樣式只認自己的寫法
        ("dash", "roc", "115-1-5", "2026-01-05"),
        ("dash", "ad", "2026-01-05", "2026-01-05"),
        ("dot", "roc", "115.01.05", "2026-01-05"),
        ("chinese", "roc", "115 年 1 月 5 日", "2026-01-05"),
        ("chinese", "ad", "2026年1月5日", "2026-01-05"),
        ("compact", "roc", "1150105", "2026-01-05"),
        ("compact", "roc", "991231", "2010-12-31"),
        ("compact", "ad", "20260105", "2026-01-05"),
        ("compact", "ad", "1150105", None),
        ("slash", "roc", "115/02/30", None),  # 不存在的日期
        ("slash", "roc", "115/13/01", None),
        ("slash", "ad", "2026/01/05 13:45", None),  # 帶時間
    ],
)
def test_parse_date_patterns(pattern, calendar, value, expected):
    out, total, _ = run(parse_date(pattern, calendar), frame({"d": [value]}))
    assert out.schema["d"] == pl.Date
    got = out["d"].to_list()[0]
    assert (got.isoformat() if got else None) == expected
    assert total == (0 if expected else 1)


def test_parse_date_custom_regex_needs_three_groups():
    from pydantic import ValidationError

    custom = parse_date("custom", "ad", regex=r"^(\d{2})/(\d{2})/(\d{4})$")
    assert custom.describe() == "轉成日期（/^(\\d{2})/(\\d{2})/(\\d{4})$/，西元年）：d"
    # 三個群組一定是年、月、日的順序：這裡的「年」是 01，西元年不是 4 位數 → 轉換失敗
    out, total, _ = run(custom, frame({"d": ["01/05/2026"]}))
    assert out["d"].to_list() == [None] and total == 1
    named = parse_date("custom", "roc", regex=r"^(?P<y>\d+)年(?P<m>\d+)月(?P<d>\d+)日$")
    assert run(named, frame({"d": ["115年1月5日"]}))[0]["d"].to_list()[0].isoformat() == (
        "2026-01-05"
    )
    for regex, message in [
        ("", "要輸入正規表示式"),
        (r"^(\d+)/(\d+)$", "要剛好三個群組（年、月、日），這個正規表示式有 2 個"),
        (r"^(\d+)/(\d+)/(\d+)/(\d+)$", "這個正規表示式有 4 個"),
        ("^(\\d+", "正規表示式有誤"),
    ]:
        with pytest.raises(ValidationError, match=re.escape(message)):
            parse_date("custom", "ad", regex=regex)
    # 不是自訂時不看 regex
    assert parse_date("slash", "ad", regex="(").active_regex().startswith("^(")


def test_parse_date_needs_pattern_and_calendar():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as missing:
        make("parse_date", columns=["d"])
    assert {e["loc"][0] for e in missing.value.errors()} == {"pattern", "calendar"}
    with pytest.raises(ValidationError):
        parse_date("auto", "roc")


def test_parse_date_blank_is_not_a_warning():
    out, total, warnings = run(parse_date("slash", "roc"), frame({"d": ["", "  ", None, "x"]}))
    assert out["d"].to_list() == [None] * 4
    assert total == 1 and warnings[0].message == "「d」的「x」不是日期，已改成空值"
    assert parse_date("compact", "roc").describe() == "轉成日期（連寫，月日各 2 碼，民國年）：d"


# ── 合併來源檔 ─────────────────────────────────────────────────


def test_merge_files_relabels_and_renumbers():
    df = frame({"line": ["a1", "a2", "b1", "b2", "c1"]}, files=["a", "a", "b", "b", "c"])
    out, _, _ = run(make("merge_files"), df)
    assert out["line"].to_list() == ["a1", "a2", "b1", "b2", "c1"]
    assert out[SOURCE_FILE].to_list() == ["合併"] * 5
    assert out[LINE_NO].to_list() == [1, 2, 3, 4, 5] and out[LINE_NO].dtype == pl.Int64


def test_merge_files_order_puts_listed_files_first():
    df = frame({"line": ["a1", "a2", "b1", "c1"]}, files=["a", "a", "b", "c"])
    out, _, _ = run(make("merge_files", order=["c", "x", "a"], name="全部"), df)
    assert out["line"].to_list() == ["c1", "a1", "a2", "b1"]  # 沒有的 x 略過，沒列到的 b 接在後面
    assert set(out[SOURCE_FILE]) == {"全部"}
    assert make("merge_files", order=["c", "a"]).describe() == "依「c、a」的順序合併成「合併」"


def test_per_file_steps_see_one_file_after_merge():
    df = frame(
        {"line": ["a1", "a2", "b1", "b2"], "g": ["A", "", "", "B"]}, files=["a", "a", "b", "b"]
    )
    merged, _, _ = run(make("merge_files"), df)
    top, _, _ = run(make("remove_top_rows", n=1), merged)
    assert top["line"].to_list() == ["a2", "b1", "b2"]  # 只去掉整份的第一列
    filled, _, _ = run(make("fill_down", columns=["g"]), merged)
    assert filled["g"].to_list() == ["A", "A", "A", "B"]  # 會跨過原本的檔界


# ── 篩選 ──────────────────────────────────────────────────────

FILTER_DATA = {
    "name": ["Apple", "banana", "Cherry", None, ""],
    "amount": ["1,200", "50", "abc", "300", "(20)"],
}


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"op": "equals", "column": "name", "value": "Apple"}, ["Apple"]),
        ({"op": "equals", "column": "name", "value": "apple", "ignore_case": True}, ["Apple"]),
        ({"op": "not_equals", "column": "name", "value": "Apple"}, ["banana", "Cherry", ""]),
        ({"op": "contains", "column": "name", "value": "an"}, ["banana"]),
        ({"op": "not_contains", "column": "name", "value": "an"}, ["Apple", "Cherry", ""]),
        ({"op": "starts_with", "column": "name", "value": "C"}, ["Cherry"]),
        ({"op": "ends_with", "column": "name", "value": "y"}, ["Cherry"]),
        ({"op": "regex", "column": "name", "pattern": "^[A-Z]"}, ["Apple", "Cherry"]),
        ({"op": "not_regex", "column": "name", "pattern": "^[A-Z]"}, ["banana", ""]),
        ({"op": "is_empty", "column": "name"}, [None, ""]),
        ({"op": "is_not_empty", "column": "name"}, ["Apple", "banana", "Cherry"]),
        ({"op": "gt", "column": "amount", "value": 100}, ["Apple", None]),
        ({"op": "le", "column": "amount", "value": 50.0}, ["banana"]),
        ({"op": "num_equals", "column": "amount", "value": 1200}, ["Apple"]),
    ],
)
def test_filter_conditions(condition, expected):
    out, _, _ = run(make("filter", conditions=[condition]), frame(FILTER_DATA))
    assert out["name"].to_list() == expected


def test_filter_any_and_non_matching():
    conditions = [
        {"op": "equals", "column": "name", "value": "Apple"},
        {"op": "equals", "column": "name", "value": "banana"},
    ]
    out, _, _ = run(make("filter", conditions=conditions, match="any"), frame(FILTER_DATA))
    assert out["name"].to_list() == ["Apple", "banana"]
    out, _, _ = run(
        make("filter", conditions=conditions, match="any", keep="non_matching"), frame(FILTER_DATA)
    )
    assert out["name"].to_list() == ["Cherry", None, ""]


def test_describe_texts():
    assert make("remove_top_rows", n=3).describe() == "移除每個檔案的前 3 列"
    step = make(
        "filter",
        conditions=[
            {"op": "starts_with", "column": "金額", "value": "("},
            {"op": "gt", "column": "金額", "value": 5},
        ],
        keep="non_matching",
    )
    assert step.describe() == "排除：金額 開頭是「(」 且 金額 > 5"


# ── split_delimited 的原生正規表示式要和 Python 的 csv 一致 ─────────────


@pytest.mark.parametrize(
    "delimiter", [",", ";", "|", "\t", "/", "-", "#", "&", "~", ":", " ", "^", "."]
)
def test_split_delimited_matches_python_csv_on_well_formed_lines(delimiter):
    import csv as pycsv
    import io
    import random

    rng = random.Random(hash(delimiter) % 1000)
    alphabet = ["a", "門", "1", " ", ",", ";", "|", "\t", '"', "x", ".", "-"]
    lines = []
    for _ in range(300):
        fields = [
            "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))
            for _ in range(rng.randint(1, 5))
        ]
        buf = io.StringIO()
        pycsv.writer(buf, delimiter=delimiter, lineterminator="").writerow(fields)
        line = buf.getvalue()
        if "\n" not in line and "\r" not in line and line:
            lines.append(line)
    expected = [next(pycsv.reader([line], delimiter=delimiter)) for line in lines]
    width = max(len(e) for e in expected)
    step = make("split_delimited", delimiter=delimiter, columns=[f"c{i}" for i in range(width)])
    out, _, _ = run(step, frame({"line": lines}))
    got = [
        [v for v in row if v is not None]
        for row in out.select([f"c{i}" for i in range(width)]).iter_rows()
    ]
    assert got == [[f for f in e] for e in expected]
