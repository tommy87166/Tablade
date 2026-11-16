"""步驟：registry 的範例（同時是文件）＋各步驟的邊界情況。"""

import polars as pl
import pytest

from tablade.engine.run import RunError, apply_step
from tablade.recipe.validator import apply_effect, resolve_effect
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
        "filter",
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


def test_remove_top_rows_ignores_physical_order_of_input():
    df = frame({"line": ["a1", "a2", "a3"]}).reverse()
    out, _, _ = run(make("remove_top_rows", n=1), df)
    assert out["line"].to_list() == ["a2", "a3"]


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
