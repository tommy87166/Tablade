"""畫面是英文時，標題列、拆欄、分段報表的步驟（名稱、說明、錯誤、警告）也是英文（docs/i18n-plan.md）。

欄名與值是中文（資料），只檢查句子本身。
"""

from __future__ import annotations

import re

import polars as pl
import pytest
from pydantic import ValidationError

from tablade import i18n
from tablade.engine.run import RunError, apply_step
from tablade.steps import get
from tablade.steps.base import LINE_NO, SOURCE_FILE
from tablade.steps.report import regex_groups

MINE = {
    "promote_headers": "Promote headers",
    "split_delimited": "Split into columns by delimiter",
    "split_column": "Split column",
    "classify_rows": "Classify rows",
    "extract_regex": "Extract with regex",
    "split_fixed_width": "Split by fixed width",
}
CJK = re.compile(r"[㐀-鿿]")


@pytest.fixture(autouse=True)
def english():
    i18n.set_lang("en")


def frame(data: dict) -> pl.DataFrame:
    n = len(next(iter(data.values())))
    return pl.DataFrame(
        {SOURCE_FILE: ["a.txt"] * n, LINE_NO: list(range(1, n + 1)), **data}
    ).with_columns(pl.col(LINE_NO).cast(pl.Int64))


def make(type_name: str, **params):
    return get(type_name).model_validate({"id": "t", "type": type_name, **params})


def test_names_and_descriptions_are_english():
    for type_name, label in MINE.items():
        cls = get(type_name)
        assert cls.display_label() == label
        assert cls.display_doc() and not CJK.search(cls.display_doc())
    i18n.set_lang("zh")
    assert get("promote_headers").display_label() == "把第一列作為欄位標題"


def test_form_text_is_english():
    """表單的欄位標題、說明、選項（含分類規則的巢狀表單）換成英文。"""
    from tablade.session import catalog

    items = {item["type"]: item for item in catalog() if item["type"] in MINE}
    assert set(items) == set(MINE)
    split = items["split_column"]["schema"]["properties"]
    assert split["column"]["title"] == "Column to split"
    assert split["on_extra"]["ui_labels"] == {
        "warn": "Warn and drop the extra parts",
        "merge": "Merge extra parts into the last column",
        "merge_into": "Merge extra parts into a chosen column",
    }
    assert split["merge_column"]["title"] == "Merge into column"
    rule = items["classify_rows"]["schema"]["$defs"]["Rule"]
    assert rule["title"] == "Classification rule"
    assert rule["properties"]["match"]["ui_labels"] == {
        "all": "All conditions",
        "any": "Any condition",
    }
    assert items["classify_rows"]["schema"]["properties"]["otherwise"]["default"] == "其他"


def test_describe_in_english():
    assert (
        make("promote_headers", names=["日期", "門市"]).describe()
        == "Use the first row as column names (日期, 門市)"
    )
    assert (
        make("split_delimited", columns=["欄1", "欄2"]).describe()
        == 'Split "line" by "," into 2 columns (欄1, 欄2)'
    )
    assert (
        make("split_column", column="備註", delimiter="|", names=["通路", "單號"]).describe()
        == 'Split "備註" by "|" into 通路, 單號'
    )
    rules = [{"label": "段落", "conditions": [{"column": "line", "op": "contains", "value": "x"}]}]
    assert (
        make("classify_rows", rules=rules).describe()
        == 'Classify rows into "類型": 段落; the rest are "其他"'
    )
    assert (
        make("classify_rows", rules=rules, otherwise="").describe()
        == 'Classify rows into "類型": 段落; the rest are null'
    )
    assert (
        make("extract_regex", column="line", pattern=r"^門市：(.+)$", names=["門市"]).describe()
        == 'Extract 門市 from "line" (/^門市：(.+)$/)'
    )
    assert (
        make("split_fixed_width", starts=[1, 12], names=["日期", "品號"]).describe()
        == 'Split "line" at positions 1, 12 into 日期, 品號'
    )


def test_warnings_in_english():
    _, total, warnings = apply_step(
        make("split_delimited", columns=["c1", "c2"]), frame({"line": ["a,b,c,d"]})
    )
    assert total == 1
    assert warnings[0].message == (
        "This row split into 4 parts, more than 2 columns; the extra parts were dropped"
    )
    _, _, warnings = apply_step(
        make("split_column", column="m", delimiter="|", names=["x"]), frame({"m": ["a|b"]})
    )
    assert warnings[0].message == (
        '"m" split into 2 parts, more than 1 new column; the extra parts were dropped'
    )
    _, _, warnings = apply_step(
        make("promote_headers", names=["日期"]), frame({"c1": ["Date", "1"]})
    )
    assert warnings[0].message == (
        "This file's header row differs from the column names in the Recipe; "
        "the Recipe's names are used"
    )


def test_step_errors_in_english():
    with pytest.raises(RunError, match=re.escape("The data has 2 columns, but 1 name is given")):
        apply_step(make("promote_headers", names=["only"]), frame({"a": ["1"], "b": ["2"]}))
    for step in (
        make("split_column", column="nope", delimiter="|", names=["x"]),
        make("extract_regex", column="nope", pattern="(a)", names=["x"]),
        make("split_fixed_width", column="nope", starts=[1], names=["x"]),
    ):
        with pytest.raises(RunError, match=re.escape('Column "nope" does not exist')):
            apply_step(step, frame({"m": ["a"]}))


@pytest.mark.parametrize(
    ("type_name", "params", "message"),
    [
        (
            "extract_regex",
            {"column": "line", "pattern": "abc", "names": ["x"]},
            "The regular expression has no groups: put the part to extract in ( )",
        ),
        (
            "extract_regex",
            {"column": "line", "pattern": "(a)(b)", "names": ["x"]},
            "The regular expression has 2 groups, but 1 new column name is given",
        ),
        (
            "split_fixed_width",
            {"starts": [0, 3], "names": ["x", "y"]},
            "Start positions begin at 1",
        ),
        (
            "split_fixed_width",
            {"starts": [5, 3], "names": ["x", "y"]},
            "Start positions must be in increasing order",
        ),
        (
            "split_fixed_width",
            {"starts": [1, 3, 5], "names": ["x", "y"]},
            "3 start positions, but 2 column names given",
        ),
    ],
)
def test_validation_errors_in_english(type_name, params, message):
    with pytest.raises(ValidationError) as err:
        make(type_name, **params)
    assert message in str(err.value)


def test_invalid_regex_in_english():
    with pytest.raises(ValueError, match=r"^Invalid regular expression: "):
        regex_groups("(")
