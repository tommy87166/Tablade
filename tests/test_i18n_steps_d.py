"""步驟的英文（docs/i18n-plan.md 第 3、4 批）：篩選、欄位（刪除、保留和排序、改欄名）、移除列。

欄名是資料（中文），只檢查句子本身是英文。
"""

from __future__ import annotations

import re

import polars as pl
import pytest
from pydantic import ValidationError

from tablade import i18n
from tablade.steps import get
from tablade.steps.base import LINE_NO, SOURCE_FILE, StepError

CJK = re.compile(r"[㐀-鿿]")
TYPES = {
    "filter": "Filter rows",
    "remove_columns": "Remove columns",
    "select_columns": "Keep and reorder columns",
    "rename_columns": "Rename columns",
    "remove_top_rows": "Remove top rows",
    "remove_bottom_rows": "Remove bottom rows",
    "remove_blank_rows": "Remove blank rows",
    "dedupe": "Remove duplicates",
}


@pytest.fixture
def english():
    i18n.set_lang("en")


def make(type_name: str, **params):
    return get(type_name).model_validate({"id": "t", "type": type_name, **params})


@pytest.mark.parametrize("type_name", sorted(TYPES))
def test_names_and_docs_are_english(english, type_name):
    cls = get(type_name)
    assert cls.display_label() == TYPES[type_name]
    assert cls.display_doc() and not CJK.search(cls.display_doc())
    assert not CJK.search(cls.scope())


def test_names_stay_chinese_by_default():
    assert get("dedupe").display_label() == "移除重複"
    assert make("remove_blank_rows").describe() == "移除空白列"


@pytest.mark.parametrize("type_name", sorted(TYPES))
def test_form_text_is_english(english, type_name):
    """送給前端的 schema（localize_schema）裡，欄位標題、說明、選項都換成英文。"""
    schema = i18n.localize_schema(get(type_name).model_json_schema())
    found = []

    def walk(value, path=""):
        if isinstance(value, dict):
            for key in ("title", "description"):
                # 最上層的 description 是 docstring（畫面用 display_doc）
                if path and isinstance(value.get(key), str) and CJK.search(value[key]):
                    found.append(f"{path}.{key}")
            labels = value.get("ui_labels")
            if isinstance(labels, dict) and any(CJK.search(str(v)) for v in labels.values()):
                found.append(f"{path}.ui_labels")
            for key, child in value.items():
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")

    walk(schema)
    assert found == []


def test_filter_describe(english):
    step = make(
        "filter",
        conditions=[
            {"column": "門市", "op": "equals", "value": "北區門市"},
            {"column": "金額", "op": "gt", "value": 500},
        ],
    )
    assert step.describe() == 'Keep: 門市 equals "北區門市" and 金額 > 500'
    step = make(
        "filter",
        conditions=[
            {"column": "備註", "op": "is_empty"},
            {"column": "單號", "op": "not_regex", "pattern": "^A"},
            {"column": "品名", "op": "not_contains", "value": "傘"},
        ],
        match="any",
        keep="non_matching",
    )
    assert step.describe() == (
        'Exclude: 備註 is blank or 單號 does not match regex /^A/ or 品名 does not contain "傘"'
    )


def test_columns_describe(english):
    assert make("remove_columns", columns=["備註", "單號"]).describe() == (
        "Remove columns: 備註, 單號"
    )
    assert make("remove_columns", by="position", positions=[3, 2, 3]).describe() == (
        "Remove columns 2, 3"
    )
    assert make("remove_columns", by="position", positions=[2]).describe() == "Remove column 2"
    assert make("select_columns", columns=["金額"]).describe() == "Keep 1 column: 金額"
    assert make("select_columns", columns=["金額", "日期"]).describe() == (
        "Keep 2 columns: 金額, 日期"
    )
    assert make("rename_columns", names={"欄1": "日期"}).describe() == "Rename columns: 欄1→日期"


def test_remove_rows_describe(english):
    assert make("remove_top_rows", n=1).describe() == "Remove the top 1 row of each file"
    assert make("remove_bottom_rows", n=3).describe() == "Remove the bottom 3 rows of each file"
    assert make("remove_blank_rows").describe() == "Remove blank rows"
    assert make("dedupe", columns=["單號"]).describe() == (
        "Remove duplicates (by 單號, keep first)"
    )
    assert make("dedupe", keep="last").describe() == "Remove duplicates (whole row, keep last)"


def test_remove_columns_needs_a_column_in_english(english):
    with pytest.raises(ValidationError, match="Choose at least one column to remove"):
        make("remove_columns", columns=[])
    with pytest.raises(ValidationError, match="Choose at least one column to remove"):
        make("remove_columns", by="position", positions=[])


def test_dedupe_missing_column_in_english(english):
    lf = pl.LazyFrame({SOURCE_FILE: ["a.txt"], LINE_NO: [1], "單號": ["0001"]})
    with pytest.raises(StepError, match=r"^Column not found: 金額$"):
        make("dedupe", columns=["金額"]).apply(lf)
    with pytest.raises(StepError, match=r"^Columns not found: 金額, 門市$"):
        make("dedupe", columns=["金額", "門市"]).apply(lf)
