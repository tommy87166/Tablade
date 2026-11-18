"""英文畫面：讀檔、轉碼的進度文字與錯誤訊息、自我檢測的項目都是英文（docs/i18n-plan.md）。

測試資料的檔名與欄名是中文（資料不翻），只檢查句子本身。
"""

import re

import polars as pl
import pytest

from tablade import i18n
from tablade.fixtures import write_excel_export, write_simple_csv
from tablade.ingest import excel, sources
from tablade.ingest.encoding import DecodeError, decode_bytes
from tablade.ingest.sources import InputError, PartsMismatch

CJK = re.compile(r"[　-〿㐀-鿿豈-﫿＀-￯]")


@pytest.fixture(autouse=True)
def english(chinese_by_default):
    i18n.set_lang("en")


def test_decode_errors_are_in_english():
    with pytest.raises(DecodeError) as excinfo:
        decode_bytes(b"ok\nline2 \xff\xff\n")
    message = str(excinfo.value)
    assert message.startswith("Couldn't detect the encoding: ")
    assert "utf-8-sig can't decode byte 0xFF at offset 9 (line 2); cp950" in message
    assert not CJK.search(message)
    with pytest.raises(DecodeError, match=r"^Unsupported encoding: latin-1 "):
        decode_bytes(b"x", "latin-1")


def test_scan_decode_errors_are_in_english(tmp_path):
    (tmp_path / "bad.txt").write_bytes("好".encode("cp950") + b"\n\xff\xff\n")
    with pytest.raises(
        DecodeError, match=r"^utf-8 can't decode byte 0x\w\w at offset 0 \(line 1\)$"
    ):
        sources.scan("bad.txt", "text_lines", "utf-8", tmp_path)
    with pytest.raises(DecodeError, match=r"^Couldn't detect the encoding: utf-8 can't decode"):
        sources.scan("bad.txt", "text_lines", "auto", tmp_path)
    with pytest.raises(DecodeError, match=r"^Unsupported encoding: latin-1 "):
        sources.prepare(tmp_path / "bad.txt", "latin-1")


def test_missing_files_are_reported_in_english(tmp_path):
    with pytest.raises(InputError, match=r"^No data files added yet$"):
        sources.resolve([], tmp_path)
    with pytest.raises(InputError, match=r'^File "缺少\.csv" not found \(relative to '):
        sources.resolve(["缺少.csv"], tmp_path)
    with pytest.raises(InputError, match=r'^No files match "\*\.csv" \(relative to '):
        sources.resolve(["*.csv"], tmp_path)


def test_reader_errors_are_in_english(tmp_path):
    write_excel_export(tmp_path / "明細.xlsx")
    with pytest.raises(InputError, match=r'is an Excel file: set "Read as" to "Excel sheet"$'):
        sources.scan_parts(["明細.xlsx"], "csv", "auto", tmp_path)
    with pytest.raises(InputError, match=r'sheet "沒有" \(sheets: '):
        sources.scan_parts(["明細.xlsx"], "excel", "auto", tmp_path, sheet="沒有")
    (tmp_path / "壞.xlsx").write_bytes(b"not excel")
    with pytest.raises(excel.ExcelError, match="is not a readable Excel file: "):
        excel.sheet_names(tmp_path / "壞.xlsx")
    (tmp_path / "壞.parquet").write_bytes(b"not parquet")
    with pytest.raises(InputError, match=r"^Cannot read .+ \(not a Parquet file\?\): "):
        sources.scan_parts(["壞.parquet"], "parquet", "auto", tmp_path)
    prepared = sources.prepare(write_simple_csv(tmp_path / "a.csv"))
    with pytest.raises(InputError, match=r"^Unsupported reader: json$"):
        sources._scan_one(prepared, "json", "a.csv")


def test_column_names_made_by_tablade_stay_chinese():
    """程式產生的欄名與來源名稱是資料（Recipe 會引用），不跟著畫面換語言。"""
    assert sources.parquet_names(["__line_no", "金額"]) == {"__line_no": "原始行號", "金額": "金額"}
    assert sources.part_name("明細.xlsx", "一月", "前段") == "@前段／明細.xlsx［一月］"
    with pytest.raises(InputError, match=r"^Duplicate column names after renaming: 原始行號$"):
        sources.parquet_names(["__line_no", "原始行號"])


def test_mismatched_sources_are_reported_in_english():
    parts = [("a.csv", pl.LazyFrame({"x": [1]})), ("b.csv", pl.LazyFrame({"x": ["1"]}))]
    with pytest.raises(InputError, match=r'^"x" has different types in different sources \('):
        sources.align(parts)
    with pytest.raises(PartsMismatch) as excinfo:
        sources.concat([("a.csv", pl.LazyFrame({"x": [1]})), ("b.csv", pl.LazyFrame({"y": [1]}))])
    assert (
        str(excinfo.value)
        == 'Source file "b.csv" has different columns (y (Int64)) from "a.csv" (x (Int64))'
    )


def test_progress_stages_are_in_english(tmp_path):
    write_simple_csv(tmp_path / "門市.csv", n=5, encoding="cp950")
    write_excel_export(tmp_path / "明細.xlsx")
    pl.DataFrame({"a": [1]}).write_parquet(tmp_path / "x.parquet")
    seen = []
    sources.scan_parts(["門市.csv"], "csv", "auto", tmp_path, progress=seen.append)
    sources.scan_parts(["明細.xlsx"], "excel", "auto", tmp_path, seen.append, sheet="明細")
    sources.scan_parts(["x.parquet"], "parquet", "auto", tmp_path, progress=seen.append)
    stages = {p["stage"] for p in seen}
    assert stages == {"Checking encoding", "Transcoding", "Reading Excel", "Reading Parquet"}


def test_selftest_is_in_english(monkeypatch):
    from tablade.selftest import format_report, run_selftest

    result = run_selftest()
    assert result["ok"], result
    assert [c["name"] for c in result["checks"]] == [
        "Generate test CSV",
        "Read CSV (auto-detect encoding)",
        "Write Parquet",
        "Read Parquet back and compare",
        "Lazy query",
        "Read Excel",
        "Multi-core transcoding",
        "Deflate64 (Rust module)",
        "Run the sample Recipe in full",
    ]
    for c in result["checks"]:
        assert not CJK.search(c["name"] + c["purpose"] + c["detail"]), c
    assert result["checks"][1]["detail"] == "200 rows × 6 columns, encoding cp950"
    assert result["checks"][-1]["detail"] == (
        "2 cp950 files → combined into 70 rows as expected; processing log is correct"
    )
    assert format_report(result).startswith("Tablade self-test: passed\n  [OK] Generate test CSV: ")


def test_selftest_skipped_checks_are_in_english(monkeypatch):
    import tablade.selftest as selftest

    def broken(*args, **kwargs):
        raise OSError("disk is read-only")

    monkeypatch.setattr(selftest, "write_simple_csv", broken)
    result = selftest.run_selftest()
    assert not result["ok"]
    assert {c["detail"] for c in result["checks"][1:]} == {
        "Skipped because an earlier check failed"
    }
    assert selftest.format_report(result).startswith("Tablade self-test: failed\n")
