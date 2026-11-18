"""Recipe 驗證的訊息切成英文（docs/i18n-plan.md 第 4 批）：recipe/validator.py、recipe/schema.py。

假資料（examples/ 的範例 Recipe）的欄名、階段名稱是中文，所以比對整句英文（名稱照原樣）；
預設中文時訊息和原本一樣（AI skill 與命令列用的也是中文）。
"""

import json
from pathlib import Path

import pytest

from tablade import i18n
from tablade.recipe import validator
from tablade.recipe.schema import RecipeError, load_json, parse_recipe
from tablade.recipe.spec import build_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC = build_spec()


def example(name: str = "sales_detail") -> dict:
    return json.loads((ROOT / "examples" / f"{name}.recipe.json").read_text(encoding="utf-8"))


def check(recipe: dict, columns: dict | None = None) -> list[dict]:
    return validator.validate(recipe, SPEC, columns)


def first(recipe: dict) -> tuple[str, str, str | None]:
    issue = check(recipe)[0]
    return issue["path"], issue["error"], issue.get("hint")


def missing_required() -> dict:
    data = example()
    data["stages"][0]["steps"][0].pop("n")
    return data


def unknown_field() -> dict:
    data = example()
    data["stages"][0]["steps"][1]["nn"] = 1
    return data


def unknown_step_type() -> dict:
    data = example()
    data["stages"][0]["steps"][0]["type"] = "remove_top_row"
    return data


def wrong_type() -> dict:
    data = example()
    data["stages"][0]["steps"][0]["n"] = "2"
    return data


def unknown_column() -> dict:
    data = example()
    data["stages"][0]["steps"][5]["columns"] = ["門"]
    return data


def duplicate_new_column() -> dict:
    data = example()
    data["stages"][0]["steps"][6]["names"] = ["通路", "門市"]
    return data


def duplicate_output_file() -> dict:
    data = example("sales_combined")
    data["stages"][1]["output_file"] = "品項對照"  # 補上 .parquet 後和 stages[0] 重複
    data["stages"][2]["output_file"] = "../上一層"
    return data


def reads_later_stage() -> dict:
    data = example("sales_combined")
    data["stages"][1]["steps"].append(
        {"id": "z", "type": "join", "right": "預設階段", "on": [{"left": "品號", "right": "品號"}]}
    )
    return data


def unused_stage() -> dict:
    data = example("sales_combined")
    data["stages"][3]["input"]["stages"] = ["文字明細"]
    return data


def test_validator_messages_in_english():
    i18n.set_lang("en")
    assert validator.LANG == "en"
    assert first(missing_required()) == (
        "stages[0].steps[0].n",
        "Required",
        None,
    )
    assert first(unknown_field()) == ("stages[0].steps[1].nn", "Unknown field", 'Did you mean "n"?')
    assert first(unknown_step_type())[2] == 'Did you mean "remove_top_rows"?'
    assert first(unknown_step_type())[1].startswith("Unsupported type: remove_top_row (allowed: ")
    assert first(wrong_type()) == (
        "stages[0].steps[0].n",
        "Wrong type: should be an integer",
        None,
    )
    path, error, hint = first(unknown_column())
    assert path == "stages[0].steps[5].columns[0]"
    assert error == 'Column "門" does not exist before this step'
    assert hint == 'Current columns: 日期, 門市, 品號, 品名, 備註, 金額. Did you mean "門市"?'
    assert first(duplicate_new_column())[1] == 'New column name "門市" already exists'


def test_stage_and_output_messages_in_english():
    i18n.set_lang("en")
    issues = {(i["path"], i["error"]) for i in check(duplicate_output_file())}
    assert issues == {
        (
            "stages[1].output_file",
            'Output file name "品項對照.parquet" is the same as stage "品項對照"',
        ),
        (
            "stages[2].output_file",
            'Output file name "../上一層" cannot contain / \\ : * ? " < > | or start with "."',
        ),
    }
    path, error, hint = first(reads_later_stage())
    assert path == "stages[1].steps[10].right"
    assert error == '"預設階段" is not an earlier stage'
    assert hint == '"預設階段" comes later: only earlier stages can be read, so reorder the stages'
    [warning] = check(unused_stage())
    assert warning["severity"] == "warning"
    assert warning["error"] == 'The result of stage "Excel明細" is not used by any later stage'
    assert warning["hint"].startswith("It has no output file name either")


def test_effect_and_version_messages_in_english():
    i18n.set_lang("en")

    def effect_error(columns: list[str], effect: dict) -> str:
        with pytest.raises(validator.EffectError) as err:
            validator.apply_effect(columns, effect)
        return str(err.value)

    assert effect_error(["a"], {"kind": "add", "names": ["a"]}) == (
        'New column name "a" already exists'
    )
    assert effect_error(["a"], {"kind": "select", "names": ["x", "y"]}) == (
        "Columns not found: x, y"
    )
    assert effect_error(["a"], {"kind": "rename_data", "names": ["x", "y"]}) == (
        "The data has 1 column, but 2 names are listed"
    )
    assert effect_error(["a", "b"], {"kind": "drop", "by": "position", "positions": [3, 4]}) == (
        "The data has only 2 columns; there is no column 3, 4"
    )
    assert validator.check_version("2.0", "1.0")["error"] == (
        "Incompatible Recipe version 2.0 (this version supports 1.x)"
    )
    assert validator.check_version("1.5", "1.0")["hint"] == "Update Tablade or the skill"


def test_recipe_error_in_english(tmp_path):
    """開啟 Recipe 時的問題（pydantic 的錯誤、階段之間的關係、壞掉的 JSON）也是英文。"""
    i18n.set_lang("en")
    with pytest.raises(RecipeError) as err:
        parse_recipe(missing_required())
    assert err.value.issues == [
        {"path": "stages[0].steps[0].n", "error": "Required", "severity": "error"}
    ]
    with pytest.raises(RecipeError, match="is not an earlier stage"):
        parse_recipe(reads_later_stage())
    data = example()
    data["stages"][0]["name"] = "x" * 41
    data["stages"].append({"name": "", "input": {"reader": "stages", "stages": ["x"]}})
    with pytest.raises(RecipeError) as err:
        parse_recipe(data)
    assert {i["error"] for i in err.value.issues} >= {"At most 40 characters", "Cannot be blank"}
    assert "; " in str(err.value) and "；" not in str(err.value)
    data = example()
    data["tablade_recipe"] = "2.0"
    with pytest.raises(RecipeError, match=r"Incompatible Recipe version 2\.0"):
        parse_recipe(data)
    broken = tmp_path / "broken.recipe.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(RecipeError, match=r"^\(Recipe\): Not valid JSON \(line 1, column 2\)"):
        load_json(broken)
    (tmp_path / "list.recipe.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RecipeError, match="The Recipe must be a JSON object"):
        load_json(tmp_path / "list.recipe.json")


def test_messages_stay_chinese_by_default():
    """預設中文（AI skill、命令列）：訊息和原本一樣；切成英文再切回中文也一樣。"""
    i18n.set_lang("en")
    i18n.set_lang("zh")
    assert validator.LANG == "zh"
    assert first(missing_required()) == ("stages[0].steps[0].n", "必填，請填寫", None)
    assert first(unknown_field()) == ("stages[0].steps[1].nn", "不認得的欄位", "是不是「n」？")
    assert first(unknown_step_type())[2] == "是不是「remove_top_rows」？"
    assert first(wrong_type())[1] == "型別錯誤：應該是整數"
    _, error, hint = first(unknown_column())
    assert error == "欄位「門」在這一步之前不存在"
    assert hint == "目前的欄位：日期、門市、品號、品名、備註、金額。是不是「門市」？"
    assert first(duplicate_new_column())[1] == "新欄名「門市」和現有欄位重複"
    assert first(reads_later_stage())[1:] == (
        "「預設階段」不是前面的階段",
        "「預設階段」在後面：只能讀前面的階段，請調整階段的順序",
    )
    assert ("stages[1].output_file", "輸出檔名「品項對照.parquet」和階段「品項對照」重複") in {
        (i["path"], i["error"]) for i in check(duplicate_output_file())
    }
    with pytest.raises(RecipeError) as err:
        parse_recipe(missing_required())
    assert err.value.issues[0]["error"] == "必填，請填寫"
