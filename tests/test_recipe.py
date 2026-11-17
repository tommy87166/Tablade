"""recipe：驗證器（結構＋語意）、與 pydantic 一致、版本號、schema lock。"""

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tablade.recipe import validator
from tablade.recipe.schema import RECIPE_VERSION, RecipeError, parse_recipe
from tablade.recipe.spec import LOCK_PATH, build_spec, lock_content, read_lock

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"
SPEC = build_spec()


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def paths(issues: list[dict]) -> list[str]:
    return [i["path"] for i in issues]


def check(recipe: dict, columns: dict | None = None) -> list[dict]:
    return validator.validate(recipe, SPEC, columns)


def test_example_recipe_passes_both():
    assert check(example()) == []
    assert len(parse_recipe(example()).stages[0].steps) == 8


# ── 結構：驗證器與 pydantic 必須一致 ─────────────────────────────


def mutate(fn):
    data = example()
    fn(data)
    return data


STRUCTURAL_CASES = {
    "unknown step type": lambda d: d["stages"][0]["steps"][0].update(type="remove_top_row"),
    "missing required": lambda d: d["stages"][0]["steps"][0].pop("n"),
    "wrong type": lambda d: d["stages"][0]["steps"][0].update(n="2"),
    "negative": lambda d: d["stages"][0]["steps"][0].update(n=-1),
    "bool is not int": lambda d: d["stages"][0]["steps"][0].update(n=True),
    "extra field": lambda d: d["stages"][0]["steps"][1].update(nn=1),
    "bad enum": lambda d: d["stages"][0]["steps"][6].update(on_extra="ignore"),
    "empty names": lambda d: d["stages"][0]["steps"][6].update(names=[]),
    "empty name item": lambda d: d["stages"][0]["steps"][6].update(names=["a", ""]),
    "bad condition op": lambda d: d["stages"][0]["steps"][7]["conditions"][0].update(
        op="startswith"
    ),
    "condition missing value": lambda d: d["stages"][0]["steps"][7]["conditions"][0].pop("value"),
    "number condition with text": lambda d: d["stages"][0]["steps"][7]["conditions"].append(
        {"column": "金額", "op": "gt", "value": "5"}
    ),
    "regex condition without pattern": lambda d: d["stages"][0]["steps"][7]["conditions"].append(
        {"column": "金額", "op": "regex"}
    ),
    "bad step id": lambda d: d["stages"][0]["steps"][0].update(id="s 1"),
    "two stages with the same name": lambda d: d["stages"].append(
        {"name": "預設階段", "input": {"reader": "stages", "stages": ["預設階段"]}}
    ),
    "no stages": lambda d: d.update(stages=[]),
    "stage without name": lambda d: d["stages"][0].pop("name"),
    "blank stage name": lambda d: d["stages"][0].update(name=" 預設 "),
    "first stage reads stages": lambda d: d["stages"][0]["input"].update(
        reader="stages", files=[], stages=["x"]
    ),
    "bad reader": lambda d: d["stages"][0]["input"].update(reader="pdf"),
    "old input id": lambda d: d["stages"][0]["input"].update(id="src"),
    "missing output": lambda d: d.pop("output"),
    "bad output format": lambda d: d["output"].update(format="csv"),
    "unknown top-level": lambda d: d.update(author="x"),
    "steps not a list": lambda d: d["stages"][0].update(steps={}),
    "newer minor": lambda d: d.update(tablade_recipe="1.9"),
    "other major": lambda d: d.update(tablade_recipe="2.0"),
    "bad version": lambda d: d.update(tablade_recipe="1"),
}


@pytest.mark.parametrize("name", STRUCTURAL_CASES)
def test_validator_agrees_with_pydantic(name):
    data = mutate(STRUCTURAL_CASES[name])
    issues = check(data)
    assert issues, f"驗證器沒抓到：{name}"
    with pytest.raises(RecipeError):
        parse_recipe(data)


def test_structural_messages_are_actionable():
    issues = check(mutate(STRUCTURAL_CASES["unknown step type"]))
    assert issues[0]["path"] == "stages[0].steps[0].type"
    assert issues[0]["hint"] == "是不是「remove_top_rows」？"
    issues = check(mutate(STRUCTURAL_CASES["extra field"]))
    assert (issues[0]["path"], issues[0]["error"]) == ("stages[0].steps[1].nn", "不認得的欄位")
    issues = check(mutate(STRUCTURAL_CASES["bad condition op"]))
    assert (
        issues[0]["path"] == "stages[0].steps[7].conditions[0].op"
        and issues[0]["hint"] == "是不是「starts_with」？"
    )


def test_output_path_and_pattern_are_not_allowed():
    """輸出位置在執行時才決定（沒有 output.path）；讀取資料用檔案清單 files（沒有 pattern）。"""
    data = example()
    data["output"]["path"] = "x.parquet"
    assert paths(check(data)) == ["output.path"]
    with pytest.raises(RecipeError):
        parse_recipe(data)
    data = example()
    data["stages"][0]["input"]["pattern"] = "x.csv"
    assert "stages[0].input.pattern" in paths(check(data))
    empty = example()
    empty["stages"][0]["input"]["files"] = []  # 剛新增的 Recipe：清單可以是空的
    assert check(empty) == []


def test_version_rules():
    assert validator.check_version("1.0", "1.4") is None
    assert "比這裡支援的" in validator.check_version("1.5", "1.4")["error"]
    assert "不相容" in validator.check_version("2.0", "1.4")["error"]
    assert validator.check_version(1, "1.4")["path"] == "tablade_recipe"


# ── 語意 ──────────────────────────────────────────────────────


def test_unknown_column_with_hint():
    data = mutate(lambda d: d["stages"][0]["steps"][5].update(columns=["門"]))
    issues = check(data)
    assert paths(issues) == ["stages[0].steps[5].columns[0]"]
    assert "是不是「門市」？" in issues[0]["hint"]
    parse_recipe(data)  # 語意問題只有驗證器抓得到


def test_new_column_steps_are_checked_by_the_validator():
    """保留和排序欄位、改欄名：欄名打錯有提示；物件的值、空的鍵、至少一項也會檢查。"""

    def with_step(step: dict) -> dict:
        return mutate(lambda d: d["stages"][0]["steps"].append({"id": "z", **step}))

    issues = check(with_step({"type": "select_columns", "columns": ["金額", "門"]}))
    assert (
        paths(issues) == ["stages[0].steps[8].columns[1]"]
        and "是不是「門市」？" in issues[0]["hint"]
    )
    issues = check(with_step({"type": "rename_columns", "names": {"品命": "商品"}}))
    assert paths(issues)[0] == "stages[0].steps[8].names.品命" and "品名" in issues[0]["hint"]
    assert paths(check(with_step({"type": "rename_columns", "names": {"品名": ""}}))) == [
        "stages[0].steps[8].names.品名"
    ]
    assert paths(check(with_step({"type": "rename_columns", "names": {"": "x"}}))) == [
        "stages[0].steps[8].names."
    ]
    assert "stages[0].steps[8].names" in paths(
        check(with_step({"type": "rename_columns", "names": {}}))
    )
    # 改完後重複：欄位效果檢查
    issues = check(with_step({"type": "rename_columns", "names": {"品名": "門市"}}))
    assert any("重複" in i["error"] for i in issues)
    assert check(with_step({"type": "rename_columns", "names": {"品名": "商品"}})) == []


def test_aggregate_steps_are_checked_by_the_validator():
    """群組彙總：彙總清單裡的欄名也會檢查；分組之後只剩分組欄與彙總欄。"""

    def with_steps(*steps: dict) -> dict:
        return mutate(
            lambda d: d["stages"][0]["steps"].extend(
                {"id": f"z{i}", **s} for i, s in enumerate(steps)
            )
        )

    group = {
        "type": "group_by",
        "by": ["門市"],
        "aggregations": [{"column": "金頟", "op": "max", "name": "最大"}],
    }
    issues = check(with_steps(group))
    assert paths(issues) == ["stages[0].steps[8].aggregations[0].column"]
    assert "是不是「金額」？" in issues[0]["hint"]
    ok = {**group, "aggregations": [{"column": "金額", "op": "max", "name": "最大"}]}
    assert check(with_steps(ok)) == []
    later = {"type": "fill_down", "columns": ["品名"]}
    issues = check(with_steps(ok, later))
    assert paths(issues) == ["stages[0].steps[9].columns[0]"]
    assert "目前的欄位：門市、最大、筆數" in issues[0]["hint"]
    empty = {"type": "group_by", "by": ["門市"], "count_as": ""}
    assert "要有至少一項彙總" in check(with_steps(empty))[0]["error"]
    pivot = {
        "type": "pivot",
        "index": ["門市"],
        "on": "通路",
        "values": "金額",
        "columns": ["門市"],
    }
    assert "新欄名「門市」和現有欄位重複" in check(with_steps(pivot))[0]["error"]
    unpivot = {"type": "unpivot", "columns": ["品號", "品名"], "value_column": "金額"}
    assert "新欄名「金額」和現有欄位重複" in check(with_steps(unpivot))[0]["error"]


def test_column_refs_inside_filter_conditions():
    data = mutate(lambda d: d["stages"][0]["steps"][7]["conditions"][0].update(column="價格"))
    assert paths(check(data)) == ["stages[0].steps[7].conditions[0].column"]


def test_column_flow_follows_disabled_steps_and_defaults():
    # s7 停用 → 「備註」沒被拆開，filter 引用「通路」就不存在
    data = mutate(
        lambda d: (
            d["stages"][0]["steps"][6].update(enabled=False),
            d["stages"][0]["steps"][7]["conditions"][0].update(column="通路"),
        )
    )
    assert paths(check(data)) == ["stages[0].steps[7].conditions[0].column"]
    # split_delimited 省略 column 時預設是 line
    data = mutate(lambda d: d["stages"][0]["steps"][3].pop("column"))
    assert check(data) == []


def test_new_column_problems():
    dup = mutate(lambda d: d["stages"][0]["steps"][6].update(names=["通路", "門市"]))
    assert "和現有欄位重複" in check(dup)[0]["error"]
    system = mutate(lambda d: d["stages"][0]["steps"][6].update(names=["__x"]))
    assert "不能以 __ 開頭" in check(system)[0]["error"]
    count = mutate(lambda d: d["stages"][0]["steps"][4].update(names=["日期", "門市"]))
    assert check(count)[0]["error"] == "目前有 6 個資料欄，但列了 2 個欄名"


def test_duplicate_step_ids_and_system_columns():
    data = mutate(lambda d: d["stages"][0]["steps"][1].update(id="s1"))
    assert check(data)[0]["error"] == "步驟 id「s1」和 stages[0].steps[0] 重複"
    ok = mutate(
        lambda d: d["stages"][0]["steps"][7]["conditions"].append(
            {"column": "__line_no", "op": "gt", "value": 3}
        )
    )
    assert check(ok) == []
    bad = mutate(
        lambda d: d["stages"][0]["steps"][7]["conditions"].append(
            {"column": "__nope", "op": "is_empty"}
        )
    )
    assert "沒有系統欄位" in check(bad)[0]["error"]


def test_csv_input_columns_are_checked_when_known():
    data = mutate(
        lambda d: (
            d["stages"][0]["input"].update(reader="csv"),
            d["stages"][0].update(steps=[{"id": "f", "type": "fill_down", "columns": ["門巿"]}]),
        )
    )
    assert check(data) == []  # 不知道 CSV 有哪些欄位時不檢查
    issues = check(data, {"預設階段": ["日期", "門市"]})
    assert paths(issues) == ["stages[0].steps[0].columns[0]"]


def test_structural_error_stops_column_flow_without_cascading():
    data = mutate(lambda d: d["stages"][0]["steps"][3].update(columns="欄1"))
    assert paths(check(data)) == ["stages[0].steps[3].columns"]


# ── 驗證器只用標準函式庫（AI skill 的沙箱不能安裝套件）──────────────


def test_validator_is_stdlib_only():
    tree = ast.parse(Path(validator.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "驗證器不能用相對 import"
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def test_validator_runs_standalone(tmp_path):
    """複製到別的地方、不裝 tablade 也能執行（模擬 AI skill 的沙箱）。"""
    script = tmp_path / "validate_recipe.py"
    script.write_text(Path(validator.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    good = tmp_path / "good.recipe.json"
    good.write_text(json.dumps(example(), ensure_ascii=False), encoding="utf-8")
    bad = tmp_path / "bad.recipe.json"
    bad.write_text(
        json.dumps(mutate(STRUCTURAL_CASES["unknown step type"]), ensure_ascii=False),
        encoding="utf-8",
    )

    def run(*args):
        return subprocess.run(
            [sys.executable, "-I", str(script), *args, "--spec", str(tmp_path / "spec.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
        )

    ok = run(str(good))
    assert ok.returncode == 0, ok.stderr
    assert "通過驗證" in ok.stdout
    failed = run(str(bad), "--json")
    assert failed.returncode == 1
    result = json.loads(failed.stdout)
    assert result["ok"] is False and result["issues"][0]["path"] == "stages[0].steps[0].type"


def test_validator_reads_csv_header_from_data(tmp_path):
    data_file = tmp_path / "x.csv"
    data_file.write_bytes("日期,門市\r\n1,2\r\n".encode("cp950"))
    assert validator.csv_header(data_file) == ["日期", "門市"]


# ── schema lock：步驟定義變了就要升版本 ──────────────────────────


def test_schema_lock_matches_current_steps():
    lock = read_lock()
    current = lock_content()
    if lock["spec"] != current["spec"]:
        if lock["recipe_version"] == RECIPE_VERSION:
            pytest.fail(
                f"步驟定義有結構性變更，但 RECIPE_VERSION 仍是 {RECIPE_VERSION}。"
                "請升版本（相容的變更升 minor，不相容的升 major），"
                "再執行 uv run python scripts/update_schema_lock.py"
            )
        pytest.fail("RECIPE_VERSION 已升級：請執行 uv run python scripts/update_schema_lock.py")
    assert lock["recipe_version"] == RECIPE_VERSION, "請執行 scripts/update_schema_lock.py"


def test_lock_ignores_human_text():
    spec = copy.deepcopy(SPEC)
    spec["steps"]["filter"]["label"] = "改個名字"
    spec["schema"]["$defs"]["Filter"]["properties"]["match"]["title"] = "改標題"
    from tablade.recipe.spec import structural

    assert structural(spec) == structural(SPEC)
    assert LOCK_PATH.name == "recipe-schema.lock.json"


# ── 階段 ──────────────────────────────────────────────────────


COMBINED = ROOT / "examples" / "sales_combined.recipe.json"


def combined() -> dict:
    return json.loads(COMBINED.read_text(encoding="utf-8"))


def test_stage_links_are_checked():
    assert check(combined()) == []
    later = combined()
    later["stages"][1]["steps"].append(
        {"id": "z", "type": "join", "right": "預設階段", "on": [{"left": "品號", "right": "品號"}]}
    )
    issues = check(later)
    assert paths(issues)[0] == "stages[1].steps[10].right"
    assert "在後面" in issues[0]["hint"]
    with pytest.raises(RecipeError, match="不是前面的階段"):
        parse_recipe(later)
    unused = combined()
    unused["stages"][3]["input"]["stages"] = ["文字明細"]
    issues = check(unused)
    assert [(i["path"], i["severity"]) for i in issues] == [("stages[2]", "warning")]
    parse_recipe(unused)  # 提醒不擋執行
    dup = combined()
    dup["stages"][2]["steps"][0]["id"] = "t1"
    assert check(dup)[0]["error"] == "步驟 id「t1」和 stages[1].steps[0] 重複"


def test_join_right_columns_are_checked_against_the_stage():
    # 讀 CSV、Excel 的階段要有資料才知道欄位（驗證器依階段名稱拿到讀到的欄位）
    known = {"品項對照": ["品號", "類別", "建議售價"], "Excel明細": list("ABCDEF")}
    data = combined()
    data["stages"][3]["steps"][1]["columns"] = ["類別", "售價"]
    assert check(data) == []  # 不知道右表的欄位時不檢查
    issues = check(data, known)
    assert paths(issues) == ["stages[3].steps[1]"]
    assert "階段「品項對照」沒有欄位：售價" in issues[0]["error"]
    # 讀前面階段時，欄位是那些階段結果的聯集
    data = combined()
    data["stages"][3]["steps"].append({"id": "f", "type": "fill_down", "columns": ["數量", "通路"]})
    assert paths(check(data, known)) == ["stages[3].steps[2].columns[1]"]


def test_old_format_is_rejected_with_a_hint():
    old = {"tablade_recipe": "1.0", "inputs": [{"files": []}], "steps": [], "output": {}}
    issues = check(old)
    assert len(issues) == 1 and "舊的 Recipe 寫法" in issues[0]["error"]
    with pytest.raises(RecipeError):
        parse_recipe(old)
