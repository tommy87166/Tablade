"""大檔處理（DESIGN.md §8）：分塊轉碼、lazy 掃描、樣本預覽、串流執行的結果要和整份讀進記憶體一樣。

這裡用小檔，把門檻調低來模擬；實際的大檔量測記錄在 README 的測試結果。
"""

import hashlib
import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from tablade.engine import progress as stream_progress
from tablade.engine import run as engine_run
from tablade.engine.progress import POSITION, Watcher, with_positions
from tablade.engine.run import execute
from tablade.fixtures import messy_export, write_all
from tablade.ingest import sources, transcode
from tablade.ingest.encoding import DecodeError
from tablade.ingest.sources import SourceFile, concat, prepare, sample, scan, scan_parts
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


def small_chunks(monkeypatch, size: int) -> None:
    """讀檔（檢查編碼）和轉碼都改用很小的區塊，讓多位元組字元與 \r\n 跨區塊。"""
    monkeypatch.setattr(sources, "CHUNK", size)
    monkeypatch.setattr(transcode, "CHUNK", size)


def test_scan_transcodes_cp950_in_chunks(tmp_path, monkeypatch):
    small_chunks(monkeypatch, 7)
    raw = "甲乙丙\r\n\r\n丁戊\r\n己\r\n".encode("cp950")
    lines, info = lines_of(tmp_path, "c.txt", raw)
    assert lines == ["甲乙丙", "", "丁戊", "己"] and info.encoding == "cp950"
    assert info.bytes == len(raw)


def test_scan_normalizes_lone_carriage_returns(tmp_path, monkeypatch):
    small_chunks(monkeypatch, 3)
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
    parts, files = scan_parts("*.txt", "text_lines", "auto", tmp_path)
    got = sample(parts, files, "text_lines", head=2, tail=1)
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


def test_stream_matches_eager_with_reordered_merge(workdir):
    """合併來源檔（改順序）後，串流與整份讀進記憶體的結果一致。"""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    # 每個檔先去掉標題雜訊與頁尾，倒過來接成一份，再對整份做向下填滿
    data["steps"].insert(2, {"id": "m", "type": "merge_files", "order": ["data/明細_02.csv"]})
    (workdir / "merge.recipe.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    r = parse_recipe(load_json(workdir / "merge.recipe.json"))
    _, eager = execute(r, workdir, output=workdir / "e.parquet", mode="eager")
    _, streamed = execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    a = pl.read_parquet(workdir / "e.parquet")
    b = pl.read_parquet(workdir / "s.parquet")
    assert a.equals(b) and streamed.rows == eager.rows
    assert set(b["__source_file"]) == {"合併"}
    assert b["__line_no"].to_list() == sorted(b["__line_no"].to_list())
    # 第二個檔排在前面
    assert b["單號"][0] == messy_export(30, 1)[1][0]["單號"]


def test_stream_matches_eager_with_conversions_and_removed_columns(workdir):
    """文字轉數字、文字轉日期、刪除欄：串流與整份讀進記憶體的結果和警告都一致。"""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["steps"] += [
        {"id": "n", "type": "parse_number", "columns": ["金額", "品號"]},  # 品號轉不了：每列都警告
        {
            "id": "d",
            "type": "parse_date",
            "columns": ["日期"],
            "pattern": "slash",
            "calendar": "roc",
        },
        {"id": "x", "type": "remove_columns", "by": "position", "positions": [5, 6]},
    ]
    (workdir / "conv.recipe.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    r = parse_recipe(load_json(workdir / "conv.recipe.json"))
    _, eager = execute(r, workdir, output=workdir / "e.parquet", mode="eager")
    _, streamed = execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    a = pl.read_parquet(workdir / "e.parquet")
    b = pl.read_parquet(workdir / "s.parquet")
    assert a.equals(b) and b.height == 70
    assert b.schema["金額"] == pl.Decimal(38, 2) and b.schema["日期"] == pl.Date
    assert b.columns == [
        "__source_file",
        "__line_no",
        "日期",
        "門市",
        "品號",
        "品名",
        "單號",
        "金額",
    ]
    assert b["品號"].null_count() == 70 and b["日期"].null_count() == 0
    pick = lambda rep: sorted(  # noqa: E731
        (w.source_file, w.line_no, w.message) for s in rep.steps for w in s.warnings
    )
    assert streamed.warning_count == eager.warning_count == 70
    assert pick(streamed) == pick(eager)


def test_stream_matches_eager_with_text_and_column_steps(workdir):
    """清理文字、取代值、改欄名、保留和排序欄位：串流與整份讀進記憶體的結果一致。"""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["steps"] += [
        {"id": "t", "type": "trim_clean", "columns": ["品名", "門市"], "collapse_spaces": True},
        {
            "id": "r",
            "type": "replace_values",
            "columns": ["單號"],
            "match": "regex",
            "replacements": [{"find": r"^單號\s*(\d+)$", "replace": "$1"}],
        },
        {"id": "m", "type": "rename_columns", "names": {"品名": "商品", "單號": "編號"}},
        {"id": "c", "type": "select_columns", "columns": ["編號", "日期", "商品", "金額"]},
    ]
    (workdir / "text.recipe.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    r = parse_recipe(load_json(workdir / "text.recipe.json"))
    execute(r, workdir, output=workdir / "e.parquet", mode="eager")
    execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    a = pl.read_parquet(workdir / "e.parquet")
    b = pl.read_parquet(workdir / "s.parquet")
    assert a.equals(b) and b.height == 70
    assert b.columns == ["__source_file", "__line_no", "編號", "日期", "商品", "金額"]
    assert b["編號"].str.contains(r"^\d+$").all()


def test_auto_mode_samples_dry_runs_and_streams_exports(workdir, monkeypatch):
    monkeypatch.setattr(engine_run, "SAMPLE_THRESHOLD", 10)
    monkeypatch.setattr(engine_run, "SAMPLE_HEAD", 20)
    monkeypatch.setattr(engine_run, "SAMPLE_TAIL", 3)
    _, dry = execute(recipe(workdir), workdir, write_output=False)
    assert dry.mode == "sample" and dry.sample_rows == 2 * (20 + 3)
    _, full = execute(recipe(workdir), workdir, output=workdir / "full.parquet")
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
    s.load_data()
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


# ── 串流執行的進度 ──────────────────────────────────────────────


def source(rows: int) -> SourceFile:
    return SourceFile("x.txt", 0, "", "utf-8", rows)


def write_positions(folder: Path, name: str, positions: list[int]) -> None:
    folder.mkdir(exist_ok=True)
    pl.DataFrame({POSITION: positions}, schema={POSITION: pl.Int64}).write_parquet(folder / name)


def test_positions_count_lines_across_files(tmp_path):
    for name, n in (("a.txt", 3), ("b.txt", 0), ("c.txt", 2)):
        (tmp_path / name).write_text("".join(f"{name}{i}\n" for i in range(n)), encoding="utf-8")
    parts, files = scan_parts(["a.txt", "b.txt", "c.txt"], "text_lines", "auto", tmp_path)
    got = concat(with_positions(parts, files, "text_lines")).collect()[POSITION].to_list()
    assert got == [1, 2, 3, 4, 5]
    (tmp_path / "d.csv").write_text("欄\n1\n2\n", encoding="utf-8")  # csv 的資料從第 2 行開始
    (tmp_path / "e.csv").write_text("欄\n3\n", encoding="utf-8")
    parts, files = scan_parts(["d.csv", "e.csv"], "csv", "auto", tmp_path)
    got = concat(with_positions(parts, files, "csv")).collect()[POSITION].to_list()
    assert got == [1, 2, 3]


def test_watcher_reads_only_finished_files_and_counts_each_source(tmp_path):
    """合併來源檔改了順序也算得對：每個來源檔各自取看過的最大位置再加起來。"""
    folder = tmp_path / "run"
    watcher = Watcher(folder, [source(3), source(0), source(4)], None)
    assert not watcher.poll()  # 資料夾還沒建立
    write_positions(folder, "00000000.parquet", [4, 5])  # 第 3 個檔的第 1、2 行
    assert not watcher.poll() and watcher.done() == 0  # 最新的檔可能還在寫
    write_positions(folder, "00000001.parquet", [6, 1])  # 篩掉了第 3 個檔的最後一行
    assert watcher.poll() and watcher.done() == 2
    write_positions(folder, "00000002.parquet", [])
    assert watcher.poll() and watcher.done() == 4  # 第 3 個檔到第 3 行、第 1 個檔到第 1 行
    assert watcher.poll(finished=True) and watcher.done() == 4
    assert not watcher.poll(finished=True)


def test_stream_reports_progress_in_rows_and_cleans_up(workdir, monkeypatch):
    monkeypatch.setattr(stream_progress, "MIN_ROWS_PER_FILE", 7)  # 很多個小暫存檔
    seen = []
    _, report = execute(
        recipe(workdir), workdir, output=workdir / "s.parquet", mode="stream", progress=seen.append
    )
    run = [p for p in seen if p["stage"] == "執行（串流）"]
    assert run and all(p["unit"] == "rows" and p["total"] == report.input_rows for p in run)
    assert [p["done"] for p in run] == sorted(p["done"] for p in run)
    assert run[0]["done"] == 0 and run[-1]["done"] == report.input_rows
    units = {p["stage"].split(" ")[0]: p["unit"] for p in seen if p["total"]}
    assert units == {"檢查編碼": "bytes", "轉碼": "bytes", "準備": "files", "執行（串流）": "rows"}
    assert (
        pl.read_parquet(workdir / "s.parquet").drop("__source_file", "__line_no").to_dicts()
        == EXPECTED
    )
    assert not list(sources.staging_dir().glob("run-*"))  # 分檔寫的暫存結果刪掉了


def test_stream_handles_an_empty_result(workdir):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["steps"].append({"id": "z", "type": "remove_top_rows", "n": 1_000_000})
    (workdir / "empty.recipe.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    r = parse_recipe(load_json(workdir / "empty.recipe.json"))
    _, report = execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    out = pl.read_parquet(workdir / "s.parquet")
    assert report.rows == 0 and out.height == 0 and "金額" in out.columns


# ── 暫存資料夾的清理 ────────────────────────────────────────────


def test_sweep_removes_only_unused_staging_folders(tmp_path):
    """當掉或被強制結束時留下的轉碼副本，下次啟動時清掉；另一個開著的 Tablade 正在用的不動。"""
    old = tmp_path / "tablade-stage-old"  # 沒有鎖檔
    old.mkdir()
    (old / "copy.txt").write_text("轉碼副本", encoding="utf-8")
    crashed = tmp_path / "tablade-stage-crashed"  # 有鎖檔，但沒有人鎖著
    crashed.mkdir()
    (crashed / sources.LOCK_NAME).write_bytes(b"")
    busy = tmp_path / "tablade-stage-busy"
    busy.mkdir()
    holder = (busy / sources.LOCK_NAME).open("a+b")
    assert sources._try_lock(holder)  # 模擬另一個還開著的 Tablade
    unrelated = tmp_path / "other-folder"
    unrelated.mkdir()
    try:
        removed = sources.sweep_stale_staging(tmp_path)
    finally:
        holder.close()
    assert sorted(p.name for p in removed) == ["tablade-stage-crashed", "tablade-stage-old"]
    assert busy.exists() and unrelated.exists()


def test_own_staging_folder_is_locked_and_cleared(tmp_path, monkeypatch):
    import tempfile

    sources.clear_staging()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    folder = sources.staging_dir()
    assert folder.parent == tmp_path and (folder / sources.LOCK_NAME).exists()
    assert sources.sweep_stale_staging(tmp_path) == []  # 自己正在用的不會被清掉
    sources.clear_staging()  # 關閉視窗時呼叫
    assert not folder.exists()


# ── 平行轉碼：結果要和單核心完全相同 ──────────────────────────────────


def both_ways(src: Path, encoding: str, tmp_path: Path):
    """同一個檔用單核心與平行（切成很小的段，強迫走平行）各轉一次。"""
    single, parallel = tmp_path / "single.txt", tmp_path / "parallel.txt"
    a = sources._transcode_single(src, encoding, single, None)
    b = sources._transcode(src, encoding, parallel, None, parallel_min=0, segment=1024)
    return (a, single.read_bytes()), (b, parallel.read_bytes())


def test_parallel_transcode_matches_single_core(tmp_path):
    from tablade.fixtures import write_big_messy_export

    src = write_big_messy_export(tmp_path / "明細.csv", 200_000, encoding="cp950")
    single, parallel = both_ways(src, "cp950", tmp_path)
    assert parallel == single
    assert single[0] == hashlib.sha256(src.read_bytes()).hexdigest()


def test_parallel_transcode_keeps_crlf_bom_and_lone_cr(tmp_path):
    lines = ["第一行\r\n", "第二\r第三\n", "x\r\n"] * 400 + ["結尾\r"]
    src = tmp_path / "mixed.txt"
    src.write_bytes(b"\xef\xbb\xbf" + "".join(lines).encode("utf-8"))
    single, parallel = both_ways(src, "utf-8-sig", tmp_path)
    assert parallel == single
    assert not parallel[1].startswith(b"\xef\xbb\xbf") and b"\r\n" in parallel[1]


def test_parallel_transcode_reports_the_first_error(tmp_path):
    good = ("115/01/05,北區門市,原子筆\r\n" * 300).encode("cp950")
    src = tmp_path / "bad.csv"
    src.write_bytes(good + b"\xff\xff bad\r\n" + good + b"\xff again\r\n" + good)
    errors = []
    for convert in (
        lambda: sources._transcode_single(src, "cp950", tmp_path / "a.txt", None),
        lambda: sources._transcode(
            src, "cp950", tmp_path / "b.txt", None, parallel_min=0, segment=512
        ),
    ):
        with pytest.raises(DecodeError) as err:
            convert()
        errors.append(str(err.value))
    assert errors[0] == errors[1] and "第 301 行" in errors[0]
    assert not list(tmp_path.glob("*.part*"))  # 暫存的段都清掉了


def test_parallel_transcode_falls_back_to_single_core(tmp_path, monkeypatch):
    from tablade.fixtures import write_big_messy_export

    def no_pool():
        raise OSError("不能開新程序")

    monkeypatch.setattr(sources, "_get_pool", no_pool)
    src = write_big_messy_export(tmp_path / "明細.csv", 50_000, encoding="cp950")
    out = tmp_path / "out.txt"
    sources._transcode(src, "cp950", out, None, parallel_min=0, segment=1024)
    assert out.read_bytes() == src.read_bytes().decode("cp950").encode("utf-8")
