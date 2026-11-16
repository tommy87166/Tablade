"""範例 recipe ＋ 假資料 → 預期輸出（M1 的完成標準）。"""

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from tablade.cli import main
from tablade.fixtures import messy_export, write_all

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

    out = pl.read_parquet(workdir / "output" / "明細.parquet")
    expected = messy_export(50, 0)[1] + messy_export(30, 1)[1]
    assert out.drop("__source_file", "__line_no").to_dicts() == expected
    assert out.columns[:2] == ["__source_file", "__line_no"]


def test_every_output_row_points_to_its_physical_line(workdir):
    assert main(["run", str(workdir / EXAMPLE.name)]) == 0
    out = pl.read_parquet(workdir / "output" / "明細.parquet")
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
    assert not (workdir / "output").exists(), "試跑不應寫出檔案"


def test_run_reports_problems_instead_of_running(workdir, capsys):
    recipe = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    recipe["steps"][5]["columns"] = ["門"]
    path = workdir / "bad.recipe.json"
    path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    assert main(["run", str(path)]) == 1
    printed = capsys.readouterr().out
    assert "steps[5].columns[0]" in printed and "是不是「門市」？" in printed
    assert not (workdir / "output").exists()


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
    assert spec["recipe_version"] == "1.0"
    assert "split_column" in spec["steps"]
