"""讀 Excel：每一列都是資料、欄名 A、B、C…、行號是 Excel 的列號、每一格都是文字。"""

import json
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
        "stages": [
            {
                "name": "預設階段",
                "input": {"files": ["*.xlsx"], "reader": "excel", "sheet": sheet},
                "steps": [
                    {"id": "s1", "type": "remove_top_rows", "n": 3},
                    {
                        "id": "s2",
                        "type": "promote_headers",
                        "names": ["日期", "門市", "品號", "品名", "數量", "金額"],
                    },
                ],
                "output_format": "parquet",
            }
        ],
        "output": {"keep_system_columns": True},
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
    assert s.recipe_dict()["stages"][0]["input"]["sheet"] == "說明"
    s.set_input({"reader": "text_lines", "sheet": ""})
    assert "sheet" not in s.recipe_dict()["stages"][0]["input"]  # 不是 Excel 時不寫工作表


def test_example_data_includes_an_excel_file(tmp_path):
    from tablade.fixtures import write_all

    paths = write_all(tmp_path)
    assert any(p.suffix == ".xlsx" for p in paths)
    shutil.rmtree(tmp_path)


# ── 全部工作表 ──────────────────────────────────────────────────


def monthly(tmp_path: Path) -> list[Path]:
    """兩個檔：甲有「一月」「二月」，乙只有「一月」。每張表上面都有一列標題，再一列欄名。"""

    def sheet(title: str, rows: list[list]) -> list:
        return [[title], ["品號", "金額"], *rows]

    a = write_xlsx(
        tmp_path / "甲.xlsx",
        {
            "一月": sheet("甲一月", [["A001", 10], ["A002", "x"]]),
            "二月": sheet("甲二月", [["B001", 30]]),
        },
    )
    b = write_xlsx(tmp_path / "乙.xlsx", {"一月": sheet("乙一月", [["C001", 50]])})
    return [a, b]


def all_sheets_recipe(*extra: dict, keep_system: bool = True) -> dict:
    return {
        "tablade_recipe": "1.0",
        "stages": [
            {
                "name": "預設階段",
                "input": {"files": ["甲.xlsx", "乙.xlsx"], "reader": "excel", "sheet": "*"},
                "steps": [
                    {"id": "s1", "type": "remove_top_rows", "n": 1},
                    {"id": "s2", "type": "promote_headers", "names": ["品號", "金額"]},
                    *extra,
                ],
                "output_format": "parquet",
            }
        ],
        "output": {"keep_system_columns": keep_system},
    }


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_all_sheets_are_separate_sources_with_a_sheet_column(tmp_path, mode):
    monthly(tmp_path)
    out = tmp_path / "out.parquet"
    number = {"id": "n", "type": "parse_number", "columns": ["金額"], "kind": "integer"}
    _, report = execute(parse_recipe(all_sheets_recipe(number)), tmp_path, output=out, mode=mode)
    result = pl.read_parquet(out)
    assert result.columns == ["__source_file", "__sheet", "__line_no", "品號", "金額"]
    # 每個工作表各自移除標題列、提升欄名（每張表都有自己的標題）
    assert result.select("__source_file", "__sheet", "__line_no", "品號").rows() == [
        ("甲.xlsx", "一月", 3, "A001"),
        ("甲.xlsx", "一月", 4, "A002"),
        ("甲.xlsx", "二月", 3, "B001"),
        ("乙.xlsx", "一月", 3, "C001"),
    ]
    assert result["金額"].to_list() == [10, None, 30, 50]
    # 讀檔資訊與警告都帶工作表
    assert [(f.path, f.sheet, f.rows) for f in report.inputs] == [
        ("甲.xlsx", "一月", 4),
        ("甲.xlsx", "二月", 3),
        ("乙.xlsx", "一月", 3),
    ]
    warning = next(w for s in report.steps for w in s.warnings)
    assert (warning.source_file, warning.sheet, warning.line_no) == ("甲.xlsx", "一月", 4)
    manifest = json.loads(Path(report.manifest).read_text(encoding="utf-8"))
    assert [i.get("sheet") for i in manifest["inputs"]] == ["一月", "二月", "一月"]
    assert manifest["warnings"]["items"][0]["sheet"] == "一月"


def test_merge_files_after_all_sheets_and_system_columns_off(tmp_path):
    monthly(tmp_path)
    out = tmp_path / "m.parquet"
    merge = {"id": "m", "type": "merge_files", "order": ["乙.xlsx［一月］"]}
    execute(parse_recipe(all_sheets_recipe(merge)), tmp_path, output=out, mode="eager")
    merged = pl.read_parquet(out)
    # 照指定的順序接起來（乙的一月排前面）；合併後不再分工作表、行號從 1 重編
    assert merged["品號"].to_list() == ["C001", "A001", "A002", "B001"]
    assert merged["__sheet"].to_list() == [None] * 4
    assert merged["__line_no"].to_list() == [1, 2, 3, 4]
    off = tmp_path / "off.parquet"
    execute(parse_recipe(all_sheets_recipe(keep_system=False)), tmp_path, output=off)
    assert pl.read_parquet(off).columns == ["品號", "金額"]


def test_all_sheets_in_the_session(tmp_path):
    monthly(tmp_path)
    s = Session()
    s.new(tmp_path / "x.recipe.json")
    s.add_files([tmp_path / "甲.xlsx", tmp_path / "乙.xlsx"])
    s.set_input({"reader": "excel", "sheet": "*"})
    s.load_data()
    state = s.state()
    assert [(f["path"], f["sheet"]) for f in state["input"]["sources"]] == [
        ("甲.xlsx", "一月"),
        ("甲.xlsx", "二月"),
        ("乙.xlsx", "一月"),
    ]
    s.add_step("merge_files", index=0)
    # 合併來源檔的順序清單：每個工作表各自是一個來源
    assert s.state()["steps"][0]["input_files"] == [
        "甲.xlsx［一月］",
        "甲.xlsx［二月］",
        "乙.xlsx［一月］",
    ]
    s.delete_step(0)
    frame = s.frame_at(-1)
    assert frame.columns[:3] == ["__source_file", "__sheet", "__line_no"]
    # 點警告跳到那一列：同一個檔、同一個行號，在不同工作表要分得出來
    assert s.find_row(-1, "甲.xlsx", 1, "二月") == 4
    assert s.find_row(-1, "甲.xlsx", 1) == 0
    assert peek_columns(["*.xlsx"], "excel", "auto", tmp_path, "*") == ["A", "B"]
