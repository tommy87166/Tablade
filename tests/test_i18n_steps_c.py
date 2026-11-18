"""畫面是英文時，轉型別、文字、向下填滿的步驟（名稱、說明、表單、錯誤、警告）也是英文（docs/i18n-plan.md）。

欄名與值是中文（資料），只檢查句子本身；解析中文日期、數字的規則不受畫面的語言影響。
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import polars as pl
import pytest
from pydantic import ValidationError

from tablade import i18n
from tablade.engine.run import RunError, apply_step
from tablade.steps import get
from tablade.steps.base import LINE_NO, SOURCE_FILE, TEMP_PREFIX

MINE = {
    "parse_number": "Text to number",
    "parse_date": "Text to date",
    "trim_clean": "Clean text",
    "replace_values": "Replace text",
    "conditional_copy": "Copy column conditionally",
    "fill_down": "Fill down",
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


def data_of(df: pl.DataFrame) -> dict:
    return {c: df[c].to_list() for c in df.columns if not c.startswith("__")}


def test_names_and_descriptions_are_english():
    for type_name, label in MINE.items():
        cls = get(type_name)
        assert cls.display_label() == label
        assert cls.display_doc() and not CJK.search(cls.display_doc())
    assert get("fill_down").scope() == "Each source file separately"
    i18n.set_lang("zh")
    assert get("fill_down").display_label() == "向下填滿"
    assert get("parse_date").display_label() == "文字轉日期"


def _form_texts(schema: object) -> list[str]:
    """schema 裡畫面上看得到的文字：標題、說明、選項。"""
    out: list[str] = []
    if isinstance(schema, dict):
        for key in ("title", "description"):
            if isinstance(schema.get(key), str):
                out.append(schema[key])
        labels = schema.get("ui_labels")
        if isinstance(labels, dict):
            out += [str(v) for v in labels.values()]
        for value in schema.values():
            out += _form_texts(value)
    elif isinstance(schema, list):
        for value in schema:
            out += _form_texts(value)
    return out


def test_form_text_is_english():
    """表單的欄位標題、說明、選項（含取代清單的巢狀表單、日期樣式的提示）換成英文。"""
    from tablade.session import catalog

    items = {item["type"]: item for item in catalog() if item["type"] in MINE}
    assert set(items) == set(MINE)
    for type_name, item in items.items():
        assert item["label"] == MINE[type_name]
        assert [s for s in _form_texts(item["schema"]) if CJK.search(s)] == []
        assert '"en"' not in str(item["schema"])
    number = items["parse_number"]["schema"]["properties"]
    assert number["kind"]["ui_labels"]["integer"] == "Integer"
    assert number["scale"]["description"] == "Extra digits are rounded"
    date_form = items["parse_date"]["schema"]["properties"]
    assert date_form["pattern"]["title"] == "Format"
    assert date_form["pattern"]["ui_labels"]["custom"] == "Custom regular expression"
    hints = date_form["pattern"]["ui_option_hints"]
    assert hints["slash"] == r"Regular expression: ^(\d{1,4})/(\d{1,2})/(\d{1,2})$"
    assert hints["custom"] == (
        "Enter a regular expression below with three groups: year, month, day"
    )
    assert date_form["calendar"]["ui_labels"] == {
        "roc": "ROC year (adds 1911)",
        "ad": "Gregorian year",
    }
    replacement = items["replace_values"]["schema"]["$defs"]["Replacement"]
    assert replacement["title"] == "Replacement"
    assert replacement["properties"]["replace"]["description"] == (
        "Leave empty to delete the matched text"
    )
    copy = items["conditional_copy"]["schema"]["properties"]
    assert copy["match"]["ui_labels"] == {"all": "All conditions", "any": "Any condition"}
    # 中文畫面不受影響
    i18n.set_lang("zh")
    zh = {item["type"]: item for item in catalog() if item["type"] in MINE}
    pattern = zh["parse_date"]["schema"]["properties"]["pattern"]
    assert pattern["title"] == "樣式"
    assert (
        pattern["ui_option_hints"]["custom"]
        == "在下面輸入正規表示式，要有三個群組，依序是年、月、日"
    )
    assert zh["trim_clean"]["schema"]["properties"]["width"]["ui_labels"]["none"] == "不轉換"


def test_describe_in_english():
    assert (
        make("parse_number", columns=["金額"]).describe()
        == "Convert to number (2 decimal places): 金額"
    )
    assert (
        make("parse_number", columns=["金額"], scale=1).describe()
        == "Convert to number (1 decimal place): 金額"
    )
    assert (
        make("parse_number", columns=["數量", "單價"], kind="integer").describe()
        == "Convert to integer: 數量, 單價"
    )
    assert (
        make("parse_date", columns=["日期"], pattern="slash", calendar="roc").describe()
        == "Convert to date (Y/M/D, ROC year): 日期"
    )
    assert (
        make("parse_date", columns=["日期"], pattern="chinese", calendar="ad").describe()
        == "Convert to date (Chinese date, Gregorian year): 日期"
    )
    custom = make(
        "parse_date",
        columns=["d"],
        pattern="custom",
        regex=r"^(\d{4})_(\d{2})_(\d{2})$",
        calendar="ad",
    )
    assert custom.describe() == r"Convert to date (/^(\d{4})_(\d{2})_(\d{2})$/, Gregorian year): d"
    assert (
        make("trim_clean", columns=["品號"]).describe()
        == "Clean text (trim spaces, full-width letters and digits to half-width, "
        "remove invisible characters): 品號"
    )
    assert (
        make(
            "trim_clean",
            columns=["品號"],
            trim=False,
            width="nfkc",
            invisible=False,
            collapse_spaces=True,
        ).describe()
        == "Clean text (full-width to half-width (NFKC), collapse spaces): 品號"
    )
    assert (
        make("trim_clean", columns=["品號"], trim=False, width="none", invisible=False).describe()
        == "Clean text (no cleaning options selected): 品號"
    )
    pairs = [{"find": f, "replace": ""} for f in ("N/A", "-", "無", "?")]
    assert (
        make(
            "replace_values", columns=["備註", "門市"], match="whole", replacements=pairs
        ).describe()
        == 'Replace text (whole cell) in 備註, 門市: "N/A" → "", "-" → "", "無" → ""… 4 pairs in total'
    )
    assert (
        make(
            "replace_values", columns=["單號"], replacements=[{"find": "單號", "replace": "No."}]
        ).describe()
        == 'Replace text in 單號: "單號" → "No."'
    )
    copy = make(
        "conditional_copy",
        conditions=[
            {"column": "A", "op": "starts_with", "value": "門市："},
            {"column": "B", "op": "is_not_empty"},
        ],
        match="any",
        column="B",
        name="門市",
    )
    first, second = (c.describe() for c in copy.conditions)
    assert copy.describe() == f'Copy "B" to "門市" where {first} or {second}'
    assert make("fill_down", columns=["門市", "科別"]).describe() == "Fill down: 門市, 科別"


def test_describe_stays_chinese_in_chinese():
    i18n.set_lang("zh")
    assert (
        make("parse_date", columns=["日期"], pattern="compact", calendar="roc").describe()
        == "轉成日期（連寫，月日各 2 碼，民國年）：日期"
    )
    pairs = [{"find": f, "replace": ""} for f in ("N/A", "-", "無", "?")]
    assert (
        make("replace_values", columns=["備註"], match="whole", replacements=pairs).describe()
        == "取代文字（整格相符）：「N/A」→「」、「-」→「」、「無」→「」…共 4 組，欄位 備註"
    )
    assert (
        make("trim_clean", columns=["a", "b"]).describe()
        == "清理文字（去掉前後空白、全形英數字轉半形、移除看不見的字元）：a、b"
    )


def test_warnings_in_english():
    _, total, warnings = apply_step(
        make("parse_number", columns=["金額"]), frame({"金額": ["1,234", "abc"]})
    )
    assert total == 1
    assert warnings[0].message == '"abc" in "金額" is not a valid number; replaced with null'
    _, _, warnings = apply_step(
        make("parse_number", columns=["數量"], kind="integer"), frame({"數量": ["12.5"]})
    )
    assert warnings[0].message == '"12.5" in "數量" is not a valid integer; replaced with null'
    _, _, warnings = apply_step(
        make("parse_date", columns=["日期"], pattern="chinese", calendar="roc"),
        frame({"日期": ["115年1月5日", "115年13月1日"]}),
    )
    assert warnings[0].message == (
        '"115年13月1日" in "日期" is not a valid date; replaced with null'
    )


def test_step_errors_in_english():
    with pytest.raises(RunError, match=re.escape("Column not found: nope")):
        apply_step(make("parse_number", columns=["nope"]), frame({"a": ["1"]}))
    with pytest.raises(RunError, match=re.escape("Columns not found: x, y")):
        apply_step(
            make("parse_date", columns=["x", "y"], pattern="slash", calendar="ad"),
            frame({"a": ["1"]}),
        )
    for step in (
        make("trim_clean", columns=["nope"]),
        make("replace_values", columns=["nope"], replacements=[{"find": "a"}]),
        make(
            "conditional_copy",
            conditions=[{"column": "a", "op": "is_empty"}],
            column="nope",
            name="n",
        ),
        make("fill_down", columns=["nope"]),
    ):
        with pytest.raises(RunError, match=re.escape("Column not found: nope")):
            apply_step(step, frame({"a": ["1"]}))
    numbers = frame({"a": ["x"]}).with_columns(pl.lit(1).alias("n"), pl.lit(2.5).alias("m"))
    with pytest.raises(RunError, match=re.escape("Only works on text columns: n is not text")):
        apply_step(make("trim_clean", columns=["a", "n"]), numbers)
    with pytest.raises(RunError, match=re.escape("Only works on text columns: n, m are not text")):
        apply_step(
            make("replace_values", columns=["n", "m"], replacements=[{"find": "1"}]), numbers
        )


def test_validation_errors_in_english():
    for regex, message in [
        ("", 'Enter a regular expression when "Custom regular expression" is selected'),
        (
            r"^(\d+)/(\d+)$",
            "Needs exactly three groups (year, month, day); this regular expression has 2",
        ),
        ("^(\\d+", "Invalid regular expression"),
    ]:
        with pytest.raises(ValidationError, match=re.escape(message)):
            make("parse_date", columns=["d"], pattern="custom", regex=regex, calendar="ad")
    with pytest.raises(ValidationError, match=re.escape("Invalid regular expression")):
        make("replace_values", columns=["a"], match="regex", replacements=[{"find": "(a"}])


PARSE_CASES = [
    pytest.param(
        "parse_date",
        {"columns": ["日期"], "pattern": "chinese", "calendar": "roc"},
        {"日期": ["115年1月5日", "115 年 12 月 31 日", "2026年1月5日", "x", ""]},
        {"日期": [date(2026, 1, 5), date(2026, 12, 31), None, None, None]},
        id="parse_date:chinese",
    ),
    pytest.param(
        "parse_date",
        {
            "columns": ["日期"],
            "pattern": "custom",
            "regex": r"^民國(\d{1,3})年(\d{1,2})月(\d{1,2})日$",
            "calendar": "roc",
        },
        {"日期": ["民國115年1月5日", "115年1月6日"]},
        {"日期": [date(2026, 1, 5), None]},
        id="parse_date:custom",
    ),
    pytest.param(
        "parse_date",
        {"columns": ["日期"], "pattern": "compact", "calendar": "ad"},
        {"日期": ["20260105", "1150105"]},
        {"日期": [date(2026, 1, 5), None]},
        id="parse_date:compact",
    ),
    pytest.param(
        "parse_number",
        {"columns": ["金額"]},
        {"金額": ["1,234", "(1,000)", "1,000-", " 12.345 ", "abc", ""]},
        {
            "金額": [
                Decimal("1234.00"),
                Decimal("-1000.00"),
                Decimal("-1000.00"),
                Decimal("12.35"),
                None,
                None,
            ]
        },
        id="parse_number",
    ),
    pytest.param(
        "trim_clean",
        {"columns": ["品號"]},
        {"品號": ["　Ａ００１ ", "B002​", "北區（總店）"]},
        {"品號": ["A001", "B002", "北區（總店）"]},
        id="trim_clean",
    ),
    pytest.param(
        "replace_values",
        {
            "columns": ["單號"],
            "match": "regex",
            "replacements": [{"find": r"^單號\s*(\d+)$", "replace": "$1"}],
        },
        {"單號": ["單號 0001", "其他"]},
        {"單號": ["0001", "其他"]},
        id="replace_values",
    ),
    pytest.param(
        "fill_down",
        {"columns": ["門市"]},
        {"門市": ["北區門市", "", None, "南區門市"]},
        {"門市": ["北區門市", "北區門市", "北區門市", "南區門市"]},
        id="fill_down",
    ),
]


@pytest.mark.parametrize(("type_name", "params", "data", "expected"), PARSE_CASES)
def test_results_do_not_depend_on_the_language(type_name, params, data, expected):
    """解析中文日期、數字、全形字元的規則不跟著畫面的語言變：英文和中文的結果一樣。"""
    results = {}
    for lang in ("en", "zh"):
        i18n.set_lang(lang)
        out, total, _ = apply_step(make(type_name, **params), frame(data))
        results[lang] = (data_of(out), total)
    assert results["en"] == results["zh"]
    assert results["en"][0] == {**data, **expected}


def test_examples_pass_in_english():
    """步驟的範例（同時是測試）在英文畫面下結果也一樣。"""
    for type_name in MINE:
        cls = get(type_name)
        for example in cls.examples:
            step = cls.model_validate({"id": "ex", "type": type_name, **example.params})
            out, _, _ = apply_step(step, frame(example.input))
            assert data_of(out) == example.output, example.title
            assert not [c for c in out.columns if c.startswith(TEMP_PREFIX)]
