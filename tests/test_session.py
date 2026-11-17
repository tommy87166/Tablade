"""Session：UI 背後的狀態。這裡照使用者在 UI 的操作順序測，不開視窗。"""

import json
from pathlib import Path

import polars as pl
import pytest

from tablade.engine import run as engine_run
from tablade.fixtures import messy_export, write_all
from tablade.recipe.schema import RECIPE_VERSION, RecipeError
from tablade.session import Session, SessionError, catalog

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"
EXPECTED = messy_export(50, 0)[1] + messy_export(30, 1)[1]


@pytest.fixture
def data(tmp_path: Path) -> Path:
    write_all(tmp_path / "data")
    return tmp_path / "data"


def files(data: Path) -> list[Path]:
    return [data / "明細_01.csv", data / "明細_02.csv"]


def opened(data: Path) -> Session:
    """照使用者的流程：新增 Recipe（存在資料夾旁邊）→ 加入兩個檔 → 載入資料。"""
    s = Session()
    s.new(data.parent / "mine.recipe.json")
    s.add_files(files(data))
    s.load_data()
    return s


def last_rows(session: Session) -> list[dict]:
    frame = session.frame_at(len(session.steps) - 1)
    return frame.drop("__source_file", "__line_no").to_dicts()


def test_build_the_example_recipe_like_a_user(data):
    """開檔 → 依序新增步驟（用建議值＋少量修改）→ 結果和範例 recipe 一樣。"""
    s = opened(data)
    state = s.state()
    assert state["input"]["files"] == ["data/明細_01.csv", "data/明細_02.csv"]
    assert state["input"]["rows"] == 94 and state["input"]["columns"] == ["line"]
    assert [f["encoding"] for f in state["input"]["sources"]] == ["cp950", "cp950"]

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
    assert recipe["inputs"][0]["files"] == ["data/明細_01.csv", "data/明細_02.csv"]


def test_invalid_step_stops_the_pipeline_but_stays_in_the_list(data):
    s = opened(data)
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
    s = opened(data)
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
    s = opened(data)
    page = s.rows(-1, 10, 5)
    assert page["total"] == 94 and page["offset"] == 10
    assert page["columns"] == ["__source_file", "__line_no", "line"]
    assert [r[1] for r in page["rows"]] == [11, 12, 13, 14, 15]
    preview = s.state()["preview"]
    assert preview["columns"][0] == {"name": "__source_file", "dtype": "String", "system": True}
    assert s.rows(5, 0, 10)["rows"] == []


def test_find_row_and_exclude_suggestion(data):
    s = opened(data)
    s.add_step("remove_top_rows")
    assert s.find_row(0, "data/明細_01.csv", 2) == 0
    assert s.find_row(0, "data/明細_02.csv", 2) == 56  # 第一個檔 57 行去掉 1 行
    assert s.find_row(0, "data/明細_01.csv", 1) is None
    i = s.add_step("filter", {"column": "line", "value": "x", "exclude": True})
    assert s.steps[i]["keep"] == "non_matching"


def test_cache_reuses_earlier_steps(data, monkeypatch):
    s = opened(data)
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
    assert s.state()["steps"][5]["status"] == "waiting"  # 還沒載入資料
    s.load_data()
    state = s.state()
    assert state["selected"] == 7 and state["issues"] == issues
    assert state["steps"][5]["status"] == "error"

    report = s.error_report()
    assert "steps[5].columns[0]" in report and "是不是「門市」？" in report
    assert f'tablade_recipe 維持 "{RECIPE_VERSION}"' in report

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
    s = opened(data)
    s.add_step("remove_top_rows")
    target = tmp_path / "recipes" / "x.recipe.json"
    target.parent.mkdir()
    s.save_recipe(target)
    # 資料不在新的 Recipe 資料夾底下：改存絕對路徑
    assert s.input["files"] == [p.resolve().as_posix() for p in files(data)]
    assert s.state()["input"]["rows"] == 94  # 只是寫法不同，不用重新載入
    assert not s.dirty

    loaded = Session()
    loaded.load_recipe(target)
    assert loaded.state()["steps"][0]["status"] == "waiting"  # 開啟後要按「載入資料」
    loaded.load_data()
    assert loaded.state()["steps"][0]["rows"] == 92

    assert loaded.export_suggestion() == (target.parent.resolve(), "x.parquet")
    result = loaded.export(tmp_path / "out.parquet")
    assert result["rows"] == 92
    assert pl.read_parquet(tmp_path / "out.parquet").height == 92
    assert loaded.export_suggestion() == (tmp_path, "out.parquet")  # 下次預設上次的位置
    assert "path" not in json.loads(target.read_text(encoding="utf-8"))["output"]


def test_export_suggestion_uses_recipe_name(data):
    s = opened(data)
    assert s.export_suggestion() == (data.parent.resolve(), "mine.parquet")
    blank = Session()  # 沒有 Recipe 檔時用名稱，去掉檔名不能用的字
    blank.set_meta({"name": 'a/b:c*"d'})
    assert blank.export_suggestion() == (Path.home(), "a_b_c__d.parquet")


def test_no_recipe_and_no_data_yet(tmp_path):
    s = Session()
    state = s.state()
    assert state["recipe"]["open"] is False and state["input"]["loaded"] is False
    assert state["preview"]["rows"] is None
    with pytest.raises(SessionError, match="先新增或開啟 Recipe"):
        s.add_files([tmp_path / "a.csv"])
    s.new(tmp_path / "x.recipe.json")
    s.add_step("remove_top_rows")  # 沒有資料時也能先建步驟
    assert s.state()["steps"][0]["status"] == "waiting"


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


def test_select_input_and_output(data):
    """-1 是「讀取資料」，步驟數是「輸出資料」（預覽最後一步的結果）。"""
    s = opened(data)
    s.select(0)
    state = s.state()
    # 沒有步驟時，「輸出資料」就是讀取的資料
    assert state["selected"] == 0 and state["preview"]["requested"] == -1

    s.add_step("remove_top_rows")
    s.add_step("remove_bottom_rows")
    s.select(99)
    state = s.state()
    assert state["selected"] == 2
    assert state["preview"]["step"] == state["preview"]["requested"] == 1
    assert state["preview"]["rows"] == state["steps"][1]["rows"]

    i = s.add_step("remove_blank_rows")  # 從「輸出資料」新增：加在最後
    assert i == 2 and s.steps[-1]["type"] == "remove_blank_rows"

    s.select(3)
    s.delete_step(0)  # 刪掉前面的步驟，仍選「輸出資料」
    assert s.selected == 2 and s.state()["preview"]["step"] == 1
    s.select(1)
    s.delete_step(0)  # 仍選同一步
    assert s.selected == 0 and s.steps[0]["type"] == "remove_blank_rows"
    s.delete_step(0)  # 刪掉選取的最後一步：沒有步驟了，回到讀取資料
    assert s.selected == -1


def test_state_reports_source_files_per_step(data):
    s = opened(data)
    s.add_step("remove_top_rows")
    s.add_step("merge_files")
    s.update_step(1, {"order": ["data/明細_02.csv"]})
    steps = s.state()["steps"]
    assert [st["files"] for st in steps] == [2, 1]
    assert steps[1]["input_files"] == ["data/明細_01.csv", "data/明細_02.csv"]  # 表單用
    assert s.rows(1, 0, 1)["rows"][0][:2] == ["合併", 1]  # 第二個檔排到最前面


# ── 新增 Recipe、檔案清單、載入資料 ─────────────────────────────────


def test_new_recipe_is_written_right_away(tmp_path):
    s = Session()
    path = s.new(tmp_path / "門市整理.recipe.json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["tablade_recipe"] == RECIPE_VERSION and saved["inputs"][0]["files"] == []
    state = s.state()
    assert state["recipe"]["open"] and state["recipe"]["name"] == "門市整理"
    assert not state["recipe"]["dirty"] and not state["input"]["can_load"]


def test_add_files_relative_inside_recipe_folder_absolute_outside(tmp_path):
    project, other = tmp_path / "proj", tmp_path / "other"
    for path in (project / "a.csv", project / "sub" / "b.csv", other / "c.csv"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    s = Session()
    s.new(project / "r.recipe.json")
    s.add_files([project / "a.csv", project / "sub" / "b.csv", other / "c.csv", project / "a.csv"])
    assert s.input["files"] == ["a.csv", "sub/b.csv", (other / "c.csv").resolve().as_posix()]
    s.add_file_entry("  sub\\*.csv ")  # 手動輸入：可以用 *，\ 轉成 /
    s.add_file_entry("sub/*.csv")  # 重複的不加
    assert s.input["files"][-1] == "sub/*.csv" and len(s.input["files"]) == 4
    entries = s.state()["input"]["entries"]
    assert [e["found"] for e in entries] == [1, 1, 1, 1]
    s.remove_file(3)
    assert len(s.input["files"]) == 3
    s.undo()
    assert s.input["files"][-1] == "sub/*.csv"


def test_missing_files_block_loading_until_removed(data):
    s = Session()
    s.new(data.parent / "mine.recipe.json")
    s.add_files(files(data))
    s.add_file_entry("data/不存在.csv")
    state = s.state()
    assert [e["found"] for e in state["input"]["entries"]] == [1, 1, 0]
    assert not state["input"]["can_load"]
    with pytest.raises(SessionError, match=r"找不到：data/不存在\.csv"):
        s.load_data()
    s.remove_file(2)
    s.load_data()
    assert s.state()["input"]["rows"] == 94


def test_changes_need_another_load(data):
    s = opened(data)
    assert s.state()["input"]["loaded"]
    s.set_input({"encoding": "utf-8"})
    state = s.state()
    assert not state["input"]["loaded"] and state["input"]["stale"]
    assert state["preview"]["rows"] is None
    s.set_input({"encoding": "auto"})
    s.load_data()
    assert s.state()["input"]["loaded"] and not s.state()["input"]["stale"]
    with (data / "明細_02.csv").open("ab") as f:  # 檔案在磁碟上被改過
        f.write(b"x\r\n")
    assert s.state()["input"]["stale"]


def test_empty_recipe_cannot_load_or_export(tmp_path):
    s = Session()
    s.new(tmp_path / "x.recipe.json")
    with pytest.raises(SessionError, match="還沒有加入資料檔"):
        s.load_data()
    with pytest.raises(SessionError, match="請先在「讀取資料」按「載入資料」"):
        s.export(tmp_path / "out.parquet")


def test_export_needs_loaded_data(data, tmp_path):
    s = Session()
    s.new(data.parent / "mine.recipe.json")
    s.add_files(files(data))
    with pytest.raises(SessionError, match="載入資料"):  # 還沒載入：不會自己讀檔處理
        s.export(tmp_path / "out.parquet")
    s.load_data()
    assert s.export(tmp_path / "out.parquet")["rows"] == 94
    s.set_input({"encoding": "utf-8"})  # 設定變了：要重新載入才能匯出
    with pytest.raises(SessionError, match="載入資料"):
        s.export(tmp_path / "out.parquet")


# ── 型別分頁 ────────────────────────────────────────────────────


def test_column_info_follows_conversions_and_positions(data):
    import shutil

    shutil.copy(EXAMPLE, data.parent / EXAMPLE.name)
    s = Session()
    s.load_recipe(data.parent / EXAMPLE.name)
    s.load_data()
    end = len(s.steps)
    before = s.column_info(end - 1)
    columns = {c["name"]: c for c in before["columns"]}
    assert before["rows"] == 70 and before["sampled"] is False
    assert columns["金額"]["label"] == "文字" and columns["金額"]["blanks"] == 0
    assert columns["__line_no"]["system"] and columns["__line_no"]["position"] is None
    # 位置是第幾個資料欄，和「刪除欄」依位置時一樣
    positions = [c["name"] for c in before["columns"] if not c["system"]]
    assert [c["position"] for c in before["columns"] if not c["system"]] == list(
        range(1, len(positions) + 1)
    )

    s.add_step("parse_number", {"column": "金額"}, index=end)
    assert s.state()["steps"][end]["input_columns"] == positions  # 下一步看到的欄位順序
    s.add_step("parse_date", {"column": "日期"}, index=end + 1)
    # 文字轉日期不會自動判斷：沒選樣式和民國／西元之前，這一步是錯誤
    added = s.state()["steps"][end + 1]
    assert added["status"] == "error"
    assert {e["path"] for e in added["errors"]} >= {"pattern", "calendar"}
    s.update_step(end + 1, {"columns": ["日期"], "pattern": "slash", "calendar": "roc"})
    s.add_step("remove_columns", index=end + 2)
    s.update_step(end + 2, {"by": "position", "positions": [positions.index("單號") + 1]})
    after = {c["name"]: c for c in s.column_info(end + 2)["columns"]}
    assert after["金額"]["label"] == "小數（2 位）" and after["金額"]["dtype"] == "Decimal(38,2)"
    assert after["金額"]["blanks"] is None and after["金額"]["nulls"] == 0
    assert after["日期"]["label"] == "日期" and after["日期"]["min"] <= after["日期"]["max"]
    assert "單號" not in after and len(after["金額"]["examples"]) == 3
    assert s.state()["steps"][end + 2]["describe"] == f"刪除第 {positions.index('單號') + 1} 欄"
    assert s.column_info(-1)["columns"][2]["name"] == "line"  # 讀取資料（-1）也可以看
