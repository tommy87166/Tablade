"""Session：UI 背後的狀態。這裡照使用者在 UI 的操作順序測，不開視窗。"""

import json
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from tablade.engine import run as engine_run
from tablade.export.outputs import default_output_name
from tablade.fixtures import messy_export, write_all
from tablade.recipe.schema import RECIPE_VERSION, RecipeError
from tablade.session import Session, SessionError, TrialError, catalog

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

    assert same(recipe["stages"][0]["steps"]) == same(example["stages"][0]["steps"])
    assert recipe["stages"][0]["steps"][3] == {
        "id": "s4",
        "type": "split_delimited",
        "columns": [f"欄{k}" for k in range(1, 7)],
    }
    assert recipe["stages"][0]["input"]["files"] == ["data/明細_01.csv", "data/明細_02.csv"]


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
    # 選的那一步算不出來：不顯示資料，記被哪一步擋住
    assert (state["preview"]["requested"], state["preview"]["step"]) == (2, 2)
    assert state["preview"]["rows"] is None and state["preview"]["blocked"]["step"] == 1
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

    def counting(step, frame, limit=100, tables=None):
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
    recipe["stages"][0]["steps"][5]["columns"] = ["門"]
    path = data.parent / "bad.recipe.json"
    path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")

    s = Session()
    issues = s.load_recipe(path)
    assert [i["path"] for i in issues] == ["stages[0].steps[5].columns[0]"]
    assert s.state()["steps"][5]["status"] == "waiting"  # 還沒載入資料
    s.load_data()
    state = s.state()
    assert state["selected"] == 7 and state["issues"] == issues
    assert state["steps"][5]["status"] == "error"

    report = s.error_report()
    assert "stages[0].steps[5].columns[0]" in report and "是不是「門市」？" in report
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

    result = loaded.export()  # 寫到新的 Recipe 資料夾底下的 output/，檔名是 Recipe 名稱
    assert result["rows"] == 92
    assert result["path"] == (target.parent / "output" / "x.parquet").as_posix()
    assert pl.read_parquet(target.parent / "output" / "x.parquet").height == 92
    assert "path" not in json.loads(target.read_text(encoding="utf-8"))["output"]


def test_last_stage_uses_the_recipe_name_by_default(data):
    s = opened(data)
    assert s.state()["stages"][0]["output"] == {
        "file": "mine.parquet",
        "path": "output/mine.parquet",
        "default": True,
        "exists": False,
        "error": None,
    }
    assert default_output_name(None, 'a/b:c*"d') == "a_b_c__d.parquet"  # 去掉檔名不能用的字


def test_stage_output_file(data):
    s = opened(data)
    s.set_stage_output(0, "  結果 ")  # 補上 .parquet
    assert s.stages[0]["output_file"] == "結果.parquet" and s.dirty
    assert s.recipe_dict()["stages"][0]["output_file"] == "結果.parquet"
    assert s.state()["stages"][0]["output"]["file"] == "結果.parquet"
    assert s.state()["stages"][0]["output"]["default"] is False
    s.undo()
    assert "output_file" not in s.recipe_dict()["stages"][0]  # 沒填就不寫進 Recipe
    s.set_stage_output(0, "a/b")
    assert "不能有 /" in s.state()["stages"][0]["output"]["error"]
    with pytest.raises(SessionError, match="不能有 /"):
        s.export()
    s.set_stage_output(0, "")
    assert s.export()["path"].endswith("/output/mine.parquet")
    assert s.state()["stages"][0]["output"]["exists"] is True  # 匯出時會覆蓋


def test_output_cannot_overwrite_an_input(data):
    previous = data.parent / "output" / "上次.parquet"
    previous.parent.mkdir()
    pl.DataFrame({"a": [1, 2]}).write_parquet(previous)
    s = Session()
    s.new(data.parent / "next.recipe.json")
    s.set_input({"reader": "parquet"})
    s.add_files([previous])
    s.set_stage_output(0, "上次")
    error = s.state()["stages"][0]["output"]["error"]
    assert (
        error
        == "「output/上次.parquet」是階段「預設階段」的輸入檔，不能寫到這裡（請換一個輸出檔名）"
    )
    s.load_data()
    with pytest.raises(SessionError, match="輸入檔"):
        s.export()
    assert pl.read_parquet(previous)["a"].to_list() == [1, 2]


def test_export_writes_every_stage_with_a_file_name(data):
    target = data.parent / "combined.recipe.json"
    target.write_text(
        (ROOT / "examples" / "sales_combined.recipe.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    s = Session()
    s.load_recipe(target)
    s.set_stage_output(0, "")  # 範例裡「品項對照」有填檔名
    assert s.state()["stages"][0]["output"] is None  # 中間的階段沒填就不寫檔
    s.set_stage_output(1, "文字")
    s.load_data()
    result = s.export()
    assert [(o["stage"], Path(o["path"]).name) for o in result["outputs"]] == [
        ("文字明細", "文字.parquet"),
        ("預設階段", "combined.parquet"),
    ]
    text = json.loads((data.parent / "log" / "文字.manifest.json").read_text(encoding="utf-8"))
    assert text["stage"] == "文字明細" and text["output"]["rows"] == 70
    assert {i["stage"] for i in text["inputs"]} == {"文字明細"}  # 只記自己往上用到的
    assert {s["stage"] for s in text["steps"]} == {"文字明細"} and text["stages"] == []
    final = json.loads((data.parent / "log" / "combined.manifest.json").read_text(encoding="utf-8"))
    assert {i["stage"] for i in final["inputs"]} == {"品項對照", "文字明細", "Excel明細"}


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
    assert saved["tablade_recipe"] == RECIPE_VERSION
    assert saved["stages"] == [
        {
            "name": "預設階段",
            "input": {"files": [], "reader": "text_lines", "encoding": "auto"},
            "steps": [],
        }
    ]
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
    with pytest.raises(SessionError, match="還沒有加入資料檔"):
        s.export()
    actions = s.state()["actions"]
    assert actions["load"] == {"ok": False, "reason": "還沒有加入資料檔"}
    assert actions["export"] == {"ok": False, "reason": "還沒有加入資料檔"}


def test_export_without_loading_first(data, monkeypatch):
    """開 Recipe 後不用先載入就能匯出：先用每個檔的前幾行試跑，再處理全部資料。"""
    s = Session()
    s.new(data.parent / "mine.recipe.json")
    s.add_files(files(data))
    actions = s.state()["actions"]
    assert actions["export"] == {"ok": True, "reason": None}
    assert actions["load"]["ok"] is True
    seen: list[dict] = []
    s.progress = seen.append
    modes = []
    real = engine_run.execute

    def spy(*args, **kwargs):
        modes.append(kwargs.get("mode", "auto"))
        return real(*args, **kwargs)

    monkeypatch.setattr("tablade.session.execute", spy)
    assert s.export()["rows"] == 94
    assert modes == ["sample", "auto"]  # 試跑，再正式處理
    phases = [p["phases"] for p in seen if "phases" in p][-1]
    assert phases == [
        {"name": "準備檔案", "status": "done", "detail": "已處理 2 / 2 個檔"},
        {"name": "試跑", "status": "done", "detail": "沒有錯誤"},
    ]
    stages = [p["stages"] for p in seen if "stages" in p][-1]
    assert [(x["name"], x["status"], x["rows"]) for x in stages] == [("預設階段", "done", 94)]
    # 載入後（每一步都算得出來）不用再試跑
    s.load_data()
    assert s.state()["actions"]["load"]["ok"] is False  # 都載入了，檔案也沒變
    modes.clear()
    seen.clear()
    s.export()
    assert modes == ["auto"]
    phases = [p["phases"] for p in seen if "phases" in p][-1]
    assert phases[1] == {"name": "試跑", "status": "done", "detail": "已用預覽檢查過，略過"}
    s.set_input({"encoding": "utf-8"})  # 設定變了：還是可以匯出（會先試跑）
    assert s.state()["actions"]["load"]["ok"] is True
    assert s.state()["actions"]["export"]["ok"] is True


def test_trial_run_stops_before_the_full_run(data, monkeypatch):
    """步驟在真實資料上會出錯時，試跑就停下，指到哪個階段的第幾步；不會開始處理全部資料。"""
    s = Session()
    s.new(data.parent / "mine.recipe.json")
    s.add_files(files(data))
    s.add_step("remove_top_rows")
    s.add_step("parse_number")
    s.update_step(1, {"columns": ["不存在"]})  # 沒載入時不知道有哪些欄：參數本身沒錯
    assert s.state()["actions"]["export"]["ok"] is True
    modes = []
    real = engine_run.execute

    def spy(*args, **kwargs):
        modes.append(kwargs.get("mode", "auto"))
        return real(*args, **kwargs)

    monkeypatch.setattr("tablade.session.execute", spy)
    with pytest.raises(TrialError) as err:
        s.export()
    assert (err.value.stage, err.value.step) == (0, 1)
    assert str(err.value) == "試跑時出錯：第 2 步「文字轉數字」：欄位不存在：不存在"
    assert modes == ["sample"]
    assert not (data.parent / "output").exists()


def test_steps_after_an_error_say_what_blocks_them(data):
    """算不出來的步驟記是被哪一步擋住；預覽記選的那一步為什麼算不出來（還沒載入時另外標）。"""
    s = opened(data)
    s.add_step("remove_top_rows", index=0)
    s.add_step("parse_number", index=1)
    s.update_step(1, {"columns": ["不存在"]})
    s.add_step("remove_bottom_rows", index=2)
    steps = s.state()["steps"]
    assert [x["status"] for x in steps] == ["ok", "error", "pending"]
    assert (steps[2]["blocked_by"], steps[2]["blocked_stage"]) == (1, None)
    assert steps[0]["blocked_by"] is None and steps[1]["blocked_by"] is None
    s.select(2)
    preview = s.state()["preview"]
    assert (preview["step"], preview["requested"]) == (2, 2)
    assert (preview["rows"], preview["columns"], preview["mode"]) == (None, [], None)  # 不顯示資料
    assert preview["blocked"] == {"unloaded": False, "step": 1, "stage": None}
    s.select(1)
    assert s.state()["preview"]["blocked"]["step"] == 1  # 這一步自己有錯
    s.select(0)
    assert s.state()["preview"]["blocked"] is None
    fresh = Session()
    fresh.load_recipe(s.save_recipe(data.parent / "blocked.recipe.json"))
    fresh.select(0)
    assert fresh.state()["preview"]["blocked"] == {"unloaded": True, "step": None, "stage": None}


def test_sample_mode_and_full_mode_after_export(data, monkeypatch):
    """大檔預覽用樣本（抽樣模式，彙總的數字不準）；匯出後「輸出資料」讀匯出檔（完整模式），
    改了步驟就回到抽樣模式。"""
    import shutil

    from tablade import session as session_module

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 10)
    monkeypatch.setattr(session_module, "SAMPLE_HEAD", 20)
    monkeypatch.setattr(session_module, "SAMPLE_TAIL", 3)
    shutil.copy(EXAMPLE, data.parent / EXAMPLE.name)
    s = Session()
    s.load_recipe(data.parent / EXAMPLE.name)
    s.load_data()
    n = len(s.steps)
    s.add_step("parse_number", index=n)
    s.update_step(n, {"columns": ["金額"]})
    s.add_step("group_by", index=n + 1)
    total = {"column": "金額", "op": "sum", "name": "合計"}
    s.update_step(n + 1, {"by": ["門市"], "aggregations": [total]})
    s.select(len(s.steps))  # 輸出資料
    preview = s.state()["preview"]
    assert (preview["mode"], preview["sampled_steps"], preview["full"]) == (
        "sample",
        [{"stage": "", "number": n + 2, "label": "群組彙總"}],
        None,
    )
    sampled = s.frame_at(n + 1)["合計"].sum()

    result = s.export()
    exported = pl.read_parquet(result["path"])
    assert exported["合計"].sum() != sampled  # 樣本算的加總不是全部資料的
    preview = s.state()["preview"]
    assert preview["mode"] == "full" and preview["step"] == len(s.steps)
    assert preview["full"]["rows"] == exported.height and preview["full"]["input_rows"] == 94
    assert preview["sampled_steps"] == [] and preview["blocked"] is None
    assert [c["name"] for c in preview["columns"]] == exported.columns
    page = s.rows(len(s.steps), 0, 100)
    assert page["total"] == exported.height
    assert page["rows"] == [
        [str(v) if isinstance(v, Decimal) else v for v in r] for r in exported.iter_rows()
    ]
    info = s.column_info(len(s.steps))
    assert info["sampled"] is False
    column = next(c for c in info["columns"] if c["name"] == "合計")
    assert (column["min"], column["max"]) == (
        str(exported["合計"].min()),
        str(exported["合計"].max()),
    )
    line = exported["__line_no"][1]
    assert s.find_row(len(s.steps), exported["__source_file"][1], line) == 1

    s.select(0)
    assert s.state()["preview"]["mode"] == "sample"  # 其他步驟還是樣本
    s.select(len(s.steps))
    s.update_step(n + 1, {"by": ["通路"], "aggregations": [total]})  # 改了步驟：匯出檔對不上了
    preview = s.state()["preview"]
    assert preview["mode"] == "sample" and preview["full"] is None


def test_stage_status_text(data):
    """階段面板圓點的說明：已算出結果、哪一步有錯、讀的前面階段有錯所以還算不出來。"""
    target = data.parent / "combined.recipe.json"
    target.write_text(
        (ROOT / "examples" / "sales_combined.recipe.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    s = Session()
    s.load_recipe(target)
    texts = [x["status_text"] for x in s.state()["stages"]]
    assert texts[0] == "還沒載入資料"
    assert texts[3] == "讀的「文字明細」還沒有結果，還算不出來"
    s.load_data()
    s.select_stage(1)
    n = len(s.steps)
    s.add_step("parse_number", index=n)
    s.update_step(n, {"columns": ["不存在的欄"]})
    texts = [x["status_text"] for x in s.state()["stages"]]
    assert texts == [
        "已算出結果：6 列",
        f"第 {n + 1} 步有錯",
        "已算出結果：20 列",
        "讀的「文字明細」有錯，還算不出來",
    ]


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


# ── 階段 ──────────────────────────────────────────────────────


def combined(data: Path) -> Session:
    """開啟合計範例（4 個階段：品項對照、文字明細、Excel明細、預設階段）並載入資料。"""
    target = data.parent / "combined.recipe.json"
    target.write_text(
        (ROOT / "examples" / "sales_combined.recipe.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    s = Session()
    assert [i for i in s.load_recipe(target) if i["severity"] == "error"] == []
    s.load_data()  # 一次載入所有讀實際檔的階段
    return s


def test_stages_preview_reads_earlier_stages_and_joins(data):
    s = combined(data)
    state = s.state()
    assert [st["name"] for st in state["stages"]] == [
        "品項對照",
        "文字明細",
        "Excel明細",
        "預設階段",
    ]
    assert state["current"] == 3 and state["stages"][3]["last"]
    assert [st["status"] for st in state["stages"]] == ["ok"] * 4
    assert state["stages"][3]["uses"] == ["文字明細", "Excel明細", "品項對照"]
    # 讀前面階段的輸出：每個來源各自是一個來源，名稱帶階段
    assert state["input"]["reader"] == "stages" and state["input"]["earlier"][:2] == [
        "品項對照",
        "文字明細",
    ]
    assert state["steps"][0]["input_files"] == [
        "@文字明細／data/明細_01.csv",
        "@文字明細／data/明細_02.csv",
        "@Excel明細／data/明細.xlsx",
    ]
    assert state["steps"][1]["warning_count"] == 18  # D300 不在品項對照表
    out = s.frame_at(1)
    assert out.height == 90 and "類別" in out.columns
    warning = state["preview"]["warnings"][0]
    assert warning["orig_source_file"] == "data/明細_01.csv" and warning["orig_stage"] == "文字明細"
    # 結合的建議值：右表用最近的前面階段
    s.select_stage(3)
    s.select(1)
    index = s.add_step("join", {"column": "品號"})
    assert s.steps[index]["right"] == "Excel明細"


def test_stage_add_rename_move_delete(data):
    s = combined(data)
    s.select_stage(1)
    s.add_stage("對照二")  # 預設插在目前階段（文字明細）的前面
    assert [st["name"] for st in s.stages] == [
        "品項對照",
        "對照二",
        "文字明細",
        "Excel明細",
        "預設階段",
    ]
    assert s.current == 1 and s.steps == []
    with pytest.raises(SessionError, match="已經有叫「品項對照」的階段"):
        s.add_stage("品項對照")
    with pytest.raises(SessionError, match="請輸入階段名稱"):
        s.add_stage("  ")
    s.add_stage("最後", before=False)
    assert [st["name"] for st in s.stages][2] == "最後"
    s.delete_stage(2)
    # 改名：引用它的地方一起改
    s.rename_stage(0, "品項")
    last = s.stages[-1]
    assert last["steps"][1]["right"] == "品項"
    s.rename_stage(2, "文字")
    assert last["input"]["stages"] == ["文字", "Excel明細"]
    assert s.state()["stages"][-1]["status"] == "ok"  # 改名不需要重新載入
    # 會弄壞引用的刪除、移動都擋下來
    with pytest.raises(SessionError, match="不能刪除「品項」：「預設階段」的j1（結合）用到它"):
        s.delete_stage(0)
    with pytest.raises(SessionError, match="不能這樣排"):
        s.move_stage(0, 4)
    s.move_stage(1, 3)  # 對照二沒有人用：可以移到 Excel明細 後面
    assert [st["name"] for st in s.stages] == ["品項", "文字", "Excel明細", "對照二", "預設階段"]
    s.delete_stage(3)
    assert len(s.stages) == 4
    s.undo()
    assert len(s.stages) == 5
    with pytest.raises(SessionError, match="至少要有一個階段"):
        lone = Session()
        lone.delete_stage(0)


def test_stage_input_must_read_earlier_stages(data):
    s = combined(data)
    s.select_stage(0)
    with pytest.raises(SessionError, match="第一個階段只能讀實際檔"):
        s.set_input({"reader": "stages"})
    s.select_stage(1)
    with pytest.raises(SessionError, match="只能讀前面的階段：預設階段"):
        s.set_input({"reader": "stages", "stages": ["預設階段"]})
    # 型別不一樣的同名欄：讀取時就報錯
    s.select_stage(3)
    s.set_input({"stages": ["品項對照", "文字明細"]})
    state = s.state()
    assert state["input"]["error"] is None  # 品號都是文字，沒有衝突
    s.select_stage(0)
    s.add_step("parse_number", {"column": "品號"})  # 品項對照的品號變成數字（全部轉不了）
    s.select_stage(3)
    assert "「品號」在各來源的型別不一樣" in s.state()["input"]["error"]


def test_recipe_dict_and_old_format(data, tmp_path):
    s = combined(data)
    saved = s.recipe_dict()
    assert saved["stages"][3]["input"] == {"reader": "stages", "stages": ["文字明細", "Excel明細"]}
    assert "stages" not in saved["stages"][0]["input"] and "selected" not in saved["stages"][0]
    old = tmp_path / "old.recipe.json"
    old.write_text(
        json.dumps({"tablade_recipe": "1.0", "inputs": [], "steps": [], "output": {}}),
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="舊的 Recipe 寫法"):
        Session().load_recipe(old)


def test_sampled_steps_lists_every_whole_source_step(data, monkeypatch):
    """抽樣模式下列出每一個用樣本算的步驟（移除重複、群組彙總、樞紐），包括往上讀到的階段的；
    停用的、還沒算到的不算，已經「完整跑一次」的階段也不算。"""
    from tablade import session as session_module

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 5)
    monkeypatch.setattr(session_module, "SAMPLE_HEAD", 2)
    monkeypatch.setattr(session_module, "SAMPLE_TAIL", 1)
    s = combined(data)
    s.select_stage(1)  # 文字明細
    s.add_step("dedupe", index=len(s.steps))
    upstream = {"stage": "文字明細", "number": len(s.steps), "label": "移除重複"}
    s.select_stage(3)  # 預設階段：讀 @文字明細、@Excel明細
    n = len(s.steps)
    s.add_step("dedupe", index=n)
    s.add_step("dedupe", index=n + 1)
    s.toggle_step(n + 1)  # 停用
    s.select(len(s.steps))
    own = {"stage": "", "number": n + 1, "label": "移除重複"}
    assert s.state()["preview"]["sampled_steps"] == [upstream, own]
    s.select(n - 1)  # 還沒算到這個階段自己的移除重複
    assert s.state()["preview"]["sampled_steps"] == [upstream]
    s.full_run(1)  # 文字明細用全部資料算過
    s.select(len(s.steps))
    assert s.state()["preview"]["sampled_steps"] == [own]


def test_full_run_only_runs_the_stages_it_needs(data, monkeypatch):
    """「完整跑一次」只跑那個階段和它用到的階段：前面沒用到的階段（例如很大的文字明細）不跑。"""
    from tablade import session as session_module

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 5)
    monkeypatch.setattr(session_module, "SAMPLE_HEAD", 2)
    monkeypatch.setattr(session_module, "SAMPLE_TAIL", 1)
    s = combined(data)
    assert s.state()["stages"][2]["sampled"]
    seen: list[dict] = []
    s.progress = seen.append
    s.full_run(2)  # Excel明細：只讀自己的檔
    runs = [[x["name"] for x in p["stages"]] for p in seen if "stages" in p]
    assert runs and all(r == ["Excel明細"] for r in runs)
    state = s.state()
    assert state["stages"][2]["full"] and state["stages"][2]["rows"] == 20
    assert not state["stages"][1]["full"]  # 文字明細沒有跑


def test_full_run_replaces_a_sampled_right_table(data, monkeypatch):
    """右表是樣本時，「完整跑一次」之後預覽用完整的右表。"""
    from tablade import session as session_module

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 5)
    monkeypatch.setattr(session_module, "SAMPLE_HEAD", 2)
    monkeypatch.setattr(session_module, "SAMPLE_TAIL", 1)
    s = combined(data)
    state = s.state()
    assert state["stages"][0]["sampled"] and state["stages"][0]["rows"] < 6
    before = state["steps"][1]["warning_count"]
    s.full_run(0)
    state = s.state()
    assert state["stages"][0]["full"] and not state["stages"][0]["sampled"]
    assert state["stages"][0]["rows"] == 6
    assert state["steps"][1]["warning_count"] <= before  # 完整的右表只會對得到更多
    # 右表的階段（或它的上游）改了：回到樣本
    s.select_stage(0)
    s.add_step("remove_blank_rows")
    assert not s.state()["stages"][0]["full"]


def test_pivot_values_are_read_from_data(data, monkeypatch):
    """樞紐要變成欄的值：建立時從資料讀出；改了依據的欄會自動重新讀；也可以按「重新從資料讀取」。"""
    s = combined(data)
    s.select_stage(1)  # 文字明細
    s.select(len(s.steps) - 1)
    index = s.add_step("pivot", {"column": "門市"})
    assert s.steps[index]["on"] == "門市"
    assert s.steps[index]["columns"] == ["北區門市", "中區門市", "南區門市"]
    params = {k: v for k, v in s.steps[index].items() if k not in ("id", "type")}
    s.update_step(index, {**params, "on": "品號"})  # 依據的欄改了、值沒動：自動重新讀
    assert s.steps[index]["columns"][:2] == ["D300", "C100"]
    edited = {**params, "on": "品號", "columns": ["A001"]}
    s.update_step(index, edited)
    s.update_step(index, {**edited, "values": "品名"})  # 依據的欄沒改：手改的值保留
    assert s.steps[index]["columns"] == ["A001"]
    found = s.refresh_values(index, "columns", {**edited, "on": "門市"})  # 帶著還沒送出的參數
    assert found == ["北區門市", "中區門市", "南區門市"] and s.steps[index]["on"] == "門市"
    s.undo()
    assert s.steps[index]["columns"] == ["A001"]
    with pytest.raises(SessionError, match="不是從資料讀出的參數"):
        s.refresh_values(index, "index")
    monkeypatch.setattr("tablade.steps.base.MAX_DATA_VALUES", 5)
    with pytest.raises(SessionError, match=r"「金額」有 \d+ 個不同的值，超過 5 個"):
        s.refresh_values(index, "columns", {**edited, "on": "金額"})  # 值太多：不默默截掉


def test_too_many_pivot_values_are_not_cut_silently(data, monkeypatch):
    """新增樞紐時，依據欄的不同值超過上限：不帶入，表單上直接說明原因。"""
    monkeypatch.setattr("tablade.steps.base.MAX_DATA_VALUES", 5)
    s = combined(data)
    s.select_stage(1)
    s.select(len(s.steps) - 1)
    index = s.add_step("pivot", {"column": "金額"})
    assert s.steps[index]["columns"] == []
    errors = s.state()["steps"][index]["errors"]
    columns = [e["error"] for e in errors if e["path"] == "columns"]
    assert len(columns) == 1 and "超過 5 個；請先篩選，或換一欄" in columns[0]
    # 換成值不多的欄：自動帶入，錯誤消失
    params = {k: v for k, v in s.steps[index].items() if k not in ("id", "type")}
    s.update_step(index, {**params, "on": "門市", "values": "金額"})
    assert s.steps[index]["columns"] == ["北區門市", "中區門市", "南區門市"]
    assert not [e for e in s.state()["steps"][index]["errors"] if e["path"] == "columns"]


def test_load_data_reports_each_stage(data):
    target = data.parent / "combined.recipe.json"
    target.write_text(
        (ROOT / "examples" / "sales_combined.recipe.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    s = Session()
    s.load_recipe(target)
    seen: list[dict] = []
    s.progress = seen.append
    s.load_data()
    last = [p["stages"] for p in seen if "stages" in p][-1]
    assert [(x["name"], x["status"], x["rows"]) for x in last] == [
        ("品項對照", "done", 6),
        ("文字明細", "done", 70),
        ("Excel明細", "done", 20),
        ("預設階段", "done", 90),
    ]
    # 有錯的階段標成失敗，還不能算的回到等待中
    s.select_stage(1)
    s.add_step("trim_clean", {"column": "金額"})
    s.add_stage("新的")  # 插在文字明細前面，還沒有檔案
    s.select_stage(0)
    seen.clear()
    s.load_data()
    last = [p["stages"] for p in seen if "stages" in p][-1]
    assert [(x["name"], x["status"]) for x in last] == [
        ("品項對照", "done"),
        ("新的", "waiting"),
        ("文字明細", "error"),
        ("Excel明細", "done"),
        ("預設階段", "waiting"),  # 讀的文字明細有錯，算不出來
    ]
