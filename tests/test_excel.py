"""讀 Excel：每一列都是資料、欄名 A、B、C…、行號是 Excel 的列號、每一格都是文字。"""

import shutil
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from tablade.engine.run import execute
from tablade.fixtures import excel_export, write_excel_export, write_xlsx
from tablade.ingest import excel
from tablade.ingest.sources import InputError, peek_columns, scan_parts
from tablade.recipe.schema import parse_recipe
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]


def test_read_sheet_keeps_row_numbers_and_reads_text(tmp_path):
    path = write_xlsx(
        tmp_path / "a.xlsx",
        {
            "表一": [
                None,  # 最上面的空白列也要保留，列號才對得上
                ["標題"],
                [date(2026, 1, 5), "北區門市", 1234.5, 3, "0012"],
                None,
                ["", "  ", 1.0, None, "x"],
            ],
            "表二": [["只有一格"]],
        },
    )
    frame = excel.read_sheet(path, "", "a.xlsx")
    assert frame.columns == ["__source_file", "__line_no", "A", "B", "C", "D", "E"]
    assert frame["__line_no"].to_list() == [1, 2, 3, 4, 5]
    assert frame.row(2) == ("a.xlsx", 3, "2026-01-05", "北區門市", "1234.5", "3", "0012")
    assert frame["C"].to_list() == [None, None, "1234.5", None, "1"]
    assert all(t == pl.String for c, t in frame.schema.items() if c != "__line_no")
    second = excel.read_sheet(path, "表二", "a.xlsx")
    assert second.columns == ["__source_file", "__line_no", "A"] and second["A"].to_list() == [
        "只有一格"
    ]
    assert excel.sheet_names(path) == ["表一", "表二"]
    assert excel.peek_columns(path, "") == ["A", "B", "C", "D", "E"]


def test_column_letters():
    assert [excel.column_letter(i) for i in (0, 25, 26, 51, 52, 701, 702)] == [
        "A",
        "Z",
        "AA",
        "AZ",
        "BA",
        "ZZ",
        "AAA",
    ]


def test_excel_errors(tmp_path):
    path = write_excel_export(tmp_path / "x.xlsx")
    with pytest.raises(InputError, match="沒有工作表「沒有」（有：明細、說明）"):
        scan_parts(["x.xlsx"], "excel", "auto", tmp_path, sheet="沒有")
    (tmp_path / "fake.xlsx").write_text("不是 Excel", encoding="utf-8")
    with pytest.raises(InputError, match="不是讀得了的 Excel 檔"):
        scan_parts(["fake.xlsx"], "excel", "auto", tmp_path)
    # Excel 檔用文字的讀取方式：直接告訴使用者要換讀取方式
    with pytest.raises(InputError, match="是 Excel 檔：讀取方式請選「Excel 工作表」"):
        scan_parts(["x.xlsx"], "text_lines", "auto", tmp_path)
    assert peek_columns(["x.xlsx"], "excel", "auto", tmp_path) == ["A", "B", "C", "D", "E", "F"]
    assert path.exists()


def excel_recipe(sheet: str = "") -> dict:
    return {
        "tablade_recipe": "1.0",
        "inputs": [{"id": "src", "files": ["*.xlsx"], "reader": "excel", "sheet": sheet}],
        "steps": [
            {"id": "s1", "type": "remove_top_rows", "n": 3},
            {
                "id": "s2",
                "type": "promote_headers",
                "names": ["日期", "門市", "品號", "品名", "數量", "金額"],
            },
        ],
        "output": {"format": "parquet", "keep_system_columns": True},
    }


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_excel_recipe_runs_like_text_files(tmp_path, mode):
    write_excel_export(tmp_path / "明細_01.xlsx", n=20, seed=1)
    write_excel_export(tmp_path / "明細_02.xlsx", n=5, seed=2)
    out = tmp_path / "out.parquet"
    _, report = execute(parse_recipe(excel_recipe("明細")), tmp_path, output=out, mode=mode)
    expected = excel_export(20, 1)[1] + excel_export(5, 2)[1]
    result = pl.read_parquet(out)
    assert result.drop("__source_file", "__line_no").to_dicts() == expected
    # 行號是 Excel 的列號：第 5 列是第一筆明細（上面是標題、匯出時間、空白列、標題列）
    assert result.filter(pl.col("__source_file") == "明細_01.xlsx")["__line_no"][0] == 5
    assert [f.encoding for f in report.inputs] == ["excel", "excel"]
    assert report.inputs[0].rows == 24


def test_session_lists_sheets_and_loads_excel(tmp_path):
    write_excel_export(tmp_path / "明細.xlsx")
    s = Session()
    s.new(tmp_path / "x.recipe.json")
    s.add_files([tmp_path / "明細.xlsx"])
    assert s.state()["input"]["sheets"] == []  # 還是文字的讀取方式
    s.set_input({"reader": "excel"})
    assert s.state()["input"]["sheets"] == ["明細", "說明"]
    s.load_data()
    assert s.state()["input"]["columns"] == ["A", "B", "C", "D", "E", "F"]
    s.set_input({"sheet": "說明"})  # 換工作表要重新載入
    assert s.state()["input"]["stale"] is True
    s.load_data()
    assert s.state()["input"]["rows"] == 1
    assert s.recipe_dict()["inputs"][0]["sheet"] == "說明"
    s.set_input({"reader": "text_lines", "sheet": ""})
    assert "sheet" not in s.recipe_dict()["inputs"][0]  # 不是 Excel 時不寫工作表


def test_example_data_includes_an_excel_file(tmp_path):
    from tablade.fixtures import write_all

    paths = write_all(tmp_path)
    assert any(p.suffix == ".xlsx" for p in paths)
    shutil.rmtree(tmp_path)
