"""Session：UI 背後的狀態。這裡照使用者在 UI 的操作順序測，不開視窗。"""

import json
from pathlib import Path

import polars as pl
import pytest

from tablade.engine import run as engine_run
from tablade.fixtures import messy_export, write_all
from tablade.recipe.schema import RecipeError
from tablade.session import Session, SessionError, catalog, common_pattern

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"
EXPECTED = messy_export(50, 0)[1] + messy_export(30, 1)[1]


@pytest.fixture
def data(tmp_path: Path) -> Path:
    write_all(tmp_path / "data")
    return tmp_path / "data"


def files(data: Path) -> list[Path]:
    return [data / "明細_01.csv", data / "明細_02.csv"]


def last_rows(session: Session) -> list[dict]:
    frame = session.frame_at(len(session.steps) - 1)
    return frame.drop("__source_file", "__line_no").to_dicts()


def test_common_pattern():
    assert common_pattern([Path("明細_01.csv"), Path("明細_02.csv")]) == "明細_0*.csv"
    assert common_pattern([Path("a.txt")]) == "a.txt"
    assert common_pattern([Path("ab.csv"), Path("abc.csv")]) == "ab*.csv"


def test_build_the_example_recipe_like_a_user(data):
    """開檔 → 依序新增步驟（用建議值＋少量修改）→ 結果和範例 recipe 一樣。"""
    s = Session()
    s.open_data(files(data))
    state = s.state()
    assert state["input"]["pattern"] == "明細_0*.csv"
    assert state["input"]["rows"] == 94 and state["input"]["columns"] == ["line"]
    assert [f["encoding"] for f in state["input"]["files"]] == ["cp950", "cp950"]

    s.update_step(s.add_step("remove_top_rows"), {"n": 2})
    s.add_step("remove_bottom_rows")
    s.add_step("remove_blank_rows")

    i = s.add_step("split_delimited")
    params = s.steps[i]
    assert "delimiter" not in params and params["columns"] == [f"欄{k}" for k in range(1, 7)]

    i = s.add_step("promote_headers")
    assert s.steps[i]["names"] == ["日期", "門市", "品號", "品名", "備註", "金額"]

    s.add_step("fill_down", {"column": "門市"})
    i = s.add_step("split_column", {"column": "備註"})
    assert s.steps[i]["delimiter"] == "|" and s.steps[i]["names"] == ["備註_1", "備註_2", "備註_3"]
    s.update_step(i, {**s.steps[i], "names": ["通路", "付款方式", "單號"]})

    i = s.add_step("filter", {"column": "金額", "value": "("})
    cond = s.steps[i]["conditions"][0]
    s.update_step(i, {"conditions": [{**cond, "op": "starts_with"}], "keep": "non_matching"})

    state = s.state()
    assert [st["status"] for st in state["steps"]] == ["ok"] * 8
    assert [st["rows"] for st in state["steps"]] == [90, 88, 82, 82, 80, 80, 80, 70]
    assert [st["delta"] for st in state["steps"]] == [-4, -2, -6, 0, -2, 0, 0, -10]
    assert state["steps"][6]["input_columns"] == ["日期", "門市", "品號", "品名", "備註", "金額"]
    assert state["steps"][7]["describe"] == "排除：金額 開頭是「(」"
    assert last_rows(s) == EXPECTED

    # 存檔後和範例 recipe 的步驟相同（預設值不會寫進去）
    saved = s.save_recipe(data.parent / "mine.recipe.json")
    recipe = json.loads(saved.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def same(steps):
        return [{k: v for k, v in s._normalize(st).items() if k != "id"} for st in steps]

    assert same(recipe["steps"]) == same(example["steps"])
    assert recipe["steps"][3] == {
        "id": "s4",
        "type": "split_delimited",
        "columns": [f"欄{k}" for k in range(1, 7)],
    }
    assert recipe["inputs"][0]["pattern"] == "data/明細_0*.csv"


def test_invalid_step_stops_the_pipeline_but_stays_in_the_list(data):
    s = Session()
    s.open_data(files(data))
    s.add_step("remove_top_rows")
    i = s.add_step("split_column", {"column": "line"})
    s.update_step(i, {**s.steps[i], "names": []})
    s.add_step("remove_blank_rows")
    state = s.state()
    assert [st["status"] for st in state["steps"]] == ["ok", "error", "pending"]
    assert state["steps"][1]["errors"][0]["path"] == "names"
    assert state["steps"][2]["rows"] is None
    assert (state["preview"]["requested"], state["preview"]["step"]) == (
        2,
        0,
    )  # 顯示最後算得出來的那一步
    # 執行時的錯誤（欄位不存在）也一樣
    s.update_step(i, {**s.steps[i], "column": "nope", "names": ["a"]})
    state = s.state()
    assert state["steps"][1]["status"] == "error" and "nope" in state["steps"][1]["run_error"]


def test_move_toggle_delete_select_and_undo(data):
    s = Session()
    s.open_data(files(data))
    s.add_step("remove_top_rows")
    s.add_step("remove_bottom_rows")
    s.add_step("remove_blank_rows")
    assert [st["type"] for st in s.steps] == [
        "remove_top_rows",
        "remove_bottom_rows",
        "remove_blank_rows",
    ]

    s.move_step(2, 0)
    assert s.steps[0]["type"] == "remove_blank_rows" and s.selected == 0
    s.toggle_step(1)
    state = s.state()
    assert state["steps"][1]["status"] == "disabled" and state["steps"][1]["rows"] is None
    assert state["steps"][2]["rows"] == state["steps"][0]["rows"] - 2  # 停用的步驟不影響後面
    s.toggle_step(1)
    assert "enabled" not in s.steps[1]
    s.delete_step(0)
    assert len(s.steps) == 2

    for _ in range(4):
        s.undo()
    assert [st["type"] for st in s.steps] == [
        "remove_top_rows",
        "remove_bottom_rows",
        "remove_blank_rows",
    ]
    s.redo()
    assert s.steps[0]["type"] == "remove_blank_rows"
    s.select(-1)
    assert s.state()["preview"]["rows"] == 94
    with pytest.raises(SessionError):
        s.delete_step(9)


def test_rows_paging_and_preview_metadata(data):
    s = Session()
    s.open_data(files(data))
    page = s.rows(-1, 10, 5)
    assert page["total"] == 94 and page["offset"] == 10
    assert page["columns"] == ["__source_file", "__line_no", "line"]
    assert [r[1] for r in page["rows"]] == [11, 12, 13, 14, 15]
    preview = s.state()["preview"]
    assert preview["columns"][0] == {"name": "__source_file", "dtype": "String", "system": True}
    assert s.rows(5, 0, 10)["rows"] == []


def test_find_row_and_exclude_suggestion(data):
    s = Session()
    s.open_data(files(data))
    s.add_step("remove_top_rows")
    assert s.find_row(0, "明細_01.csv", 2) == 0
    assert s.find_row(0, "明細_02.csv", 2) == 56  # 第一個檔 57 行去掉 1 行
    assert s.find_row(0, "明細_01.csv", 1) is None
    i = s.add_step("filter", {"column": "line", "value": "x", "exclude": True})
    assert s.steps[i]["keep"] == "non_matching"


def test_cache_reuses_earlier_steps(data, monkeypatch):
    s = Session()
    s.open_data(files(data))
    s.add_step("remove_top_rows")
    s.add_step("remove_bottom_rows")
    s.state()
    calls = []
    original = engine_run.apply_step

    def counting(step, frame, limit=100):
        calls.append(step.id)
        return original(step, frame, limit)

    monkeypatch.setattr("tablade.session.apply_step", counting)
    s.state()
    assert calls == []
    s.update_step(1, {"n": 3})
    s.state()
    assert calls == ["s2"]


def test_load_recipe_with_problems_and_error_report(data):
    recipe = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    recipe["steps"][5]["columns"] = ["門"]
    path = data.parent / "bad.recipe.json"
    path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")

    s = Session()
    issues = s.load_recipe(path)
    assert [i["path"] for i in issues] == ["steps[5].columns[0]"]
    state = s.state()
    assert state["selected"] == 7 and state["issues"] == issues
    assert state["steps"][5]["status"] == "error"

    report = s.error_report()
    assert "steps[5].columns[0]" in report and "是不是「門市」？" in report
    assert 'tablade_recipe 維持 "1.0"' in report

    s.update_step(5, {"columns": ["門市"]})
    report = s.error_report()
    assert "沒有發現問題" in report and "s8" in report


def test_load_recipe_rejects_newer_version(data):
    recipe = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    recipe["tablade_recipe"] = "1.9"
    path = data.parent / "new.recipe.json"
    path.write_text(json.dumps(recipe), encoding="utf-8")
    with pytest.raises(RecipeError, match="比這裡支援的"):
        Session().load_recipe(path)


def test_save_relocates_paths_and_export(data, tmp_path):
    s = Session()
    s.open_data(files(data))
    s.add_step("remove_top_rows")
    target = tmp_path / "recipes" / "x.recipe.json"
    target.parent.mkdir()
    s.save_recipe(target)
    assert s.input["pattern"] == "../data/明細_0*.csv"
    assert s.state()["input"]["rows"] == 94  # 換位置後仍找得到資料
    assert not s.dirty

    loaded = Session()
    loaded.load_recipe(target)
    assert loaded.state()["steps"][0]["rows"] == 92

    result = loaded.export(tmp_path / "out.parquet")
    assert result["rows"] == 92
    assert pl.read_parquet(tmp_path / "out.parquet").height == 92


def test_no_data_yet():
    s = Session()
    state = s.state()
    assert state["input"]["error"] == "還沒有開啟資料"
    assert state["preview"]["rows"] is None
    s.add_step("remove_top_rows")  # 沒有資料時也能先建步驟
    assert s.state()["steps"][0]["status"] == "pending"


def test_catalog_has_every_step_with_schema():
    items = catalog()
    assert [i["type"] for i in items][:3] == [
        "remove_top_rows",
        "remove_bottom_rows",
        "remove_blank_rows",
    ]
    split = next(i for i in items if i["type"] == "split_column")
    assert split["label"] == "拆欄"
    assert split["schema"]["properties"]["column"]["ui"] == "column"
