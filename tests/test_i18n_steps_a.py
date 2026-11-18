"""步驟的英文：取消樞紐、群組彙總、樞紐、結合、合併來源檔（docs/i18n-plan.md）。

欄名與值是中文的假資料，只檢查句子本身是英文；conftest.py 會在每個測試前後換回中文。
"""

import re

import polars as pl
import pytest

from tablade import i18n
from tablade.engine.run import RunError, apply_step
from tablade.steps import get
from tablade.steps.base import LINE_NO, SOURCE_FILE

MINE = ["unpivot", "group_by", "pivot", "join", "merge_files"]
CJK = re.compile(r"[㐀-鿿]")


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


def right_table(data: dict) -> dict:
    n = len(next(iter(data.values())))
    return {"對照": frame(data, files=["對照.csv"] * n)}


def error_of(step, df, tables=None) -> str:
    with pytest.raises(RunError) as info:
        apply_step(step, df, tables=tables)
    return info.value.message


def test_names_docs_and_scope_follow_the_language():
    labels = {name: get(name).display_label() for name in MINE}
    assert labels == {
        "unpivot": "取消樞紐",
        "group_by": "群組彙總",
        "pivot": "樞紐",
        "join": "結合",
        "merge_files": "合併來源檔",
    }
    assert get("merge_files").scope() == "之後的步驟不再分來源檔"
    i18n.set_lang("en")
    labels = {name: get(name).display_label() for name in MINE}
    assert labels == {
        "unpivot": "Unpivot",
        "group_by": "Group and summarize",
        "pivot": "Pivot",
        "join": "Join",
        "merge_files": "Combine source files",
    }
    for name in MINE:
        doc = get(name).display_doc()
        assert doc == get(name).doc_en and not CJK.search(doc)
    assert get("merge_files").scope() == "Later steps no longer split by source file"
    assert get("group_by").display_category() == "Summarize"
    assert get("join").display_category() == "Combine"


def test_form_text_is_english_including_nested_models():
    i18n.set_lang("en")
    group = i18n.localize_schema(get("group_by").model_json_schema())
    assert group["properties"]["by"]["title"] == "Group by"
    assert group["properties"]["count_as"]["default"] == "筆數"  # 預設的新欄名是資料，維持中文
    agg = group["$defs"]["Aggregation"]
    assert agg["title"] == "Aggregation"
    assert agg["properties"]["op"]["ui_labels"]["n_unique"] == "Count distinct"
    pivot = i18n.localize_schema(get("pivot").model_json_schema())
    assert "n_unique" not in pivot["properties"]["op"]["ui_labels"]
    assert pivot["properties"]["op"]["ui_labels"]["sum"] == "Sum"
    join = i18n.localize_schema(get("join").model_json_schema())
    how = join["properties"]["how"]
    assert how["title"] == "Join kind" and how["ui_labels"]["left"] == "Left join"
    assert not any(CJK.search(hint) for hint in how["ui_option_hints"].values())
    assert join["$defs"]["JoinKey"]["properties"]["right"]["title"] == "Other table"
    assert "en" not in how
    for name in MINE:
        schema = i18n.localize_schema(get(name).model_json_schema())
        props = schema["properties"]
        texts = [p.get(k) for p in props.values() for k in ("title", "description")]
        assert not [x for x in texts if isinstance(x, str) and CJK.search(x)], name


def test_describe_in_english():
    i18n.set_lang("en")
    group = make(
        "group_by",
        by=["門市"],
        aggregations=[{"column": "金額", "op": "sum", "name": "金額合計"}],
    )
    assert group.describe() == "Group and summarize (by 門市): Sum(金額), Row count"
    whole = make("group_by", count_as="")
    assert whole.describe() == "Group and summarize (whole source file): no aggregations"
    unpivot = make("unpivot", columns=["1月", "2月"])
    assert unpivot.describe() == 'Unpivot: 1月, 2月 → "名稱", "值"'
    pivot = make("pivot", index=["門市"], on="月份", values="金額", columns=["1月", "2月"])
    assert pivot.describe() == (
        'Pivot (by 門市): 1月, 2月 of "月份" become columns; values: Sum of "金額"'
    )
    on = [{"left": "門市代號", "right": "代號"}]
    join = make("join", right="對照", on=on, columns=["門市名稱"])
    assert join.describe() == "Join (left join with @對照 on 門市代號 = 代號): bring in 門市名稱"
    anti = make("join", right="對照", on=on, how="anti")
    assert anti.describe() == "Keep rows not found in @對照 (門市代號 = 代號)"
    assert make("merge_files").describe() == 'Combine into "合併" in the original order'
    ordered = make("merge_files", order=["b.csv", "a.csv"], name="全部")
    assert ordered.describe() == 'Combine into "全部" in the order "b.csv", "a.csv"'


def test_aggregate_errors_in_english():
    i18n.set_lang("en")
    df = frame({"門市": ["北"], "金額": [1], "品名": ["筆"]})
    by_number = make("group_by", by=["金額"])
    assert error_of(by_number, df) == "Group by columns must be text or date columns: 金額 (number)"
    sum_text = make("group_by", aggregations=[{"column": "品名", "op": "sum", "name": "x"}])
    assert error_of(sum_text, df) == (
        '"品名" is a text column; to use Sum, first convert it with "Text to number"'
    )
    assert error_of(make("unpivot", columns=["不存在"]), df) == "Column not found: 不存在"
    pivot = make("pivot", index=["金額"], on="品名", values="金額", columns=["筆"])
    assert error_of(pivot, df).startswith("Row fields must be text or date columns")


def test_pivot_warning_in_english():
    i18n.set_lang("en")
    df = frame({"門市": ["北", "北", "北"], "月份": ["1月", "3月", "4月"], "金額": [1, 2, 3]})
    pivot = make("pivot", index=["門市"], on="月份", values="金額", columns=["1月"])
    _, total, warnings = apply_step(pivot, df)
    assert total == 1
    # 只記第一個值與共幾種（串流時不能把全部的值接成一串，F-013）
    assert warnings[0].message == (
        'This group has "月份" values not turned into columns: 3月 (2 in all)'
    )


def test_join_errors_and_warnings_in_english():
    i18n.set_lang("en")
    df = frame({"代號": ["A", None, "C"], "金額": [1, 2, 3]})
    on = [{"left": "代號", "right": "代號"}]
    tables = right_table({"代號": ["A", "B"], "名稱": ["甲", "乙"]})
    _, total, warnings = apply_step(
        make("join", right="對照", on=on, columns=["名稱"]), df, tables=tables
    )
    assert total == 2 and [w.message for w in warnings] == [
        '"代號" value "(null)" not found in @對照',
        '"代號" value "C" not found in @對照',
    ]
    assert error_of(make("join", right="對照", on=on, columns=["區域"]), df, tables) == (
        "Not found in @對照: 區域 (its columns: 代號, 名稱)"
    )
    assert error_of(make("join", right="對照", on=on, columns=["代號"]), df, tables) == (
        "No need to bring in the other table's keys: 代號"
    )
    missing = make(
        "join", right="對照", on=[{"left": "沒有", "right": "代號"}], warn_unmatched=False
    )
    assert error_of(missing, df, tables) == "Column not found: 沒有"
    numbers = frame({"代號": [1]})
    assert error_of(make("join", right="對照", on=on), numbers, tables) == (
        'Key types differ: "代號" is Integer, but "代號" in @對照 is Text; '
        "convert them to the same type first"
    )
    dup = right_table({"代號": ["A", "B", "A"], "類別": ["1", "2", "3"]})
    assert error_of(make("join", right="對照", on=on, columns=["類別"]), df, dup) == (
        'Key "代號" has duplicates in @對照: "A" (對照.csv, lines 1, 3). '
        'Remove duplicates or fix the data in stage "對照" first'
    )
