"""範例 recipe ＋ 假資料 → 預期輸出（M1 的完成標準）。"""

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from tablade.cli import main
from tablade.fixtures import messy_export, write_all
from tablade.recipe.schema import RECIPE_VERSION

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLE, tmp_path / EXAMPLE.name)
    write_all(tmp_path / "data")
    return tmp_path


def test_run_example_recipe(workdir, capsys):
    assert main(["run", str(workdir / EXAMPLE.name)]) == 0
    printed = capsys.readouterr().out
    assert "結果：70 列 × 8 欄" in printed

    out = pl.read_parquet(workdir / "sales_detail.parquet")
    expected = messy_export(50, 0)[1] + messy_export(30, 1)[1]
    assert out.drop("__source_file", "__line_no").to_dicts() == expected
    assert out.columns[:2] == ["__source_file", "__line_no"]


def test_run_writes_to_the_given_output(workdir):
    target = workdir / "out" / "結果.parquet"
    assert main(["run", str(workdir / EXAMPLE.name), "-o", str(target)]) == 0
    assert pl.read_parquet(target).height == 70
    assert not (workdir / "sales_detail.parquet").exists()


def test_every_output_row_points_to_its_physical_line(workdir):
    assert main(["run", str(workdir / EXAMPLE.name)]) == 0
    out = pl.read_parquet(workdir / "sales_detail.parquet")
    files = {}
    for row in out.iter_rows(named=True):
        path = row["__source_file"]
        if path not in files:
            files[path] = (workdir / path).read_bytes().decode("cp950").split("\r\n")
        line = files[path][row["__line_no"] - 1]
        assert row["單號"] in line and row["品號"] in line


def test_validate_with_dry_run_reports_each_step(workdir, capsys):
    assert main(["validate", str(workdir / EXAMPLE.name), "--run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    rows = [s["rows"] for s in result["report"]["steps"]]
    assert rows == [90, 88, 82, 82, 80, 80, 80, 70]
    assert result["report"]["inputs"][0]["encoding"] == "cp950"
    assert not list(workdir.glob("*.parquet")), "試跑不應寫出檔案"


def test_run_reports_problems_instead_of_running(workdir, capsys):
    recipe = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    recipe["steps"][5]["columns"] = ["門"]
    path = workdir / "bad.recipe.json"
    path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    assert main(["run", str(path)]) == 1
    printed = capsys.readouterr().out
    assert "steps[5].columns[0]" in printed and "是不是「門市」？" in printed
    assert not list(workdir.glob("*.parquet"))


def test_missing_data_is_reported(tmp_path, capsys):
    shutil.copy(EXAMPLE, tmp_path / EXAMPLE.name)
    assert main(["validate", str(tmp_path / EXAMPLE.name)]) == 0  # 靜態檢查：只提醒
    assert "找不到符合" in capsys.readouterr().out
    assert main(["validate", str(tmp_path / EXAMPLE.name), "--run"]) == 1


def test_invalid_json(tmp_path, capsys):
    path = tmp_path / "x.recipe.json"
    path.write_text('{"tablade_recipe": "1.0",', encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    assert "不是合法的 JSON" in capsys.readouterr().out


def test_schema_command(tmp_path):
    target = tmp_path / "spec.json"
    assert main(["schema", "--out", str(target)]) == 0
    spec = json.loads(target.read_text(encoding="utf-8"))
    assert spec["recipe_version"] == RECIPE_VERSION
    assert "split_column" in spec["steps"]


# ── 分段報表範例（M4 的完成標準）──────────────────────────────────

REPORT = ROOT / "examples" / "sales_report.recipe.json"


def report_number(value: str) -> int:
    """報表上的數字：千分位、括號是負數。"""
    if value.startswith("("):
        return -int(value.strip("()").replace(",", ""))
    return int(value.replace(",", ""))


def expected_report_rows() -> list[dict]:
    """假報表的明細，轉成範例 Recipe 輸出的型別（數字是整數、民國日期轉成日期）。"""
    from datetime import date

    from tablade.fixtures import sectioned_report

    _, rows = sectioned_report(0, entries=20)
    out = []
    for r in rows:
        y, m, d = (int(x) for x in r["日期"].split("/"))
        out.append(
            {
                "門市": r["門市"],
                "日期": date(y + 1911, m, d),
                "品號": r["品號"],
                "品名": r["品名"],
                "數量": int(r["數量"]),
                "單價": report_number(r["單價"]),
                "金額": report_number(r["金額"]),
            }
        )
    return out


@pytest.fixture
def reportdir(tmp_path: Path) -> Path:
    shutil.copy(REPORT, tmp_path / REPORT.name)
    write_all(tmp_path / "data")
    return tmp_path


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_sectioned_report_example_flattens_completely(reportdir, mode):
    """分段、跨頁、小計、總計的列印格式報表 → 每一列明細（跨頁的明細也補上門市）。"""
    from tablade.engine.run import execute
    from tablade.recipe.schema import load_json, parse_recipe

    recipe = parse_recipe(load_json(reportdir / REPORT.name))
    out_path = reportdir / f"{mode}.parquet"
    _, report = execute(recipe, reportdir, output=out_path, mode=mode)
    out = pl.read_parquet(out_path)
    assert report.warning_count == 0 and out.height == 60
    assert out.drop("__source_file", "__line_no").to_dicts() == expected_report_rows()
    assert out.schema["日期"] == pl.Date and out.schema["金額"] == pl.Int64
    # 每一列的行號都指回報表裡那一行明細
    lines = (reportdir / "data" / "銷售報表.txt").read_bytes().decode("cp950").split("\r\n")
    for row in out.iter_rows(named=True):
        line = lines[row["__line_no"] - 1]
        assert row["品號"] in line and row["品名"] in line


def test_sectioned_report_spans_pages_and_sections():
    from tablade.fixtures import sectioned_report

    lines, rows = sectioned_report(0, entries=20)
    assert sum("頁次：" in line for line in lines) >= 2  # 有換頁
    assert sum(line.startswith("門市：") for line in lines) == 3  # 換頁時不重複段落標題
    assert sum("小計" in line for line in lines) == 3 and sum("總計" in line for line in lines) == 1
    assert len(rows) == 60 and any(r["數量"].startswith("-") for r in rows)  # 有退貨


# ── Excel 範例 ──────────────────────────────────────────────────

EXCEL = ROOT / "examples" / "sales_excel.recipe.json"


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_excel_example_reads_the_sheet_and_converts_types(tmp_path, mode):
    """Excel 工作表：去掉標題、匯出時間、空白列，提升標題列，日期與數字轉型。"""
    from datetime import date

    from tablade.engine.run import execute
    from tablade.fixtures import excel_export
    from tablade.recipe.schema import load_json, parse_recipe

    shutil.copy(EXCEL, tmp_path / EXCEL.name)
    write_all(tmp_path / "data")
    out_path = tmp_path / "out.parquet"
    _, report = execute(
        parse_recipe(load_json(tmp_path / EXCEL.name)), tmp_path, output=out_path, mode=mode
    )
    out = pl.read_parquet(out_path)
    expected = [
        {**r, "日期": date.fromisoformat(r["日期"]), "數量": int(r["數量"]), "金額": int(r["金額"])}
        for r in excel_export(20, seed=4)[1]  # write_all 用 seed + 4 產生 明細.xlsx
    ]
    assert report.warning_count == 0
    assert out.drop("__source_file", "__line_no").to_dicts() == expected
    assert out["__line_no"].to_list() == list(range(5, 25))  # Excel 的第 5～24 列
    assert out.schema["日期"] == pl.Date and out.schema["金額"] == pl.Int64
