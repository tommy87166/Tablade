"""輸出檔可以選 CSV（每個階段各自選；docs/csv-output-plan.md）。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from tablade import i18n
from tablade.engine.run import execute
from tablade.export.manifest import manifest_path
from tablade.export.outputs import plan_outputs
from tablade.fixtures import write_all
from tablade.recipe import validator
from tablade.recipe.schema import RecipeError, load_json, parse_recipe
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "examples" / "sales_detail.recipe.json"
COMBINED = ROOT / "examples" / "sales_combined.recipe.json"
BOM = b"\xef\xbb\xbf"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    write_all(tmp_path / "data")
    return tmp_path


def detail(fmt: str, keep_system: bool = True):
    """門市銷售明細，再加上文字轉數字（小數）、文字轉日期（民國年），檢查 CSV 怎麼寫這些型別。"""
    data = load_json(DETAIL)
    data["stages"][0]["steps"] += [
        {"id": "n1", "type": "parse_number", "columns": ["金額"], "kind": "decimal", "scale": 2},
        {
            "id": "d1",
            "type": "parse_date",
            "columns": ["日期"],
            "pattern": "slash",
            "calendar": "roc",
        },
    ]
    data["stages"][0]["output_format"] = fmt
    data["output"]["keep_system_columns"] = keep_system
    return parse_recipe(data)


@pytest.mark.parametrize("keep_system", [True, False])
def test_csv_has_bom_and_the_same_values_as_parquet(workdir, keep_system):
    """CSV 是 UTF-8 加 BOM；內容和 Parquet 一樣（小數、日期照原樣，空值是空白）；整份在記憶體和串流寫出的完全一樣。"""
    written = {}
    for mode in ("eager", "stream"):
        out = workdir / mode / "明細.csv"
        _, report = execute(detail("csv", keep_system), workdir, output=out, mode=mode)
        assert report.outputs[0].format == "csv" and report.rows == 70
        written[mode] = out.read_bytes()
    assert written["eager"] == written["stream"]
    text = written["eager"]
    assert text.startswith(BOM) and not text[3:].startswith(BOM)

    parquet = workdir / "明細.parquet"
    execute(detail("parquet", keep_system), workdir, output=parquet, mode="eager")
    expected = pl.read_parquet(parquet)
    got = pl.read_csv(workdir / "eager" / "明細.csv", infer_schema=False)  # 全部當文字讀
    assert got.columns == expected.columns
    assert any(c.startswith("__") for c in got.columns) is keep_system
    assert isinstance(expected.schema["金額"], pl.Decimal) and expected.schema["日期"] == pl.Date
    assert got.equals(expected.with_columns(pl.all().cast(pl.String)))
    first = got.row(0, named=True)
    assert first["日期"] == "2026-01-14"  # 民國 115/01/14 → 日期是 2026-01-14 的寫法
    assert first["金額"].endswith(".00")  # 小數照原樣（2 位）
    assert got["金額"].null_count() == expected["金額"].null_count()  # 空值是空白


def test_default_name_and_extension_follow_the_format(tmp_path):
    """最後一個階段沒填檔名時用 Recipe 名稱＋那個格式的副檔名；中間的階段照它自己的格式。"""
    data = load_json(COMBINED)
    data["stages"][-1]["output_format"] = "csv"
    planned = plan_outputs(data, tmp_path, "門市")
    assert [(p.file, p.format) if p else None for p in planned] == [
        ("品項對照.csv", "csv"),
        None,
        None,
        ("門市.csv", "csv"),
    ]


def test_output_format_is_required_and_checked():
    """每個階段都要有 output_format；副檔名要和格式一樣；主檔名不能重複（明細.csv 和 明細.parquet 也算）。"""
    data = load_json(COMBINED)
    del data["stages"][1]["output_format"]
    with pytest.raises(RecipeError, match=r"stages\[1\]\.output_format"):
        parse_recipe(data)

    data = load_json(COMBINED)
    data["stages"][0]["output_file"] = "品項對照.parquet"  # 格式是 csv
    issues = validator.output_issues(data, "門市")
    assert issues[0]["path"] == "stages[0].output_file"
    assert "副檔名和輸出格式（csv）不一樣" in issues[0]["error"]

    data = load_json(COMBINED)
    data["stages"][1]["output_file"] = "品項對照"  # 文字明細是 parquet：品項對照.parquet
    issues = validator.output_issues(data, "門市")
    assert [i["path"] for i in issues] == ["stages[1].output_file"]
    assert "重複" in issues[0]["error"]

    i18n.set_lang("en")
    data = load_json(COMBINED)
    data["stages"][0]["output_file"] = "品項對照.parquet"
    assert "does not match the output format (csv)" in validator.output_issues(data)[0]["error"]


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_manifest_records_the_csv(workdir, mode):
    """CSV 也寫處理紀錄：記下格式，SHA-256 是 CSV 檔本身的。"""
    out = workdir / mode / "明細.csv"
    _, report = execute(detail("csv"), workdir, output=out, mode=mode)
    path = manifest_path(out)
    assert report.manifest == path.as_posix() and path.name == "明細.manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    assert m["output"]["file"] == "明細.csv" and m["output"]["format"] == "csv"
    assert m["output"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


def test_csv_output_keeps_sample_mode(workdir, monkeypatch):
    """CSV 輸出匯出後不提供完整模式（「輸出資料」照舊是抽樣模式）；Parquet 照舊變完整模式。"""
    from tablade import session as session_module

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 10)
    monkeypatch.setattr(session_module, "SAMPLE_HEAD", 20)
    monkeypatch.setattr(session_module, "SAMPLE_TAIL", 3)
    shutil.copy(DETAIL, workdir / DETAIL.name)
    s = Session()
    s.load_recipe(workdir / DETAIL.name)
    s.load_data()
    s.select(len(s.steps))  # 輸出資料
    s.set_stage_output(0, "", "csv")
    result = s.export()
    assert result["outputs"][0]["format"] == "csv" and result["path"].endswith(
        "/output/sales_detail.csv"
    )
    preview = s.state()["preview"]
    assert preview["mode"] == "sample" and preview["full"] is None

    s.set_stage_output(0, "", "parquet")
    s.export()
    preview = s.state()["preview"]
    assert preview["mode"] == "full" and preview["full"]["path"].endswith("sales_detail.parquet")


def test_unsupported_output_format_in_english(workdir):
    i18n.set_lang("en")
    s = Session()
    s.new(workdir / "x.recipe.json")
    with pytest.raises(Exception, match="Unsupported output format: xlsx"):
        s.set_stage_output(0, "x", "xlsx")
