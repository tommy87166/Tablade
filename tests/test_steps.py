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


def run(step, df, tables=None):
    out, total, warnings = apply_step(step, df, tables=tables)
    return out, total, warnings


def tables_of(example) -> dict | None:
    """範例的右表（前面階段的結果），補上系統欄位。"""
    if not example.tables:
        return None
    return {
        name: frame(data, files=[f"{name}.csv"] * len(next(iter(data.values()))))
        for name, data in example.tables.items()
    }


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
    out, _, _ = run(step, frame(example.input), tables_of(example))
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
        "dedupe",
        "group_by",
        "pivot",
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
    one, _, _ = run(step, frame(example.input), tables_of(example))
    two, _, _ = run(step, frame(example.input, split), tables_of(example))
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
        "dedupe",
        "filter",
        "split_delimited",
        "split_column",
        "classify_rows",
        "extract_regex",
        "split_fixed_width",
        "promote_headers",
        "conditional_copy",
        "fill_down",
        "remove_columns",
        "select_columns",
        "rename_columns",
        "trim_clean",
        "replace_values",
        "parse_number",
        "parse_date",
        "unpivot",
        "group_by",
        "pivot",
        "merge_files",
        "join",
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


# ── 多的段併回指定的欄（docs/split-merge-plan.md）────────────────────


def _merged(line: str, names: list[str], into: int) -> list[str | None]:
    """照計畫的規則算出來的值：左邊照順序、右邊從右邊往回數、中間接回去。"""
    parts = line.split("|")
    extra = len(parts) - len(names)
    if extra <= 0:
        return parts + [None] * (len(names) - len(parts))
    return [*parts[:into], "|".join(parts[into : into + extra + 1]), *parts[into + extra + 1 :]]


@pytest.mark.parametrize("into", ["c1", "c2", "c4"])
def test_split_delimited_merge_into(into):
    lines = ["a|b|c|d", "a|b|c|d|x|e", "a|b|c|d|x|y|e"]
    names = ["c1", "c2", "c3", "c4"]
    step = make(
        "split_delimited", delimiter="|", columns=names, on_extra="merge_into", merge_column=into
    )
    out, total, warnings = run(step, frame({"line": lines}))
    k = names.index(into)
    assert [list(r) for r in out.select(names).rows()] == [_merged(x, names, k) for x in lines]
    assert total == 2 and [w.line_no for w in warnings] == [2, 3]
    assert warnings[0].message == f"「line」拆出 6 段，比 4 個欄名多，多的段已併回「{into}」"
    assert f"多的段併回「{into}」" in step.describe()


def test_split_delimited_merge_into_values():
    df = frame({"line": ["a|b|c|d", "a|b|X|Y|d", "a|b", ""]})
    names = ["c1", "c2", "c3", "c4"]
    step = make(
        "split_delimited", delimiter="|", columns=names, on_extra="merge_into", merge_column="c3"
    )
    out, total, _ = run(step, df)
    assert [list(r) for r in out.select(names).rows()] == [
        ["a", "b", "c", "d"],
        ["a", "b", "X|Y", "d"],
        ["a", "b", None, None],  # 段數比較少：和照順序拆一樣
        [None, None, None, None],
    ]
    assert total == 1


def test_split_delimited_merge_into_last_is_merge():
    df = frame({"line": ["a;b;c;d", "a;b", '"x;y";z;w']})
    names = ["c1", "c2"]
    merged = run(make("split_delimited", delimiter=";", columns=names, on_extra="merge"), df)[0]
    into = run(
        make(
            "split_delimited",
            delimiter=";",
            columns=names,
            on_extra="merge_into",
            merge_column="c2",
        ),
        df,
    )[0]
    assert merged.select(names).equals(into.select(names))


def test_split_delimited_merge_into_respects_quotes():
    df = frame({"line": ['a|"b|c"|d', 'a|"b|c"|x|d']})
    step = make(
        "split_delimited",
        delimiter="|",
        columns=["c1", "c2", "c3"],
        on_extra="merge_into",
        merge_column="c2",
    )
    out, total, _ = run(step, df)
    assert out["c2"].to_list() == ["b|c", "b|c|x"] and out["c3"].to_list() == ["d", "d"]
    assert total == 1


def test_split_column_merge_into_trims_and_keeps_nulls():
    df = frame({"m": ["1 | a | b | 2", "1|x|2", None]})
    step = make(
        "split_column",
        column="m",
        delimiter="|",
        names=["x", "y", "z"],
        on_extra="merge_into",
        merge_column="y",
    )
    out, total, warnings = run(step, df)
    assert data_of(out) == {"x": ["1", "1", None], "y": ["a | b", "x", None], "z": ["2", "2", None]}
    assert total == 1 and warnings[0].line_no == 1


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"on_extra": "merge_into"}, "要選多的段併回哪一欄"),
        ({"on_extra": "merge_into", "merge_column": "nope"}, "「nope」不是拆出來的欄名"),
        ({"merge_column": "c1"}, "才能填 merge_column"),
    ],
)
@pytest.mark.parametrize("kind", ["split_delimited", "split_column"])
def test_merge_column_rules(kind, params, message):
    from pydantic import ValidationError

    base = (
        {"columns": ["c1", "c2"]}
        if kind == "split_delimited"
        else {"column": "m", "delimiter": "|", "names": ["c1", "c2"]}
    )
    with pytest.raises(ValidationError, match=message):
        make(kind, **base, **params)


def test_extra_delimiter_fixture_splits_back_exactly():
    from tablade.fixtures import EXTRA_DELIMITER_COLUMNS, extra_delimiter_rows

    lines, values = extra_delimiter_rows()
    names = list(EXTRA_DELIMITER_COLUMNS)
    step = make(
        "split_delimited", delimiter="|", columns=names, on_extra="merge_into", merge_column="備註"
    )
    out, total, _ = run(step, frame({"line": lines}))
    assert [list(r) for r in out.select(names).rows()] == values
    assert total == sum(1 for v in values if "|" in v[4]) == 10
    wrong = run(make("split_delimited", delimiter="|", columns=names), frame({"line": lines}))[0]
    assert [list(r) for r in wrong.select(names).rows()] != values  # 照順序拆會錯位


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
    assert (
        step.describe()
        == "取代文字（整格相符）：「0」→「」、「1」→「」、「2」→「」…共 5 組，欄位 t"
    )


def test_validator_checks_new_effects_and_maps():
    from tablade.recipe.validator import EffectError, apply_effect

    assert apply_effect(["a", "b"], {"kind": "rename", "names": {"a": "b", "b": "a"}}) == ["b", "a"]
    assert apply_effect(None, {"kind": "select", "names": ["x"]}) is None
    with pytest.raises(EffectError, match="要填至少一個新欄名"):
        apply_effect(["a"], {"kind": "rename", "names": {}})
    with pytest.raises(EffectError, match="要選至少一個要保留的欄位"):
        apply_effect(["a"], {"kind": "select", "names": []})


# ── 分段報表 ───────────────────────────────────────────────────


def rule(label, op="starts_with", **cond):
    return {"label": label, "conditions": [{"column": "line", "op": op, **cond}]}


def test_classify_rows_first_match_wins_and_otherwise():
    df = frame({"line": ["門市：北", "小計 門市：", "", "x"]})
    rules = [
        rule("段落", value="門市："),
        rule("小計", "contains", value="小計"),
        rule("空白", "is_empty"),
    ]
    out, _, _ = run(make("classify_rows", rules=rules), df)
    assert out["類型"].to_list() == ["段落", "小計", "空白", "其他"]
    # 名稱、都不符合時的類別可以改；空白代表空值
    out, _, _ = run(make("classify_rows", column="kind", rules=rules[:1], otherwise=""), df)
    assert out["kind"].to_list() == ["段落", None, None, None]
    any_rule = {
        "label": "有字",
        "match": "any",
        "conditions": [
            {"column": "line", "op": "contains", "value": "北"},
            {"column": "line", "op": "equals", "value": "x"},
        ],
    }
    out, _, _ = run(make("classify_rows", rules=[any_rule]), df)
    assert out["類型"].to_list() == ["有字", "其他", "其他", "有字"]
    with pytest.raises(RunError, match="新欄名「line」和現有欄位重複"):
        run(make("classify_rows", column="line", rules=rules), df)
    assert make("classify_rows", rules=rules).describe() == (
        "將列分類到「類型」：段落、小計、空白，其他是「其他」"
    )


def test_extract_regex_groups_and_validation():
    from pydantic import ValidationError

    df = frame({"t": ["門市：北區 115/01/05", "沒有", None]})
    step = make(
        "extract_regex",
        column="t",
        pattern=r"^門市：(\S+) (\d+/\d+/\d+)$",
        names=["門市", "日期"],
        keep_source=False,
    )
    out, _, _ = run(step, df)
    assert data_of(out) == {"門市": ["北區", None, None], "日期": ["115/01/05", None, None]}
    for pattern, names, message in [
        ("門市", ["x"], "沒有括號群組"),
        ("(a)(b)", ["x"], "有 2 個群組，但列了 1 個新欄名"),
        ("(a", ["x"], "正規表示式有誤"),
    ]:
        with pytest.raises(ValidationError, match=message):
            make("extract_regex", column="t", pattern=pattern, names=names)
    assert make("extract_regex", column="t", pattern="(?:a)(b)", names=["x"])  # (?:) 不算群組
    with pytest.raises(RunError, match="欄位「nope」不存在"):
        run(make("extract_regex", column="nope", pattern="(a)", names=["x"]), df)


def test_split_fixed_width_counts_wide_characters():
    from pydantic import ValidationError

    lines = ["中文AB 12", "ab  CD 3", "短", None]
    step = make("split_fixed_width", starts=[1, 6, 8], names=["a", "b", "c"])
    out, _, _ = run(step, frame({"line": lines}))
    # 「中文」佔 4 格，「A」在第 5 格：第二欄從第 6 格開始，「12」從第 8 格開始
    assert data_of(out) == {
        "a": ["中文A", "ab  C", "短", None],
        "b": ["B", "D", "", None],
        "c": ["12", "3", "", None],
    }
    raw, _, _ = run(
        make("split_fixed_width", starts=[1, 3], names=["a", "b"], trim=False, keep_source=True),
        frame({"line": ["中文字"]}),
    )
    assert data_of(raw) == {"line": ["中文字"], "a": ["中"], "b": ["文字"]}
    # 寬字被切在中間：整個字歸到前一欄
    half, _, _ = run(
        make("split_fixed_width", starts=[1, 2], names=["a", "b"]), frame({"line": ["中a"]})
    )
    assert data_of(half) == {"a": ["中"], "b": ["a"]}
    for starts, names, message in [
        ([0, 3], ["a", "b"], "從 1 起算"),
        ([5, 3], ["a", "b"], "由小到大"),
        ([1, 3], ["a"], "有 2 個開始位置，但列了 1 個欄名"),
    ]:
        with pytest.raises(ValidationError, match=message):
            make("split_fixed_width", starts=starts, names=names)


def test_split_fixed_width_suggests_starts_from_aligned_blanks():
    from tablade.steps.report import SplitFixedWidth

    lines = ["115/01/05  A001  原子筆        10", "115/01/06  D300  無線耳機     1,290"]
    suggested = SplitFixedWidth.suggest(frame({"line": lines}), {})
    assert suggested["starts"] == [1, 10, 16, 26] and len(suggested["names"]) == 4
    step = make(
        "split_fixed_width", starts=suggested["starts"], names=["日期", "品號", "品名", "金額"]
    )
    out, _, _ = run(step, frame({"line": lines}))
    assert out["品名"].to_list() == ["原子筆", "無線耳機"] and out["金額"].to_list() == [
        "10",
        "1,290",
    ]


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
        # 否定的條件：空值當成空字串，所以會留下（F-004）
        ({"op": "not_equals", "column": "name", "value": "Apple"}, ["banana", "Cherry", None, ""]),
        ({"op": "contains", "column": "name", "value": "an"}, ["banana"]),
        ({"op": "not_contains", "column": "name", "value": "an"}, ["Apple", "Cherry", None, ""]),
        ({"op": "starts_with", "column": "name", "value": "C"}, ["Cherry"]),
        ({"op": "ends_with", "column": "name", "value": "y"}, ["Cherry"]),
        ({"op": "regex", "column": "name", "pattern": "^[A-Z]"}, ["Apple", "Cherry"]),
        ({"op": "not_regex", "column": "name", "pattern": "^[A-Z]"}, ["banana", None, ""]),
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


# ── 移除重複、彙總 ───────────────────────────────────────────────


def lines(df: pl.DataFrame) -> list[tuple[str, int]]:
    return list(zip(df[SOURCE_FILE].to_list(), df[LINE_NO].to_list(), strict=True))


def test_dedupe_by_columns_or_whole_row_per_file():
    df = frame(
        {"單號": ["1", "2", "1", "1", "3"], "金額": ["10", "20", "99", "10", "30"]},
        files=["a", "a", "a", "b", "b"],
    )
    first, _, _ = run(make("dedupe", columns=["單號"]), df)
    assert lines(first) == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]  # 同樣的值在不同檔都留著
    last, _, _ = run(make("dedupe", columns=["單號"], keep="last"), df)
    assert lines(last) == [("a", 2), ("a", 3), ("b", 1), ("b", 2)]  # 留下的列照原本的順序
    whole, _, _ = run(make("dedupe"), df)
    assert whole.height == 5  # 整列相同才算：「1, 99」和「1, 10」不一樣
    same = frame({"x": ["a", "a", "", None, "", None]})
    assert run(make("dedupe"), same)[0]["x"].to_list() == ["a", "", None]  # 空字串和空值不一樣
    with pytest.raises(RunError, match="欄位不存在：單"):
        run(make("dedupe", columns=["單"]), df)


def test_unpivot_keeps_row_order_line_numbers_and_types():
    df = frame({"門市": ["北", "中"], "1月": [1, 2], "2月": [3, None]}, files=["a", "b"])
    out, _, _ = run(make("unpivot", columns=["1月", "2月"]), df)
    assert lines(out) == [("a", 1), ("a", 1), ("b", 1), ("b", 1)]
    assert data_of(out) == {
        "門市": ["北", "北", "中", "中"],
        "名稱": ["1月", "2月", "1月", "2月"],
        "值": [1, 3, 2, None],
    }
    assert out.schema["值"] == pl.Int64  # 型別一樣：保留
    skip, _, _ = run(make("unpivot", columns=["1月", "2月"], skip_empty=True), df)
    assert skip["值"].to_list() == [1, 3, 2]
    mixed = frame({"a": ["x", " "], "b": [1, 2]})
    out, _, _ = run(make("unpivot", columns=["a", "b"], skip_empty=True), mixed)
    assert out.schema["值"] == pl.String and out["值"].to_list() == ["x", "1", "2"]
    with pytest.raises(RunError, match="新欄名「門市」和現有欄位重複"):
        run(make("unpivot", columns=["1月"], name_column="門市"), df)


def test_group_by_types_and_first_occurrence_order():
    df = frame(
        {
            "門市": ["北", "中", "北", "中"],
            "日期": ["2026-01-02", "2026-01-05", "2026-01-01", None],
            "整數": [1, 2, 3, 4],
            "小數": ["1.50", "2.25", "3.00", "0.10"],
            "名稱": ["甲", None, "乙", "甲"],
        }
    ).with_columns(
        pl.col("日期").str.to_date(),
        pl.col("小數").str.to_decimal(scale=2),
        __line_no=pl.Series([3, 5, 7, 9]),
    )
    aggs = [
        {"column": "整數", "op": "sum", "name": "整數合計"},
        {"column": "小數", "op": "sum", "name": "小數合計"},
        {"column": "整數", "op": "mean", "name": "平均"},
        {"column": "名稱", "op": "count", "name": "有名稱"},
        {"column": "名稱", "op": "n_unique", "name": "幾種"},
        {"column": "日期", "op": "max", "name": "最後日期"},
        {"column": "名稱", "op": "first", "name": "第一個"},
        {"column": "名稱", "op": "last", "name": "最後一個"},
    ]
    out, _, _ = run(make("group_by", by=["門市"], aggregations=aggs), df)
    assert out[LINE_NO].to_list() == [3, 5]  # 每組第一筆的行號，照第一次出現的順序
    assert out["門市"].to_list() == ["北", "中"]
    assert out["整數合計"].to_list() == [4, 6] and out.schema["整數合計"] == pl.Int64
    assert out.schema["小數合計"] == pl.Decimal(38, 2)
    assert [str(v) for v in out["小數合計"]] == ["4.50", "2.35"]
    assert out.schema["平均"] == pl.Float64 and out["平均"].to_list() == [2.0, 3.0]
    assert out["有名稱"].to_list() == [2, 1] and out.schema["有名稱"] == pl.Int64
    assert out["幾種"].to_list() == [2, 1]  # 空值不算一種（和「有值的筆數」一樣不算空值）
    assert out.schema["最後日期"] == pl.Date
    assert out["第一個"].to_list() == ["甲", None] and out["最後一個"].to_list() == ["乙", "甲"]
    assert out["筆數"].to_list() == [2, 2] and out.schema["筆數"] == pl.Int64


def test_group_by_each_file_and_whole_file():
    df = frame({"門市": ["北", "中", "北"], "金額": [1, 2, 3]}, files=["a", "a", "b"])
    out, _, _ = run(make("group_by", by=["門市"]), df)
    assert lines(out) == [("a", 1), ("a", 2), ("b", 1)]
    assert data_of(out) == {"門市": ["北", "中", "北"], "筆數": [1, 1, 1]}
    no_count = [{"column": "金額", "op": "max", "name": "最大"}]
    out, _, _ = run(make("group_by", by=["門市"], aggregations=no_count, count_as=""), df)
    assert data_of(out) == {"門市": ["北", "中", "北"], "最大": [1, 2, 3]}
    whole = make("group_by", aggregations=[{"column": "金額", "op": "sum", "name": "合計"}])
    out, _, _ = run(whole, df)
    assert lines(out) == [("a", 1), ("b", 1)] and data_of(out) == {"合計": [3, 3], "筆數": [2, 1]}
    assert run(whole, df.clear())[0].height == 0  # 沒有資料：不產生彙總列


def test_group_by_type_errors():
    df = frame({"門市": ["北"], "金額": ["100"], "數量": [1]})
    with pytest.raises(RunError, match="「金額」是文字，合計要先用「文字轉數字」"):
        run(make("group_by", aggregations=[{"column": "金額", "op": "sum", "name": "合計"}]), df)
    with pytest.raises(RunError, match="分組欄只能是文字或日期欄：數量（數字）"):
        run(make("group_by", by=["數量"]), df)
    with pytest.raises(RunError, match="欄位不存在：店"):
        run(make("group_by", by=["店"]), df)
    # 最大、最小、筆數可以用在文字欄
    ok = make("group_by", aggregations=[{"column": "金額", "op": "max", "name": "最大"}])
    assert run(ok, df)[0]["最大"].to_list() == ["100"]


def test_pivot_empty_cells_ops_and_new_values():
    df = frame(
        {
            "門市": ["北", "北", "中", "北", "中", "南"],
            "月份": ["1月", "2月", "1月", "1月", "3月", None],
            "金額": [10, 20, 5, 30, 7, 9],
        },
        files=["a", "a", "a", "a", "a", "b"],
    )
    pivot = make("pivot", index=["門市"], on="月份", values="金額", columns=["1月", "2月"])
    out, total, warnings = run(pivot, df)
    assert lines(out) == [("a", 1), ("a", 3), ("b", 1)]
    assert data_of(out) == {
        "門市": ["北", "中", "南"],
        "1月": [40, 5, None],
        "2月": [20, None, None],
    }
    # 「3月」不在清單裡：警告指到這一組第一筆的行號；月份是空的那一列的金額也不在任何一欄（F-015）
    assert total == 2
    assert (warnings[0].source_file, warnings[0].line_no) == ("a", 3)
    assert warnings[0].message == "這一組有沒轉成欄的「月份」：3月"
    assert (warnings[1].source_file, warnings[1].message) == (
        "b",
        "這一組有沒轉成欄的「月份」：（空白）",
    )
    assert not [c for c in out.columns if c.startswith(TEMP_PREFIX)]
    last = pivot.model_copy(update={"op": "last"})
    assert run(last, df)[0]["1月"].to_list() == [30, 5, None]
    count = pivot.model_copy(update={"op": "count"})
    assert run(count, df)[0]["1月"].to_list() == [2, 1, None]
    whole = make("pivot", on="月份", values="金額", columns=["1月", "2月", "3月"])
    out, total, _ = run(whole, df)
    assert lines(out) == [("a", 1), ("b", 1)] and total == 1  # b 的月份是空的
    assert data_of(out) == {"1月": [45, None], "2月": [20, None], "3月": [7, None]}


def test_pivot_errors():
    df = frame({"門市": ["北"], "月份": ["1月"], "金額": ["10"]})
    with pytest.raises(RunError, match="「金額」是文字，合計要先用「文字轉數字」"):
        run(make("pivot", index=["門市"], on="月份", values="金額", columns=["1月"]), df)
    first = make("pivot", index=["門市"], on="月份", values="金額", op="first", columns=["1月"])
    assert run(first, df)[0]["1月"].to_list() == ["10"]
    with pytest.raises(RunError, match="新欄名「門市」和現有欄位重複"):
        run(
            make("pivot", index=["門市"], on="月份", values="金額", op="first", columns=["門市"]),
            df,
        )


def test_aggregate_suggestions():
    df = frame({"門市": ["北", "中"], "金額": [1, 2], "月份": ["1月", "2月"]})
    assert get("group_by").suggest(df, {"column": "門市"}) == {
        "by": ["門市"],
        "aggregations": [{"column": "金額", "op": "sum", "name": "金額合計"}],
    }
    assert get("group_by").suggest(df, {"column": "金額"})["by"] == []  # 數字欄不能當分組欄
    assert get("pivot").suggest(df, {"column": "月份"}) == {"on": "月份", "columns": ["1月", "2月"]}
    assert get("dedupe").suggest(df, {"column": "門市"}) == {"columns": ["門市"]}
    assert get("unpivot").suggest(df, {"column": "金額"}) == {"columns": ["金額"]}


def test_aggregate_effects():
    cols = ["門市", "1月", "2月"]
    unpivot = {"kind": "unpivot", "columns": ["1月", "2月"], "name": "月份", "value": "金額"}
    assert apply_effect(cols, unpivot) == ["門市", "月份", "金額"]
    assert apply_effect(None, unpivot) is None
    with pytest.raises(EffectError, match="欄位不存在：3月"):
        apply_effect(cols, {**unpivot, "columns": ["3月"]})
    group = {
        "kind": "group",
        "by": ["門市"],
        "aggregations": [{"column": "1月", "op": "sum", "name": "合計"}],
        "count": "筆數",
    }
    assert apply_effect(cols, group) == ["門市", "合計", "筆數"]
    assert apply_effect(None, group) == ["門市", "合計", "筆數"]  # 分組後的欄位不看之前的欄位
    with pytest.raises(EffectError, match="要有至少一項彙總"):
        apply_effect(cols, {**group, "aggregations": [], "count": ""})
    with pytest.raises(EffectError, match="新欄名「門市」和現有欄位重複"):
        apply_effect(cols, {**group, "count": "門市"})
    pivot = {"kind": "pivot", "index": ["門市"], "on": "月", "values": "金額", "names": ["1月"]}
    assert apply_effect(None, pivot) == ["門市", "1月"]
    with pytest.raises(EffectError, match="欄位不存在：月、金額"):
        apply_effect(cols, pivot)
    with pytest.raises(EffectError, match="已經是樞紐表格列依據欄位"):
        apply_effect(cols, {**pivot, "on": "門市"})


# ── 結合 ─────────────────────────────────────────────────────


def right_table(data: dict) -> dict:
    """右表（前面階段的結果），補上系統欄位。"""
    n = len(next(iter(data.values())))
    return {"對照": frame(data, files=["對照.csv"] * n)}


def test_join_ways_and_unmatched_warnings():
    df = frame(
        {"代號": ["A", "B", None, "C", "A"], "金額": [1, 2, 3, 4, 5]},
        files=["a", "a", "a", "b", "b"],
    )
    tables = right_table({"代號": ["A", "B"], "名稱": ["甲", "乙"]})
    on = [{"left": "代號", "right": "代號"}]
    left, total, warnings = run(make("join", right="對照", on=on, columns=["名稱"]), df, tables)
    assert left["名稱"].to_list() == ["甲", "乙", None, None, "甲"]
    assert lines(left) == [("a", 1), ("a", 2), ("a", 3), ("b", 1), ("b", 2)]  # 順序與行號不變
    assert total == 2 and [w.message for w in warnings] == [
        "「代號」的「（空值）」在 @對照 找不到",  # 空值不算對到
        "「代號」的「C」在 @對照 找不到",
    ]
    inner, total, warnings = run(
        make("join", right="對照", on=on, how="inner", columns=["名稱"]), df, tables
    )
    assert inner["代號"].to_list() == ["A", "B", "A"]
    assert total == 2 and (warnings[1].source_file, warnings[1].line_no) == (
        "b",
        1,
    )  # 拿掉之前就算好
    anti, total, _ = run(make("join", right="對照", on=on, how="anti"), df, tables)
    assert anti["代號"].to_list() == [None, "C"] and total == 0 and "名稱" not in anti.columns
    semi, _, _ = run(make("join", right="對照", on=on, how="semi"), df, tables)
    assert semi["代號"].to_list() == ["A", "B", "A"]
    quiet = make("join", right="對照", on=on, columns=["名稱"], warn_unmatched=False)
    assert run(quiet, df, tables)[1] == 0


def test_join_several_keys_and_types():
    df = frame({"門市": ["北", "北", "中"], "月": ["1", "2", "1"], "金額": [1, 2, 3]})
    tables = right_table({"店": ["北", "中"], "月份": ["1", "1"], "目標": [10, 20]})
    on = [{"left": "門市", "right": "店"}, {"left": "月", "right": "月份"}]
    out, total, warnings = run(make("join", right="對照", on=on, columns=["目標"]), df, tables)
    assert out["目標"].to_list() == [10, None, 20] and out.schema["目標"] == pl.Int64
    assert total == 1 and warnings[0].message == "「門市、月」的「北、2」在 @對照 找不到"
    numbers = frame({"門市": ["北"], "月": [1], "金額": [1]})
    with pytest.raises(RunError, match="鍵的型別不一樣：「月」是整數，@對照 的「月份」是文字"):
        run(make("join", right="對照", on=on, columns=["目標"]), numbers, tables)


def test_join_errors():
    df = frame({"代號": ["A"], "名稱": ["x"]})
    on = [{"left": "代號", "right": "代號"}]
    dup = right_table({"代號": ["A", "B", "A", None, None], "類別": ["1", "2", "3", "4", "5"]})
    with pytest.raises(RunError, match=r"@對照 的鍵「代號」重複：「A」（對照\.csv 第 1、3 行）"):
        run(make("join", right="對照", on=on, columns=["類別"]), df, dup)  # 空值的鍵重複不算
    tables = right_table({"代號": ["A"], "名稱": ["甲"], "類別": ["1"]})
    with pytest.raises(RunError, match="新欄名「名稱」和現有欄位重複"):
        run(make("join", right="對照", on=on, columns=["名稱"]), df, tables)
    with pytest.raises(RunError, match="@對照 沒有欄位：區域"):
        run(make("join", right="對照", on=on, columns=["區域"]), df, tables)
    with pytest.raises(RunError, match="「代號」是另一張表的鍵值，不用帶進來"):
        run(make("join", right="對照", on=on, columns=["代號"]), df, tables)
    with pytest.raises(RunError, match="沒有階段「對照」的結果"):
        run(make("join", right="對照", on=on, columns=["類別"]), df)


def test_join_effect_checks_right_columns():
    effect = {
        "kind": "join",
        "stage": "對照",
        "how": "left",
        "names": ["類別"],
        "on": [{"left": "品號", "right": "品號"}],
    }
    assert apply_effect(["品號", "金額"], effect, {"對照": ["品號", "類別"]}) == [
        "品號",
        "金額",
        "類別",
    ]
    assert apply_effect(["品號"], {**effect, "how": "anti"}, {"對照": ["品號", "類別"]}) == ["品號"]
    with pytest.raises(EffectError, match="階段「對照」沒有欄位：類別"):
        apply_effect(["品號"], effect, {"對照": ["品號"]})
    assert apply_effect(None, effect, {}) is None  # 不知道欄位時不檢查
    with pytest.raises(EffectError, match="要設定至少一組鍵"):
        apply_effect(["品號"], {**effect, "on": []})


# ── 依條件複製欄位 ─────────────────────────────────────────────────


def test_conditional_copy_then_fill_down_brings_section_titles_to_details():
    df = frame(
        {
            "A": ["門市：", "115/01/05", None, "門市：", "115/01/07"],
            "B": ["北區門市", "原子筆", "保溫瓶", "中區門市", "滑鼠"],
        },
        files=["a", "a", "a", "a", "b"],
    )
    copy = make(
        "conditional_copy",
        conditions=[{"column": "A", "op": "starts_with", "value": "門市："}],
        column="B",
        name="門市",
    )
    out, total, _ = run(copy, df)
    assert out["門市"].to_list() == [
        "北區門市",
        None,
        None,
        "中區門市",
        None,
    ]  # 空值的條件欄不算符合
    assert lines(out) == [("a", 1), ("a", 2), ("a", 3), ("a", 4), ("b", 1)] and total == 0
    filled, _, _ = run(make("fill_down", columns=["門市"]), out)
    assert filled["門市"].to_list() == ["北區門市", "北區門市", "北區門市", "中區門市", None]
    assert copy.describe() == "符合「A 開頭是「門市：」」的列，把「B」複製到「門市」"


def test_conditional_copy_any_and_errors():
    df = frame({"A": ["x", "y", "z"], "B": ["1", "2", "3"]})
    either = make(
        "conditional_copy",
        conditions=[
            {"column": "A", "op": "equals", "value": "x"},
            {"column": "A", "op": "equals", "value": "z"},
        ],
        match="any",
        column="B",
        name="C",
    )
    assert run(either, df)[0]["C"].to_list() == ["1", None, "3"]
    with pytest.raises(RunError, match="新欄名「A」和現有欄位重複"):
        run(either.model_copy(update={"name": "A"}), df)
    with pytest.raises(RunError, match="欄位不存在：D"):
        run(either.model_copy(update={"column": "D"}), df)


def test_step_errors_are_in_chinese():
    from pydantic import ValidationError

    from tablade.recipe.schema import pydantic_issues

    with pytest.raises(ValidationError) as err:
        get("conditional_copy").model_validate(
            {"id": "c", "type": "conditional_copy", "conditions": [], "column": ""}
        )
    issues = {i["path"]: i["error"] for i in pydantic_issues(err.value)}
    assert issues == {"conditions": "至少要有 1 項", "column": "不能是空白", "name": "必填，請填寫"}


def test_split_merge_into_stream_matches_eager(tmp_path):
    from tablade.engine.run import execute
    from tablade.fixtures import EXTRA_DELIMITER_COLUMNS, extra_delimiter_rows
    from tablade.recipe.schema import parse_recipe

    lines, values = extra_delimiter_rows(rows=400)
    (tmp_path / "d.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    recipe = parse_recipe(
        {
            "tablade_recipe": "1.2",
            "name": "m",
            "stages": [
                {
                    "name": "a",
                    "output_format": "parquet",
                    "input": {"files": ["d.txt"], "reader": "text_lines", "encoding": "utf-8"},
                    "steps": [
                        {
                            "id": "s1",
                            "type": "split_delimited",
                            "delimiter": "|",
                            "columns": list(EXTRA_DELIMITER_COLUMNS),
                            "on_extra": "merge_into",
                            "merge_column": "備註",
                        }
                    ],
                }
            ],
            "output": {},
        }
    )
    _, eager = execute(recipe, tmp_path, output=tmp_path / "e.parquet", mode="eager")
    execute(recipe, tmp_path, output=tmp_path / "s.parquet", mode="stream")
    a, b = pl.read_parquet(tmp_path / "e.parquet"), pl.read_parquet(tmp_path / "s.parquet")
    assert a.equals(b)
    assert [list(r) for r in a.select(list(EXTRA_DELIMITER_COLUMNS)).rows()] == values
    assert eager.warning_count == 20
