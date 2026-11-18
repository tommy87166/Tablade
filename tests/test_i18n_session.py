"""Session 產生的畫面文字切成英文（docs/i18n-plan.md 第 2 批）：狀態說明、按鈕不能按的原因、錯誤訊息。

假資料（tablade.fixtures）的檔名、欄名、階段名稱是中文，所以只比對句子本身；
步驟的名稱與錯誤之後幾批才翻（steps/），用到的地方用正規表示式略過。
"""

import json
from pathlib import Path

import pytest

from tablade.fixtures import write_all
from tablade.recipe.schema import RecipeError
from tablade.session import Session, SessionError, TrialError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data(tmp_path: Path) -> Path:
    write_all(tmp_path / "data")
    return tmp_path / "data"


def english() -> Session:
    s = Session()
    s.set_language("en")
    return s


def single(data: Path) -> Session:
    """一個階段：新增 Recipe、加入兩個檔（還沒載入）。"""
    s = english()
    s.new(data.parent / "mine.recipe.json")
    s.add_files([data / "明細_01.csv", data / "明細_02.csv"])
    return s


def combined(data: Path, load: bool = True) -> Session:
    """合計範例（4 個階段：品項對照、文字明細、Excel明細、預設階段）。"""
    target = data.parent / "combined.recipe.json"
    target.write_text(
        (ROOT / "examples" / "sales_combined.recipe.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    s = english()
    s.load_recipe(target)
    if load:
        s.load_data()
    return s


def test_stage_status_text(data):
    s = combined(data, load=False)
    texts = [x["status_text"] for x in s.state()["stages"]]
    assert texts[0] == "Data not loaded yet"
    assert texts[3] == 'Can\'t compute yet: "文字明細" has no result yet'
    s.load_data()
    s.select_stage(1)
    n = len(s.steps)
    s.add_step("parse_number", index=n)
    s.update_step(n, {"columns": ["不存在的欄"]})
    state = s.state()
    assert [x["status_text"] for x in state["stages"]] == [
        "Result ready: 6 rows",
        f"Step {n + 1} has an error",
        "Result ready: 20 rows",
        'Can\'t compute yet: "文字明細" has errors',
    ]
    assert state["actions"]["export"]["reason"] == (
        'Some steps in stage "文字明細" have errors; fix them first'
    )
    s.add_stage("新的")  # 插在文字明細前面，還沒有檔案
    assert s.state()["stages"][1]["status_text"] == "No data files added yet"
    # 切回中文：狀態說明跟著換
    s.set_language("zh")
    assert s.state()["stages"][1]["status_text"] == "還沒有加入資料檔"


def test_button_reasons(data, tmp_path):
    s = english()
    actions = s.state()["actions"]
    assert actions["load"]["reason"] == actions["export"]["reason"] == "No Recipe open"
    s.new(tmp_path / "x.recipe.json")
    assert s.state()["actions"]["load"]["reason"] == "No data files added yet"
    with pytest.raises(SessionError, match=r"^No data files added yet$"):
        s.load_data()
    s.add_files([data / "明細_01.csv"])
    s.add_file_entry("data/不存在.csv")
    reason = "Not found: data/不存在.csv. Remove it from the file list first"
    actions = s.state()["actions"]
    assert actions["load"]["reason"] == actions["export"]["reason"] == reason
    with pytest.raises(SessionError, match=r"^Not found: data/不存在\.csv\. Remove it from"):
        s.load_data()
    s.remove_file(1)
    s.load_data()
    assert s.state()["actions"]["load"]["reason"] == (
        "Already loaded; you can reload after changing the file list, “Read as”, or encoding"
    )
    s.add_step("parse_number")
    s.update_step(0, {"columns": ["不存在"]})
    assert s.state()["actions"]["export"]["reason"] == "Some steps have errors; fix them first"


def test_stage_errors(data):
    with pytest.raises(SessionError, match=r"^Create or open a Recipe first$"):
        english().add_file_entry("a.csv")
    with pytest.raises(SessionError, match=r"^There must be at least one stage$"):
        english().delete_stage(0)
    s = combined(data)
    with pytest.raises(SessionError, match=r'^A stage named "品項對照" already exists$'):
        s.add_stage("品項對照")
    with pytest.raises(SessionError, match=r"^Enter a stage name$"):
        s.add_stage("  ")
    with pytest.raises(SessionError, match=r"^There is no stage 9$"):
        s.select_stage(8)
    with pytest.raises(
        SessionError,
        match=r'^Cannot delete "品項對照": it is used by step j1 \(.+\) in "預設階段"\. Remove',
    ):
        s.delete_stage(0)
    with pytest.raises(SessionError) as err:
        s.delete_stage(1)
    assert str(err.value) == (
        'Cannot delete "文字明細": it is used by “Read data” in "預設階段". '
        "Remove these references first"
    )
    with pytest.raises(SessionError) as err:
        s.move_stage(1, 3)
    assert str(err.value) == (
        'Can\'t reorder: "預設階段" uses "文字明細" in “Read data”. '
        "A stage can only use earlier stages (update these references first)"
    )
    s.select_stage(0)
    with pytest.raises(SessionError, match=r"^The first stage can only read files \("):
        s.set_input({"reader": "stages"})
    s.select_stage(1)
    with pytest.raises(SessionError, match=r"^Only earlier stages can be read: 預設階段$"):
        s.set_input({"reader": "stages", "stages": ["預設階段"]})
    s.select_stage(3)
    s.set_input({"stages": []})
    state = s.state()
    assert state["input"]["error"] == "Choose at least one earlier stage"
    assert state["stages"][3]["status_text"] == "“Read data” has an error"


def test_values_read_from_data(data):
    s = combined(data)
    s.select_stage(1)  # 文字明細
    s.select(len(s.steps) - 1)
    index = s.add_step("pivot", {"column": "門市"})
    with pytest.raises(SessionError, match=r'^"index" is not a parameter read from data$'):
        s.refresh_values(index, "index")
    params = {k: v for k, v in s.steps[index].items() if k not in ("id", "type")}
    with pytest.raises(SessionError, match=r'^Column "不存在" does not exist$'):
        s.refresh_values(index, "columns", {**params, "on": "不存在"})
    s.update_step(index, {**params, "columns": []})  # 依據的欄沒改：不自動重新讀
    errors = s.state()["steps"][index]["errors"]
    assert [e["error"] for e in errors if e["path"] == "columns"] == [
        "Click “Reload from data” to fill in 3 values"
    ]


def test_load_hint_and_progress(data, monkeypatch):
    with pytest.raises(SessionError) as err:
        combined(data, load=False).full_run(3)
    assert str(err.value) == (
        "Click “Load & preview” on the toolbar first (品項對照, 文字明細, Excel明細)"
    )
    monkeypatch.setattr("tablade.session.SAMPLE_THRESHOLD", 10)  # 小檔也讀樣本
    s = single(data)
    seen: list[dict] = []
    s.progress = seen.append
    s.load_data()
    assert any(p.get("stage") == "Reading preview sample" for p in seen)


def test_export_messages(data):
    s = single(data)
    s.add_step("remove_top_rows")
    s.add_step("parse_number")
    s.update_step(1, {"columns": ["不存在"]})  # 沒載入時不知道有哪些欄：試跑才出錯
    with pytest.raises(TrialError) as err:
        s.export()
    assert str(err.value).startswith('Trial run failed: Step 2 "')
    s.delete_step(1)
    s.load_data()
    seen: list[dict] = []
    s.progress = seen.append
    s.export()  # 已經用預覽檢查過：不用再試跑
    phases = [p["phases"] for p in seen if "phases" in p][-1]
    assert phases[1]["detail"] == "Already checked with the preview; skipped"


def test_error_report_and_old_format(data, tmp_path):
    report = single(data).error_report()
    assert report.startswith("Tablade validation results (Recipe version ")
    assert "\nRecipe: mine.recipe.json\n" in report
    assert 'Fix the Recipe based on the issues above, keeping tablade_recipe at "' in report
    old = tmp_path / "old.recipe.json"
    old.write_text(
        json.dumps({"tablade_recipe": "1.0", "inputs": [], "steps": [], "output": {}}),
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="This Recipe uses the old format"):
        english().load_recipe(old)
