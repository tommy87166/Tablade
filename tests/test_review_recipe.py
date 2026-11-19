"""審查發現（docs/review-findings.md）的回歸測試：驗證器、Recipe schema、AI skill、profile、打包與程式規則。"""

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tracemalloc
from pathlib import Path
from typing import Literal

import polars as pl
import pytest
from pydantic import ValidationError

from tablade.cli import main as cli_main
from tablade.engine.check import check_recipe
from tablade.engine.run import execute
from tablade.i18n import using
from tablade.ingest.transcode import CHUNK
from tablade.profile import mask_name, mask_text, profile_file
from tablade.recipe import validator
from tablade.recipe.schema import RecipeError, parse_recipe, pydantic_issues
from tablade.recipe.spec import build_spec, structural
from tablade.skill import BUTTONS, skill_files, skill_markdown
from tablade.steps import all_steps, get
from tablade.steps.base import Example, Step
from tablade.steps.filter import Condition
from tablade.steps.registry import register

ROOT = Path(__file__).resolve().parents[1]
SPEC = build_spec()


def recipe(*steps, reader="text_lines", **stage) -> dict:
    one = {
        "name": "主要",
        "input": {"files": ["a.txt"], "reader": reader, "encoding": "auto"},
        "steps": list(steps),
        "output_format": "parquet",
    }
    return {
        "tablade_recipe": "1.0",
        "name": "x",
        "stages": [{**one, **stage}],
        "output": {"keep_system_columns": True, "manifest": False},
    }


def errors(data: dict) -> list[dict]:
    return [i for i in validator.validate(data, SPEC, {}) if i["severity"] == "error"]


def strict_error(data: dict) -> str | None:
    """pydantic（Tablade 開啟 Recipe 時）的第一個錯誤；通過時是 None。"""
    try:
        parse_recipe(data)
    except RecipeError as err:
        return err.issues[0]["error"]
    return None


def both_reject(data: dict) -> list[dict]:
    found = errors(data)
    assert found, "驗證器沒抓到"
    assert strict_error(data) is not None, "pydantic 沒抓到"
    return found


# ── F-019：type／op／reader／output_format 是清單或物件 ──────────────────


def _set(data: dict, path: list, value) -> dict:
    data = copy.deepcopy(data)
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


F019_BASE = recipe(
    {"id": "s1", "type": "remove_top_rows", "n": 2},
    {
        "id": "s2",
        "type": "filter",
        "conditions": [{"column": "line", "op": "equals", "value": "x"}],
    },
    output_file="out",
)
F019_CASES = {
    "步驟的 type 是清單": (["stages", 0, "steps", 0, "type"], ["remove_top_rows"]),
    "步驟的 type 是物件": (["stages", 0, "steps", 0, "type"], {"x": 1}),
    "條件的 op 是清單": (["stages", 0, "steps", 1, "conditions", 0, "op"], ["equals"]),
    "output_format 是清單": (["stages", 0, "output_format"], ["csv"]),
    "reader 是清單": (["stages", 0, "input", "reader"], ["csv"]),
    "reader 是物件": (["stages", 0, "input", "reader"], {"csv": True}),
}


@pytest.mark.parametrize("title", F019_CASES)
def test_f019_unhashable_values_are_reported_not_raised(title):
    """F-019：值是清單或物件時，驗證器回報一個問題（指出欄位），不是丟出 TypeError。"""
    path, value = F019_CASES[title]
    data = _set(F019_BASE, path, value)
    found = both_reject(data)
    where = re.sub(r"\.(\d+)", r"[\1]", ".".join(str(p) for p in path)).replace(".[", "[")
    assert found[0]["path"] == where


def test_f019_validator_cli_prints_json_for_odd_types(tmp_path, capsys):
    """F-019：skill 的 validate_recipe.py --json 一定有 JSON 輸出（AI 拿得到 path 與 error）。"""
    path = tmp_path / "x.recipe.json"
    data = _set(F019_BASE, ["stages", 0, "steps", 0, "type"], ["remove_top_rows"])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert validator.main([str(path), "--json"], spec=SPEC) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["issues"][0]["path"] == "stages[0].steps[0].type"


def test_f019_unexpected_errors_become_an_issue(monkeypatch):
    """F-019：驗證器自己出錯時也回報成一個問題，不讓例外跑出去。"""

    def broken(*args):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(validator, "_semantic", broken)
    issues = validator.validate(recipe(), SPEC, {})
    assert len(issues) == 1 and issues[0]["path"] == "(Recipe)"
    assert "驗證器無法檢查這份 Recipe（TypeError" in issues[0]["error"]


# ── F-021：系統欄位依階段實際有沒有 ──────────────────────────────────


def cond(column: str, op: str = "is_not_empty") -> dict:
    return {"id": "f", "type": "filter", "conditions": [{"column": column, "op": op}]}


def test_f021_row_type_is_not_a_system_column():
    """F-021：__row_type 程式裡沒有任何地方產生，不是系統欄位。"""
    assert "__row_type" not in validator.SYSTEM_COLUMNS
    assert "__row_type" not in SPEC["system_columns"]
    assert "沒有系統欄位「__row_type」" in errors(recipe(cond("__row_type")))[0]["error"]


@pytest.mark.parametrize(
    ("column", "reader", "sheet", "ok"),
    [
        ("__source_file", "csv", "", True),
        ("__line_no", "text_lines", "", True),
        ("__sheet", "csv", "", False),
        ("__sheet", "excel", "", False),  # 只讀一個工作表時沒有 __sheet
        ("__sheet", "excel", "*", True),
        ("__stage", "csv", "", False),
        ("__orig_line_no", "csv", "", False),  # 還沒合併來源檔
    ],
)
def test_f021_system_columns_depend_on_the_reader(column, reader, sheet, ok):
    """F-021：__sheet 只有讀 Excel 全部工作表才有、__stage 只有讀前面階段才有、__orig_* 要合併之後。"""
    data = recipe(cond(column), reader=reader)
    if sheet:
        data["stages"][0]["input"]["sheet"] = sheet
    found = errors(data)
    if ok:
        assert found == []
    else:
        assert found[0]["path"] == "stages[0].steps[0].conditions[0].column"
        assert f"這一步之前沒有系統欄位「{column}」" in found[0]["error"]


def test_f021_system_columns_after_merge_and_across_stages():
    """F-021：合併來源檔之後有 __orig_*；讀前面階段時有 __stage，也帶著前面階段的系統欄位。"""
    merged = recipe(
        {"id": "m", "type": "merge_files"},
        cond("__orig_source_file"),
        reader="excel",
    )
    merged["stages"][0]["input"]["sheet"] = "*"
    merged["stages"][0]["steps"].append({**cond("__orig_sheet"), "id": "g"})
    merged["stages"][0]["steps"].append({**cond("__orig_stage"), "id": "h"})
    found = errors(merged)
    assert [i["path"] for i in found] == ["stages[0].steps[3].conditions[0].column"]
    assert "合併前要有 __stage" in found[0]["hint"]

    data = recipe(reader="excel")
    data["stages"][0]["input"]["sheet"] = "*"
    data["stages"].append(
        {
            "name": "後面",
            "input": {"reader": "stages", "stages": ["主要"]},
            "steps": [
                cond("__sheet"),
                {**cond("__stage"), "id": "g"},
                {"id": "m", "type": "merge_files"},
                {**cond("__orig_stage"), "id": "h"},
                {**cond("__orig_sheet"), "id": "i"},
            ],
            "output_format": "parquet",
        }
    )
    assert errors(data) == []


@pytest.mark.parametrize(
    "step",
    [
        {"id": "s", "type": "fill_down", "columns": ["__source_file"]},
        {"id": "s", "type": "trim_clean", "columns": ["__line_no"]},
        {"id": "s", "type": "dedupe", "columns": ["__line_no"]},
        {
            "id": "s",
            "type": "split_column",
            "column": "__line_no",
            "delimiter": "-",
            "names": ["a"],
        },
        {
            "id": "s",
            "type": "conditional_copy",
            "column": "__line_no",
            "name": "x",
            "conditions": [{"column": "__line_no", "op": "is_not_empty"}],
        },
    ],
)
def test_f021_system_columns_only_in_conditions(step):
    """F-021：系統欄位只能用在條件裡；其他參數引擎只接受資料欄位（會回報欄位不存在）。"""
    found = errors(recipe(step))
    assert found and "只能用在條件裡" in found[0]["error"]
    assert not found[0]["path"].endswith("conditions[0].column")


def test_f021_condition_steps_accept_system_columns():
    """F-021：篩選、將列進行分類、依條件複製欄位的條件可以引用系統欄位。"""
    for cls in all_steps():
        example = cls.examples[0]
        text = json.dumps(example.params)
        if '"conditions"' not in text:
            continue
        params = json.loads(
            re.sub(r'"column": "[^"]*"(?=[^{}]*"op")', '"column": "__line_no"', text)
        )
        step = {"id": "s", "type": cls.type_name(), **params}
        assert errors(recipe(step, reader="csv")) == [], cls.type_name()
    assert set(SPEC["system_column_defs"]) == {m.__name__ for m in Condition.__origin__.__args__}


def test_f021_validator_and_engine_agree_on_system_columns(tmp_path):
    """F-021：驗證通過的系統欄位引用，引擎也真的跑得過；驗證擋下的，引擎也會失敗。"""
    (tmp_path / "a.csv").write_text("品號,金額\nP1,1\nP2,2\n", encoding="utf-8")
    data = recipe(reader="csv")
    data["stages"][0]["input"]["files"] = ["a.csv"]
    data["stages"].append(
        {
            "name": "後面",
            "input": {"reader": "stages", "stages": ["主要"]},
            "steps": [
                {"id": "m", "type": "merge_files"},
                {
                    "id": "f",
                    "type": "filter",
                    "conditions": [
                        {"column": "__orig_stage", "op": "equals", "value": "主要"},
                        {"column": "__orig_line_no", "op": "gt", "value": 2},
                    ],
                },
            ],
            "output_format": "parquet",
        }
    )
    result = check_recipe(data, tmp_path)
    assert result.ok, result.issues
    frame, _ = execute(result.recipe, tmp_path, mode="eager")
    assert frame["品號"].to_list() == ["P2"]
    data["stages"][0]["steps"] = [cond("__sheet")]
    assert not check_recipe(data, tmp_path).ok


# ── F-024：步驟自己的規則（pydantic 的 field_validator）也在驗證器 ──────────


F024_REJECT = {
    "extract_regex：用了 look-ahead": (
        {
            "id": "s1",
            "type": "extract_regex",
            "column": "line",
            "pattern": "(?=a)(.)",
            "names": ["x"],
        },
        "stages[0].steps[0].pattern",
    ),
    "extract_regex：反向參照": (
        {
            "id": "s1",
            "type": "extract_regex",
            "column": "line",
            "pattern": r"(a)\1",
            "names": ["x"],
        },
        "stages[0].steps[0].pattern",
    ),
    "extract_regex：括號不成對": (
        {"id": "s1", "type": "extract_regex", "column": "line", "pattern": "(a", "names": ["x"]},
        "stages[0].steps[0].pattern",
    ),
    "extract_regex：2 個群組、1 個欄名": (
        {
            "id": "s1",
            "type": "extract_regex",
            "column": "line",
            "pattern": "(a)(b)",
            "names": ["x"],
        },
        "stages[0].steps[0].names",
    ),
    "extract_regex：沒有群組": (
        {
            "id": "s1",
            "type": "extract_regex",
            "column": "line",
            "pattern": "a(?:b)",
            "names": ["x"],
        },
        "stages[0].steps[0].names",
    ),
    "split_fixed_width：位置沒有由小到大": (
        {"id": "s1", "type": "split_fixed_width", "starts": [5, 1], "names": ["a", "b"]},
        "stages[0].steps[0].starts",
    ),
    "split_fixed_width：位置和欄名數量不同": (
        {"id": "s1", "type": "split_fixed_width", "starts": [1, 5], "names": ["a"]},
        "stages[0].steps[0].names",
    ),
    "parse_date：自訂正規表示式只有 2 個群組": (
        {
            "id": "s1",
            "type": "parse_date",
            "columns": ["line"],
            "pattern": "custom",
            "regex": r"(\d+)/(\d+)",
            "calendar": "ad",
        },
        "stages[0].steps[0].regex",
    ),
    "parse_date：自訂但沒填正規表示式": (
        {
            "id": "s1",
            "type": "parse_date",
            "columns": ["line"],
            "pattern": "custom",
            "calendar": "ad",
        },
        "stages[0].steps[0].regex",
    ),
    "replace_values：正規表示式用了 look-behind": (
        {
            "id": "s1",
            "type": "replace_values",
            "columns": ["line"],
            "match": "regex",
            "replacements": [{"find": "(?<=a)b", "replace": "c"}],
        },
        "stages[0].steps[0].replacements[0].find",
    ),
    "split_delimited：merge_into 沒填 merge_column": (
        {"id": "s1", "type": "split_delimited", "columns": ["a", "b"], "on_extra": "merge_into"},
        "stages[0].steps[0].merge_column",
    ),
    "split_column：merge_column 不是拆出來的欄名": (
        {
            "id": "s1",
            "type": "split_column",
            "column": "line",
            "delimiter": "|",
            "names": ["a", "b"],
            "on_extra": "merge_into",
            "merge_column": "x",
        },
        "stages[0].steps[0].merge_column",
    ),
    "split_delimited：沒選 merge_into 卻填了 merge_column": (
        {"id": "s1", "type": "split_delimited", "columns": ["a", "b"], "merge_column": "a"},
        "stages[0].steps[0].merge_column",
    ),
    "步驟 id 結尾有換行": (
        {"id": "s1\n", "type": "remove_top_rows", "n": 1},
        "stages[0].steps[0].id",
    ),
}


@pytest.mark.parametrize("title", F024_REJECT)
def test_f024_step_rules_are_in_the_validator(title):
    """F-024：只有 pydantic 會擋的規則，skill 的驗證器也要擋（路徑指到那個參數）。"""
    step, where = F024_REJECT[title]
    found = both_reject(recipe(step))
    assert found[0]["path"] == where


def test_f024_messages_match_pydantic():
    """F-024：群組數、開始位置的訊息和 Tablade 的一樣。"""
    for title in (
        "extract_regex：2 個群組、1 個欄名",
        "split_fixed_width：位置沒有由小到大",
        "split_fixed_width：位置和欄名數量不同",
        "parse_date：自訂正規表示式只有 2 個群組",
        "split_delimited：merge_into 沒填 merge_column",
        "split_column：merge_column 不是拆出來的欄名",
        "split_delimited：沒選 merge_into 卻填了 merge_column",
    ):
        data = recipe(F024_REJECT[title][0])
        assert errors(data)[0]["error"] == strict_error(data), title


def test_f024_trailing_newline_in_stage_name_and_version():
    """F-024：字串結尾多一個換行（Python 的 $ 會比對到換行之前），pydantic 不接受，驗證器也不接受。"""
    data = recipe()
    data["stages"][0]["name"] = "主要\n"
    assert both_reject(data)[0]["path"] == "stages[0].name"
    data = recipe()
    data["tablade_recipe"] = "1.0\n"
    assert both_reject(data)[0]["path"] == "tablade_recipe"


@pytest.mark.parametrize(
    "step",
    [
        {
            "id": "s",
            "type": "extract_regex",
            "column": "line",
            "pattern": r"\p{Han}+(\d+)",
            "names": ["x"],
        },
        {
            "id": "s",
            "type": "extract_regex",
            "column": "line",
            "pattern": r"(?<店>[^,]+),(?P<號>\d+)",
            "names": ["a", "b"],
        },
        {
            "id": "s",
            "type": "extract_regex",
            "column": "line",
            "pattern": r"[(?=](x)",
            "names": ["a"],
        },
        {
            "id": "s",
            "type": "parse_date",
            "columns": ["line"],
            "pattern": "slash",
            "regex": "(",
            "calendar": "ad",
        },
        {
            "id": "s",
            "type": "filter",
            "conditions": [{"column": "line", "op": "regex", "pattern": r"^\p{Han}{2}[[:digit:]]"}],
        },
    ],
)
def test_f024_rust_only_syntax_is_not_rejected(step):
    """F-024：Python 的 re 不認得、Rust 可以的寫法（\\p{Han}、(?<名稱>…)）不能誤報；沒用到的 regex 不檢查。"""
    data = recipe(step)
    assert errors(data) == [] and strict_error(data) is None


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a)(b)",
        r"(?:a)(b)",
        r"(?i)(a)",
        r"(?P<x>a)(?<y>b)",
        r"[(](x)",
        r"[]a](c)",
        r"[^]a](c)",
        r"[a\]](c)",
        r"[[:alpha:]](x)",
        r"\(not a group\)",
        r"(?=a)",
        r"(?<!a)b",
        r"\1",
        r"[\1]",
        r"(a",
        r"a)",
        r"[ab",
    ],
)
def test_f024_regex_scan_agrees_with_polars(pattern):
    """F-024：驗證器掃到的群組數和錯誤，和 Polars（Rust regex）一致；不確定的寧可放過。"""
    scan = validator.RegexScan(pattern)
    try:
        groups = len(pl.Series([""], dtype=pl.String).str.extract_groups(pattern).struct.fields)
    except pl.exceptions.PolarsError:
        groups = None
    if groups is None:
        assert scan.error is not None
    else:
        assert scan.error is None and scan.groups == groups


def _field_validators() -> set[tuple[str, str]]:
    models = [(cls.type_name(), cls) for cls in all_steps()]
    models += [(f"condition:{m.__name__}", m) for m in Condition.__origin__.__args__]
    found = set()
    for name, model in models:
        decorators = model.__pydantic_decorators__
        assert not decorators.model_validators, f"{name} 有 model_validator：驗證器也要有同樣的檢查"
        for dec in decorators.field_validators.values():
            found |= {(name, field) for field in dec.info.fields}
    return found


def test_f024_every_pydantic_rule_has_a_validator_check():
    """F-024：新增 pydantic 的 field_validator 時，recipe/validator.py 的 step_rules 也要補上同樣的檢查。"""
    covered = {
        ("remove_columns", "columns"),  # 欄位效果 drop：「要選至少一個要刪除的欄位」
        ("remove_columns", "positions"),
        ("extract_regex", "names"),  # step_rules：群組數
        ("split_fixed_width", "starts"),
        ("split_fixed_width", "names"),
        ("parse_date", "regex"),
        ("replace_values", "replacements"),  # RegexScan：環顧、反向參照、括號
        ("condition:RegexCondition", "pattern"),
        ("split_delimited", "merge_column"),  # step_rules：_merge_column_rules
        ("split_column", "merge_column"),
    }
    assert _field_validators() <= covered


# ── F-025：打包後的 App 也帶全部範例 ─────────────────────────────────


class _Recorder:
    calls: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).calls[type(self).__name__] = (args, kwargs)
        self.scripts = self.binaries = self.datas = self.pure = []


def _spec_datas(mode: str) -> list[tuple[str, str]]:
    namespace = {
        name: type(name, (_Recorder,), {})
        for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")
    }
    namespace["SPECPATH"] = str(ROOT)
    namespace["__file__"] = str(ROOT / "tablade.spec")
    _Recorder.calls = {}
    old = dict(os.environ)
    try:
        os.environ["TABLADE_BUILD"] = mode
        exec(
            compile((ROOT / "tablade.spec").read_text(encoding="utf-8"), "tablade.spec", "exec"),
            namespace,
        )
    finally:
        os.environ.clear()
        os.environ.update(old)
    return _Recorder.calls["Analysis"][1]["datas"]


@pytest.mark.parametrize("mode", ["onefile", "onedir"])
def test_f025_every_example_is_packaged(mode):
    """F-025：tablade.spec 把 examples/ 底下每一個範例都打包（App 下載的 skill 和 Release 的一樣）。"""
    datas = _spec_datas(mode)
    packaged = {Path(src).name for src, dest in datas if dest == "examples"}
    examples = {p.name for p in (ROOT / "examples").glob("*.recipe.json")}
    assert len(examples) >= 4 and packaged == examples
    assert ("src/tablade/recipe/validator.py", "skill") in datas


def test_f025_build_checks_the_skill_against_the_source():
    """F-025：scripts/build.py 檢查打包後產生的 skill：檔案（含每個範例）和原始碼產生的一樣。"""
    spec = importlib.util.spec_from_file_location("build_script", ROOT / "scripts" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.expected_skill_files() == set(skill_files())
    assert (
        len([n for n in build.expected_skill_files() if n.startswith("references/examples/")]) >= 4
    )


# ── F-047：版本號、輸出檔名、舊寫法的提示 ────────────────────────────


@pytest.mark.parametrize("version", ["1.00", "01.0", "１.０", "1.0 ", "1.-0", "1"])
def test_f047_version_must_be_plain(version):
    """F-047：版本號只能是半形數字、沒有多的 0（SKILL.md 寫「一定要是 "1.0"」）。"""
    data = recipe()
    data["tablade_recipe"] = version
    assert both_reject(data)[0]["path"] == "tablade_recipe"


def test_f047_valid_versions_still_pass():
    assert validator.check_version("1.0", "1.0") is None
    assert validator.check_version("1.10", "1.12") is None
    assert "比這裡支援的" in validator.check_version("1.10", "1.9")["error"]


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("CON", "保留名稱"),
        ("con", "保留名稱"),
        ("Lpt1 ", "保留名稱"),
        ("NUL.明細", "保留名稱"),
        ("a\x7fb", "不能有"),
        ("長" * 67, "太長"),
        ("a" * 201, "太長"),
    ],
)
def test_f047_output_file_names(name, message):
    """F-047：Windows 的保留名稱、\\x7f、太長的檔名都在驗證時擋下（Tablade 開啟時也一樣）。"""
    data = recipe(output_file=name)
    found = both_reject(data)
    assert found[0]["path"] == "stages[0].output_file" and message in found[0]["error"]


@pytest.mark.parametrize("name", ["CONSOLE", "COM10", "明細_CON", "長" * 66, "a" * 200])
def test_f047_ordinary_output_file_names_pass(name):
    data = recipe(output_file=name)
    assert errors(data) == [] and strict_error(data) is None


def test_f047_old_format_hint_mentions_output_format():
    """F-047：照舊寫法的提示改完要能通過：提示裡要有每個階段必填的 output_format。"""
    old = {"tablade_recipe": "1.0", "inputs": [{"files": ["a.csv"]}], "steps": [], "output": {}}
    hint = validator.validate(old, SPEC, {})[0]["hint"]
    assert '"output_format": "parquet"' in hint
    fixed = {
        "tablade_recipe": "1.0",
        "stages": [
            {
                "name": "預設階段",
                "input": {"files": ["a.csv"]},
                "steps": [],
                "output_format": "parquet",
            }
        ],
        "output": {},
    }
    assert errors(fixed) == []


def test_f001_output_names_compare_unicode_normalized():
    """F-001：主檔名不分大小寫，也不分 NFC／NFD（磁碟上 NFD 的 café 和 NFC 的 café 是同一個檔）。"""
    nfc, nfd = "caf\u00e9", "cafe\u0301"
    data = recipe(output_file=nfc)
    data["stages"].append(
        {
            "name": "後面",
            "input": {"reader": "stages", "stages": ["主要"]},
            "steps": [],
            "output_file": nfd.upper(),
            "output_format": "csv",
        }
    )
    found = both_reject(data)
    assert found[0]["path"] == "stages[1].output_file" and "重複" in found[0]["error"]


def test_f023_validate_reports_duplicate_output_names(tmp_path, capsys):
    """F-023：最後一個階段沒填檔名、用 Recipe 名稱而撞名時，tablade validate 就回報（不用等到 run）。"""
    (tmp_path / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")
    data = recipe(reader="csv", output_file="合計")
    data["name"] = "合計"
    data["stages"][0]["input"]["files"] = ["a.csv"]
    data["stages"].append(
        {
            "name": "最後",
            "input": {"reader": "stages", "stages": ["主要"]},
            "steps": [],
            "output_format": "parquet",
        }
    )
    path = tmp_path / "合計.recipe.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for command in (["validate"], ["run", "--out-dir", str(tmp_path / "out")]):
        assert cli_main([*command, "--json", str(path)]) == 1, command
        issues = json.loads(capsys.readouterr().out)["issues"]
        assert any("重複" in i["error"] for i in issues), (command, issues)
    assert not (tmp_path / "out").exists()


def test_f023_run_checks_the_plan_with_out_dir(tmp_path, capsys):
    """F-023：tablade run --out-dir 先用那個資料夾檢查輸出的規劃（寫出的位置是輸入檔時，驗證就擋下）。"""
    (tmp_path / "out" / "output").mkdir(parents=True)
    (tmp_path / "out" / "output" / "明細.csv").write_text("品號\nP1\n", encoding="utf-8")
    data = recipe(reader="csv", output_file="明細", output_format="csv")
    data["stages"][0]["input"]["files"] = ["out/output/明細.csv"]
    path = tmp_path / "x.recipe.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert cli_main(["run", "--json", "--out-dir", str(tmp_path / "out"), str(path)]) == 1
    issues = json.loads(capsys.readouterr().out)["issues"]
    assert [i["path"] for i in issues] == ["stages[0].output_file"]
    assert "輸入檔" in issues[0]["error"]


# ── F-048：tablade profile ───────────────────────────────────────


def test_f048_mask_hides_every_non_layout_character():
    """F-048：遮罩只留空白與標點（版面），㈱、Ⅳ、½、㊣、㎡ 這類字也遮。"""
    assert (
        mask_text("２０２６／０１／０６,Ⅳ號店㈱,½ ㊣ ㎡ x²") == "9999／99／99,〇〇〇〇,〇 〇 〇 a9"
    )
    assert mask_text("115/01/05 北區門市 A001 1,280") == "999/99/99 〇〇〇〇 a999 9,999"
    assert mask_name("明細_北區門市.csv") == "〇〇_〇〇〇〇.csv"


def test_f048_mask_also_hides_the_file_name(tmp_path):
    path = tmp_path / "明細_北區門市.csv"
    path.write_text("日期,金額\n115/01/05,1\n", encoding="utf-8")
    text = profile_file(path, mask=True)
    assert text.splitlines()[0] == "## 〇〇_〇〇〇〇.csv" and "北區" not in text


def test_f048_lines_cannot_be_negative(tmp_path, capsys):
    path = tmp_path / "a.txt"
    path.write_text("".join(f"{i}\n" for i in range(57)), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["profile", str(path), "--lines", "-1"])
    assert exit_info.value.code == 2 and "不能小於 0" in capsys.readouterr().err
    with pytest.raises(ValueError):
        profile_file(path, head=-1)


def test_f048_profile_uses_fixed_memory(tmp_path):
    """F-048：一塊一塊讀，不把整個檔讀進記憶體（以前約是檔案大小的 6 倍）。"""
    path = tmp_path / "big.csv"
    line = ("北區門市,保溫瓶," + "１２３" * 30 + "\n").encode("cp950")
    with path.open("wb") as f:
        f.write(line * (16_000_000 // len(line)))
    size = path.stat().st_size
    tracemalloc.start()
    try:
        text = profile_file(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert "- 編碼：cp950" in text and f"- 行數：{size // len(line):,}（" in text
    assert peak < size / 2, f"記憶體峰值 {peak:,} bytes，檔案 {size:,} bytes"


def test_f048_chunk_boundaries_do_not_change_the_counts(tmp_path):
    """F-048：\\r\\n 剛好跨兩塊、沒有換行的超長一行：行數與換行的統計和整份讀時一樣。"""
    path = tmp_path / "edge.txt"
    first = b"a," * ((CHUNK - 1) // 2) + b"x" * ((CHUNK - 1) % 2)
    path.write_bytes(first + b"\r\n" + "門市,1\r\n".encode() * 3 + b"z" * 300_000)
    text = profile_file(path, head=2, tail=2)
    assert "- 行數：5（空白行 0）；換行：CRLF 4" in text
    assert "最長一行：1,048,575 字元" in text
    lines = text.splitlines()
    assert lines[lines.index("最後 2 行：") + 3] == "    5: " + "z" * 100_000 + "…"


# ── F-049：check_code_policy 擋得住常見的寫法 ────────────────────────


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/tablade/x.py", "from importlib import import_module\nimport_module('socket')\n"),
        ("src/tablade/x.py", "import importlib as il\nil.import_module('socket')\n"),
        ("src/tablade/x.py", "import builtins\nbuiltins.eval('1+1')\n"),
        ("src/tablade/x.py", "import builtins as b\nb.exec('1')\n"),
        ("src/tablade/x.py", "e = eval\ne('1+1')\n"),
        ("src/tablade/x.py", "getattr(__builtins__, 'exec')('1')\n"),
        (
            "src/tablade/x.py",
            "import subprocess\nsubprocess.run(['curl', 'https://example.com'])\n",
        ),
        ("src/tablade/x.py", "import os\nos.system('curl https://example.com')\n"),
        ("src/tablade/x.py", "from os import popen\n"),
        ("src/tablade/x.py", "import polars as pl\npl.read_csv('https://example.com/a.csv')\n"),
        (
            "src/tablade/x.py",
            "import polars as pl\npl.scan_parquet('a.parquet', storage_options={})\n",
        ),
        ("src/tablade/vendor/tablade/update.py", "import urllib.request\n"),
    ],
)
def test_f049_code_policy_rejects(check_code_policy, path, source):
    """F-049：別名、從模組取、呼叫外部程式、Polars 讀網址，以及同名但不同位置的 update.py。"""
    assert check_code_policy.check_source(path, source)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/tablade/update.py", "import urllib.request\n"),
        ("./src/tablade/update.py", "import urllib.request\n"),
        (str(ROOT / "src" / "tablade" / "update.py"), "import urllib.request\n"),
        ("src/tablade/desktop.py", "import subprocess\nimport os\nos.startfile('x')\n"),
        ("src/tablade/x.py", "import re\nre.compile('x')\n"),
        ("src/tablade/x.py", "import polars as pl\npl.col('a').list.eval(pl.element())\n"),
        ("src/tablade/x.py", "import polars as pl\npl.read_csv('data/a.csv')\n"),
    ],
)
def test_f049_code_policy_allows(check_code_policy, path, source):
    assert check_code_policy.check_source(path, source) == []


# ── F-050：schema lock 不記選項的說明文字 ────────────────────────────


def test_f050_lock_ignores_option_hints():
    """F-050：只改 ui_option_hints（選項下面的說明）不用升 RECIPE_VERSION。"""
    spec = copy.deepcopy(SPEC)
    hints = spec["schema"]["$defs"]["ParseDate"]["properties"]["pattern"]["ui_option_hints"]
    hints["slash"] = "改了說明"
    assert structural(spec) == structural(SPEC)


# ── F-054：pydantic 的錯誤都有中英文 ─────────────────────────────────


F054_CASES = {
    "篩選的條件沒有 op": (
        "filter",
        {"conditions": [{"column": "金額", "value": "x"}]},
        ("缺少 op", "Missing op"),
    ),
    "篩選的 op 是空字串": (
        "filter",
        {"conditions": [{"column": "金額", "op": "", "value": "x"}]},
        ("不支援的 op：", "Unsupported op:"),
    ),
    "篩選的條件不是物件": (
        "filter",
        {"conditions": ["金額"]},
        ("應該是物件", "Should be an object"),
    ),
    "數值條件的值是文字": (
        "filter",
        {"conditions": [{"column": "金額", "op": "gt", "value": "abc"}]},
        ("應該是數字", "Should be a number"),
    ),
    "小數位數超過上限": (
        "parse_number",
        {"columns": ["金額"], "kind": "decimal", "scale": 10},
        ("不能大於 9", "Cannot be greater than 9"),
    ),
    "kind 填錯": (
        "parse_number",
        {"columns": ["金額"], "kind": "money"},
        ("不支援的值（可用：decimal、integer）", "Unsupported value (allowed: decimal, integer)"),
    ),
}


@pytest.mark.parametrize("title", F054_CASES)
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_f054_pydantic_messages_are_translated(title, lang):
    """F-054：這幾種錯誤不再是 pydantic 的英文原文；選項用「、」接。"""
    name, params, expected = F054_CASES[title]
    with using(lang), pytest.raises(ValidationError) as err:
        get(name).model_validate({"id": "s1", "type": name, **params})
    with using(lang):
        message = pydantic_issues(err.value)[0]["error"]
    assert expected[0 if lang == "zh" else 1] in message
    assert "Input " not in message and "Unable to" not in message and " or '" not in message


def test_f054_union_tag_lists_the_choices():
    data = recipe({"id": "s1", "type": "remove_top_row", "n": 1})
    message = strict_error(data)
    assert message.startswith("不支援的 type：remove_top_row（可用：remove_top_rows、")


# ── F-068：registry 檢查 CLAUDE.md 寫的規則 ──────────────────────────


def _dummy(**attrs):
    body = {
        "__doc__": "測試用的步驟。",
        "__annotations__": {"type": Literal["zz_review_dummy"]},
        "type": "zz_review_dummy",
        "label": "測試",
        "label_en": "Test",
        "doc_en": "A step for tests.",
        "category": "列",
        "ai_notes": "測試用",
        "examples": (Example(title="x", params={}, input={"a": [1]}, output={"a": [1]}),),
        **attrs,
    }
    return type("ZzReviewDummy", (Step,), body)


@pytest.mark.parametrize(
    ("attrs", "message"),
    [({"ai_notes": ""}, "ai_notes"), ({"category": "其他"}, "CATEGORY_EN")],
)
def test_f068_register_checks_ai_notes_and_category(attrs, message):
    """F-068：沒有 ai_notes、分類不在 CATEGORY_EN（英文畫面的分頁會顯示中文）時登錄失敗。"""
    with pytest.raises(ValueError, match=message):
        register(_dummy(**attrs))
    assert "zz_review_dummy" not in {cls.type_name() for cls in all_steps()}


# ── F-070：SKILL.md 和程式一致 ──────────────────────────────────────


def test_f070_skill_md_matches_the_app():
    """F-070：SKILL.md 提到的按鈕畫面上真的有；輸出位置、格式的說法正確；速查表不重複步驟名稱。"""
    text = skill_markdown()
    frontend = "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "frontend" / "src").rglob("*.tsx")
    )
    for label in BUTTONS.values():
        assert f"「{label}」" in text
        assert (
            re.search(r"tr\(\s*'" + re.escape(label) + "'", frontend) or f"> {label}" in frontend
        ), label
    assert "選存檔位置" not in text and "複製錯誤報告」貼回來" not in text
    assert "輸出 Parquet 或 CSV" in text and "`output/`" in text
    rows = [line.split("|")[2].strip() for line in text.splitlines() if line.startswith("| `")]
    assert len(rows) == len(all_steps())
    assert not [r for r in rows if r.split("：")[1].startswith(r.split("：")[0])]


# ── F-076：設了 TABLADE_NO_UPDATE_CHECK 也能跑 test_update.py ─────────────


def test_f076_update_tests_ignore_the_environment():
    """F-076：網路受管控的電腦可能在系統裡設了 TABLADE_NO_UPDATE_CHECK；test_update.py 的結果不受影響。"""

    def summary(**extra) -> str:
        env = {k: v for k, v in os.environ.items() if k != "TABLADE_NO_UPDATE_CHECK"}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "tests/test_update.py",
            ],
            cwd=ROOT,
            env={**env, **extra},
            capture_output=True,
            text=True,
        )
        return re.sub(r" in [\d.]+s", "", result.stdout.strip().splitlines()[-1])

    assert summary(TABLADE_NO_UPDATE_CHECK="1") == summary()
