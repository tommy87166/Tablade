"""審查報告（docs/review-findings.md）裡 session.py、api.py 的發現：每個測試的 docstring 寫是哪一項。

資料都是測試裡產生的假資料（門市、品項），放在 pytest 的暫存資料夾。
"""

import json
import math
import re
from pathlib import Path

import polars as pl
import pytest

from tablade import api as api_module
from tablade.api import Api
from tablade.engine import run as engine_run
from tablade.ingest.excel import EXCEL_SUFFIXES
from tablade.session import Session, SessionError

CSV = {"files": [], "reader": "csv", "encoding": "auto"}


def stage(name: str, file: str | None, *steps: dict, reads: list[str] | None = None) -> dict:
    source = {"reader": "stages", "stages": reads} if reads else {**CSV, "files": [file]}
    return {"name": name, "input": source, "steps": list(steps), "output_format": "parquet"}


def write_recipe(folder: Path, *stages: dict, name: str = "review") -> Path:
    path = folder / f"{name}.recipe.json"
    recipe = {
        "tablade_recipe": "1.0",
        "name": name,
        "stages": list(stages),
        "output": {"keep_system_columns": False, "manifest": False},
    }
    path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    return path


def small_samples(monkeypatch, threshold: int = 50, head: int = 10, tail: int = 2) -> None:
    """把「大檔」的門檻調小（和 tests/test_session.py 的做法一樣），不用真的產生 20 萬行。"""
    from tablade import session as session_module

    for module in (engine_run, session_module):
        monkeypatch.setattr(module, "SAMPLE_THRESHOLD", threshold)
        monkeypatch.setattr(module, "SAMPLE_HEAD", head)
        monkeypatch.setattr(module, "SAMPLE_TAIL", tail)


JOIN = {
    "id": "s1",
    "type": "join",
    "right": "品項對照",
    "on": [{"left": "品號", "right": "品號"}],
    "columns": ["類別"],
}


def join_recipe(tmp_path: Path) -> Path:
    """右表 100 列（超過門檻：樣本只有前 10 列＋最後 2 列）；左表 3 列，在完整的右表裡都對得到。"""
    (tmp_path / "品項.csv").write_text(
        "品號,類別\n" + "".join(f"P{i:03d},類別{i % 3}\n" for i in range(1, 101)),
        encoding="utf-8",
    )
    (tmp_path / "明細.csv").write_text("品號,數量\nP005,1\nP050,2\nP099,3\n", encoding="utf-8")
    return write_recipe(tmp_path, stage("品項對照", "品項.csv"), stage("明細", "明細.csv", JOIN))


# ── F-008 ─────────────────────────────────────────────────────


def test_join_with_a_sampled_other_table_is_sample_mode(tmp_path, monkeypatch):
    """F-008：結合的另一張表是樣本時，預覽是抽樣模式，並列出是哪一步的另一張表用了樣本；
    另一張表「完整跑一次」之後回到完整模式。"""
    small_samples(monkeypatch)
    s = Session()
    s.load_recipe(join_recipe(tmp_path))
    s.load_data()
    state = s.state()
    assert [(x["name"], x["sampled"]) for x in state["stages"]] == [
        ("品項對照", True),
        ("明細", True),  # 自己的資料不大，但結合的另一張表是樣本
    ]
    preview = state["preview"]
    assert preview["mode"] == "sample"
    assert preview["sampled_steps"] == []  # 沒有要看整個來源檔的步驟
    assert preview["sampled_refs"] == [
        {"stage": "", "number": 1, "label": "結合", "ref": "品項對照"}
    ]
    assert state["input"]["sampled"] is False  # 這個階段自己讀的檔沒有抽樣
    assert s.column_info(0)["sampled"] is True
    assert s.column_info(-1)["sampled"] is False
    s.select(-1)  # 「讀取資料」：還沒結合，是完整的
    assert s.state()["preview"]["mode"] == "full"
    assert s.state()["preview"]["sampled_refs"] == []

    s.full_run(0)  # 另一張表用全部資料算過
    s.select(len(s.steps))
    state = s.state()
    assert state["stages"][1]["sampled"] is False
    assert (state["preview"]["mode"], state["preview"]["sampled_refs"]) == ("full", [])
    assert [row[-1] for row in s.rows(0, 0, 10)["rows"]] == ["類別2", "類別2", "類別0"]
    assert state["preview"]["warnings"] == []


def test_full_run_of_a_stage_whose_other_table_is_sampled(tmp_path, monkeypatch):
    """F-008：自己的資料不大、但另一張表是樣本的階段，也可以「完整跑一次」（連同另一張表一起跑）。"""
    small_samples(monkeypatch)
    s = Session()
    s.load_recipe(join_recipe(tmp_path))
    s.load_data()
    s.full_run(1)
    state = s.state()
    assert state["stages"][1]["full"] is True and state["stages"][1]["sampled"] is False


def test_sampled_refs_lists_upstream_joins(tmp_path, monkeypatch):
    """F-008：往上讀到的階段裡，結合用了樣本的那一步也列出來（帶階段名稱）。"""
    small_samples(monkeypatch)
    path = join_recipe(tmp_path)
    recipe = json.loads(path.read_text(encoding="utf-8"))
    recipe["stages"].append(stage("合計", None, reads=["明細"]))
    path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    s = Session()
    s.load_recipe(path)
    s.load_data()
    preview = s.state()["preview"]
    assert preview["mode"] == "sample"
    assert preview["sampled_refs"] == [
        {"stage": "明細", "number": 1, "label": "結合", "ref": "品項對照"}
    ]


# ── F-009 ─────────────────────────────────────────────────────


def issues_recipe(tmp_path: Path) -> Path:
    (tmp_path / "a.csv").write_text("品名,金額\n筆,12\n", encoding="utf-8")
    bad = {"id": "s1", "type": "parse_number", "columns": ["金額錯字"], "kind": "integer"}
    return write_recipe(tmp_path, stage("主要", "a.csv", bad))


def test_error_report_issues_follow_the_current_recipe(tmp_path):
    """F-009：檢查並複製錯誤報告的問題清單是當場算的：修正後是空的，和報告文字一致；
    切換語言後是那個語言；state 不再帶開啟當時的 issues。"""
    s = Session()
    opened = s.load_recipe(issues_recipe(tmp_path))
    assert [i["path"] for i in opened] == ["stages[0].steps[0].columns[0]"]
    assert "issues" not in s.state()
    report = s.report()
    assert report["issues"] == opened and "金額錯字" in report["text"]

    s.set_language("en")
    report = s.report()
    assert report["issues"][0]["error"].startswith('Column "金額錯字" does not exist')
    assert not re.search(r"[一-鿿]", report["issues"][0]["error"].replace("金額錯字", ""))

    s.update_step(0, {"columns": ["金額"], "kind": "integer"})  # 在畫面上把欄名改對
    s.load_data()
    report = s.report()
    assert report["issues"] == [] and "No issues found." in report["text"]
    assert s.error_report() == report["text"]


def test_api_error_report_returns_issues(tmp_path):
    """F-009：api.error_report() 一起回傳問題清單（對話框用它，不用 state.issues）。"""
    api = Api()
    api._session.load_recipe(issues_recipe(tmp_path))
    res = api.error_report()
    assert res["ok"] and [i["path"] for i in res["issues"]] == ["stages[0].steps[0].columns[0]"]
    assert "金額錯字" in res["text"]


# ── F-011 ─────────────────────────────────────────────────────


def test_undo_rename_keeps_loaded_data(tmp_path):
    """F-011：改名 → 復原 → 重做，已載入的資料都還在（不用重新載入），記憶體裡只有一份。"""
    (tmp_path / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    s = Session()
    s.load_recipe(write_recipe(tmp_path, stage("原名", "a.csv")))
    s.load_data()

    def status() -> tuple[str, str]:
        item = s.state()["stages"][0]
        return item["name"], item["status_text"]

    assert status() == ("原名", "已算出結果：1 列")
    s.rename_stage(0, "新名")
    assert status() == ("新名", "已算出結果：1 列")
    s.undo()
    assert status() == ("原名", "已算出結果：1 列")
    s.redo()
    assert status() == ("新名", "已算出結果：1 列")
    assert len(s._sources) == 1


def test_rename_keeps_references_and_full_run(tmp_path, monkeypatch):
    """F-011：被結合的階段改名、復原：「完整跑一次」的結果跟著階段，不會要重跑。"""
    small_samples(monkeypatch)
    s = Session()
    s.load_recipe(join_recipe(tmp_path))
    s.load_data()
    s.full_run(0)
    assert s.state()["stages"][0]["full"] is True
    s.rename_stage(0, "對照表")
    assert s.steps[0]["right"] == "對照表"
    assert [x["full"] for x in s.state()["stages"]] == [True, False]
    s.undo()
    assert [x["full"] for x in s.state()["stages"]] == [True, False]
    assert s.state()["preview"]["mode"] == "full"


def test_deleted_stage_cache_is_released_after_the_next_change(tmp_path):
    """F-011：刪除階段後馬上復原，資料還在；再做別的修改，被刪除的階段的資料就放掉。"""
    (tmp_path / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("a,b\n3,4\n5,6\n", encoding="utf-8")
    s = Session()
    s.load_recipe(write_recipe(tmp_path, stage("甲", "a.csv"), stage("乙", "b.csv")))
    s.load_data()
    assert len(s._sources) == 2
    s.delete_stage(0)
    s.undo()
    assert s.state()["stages"][0]["status_text"] == "已算出結果：1 列"
    s.delete_stage(0)
    s.set_meta({"description": "改了別的"})
    assert len(s._sources) == 1 and len(s._computed) == 1


# ── F-012 ─────────────────────────────────────────────────────


def test_big_integers_and_nan_are_safe_for_json(tmp_path):
    """F-012：超過 2^53 的整數變成文字（JS 不會失去精確度），NaN／Infinity 變成文字（JSON 合法）。"""
    pl.DataFrame(
        {
            "單號": [1234567890123456789, 9007199254740991, -9007199254740993],
            "比率": [float("nan"), float("inf"), float("-inf")],
        }
    ).write_parquet(tmp_path / "a.parquet")
    source = {"files": ["a.parquet"], "reader": "parquet"}
    path = write_recipe(tmp_path, {**stage("主要", None), "input": source})
    s = Session()
    s.load_recipe(path)
    s.load_data()
    rows = s.rows(-1, 0, 10)["rows"]
    json.dumps(rows, allow_nan=False)
    assert [row[2:] for row in rows] == [
        ["1234567890123456789", "NaN"],
        [9007199254740991, "inf"],
        ["-9007199254740993", "-inf"],
    ]
    info = s.column_info(-1)
    json.dumps(info, allow_nan=False)
    number = next(c for c in info["columns"] if c["name"] == "單號")
    assert (number["min"], number["max"]) == ("-9007199254740993", "1234567890123456789")


def test_ordinary_numbers_stay_numbers():
    """F-012：一般的整數、小數照原樣（表格靠型別靠右對齊）。"""
    from tablade.session import _jsonable

    assert _jsonable(12) == 12 and _jsonable(-3.5) == -3.5 and _jsonable(True) is True
    assert _jsonable(2**53 - 1) == 2**53 - 1 and _jsonable(2**53) == str(2**53)
    assert _jsonable(math.nan) == "NaN"


# ── F-026 / F-027 ─────────────────────────────────────────────


def export_recipe(tmp_path: Path) -> Path:
    (tmp_path / "a.csv").write_text("門市,金額\n北區,1\n南區,2\n", encoding="utf-8")
    return write_recipe(tmp_path, stage("主要", "a.csv"))


def test_export_returns_the_new_state(tmp_path):
    """F-026：「處理並匯出資料」一起回傳新的 state：停在「輸出資料」時馬上是完整模式。
    F-027：沒先載入就匯出時 input.loaded 仍是 False，但 preview.full 有值、rows() 讀得到匯出檔
    （前端畫表格的條件要看 preview.full）。"""
    api = Api()
    api._session.load_recipe(export_recipe(tmp_path))
    api.select_step(0)  # 沒有步驟：0 是「輸出資料」
    res = api.export_parquet()
    assert res["ok"] and res["rows"] == 2
    state = res["state"]
    assert state["input"]["loaded"] is False
    assert state["preview"]["mode"] == "full" and state["preview"]["full"]["rows"] == 2
    assert state["stages"][0]["output"]["exists"] is True
    page = api.rows(state["preview"]["step"], 0, 10)
    assert [row[-2:] for row in page["rows"]] == [["北區", "1"], ["南區", "2"]]


# ── F-029 ─────────────────────────────────────────────────────


def test_update_step_checks_the_step_id():
    """F-029：表單送來的 values 帶著 id：和那一步現在的 id 不同就拒絕（刪除、移動後延後送出的修改
    不會寫到遞補上來的另一步）；沒帶 id 時照舊用位置。"""
    s = Session()
    s.add_step("remove_top_rows")
    s.add_step("remove_top_rows")
    first = dict(s.steps[0])
    s.delete_step(0)
    kept = dict(s.steps[0])
    with pytest.raises(SessionError, match=r"^第 1 步已經不是這個表單的步驟"):
        s.update_step(0, {**first, "n": 5})
    assert s.steps[0] == kept
    s.update_step(0, {**kept, "n": 7})
    assert s.steps[0] == {**kept, "n": 7}
    s.update_step(0, {"n": 2})  # 沒帶 id：照舊用位置
    assert s.steps[0]["n"] == 2
    s.set_language("en")
    with pytest.raises(SessionError, match=r"^Step 1 is no longer the step in this form"):
        s.update_step(0, {**first, "n": 5})


def test_refresh_values_checks_the_step_id(tmp_path):
    """F-029：「重新從資料讀取」送來的表單內容也帶 id：對不上就拒絕。"""
    (tmp_path / "a.csv").write_text("門市,月份,金額\n北區,1月,1\n南區,2月,2\n", encoding="utf-8")
    s = Session()
    s.load_recipe(write_recipe(tmp_path, stage("主要", "a.csv")))
    s.load_data()
    s.add_step("pivot", {})
    draft = {**s.steps[0], "index": ["門市"], "on": "月份", "values": "金額"}
    with pytest.raises(SessionError, match="已經不是這個表單的步驟"):
        s.refresh_values(0, "columns", {**draft, "id": "別的步驟"})
    assert s.refresh_values(0, "columns", draft) == ["1月", "2月"]
    assert s.refresh_values(0, "columns") == ["1月", "2月"]  # 沒有表單內容：用目前的參數


def test_api_update_step_with_stale_id():
    """F-029：bridge 的 update_step 用 values 裡的 id 檢查（參數不變）。"""
    api = Api()
    api.add_step("remove_top_rows", {})
    step = api._session.steps[0]
    res = api.update_step(0, {**step, "id": "不存在的id", "n": 3})
    assert res["ok"] is False and "已經不是這個表單的步驟" in res["error"]
    res = api.update_step(0, {**step, "n": 3})
    assert res["ok"] and res["state"]["steps"][0]["params"]["n"] == 3


# ── F-030 ─────────────────────────────────────────────────────


def test_rows_carry_the_preview_key(tmp_path):
    """F-030：rows()、column_info() 回傳資料的指紋，和 state.preview.key 一樣；
    內容變了（改步驟、切換語言）指紋就不同，前端可以丟掉過時的回應。"""
    s = Session()
    s.load_recipe(export_recipe(tmp_path))
    s.load_data()
    s.add_step("remove_top_rows")
    key = s.state()["preview"]["key"]
    assert key and s.rows(0, 0, 10)["key"] == key and s.column_info(0)["key"] == key
    assert s.rows(-1, 0, 10)["key"] != key
    s.update_step(0, {"n": 2})
    changed = s.state()["preview"]["key"]
    assert changed != key and s.rows(0, 0, 10)["key"] == changed
    s.set_language("en")
    assert s.state()["preview"]["key"] not in (key, changed)


def test_preview_key_follows_the_content(tmp_path):
    """F-028：preview.key 是預覽內容的指紋：內容沒變時一樣；改了用到的前面階段（列數、欄名都沒變）
    就不同；匯出後（完整模式）也不同。"""
    (tmp_path / "a.csv").write_text("門市,金額\n 北區 ,1\n南區  ,2\n", encoding="utf-8")
    path = write_recipe(tmp_path, stage("甲", "a.csv"), stage("乙", None, reads=["甲"]))
    s = Session()
    s.load_recipe(path)
    s.load_data()
    s.select(0)  # 乙的「輸出資料」（沒有步驟：預覽讀取資料）
    key = s.state()["preview"]["key"]
    assert key and s.state()["preview"]["key"] == key
    s.select(-1)
    assert s.state()["preview"]["key"] == key and s.rows(-1, 0, 10)["key"] == key
    s.select_stage(0)
    assert s.state()["preview"]["key"] != key  # 別的階段
    s.add_step("trim_clean", {})
    s.update_step(0, {"columns": ["門市"]})
    s.select_stage(1)
    state = s.state()
    assert (state["preview"]["rows"], state["input"]["columns"]) == (2, ["門市", "金額"])
    changed = state["preview"]["key"]
    assert changed != key
    s.select(0)
    s.export()
    full = s.state()["preview"]
    assert full["mode"] == "full" and full["key"] not in (key, changed)
    assert s.rows(full["step"], 0, 10)["key"] == full["key"]


# ── F-031 ─────────────────────────────────────────────────────


def test_preview_reports_the_real_warning_count(tmp_path):
    """F-031：預覽列出的警告最多 100 則，preview.warning_count 是實際的警告數。"""
    (tmp_path / "a.csv").write_text("金額\n" + "N/A\n" * 350, encoding="utf-8")
    step = {"id": "s1", "type": "parse_number", "columns": ["金額"], "kind": "integer"}
    s = Session()
    s.load_recipe(write_recipe(tmp_path, stage("主要", "a.csv", step)))
    s.load_data()
    state = s.state()
    assert state["steps"][0]["warning_count"] == 350
    assert len(state["preview"]["warnings"]) == 100
    assert state["preview"]["warning_count"] == 350
    s.select(-1)
    assert s.state()["preview"]["warning_count"] == 0


# ── F-035 ─────────────────────────────────────────────────────


def test_unsupported_step_type_has_a_hint_and_a_label(tmp_path):
    """F-035：不支援的步驟類型：錯誤附上最接近的類型；沒寫 type 時說缺少 type；label 一定是文字。"""
    (tmp_path / "a.csv").write_text("a\n1\n", encoding="utf-8")
    steps = ({"id": "s1", "type": "filtr"}, {"id": "s2"})
    s = Session()
    s.load_recipe(write_recipe(tmp_path, stage("主要", "a.csv", *steps)))
    first, second = s.state()["steps"]
    assert first["status"] == "error" and first["label"] == "filtr"
    assert first["errors"] == [
        {
            "path": "type",
            "error": "不支援的步驟類型：filtr",
            "hint": "是不是「filter」？",
            "severity": "error",
        }
    ]
    assert second["label"] == "不明的步驟"
    assert second["errors"][0]["error"] == "沒有寫步驟類型（type）"
    s.set_language("en")
    second = s.state()["steps"][1]
    assert (second["label"], second["errors"][0]["error"]) == (
        "Unknown step",
        "Step type (type) is missing",
    )


# ── F-038 ─────────────────────────────────────────────────────


class FakeWindow:
    def __init__(self) -> None:
        self.pushed: list = []
        outer = self

        class State:
            def __setattr__(self, name, value):
                if name == "progress":
                    outer.pushed.append(value)

        self.state = State()


def test_only_long_tasks_clear_the_progress(tmp_path, monkeypatch):
    """F-038：check_update 這類呼叫結束時不動進度；載入、匯出、完整跑一次結束時才清掉。"""
    monkeypatch.setenv("TABLADE_NO_UPDATE_CHECK", "1")
    api = Api()
    api._window = FakeWindow()
    api._push_progress({"stage": "執行（串流）", "file": "", "done": 10, "total": 100})
    api.check_update()
    api.state()
    api.update_step(9, {})  # 失敗的呼叫也不動進度
    assert api._window.pushed == [{"stage": "執行（串流）", "file": "", "done": 10, "total": 100}]
    api._session.load_recipe(export_recipe(tmp_path))
    api.load_data()
    assert api._window.pushed[-1] is None
    api._window.pushed.clear()
    api.export_parquet()
    assert api._window.pushed[-1] is None


# ── F-041 ─────────────────────────────────────────────────────


def test_step_error_in_the_real_run_points_at_the_step(tmp_path):
    """F-041：只有全部資料才出現的步驟錯誤（另一張表後面才有重複的鍵）：訊息寫第幾個階段第幾步，
    和試跑出錯一樣回傳 trial_error（可以「前往這一步」），不是內部的步驟 id 與類型代碼。"""
    keys = [f"P{i:04d}" for i in range(1, 3001)]
    keys[2499] = "P0001"  # 試跑只看每個檔的前 2,000 行，看不到
    (tmp_path / "品項.csv").write_text(
        "品號,類別\n" + "".join(f"{k},文具\n" for k in keys), encoding="utf-8"
    )
    (tmp_path / "明細.csv").write_text("品號,數量\nP0001,1\n", encoding="utf-8")
    path = write_recipe(tmp_path, stage("品項對照", "品項.csv"), stage("明細", "明細.csv", JOIN))
    api = Api()
    api._session.load_recipe(path)
    res = api.export_parquet()  # 沒有先載入：先試跑，再正式處理
    assert res["ok"] and "state" in res
    error = res["trial_error"]
    assert (error["stage"], error["step"]) == (1, 0)
    assert error["message"].startswith("處理時出錯：階段「明細」第 1 步「結合」：")
    assert "s1" not in error["message"] and "join" not in error["message"]

    api.set_language("en")
    error = api.export_parquet()["trial_error"]
    assert error["message"].startswith('Processing failed: Stage "明細", step 1 "Join": ')


def test_trial_error_returns_state(tmp_path):
    """F-026：試跑出錯時也一起回傳 state；trial_error 指到哪個階段第幾步。"""
    (tmp_path / "a.csv").write_text("a\n1\n", encoding="utf-8")
    step = {"id": "s1", "type": "parse_number", "columns": ["沒有這欄"], "kind": "integer"}
    api = Api()
    api._session.load_recipe(write_recipe(tmp_path, stage("主要", "a.csv", step)))
    res = api.export_parquet()
    assert res["ok"] and "state" in res
    error = res["trial_error"]
    assert (error["stage"], error["step"]) == (0, 0)
    assert error["message"].startswith("試跑時出錯：第 1 步")


# ── F-042 ─────────────────────────────────────────────────────


def test_missing_step_number_is_one_based():
    """F-042：錯誤訊息裡的步驟編號和階段、檔案清單一樣從 1 起算。"""
    s = Session()
    with pytest.raises(SessionError, match=r"^沒有第 10 步$"):
        s.move_step(9, 0)
    s.set_language("en")
    with pytest.raises(SessionError, match=r"^There is no step 10$"):
        s.delete_step(9)


# ── F-043 ─────────────────────────────────────────────────────


def test_data_file_dialog_lists_every_excel_suffix():
    """F-043：「瀏覽檔案」預設的「資料檔」包含 Excel 讀取支援的每一種副檔名。"""
    patterns = api_module.DATA_PATTERNS.split(";")
    assert all(f"*{suffix}" in patterns for suffix in EXCEL_SUFFIXES)
    excel = next(x for x in api_module.data_types() if x.startswith("Excel"))
    assert excel == f"Excel ({';'.join(f'*{s}' for s in EXCEL_SUFFIXES)})"


# ── F-051 ─────────────────────────────────────────────────────


def test_disabled_step_after_an_error_is_blocked_by_it(tmp_path):
    """F-051：出錯的步驟後面被停用的步驟也有 blocked_by（前往出錯的那一步）；前面沒出錯的停用步驟沒有。"""
    (tmp_path / "a.csv").write_text("a\n1\n", encoding="utf-8")
    steps = (
        {"id": "s1", "type": "remove_top_rows", "n": 1, "enabled": False},
        {"id": "s2", "type": "parse_number", "columns": ["沒有這欄"], "kind": "integer"},
        {"id": "s3", "type": "remove_top_rows", "n": 1, "enabled": False},
    )
    s = Session()
    s.load_recipe(write_recipe(tmp_path, stage("主要", "a.csv", *steps)))
    s.load_data()
    state = s.state()
    assert [x["status"] for x in state["steps"]] == ["disabled", "error", "disabled"]
    assert [x["blocked_by"] for x in state["steps"]] == [None, None, 1]
    s.select(2)
    assert s.state()["preview"]["blocked"] == {"unloaded": False, "step": 1, "stage": None}
