"""大檔處理（DESIGN.md §8）：分塊轉碼、lazy 掃描、樣本預覽、串流執行的結果要和整份讀進記憶體一樣。

這裡用小檔，把門檻調低來模擬；實際的大檔量測記錄在 README 的測試結果。
"""

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from tablade.engine import run as engine_run
from tablade.engine.run import execute
from tablade.fixtures import messy_export, write_all
from tablade.ingest import sources
from tablade.ingest.encoding import DecodeError
from tablade.ingest.sources import prepare, sample, scan
from tablade.recipe.schema import load_json, parse_recipe
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"
EXPECTED = messy_export(50, 0)[1] + messy_export(30, 1)[1]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLE, tmp_path / EXAMPLE.name)
    write_all(tmp_path / "data")
    return tmp_path


def recipe(workdir: Path):
    return parse_recipe(load_json(workdir / EXAMPLE.name))


# ── 讀檔 ──────────────────────────────────────────────────────


def lines_of(tmp_path: Path, name: str, raw: bytes, encoding: str = "auto"):
    (tmp_path / name).write_bytes(raw)
    lf, files = scan(name, "text_lines", encoding, tmp_path)
    return lf.collect()["line"].to_list(), files[0]


def test_scan_keeps_blank_lines_and_line_numbers(tmp_path):
    lines, info = lines_of(tmp_path, "a.txt", "﻿第一\n\n三\r\n四".encode())
    assert lines == ["第一", "", "三", "四"] and info.encoding == "utf-8-sig" and info.rows == 4


def test_scan_transcodes_cp950_in_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "CHUNK", 7)  # 讓多位元組字元與 \r\n 跨區塊
    raw = "甲乙丙\r\n\r\n丁戊\r\n己\r\n".encode("cp950")
    lines, info = lines_of(tmp_path, "c.txt", raw)
    assert lines == ["甲乙丙", "", "丁戊", "己"] and info.encoding == "cp950"
    assert info.bytes == len(raw)


def test_scan_normalizes_lone_carriage_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "CHUNK", 3)
    lines, _ = lines_of(tmp_path, "r.txt", b"a\rb\r\nc\rd")
    assert lines == ["a", "b", "c", "d"]


def test_scan_reports_decode_errors(tmp_path):
    (tmp_path / "bad.txt").write_bytes("好".encode("cp950") + b"\n\xff\xff\n")
    with pytest.raises(DecodeError, match="無法判斷編碼"):
        scan("bad.txt", "text_lines", "auto", tmp_path)
    with pytest.raises(DecodeError, match=r"utf-8 在第 0 個位元組（第 1 行"):
        scan("bad.txt", "text_lines", "utf-8", tmp_path)


def test_scan_empty_file_and_csv_header(tmp_path):
    (tmp_path / "e.txt").write_bytes(b"")
    lf, files = scan("e.txt", "text_lines", "auto", tmp_path)
    assert lf.collect().height == 0 and files[0].rows == 0
    (tmp_path / "x.csv").write_bytes("﻿日期,門市\r\n1,北\r\n".encode())
    lf, files = scan("x.csv", "csv", "auto", tmp_path)
    assert lf.collect().to_dicts() == [
        {"__source_file": "x.csv", "__line_no": 2, "日期": "1", "門市": "北"}
    ]


def test_prepare_is_cached(tmp_path):
    (tmp_path / "c.txt").write_bytes("甲\n".encode("cp950"))
    first = prepare(tmp_path / "c.txt")
    assert first.effective != tmp_path / "c.txt"  # 轉碼成暫存檔
    assert prepare(tmp_path / "c.txt") is first


def test_sample_takes_head_and_tail_of_each_file(tmp_path):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("\n".join(f"{name}{i}" for i in range(1, 11)) + "\n")
    lf, files = scan("*.txt", "text_lines", "auto", tmp_path)
    got = sample(lf, files, "text_lines", head=2, tail=1)
    assert got["line"].to_list() == ["a.txt1", "a.txt2", "a.txt10", "b.txt1", "b.txt2", "b.txt10"]


# ── 串流執行＝整份讀進記憶體 ────────────────────────────────────


def test_stream_mode_matches_eager_mode(workdir):
    _, eager = execute(recipe(workdir), workdir, output=workdir / "eager.parquet", mode="eager")
    _, streamed = execute(
        recipe(workdir), workdir, output=workdir / "stream.parquet", mode="stream"
    )
    assert streamed.mode == "stream" and streamed.rows == eager.rows == 70
    a = pl.read_parquet(workdir / "eager.parquet")
    b = pl.read_parquet(workdir / "stream.parquet")
    assert a.equals(b)
    assert b.drop("__source_file", "__line_no").to_dicts() == EXPECTED
    assert not [c for c in b.columns if c.startswith("__w_")]


def test_stream_mode_collects_warnings_with_line_numbers(workdir):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["steps"][6]["names"] = ["通路", "付款方式"]  # 備註有 3 段，只拆 2 欄 → 每列都有警告
    (workdir / "warn.recipe.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    r = parse_recipe(load_json(workdir / "warn.recipe.json"))
    _, eager = execute(r, workdir, output=workdir / "e.parquet", mode="eager")
    _, streamed = execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    pick = lambda rep: [(w.source_file, w.line_no) for s in rep.steps for w in s.warnings]  # noqa: E731
    assert streamed.warning_count > 0
    # 串流模式的警告取自最後的結果：之後被篩掉的列不算
    assert set(pick(streamed)) <= set(pick(eager))
    assert streamed.warning_count == len(pl.read_parquet(workdir / "s.parquet"))


def test_auto_mode_samples_dry_runs_and_streams_exports(workdir, monkeypatch):
    monkeypatch.setattr(engine_run, "SAMPLE_THRESHOLD", 10)
    monkeypatch.setattr(engine_run, "SAMPLE_HEAD", 20)
    monkeypatch.setattr(engine_run, "SAMPLE_TAIL", 3)
    _, dry = execute(recipe(workdir), workdir, write_output=False)
    assert dry.mode == "sample" and dry.sample_rows == 2 * (20 + 3)
    _, full = execute(recipe(workdir), workdir)
    assert full.mode == "stream" and full.rows == 70


def test_session_previews_a_sample_and_exports_everything(workdir, monkeypatch, tmp_path):
    import tablade.session as session_module

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 10)
    monkeypatch.setattr(session_module, "SAMPLE_HEAD", 20)
    monkeypatch.setattr(session_module, "SAMPLE_TAIL", 3)
    monkeypatch.setattr(engine_run, "SAMPLE_THRESHOLD", 10)
    seen = []
    s = Session()
    s.progress = seen.append
    s.load_recipe(workdir / EXAMPLE.name)
    state = s.state()
    assert state["input"]["sampled"] is True
    assert state["input"]["total_rows"] == 94 and state["input"]["rows"] == 46
    assert any(p["stage"] == "讀取預覽樣本" for p in seen)
    result = s.export(tmp_path / "out.parquet")
    assert result["mode"] == "stream" and result["rows"] == 70
    assert (
        pl.read_parquet(tmp_path / "out.parquet").drop("__source_file", "__line_no").to_dicts()
        == EXPECTED
    )
