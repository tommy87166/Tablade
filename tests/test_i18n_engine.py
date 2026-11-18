"""畫面是英文時，引擎產生的文字（型別名稱、錯誤報告、輸出檔名錯誤、進度、檢查更新）也是英文；AI skill 一律中文。

測試資料的檔名與欄名是中文（資料），只檢查句子本身。
"""

from __future__ import annotations

import json
import shutil
import urllib.error
from pathlib import Path

import polars as pl
import pytest

from tablade import i18n, update
from tablade.api import Api, data_types, recipe_types
from tablade.dtypes import type_label
from tablade.engine.check import check_recipe, format_issues, format_report, warning_place
from tablade.engine.progress import Watcher, stage_label
from tablade.engine.run import RowWarning
from tablade.export.outputs import OutputError, plan_outputs, resolve_outputs
from tablade.fixtures import write_all
from tablade.recipe.schema import load_json, parse_recipe

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"


@pytest.fixture
def english():
    i18n.set_lang("en")


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLE, tmp_path / EXAMPLE.name)
    write_all(tmp_path / "data")
    return tmp_path


def test_type_labels_follow_the_language():
    cases = {
        pl.String: ("文字", "Text"),
        pl.Decimal(38, 2): ("小數（2 位）", "Decimal (2 places)"),
        pl.Decimal(38, 1): ("小數（1 位）", "Decimal (1 place)"),
        pl.Int64: ("整數", "Integer"),
        pl.Float64: ("浮點數", "Float"),
        pl.Date: ("日期", "Date"),
        pl.Datetime("us"): ("日期時間", "Date/Time"),
        pl.Boolean: ("是／否", "True/False"),
        pl.Null: ("空", "Null"),
    }
    assert {dtype: type_label(dtype) for dtype in cases} == {d: zh for d, (zh, _) in cases.items()}
    i18n.set_lang("en")
    assert {dtype: type_label(dtype) for dtype in cases} == {d: en for d, (_, en) in cases.items()}


def test_issues_and_warning_place_in_english(english):
    issues = [
        {"path": "stages[0]", "error": "x", "severity": "error", "hint": "fix it"},
        {"path": "stages[1]", "error": "y", "severity": "warning"},
    ]
    assert format_issues(issues) == "  [Error] stages[0]: x (fix it)\n  [Warning] stages[1]: y"
    w = RowWarning(
        "s1", "m", "a.xlsx", 3, sheet="S1", stage="A", orig_source_file="b.csv", orig_line_no=7
    )
    # 來源名稱是資料（__source_file 裡是「@階段／檔名」）：英文畫面也照資料的寫法
    assert warning_place(w) == "@A／a.xlsx [S1]:3 (original: b.csv:7)"


def test_error_report_in_english(workdir, english):
    result = check_recipe(load_json(workdir / EXAMPLE.name), workdir, dry_run=True)
    assert result.ok and result.report
    lines = format_report(result.report).split("\n")
    assert lines[0] == "Inputs:"
    assert lines[1].endswith("bytes, 57 lines)")
    assert lines[3] == "Steps (94 input lines):"
    assert lines[4].split()[1:4] == ["90", "rows", "(-4)"]
    assert lines[-1].startswith("Result: 70 rows × 8 columns (")
    assert ", ".join(result.report.columns) in lines[-1]


def test_output_errors_in_english(tmp_path, english):
    previous = tmp_path / "output" / "上次.parquet"
    previous.parent.mkdir()
    pl.DataFrame({"a": [1]}).write_parquet(previous)
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    stage = data["stages"][0]
    stage["input"] = {"reader": "parquet", "files": ["output/上次.parquet"]}
    stage["steps"] = []
    stage["output_file"] = "上次"
    (planned,) = plan_outputs(data, tmp_path, "x.parquet")
    assert planned is not None
    assert planned.error == (
        '"output/上次.parquet" is an input file of stage "預設階段" and can\'t be overwritten '
        "(use a different output file name)"
    )
    with pytest.raises(OutputError, match=r'^Stage "預設階段": "output/上次.parquet" is an input'):
        resolve_outputs(parse_recipe(data), tmp_path, "x.parquet")


def test_progress_label_in_english(tmp_path, english):
    assert stage_label() == "Running (streaming)"
    seen = []
    with Watcher(tmp_path / "run", [], seen.append):
        pass
    assert seen[0]["stage"] == "Running (streaming)"
    i18n.set_lang("zh")
    assert stage_label() == "執行（串流）"


def test_update_messages_in_english(monkeypatch, english):
    monkeypatch.setenv("TABLADE_NO_UPDATE_CHECK", "1")
    assert update.check_latest()["error"] == "Update check is turned off"
    monkeypatch.delenv("TABLADE_NO_UPDATE_CHECK")

    def fail(error):
        def urlopen(request, timeout):
            raise error

        monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)

    fail(urllib.error.HTTPError(update.LATEST_API, 404, "Not Found", {}, None))
    assert update.check_latest()["error"] == "No releases on GitHub yet"
    fail(urllib.error.HTTPError(update.LATEST_API, 500, "Oops", {}, None))
    assert update.check_latest()["error"] == "Couldn't check for updates (HTTP 500)"
    fail(OSError("offline"))
    assert update.check_latest()["error"] == "Couldn't connect to GitHub; skipped the update check"


def test_file_dialog_types_follow_the_language():
    assert data_types()[-1] == "所有檔案 (*.*)" and recipe_types()[-1] == "所有檔案 (*.*)"
    i18n.set_lang("en")
    assert data_types()[0].startswith("Data files (*.csv;")
    assert data_types()[-1] == "All files (*.*)" and recipe_types()[-1] == "All files (*.*)"


def test_download_skill_is_always_chinese(tmp_path, monkeypatch, english):
    """畫面是英文時，下載的 AI skill 仍是中文；下載完畫面的語言不變。"""
    from tablade import skill

    seen = []
    build = skill.build_zip

    def build_zip(*args, **kwargs):
        seen.append((i18n.lang(), type_label(pl.String)))
        return build(*args, **kwargs)

    monkeypatch.setattr(skill, "build_zip", build_zip)
    api = Api()
    monkeypatch.setattr(api, "_dialog", lambda *a, **k: [tmp_path / "skill.zip"])
    res = api.download_skill()
    assert res["ok"] and not res["cancelled"]
    assert (tmp_path / "skill.zip").stat().st_size == res["bytes"]
    assert seen == [("zh", "文字")]
    assert i18n.lang() == "en"
