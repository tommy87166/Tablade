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
from tablade.engine.run import RunError, execute
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
    data["stages"][0]["steps"][6]["names"] = [
        "通路",
        "付款方式",
    ]  # 備註有 3 段，只拆 2 欄 → 每列都有警告
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
    data["stages"][0]["steps"].insert(
        2, {"id": "m", "type": "merge_files", "order": ["data/明細_02.csv"]}
    )
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
    data["stages"][0]["steps"] += [
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
    """清理文字、取代文字、改欄名、保留和排序欄位：串流與整份讀進記憶體的結果一致。"""
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["stages"][0]["steps"] += [
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


def run_both(workdir: Path, steps: list[dict], name: str, same_warnings: bool = True):
    """範例 recipe 後面接上 steps，整份讀進記憶體與串流各跑一次。

    串流執行的警告取自最後的結果（或下一個彙總、合併來源檔之前）：之後被移除的列不算，
    所以後面有篩選、移除重複時只會是整份讀進記憶體的一部分（``same_warnings=False``）。
    """
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["stages"][0]["steps"] += steps
    path = workdir / f"{name}.recipe.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    r = parse_recipe(load_json(path))
    _, eager = execute(r, workdir, output=workdir / "e.parquet", mode="eager")
    _, streamed = execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    a = pl.read_parquet(workdir / "e.parquet")
    b = pl.read_parquet(workdir / "s.parquet")
    assert a.equals(b)
    assert not [c for c in b.columns if c.startswith("__w_")]
    if same_warnings:
        assert warnings_of(streamed) == warnings_of(eager)
    return b, eager, streamed


def warnings_of(report) -> list[tuple]:
    return sorted(
        (s.id, s.warning_count, w.source_file, w.line_no, w.message)
        for s in report.steps
        for w in s.warnings
    )


AMOUNT = {"id": "n", "type": "parse_number", "columns": ["金額"]}
BAD_NUMBER = {"id": "bad", "type": "parse_number", "columns": ["品號"]}  # 轉不了：每列都警告


def test_stream_warnings_before_merge_point_to_the_original_lines(workdir):
    """合併來源檔之前的警告：串流時在合併之前就算好，指到原本的來源檔與行號（和整份讀進記憶體一樣）。"""
    _, _, streamed = run_both(workdir, [BAD_NUMBER, {"id": "m", "type": "merge_files"}], "mw")
    bad = streamed.steps[-2]
    assert bad.warning_count == 70
    assert {w.source_file for w in bad.warnings} == {"data/明細_01.csv", "data/明細_02.csv"}


def test_stream_matches_eager_with_group_by_per_file(workdir):
    """群組彙總在合併來源檔之前：每個來源檔各自分組；之前步驟的警告也一致。"""
    steps = [
        AMOUNT,
        BAD_NUMBER,
        {
            "id": "g",
            "type": "group_by",
            "by": ["門市", "通路"],
            "aggregations": [
                {"column": "金額", "op": "sum", "name": "合計"},
                {"column": "品名", "op": "n_unique", "name": "品項"},
            ],
        },
    ]
    b, eager, streamed = run_both(workdir, steps, "group")
    assert set(b["__source_file"]) == {"data/明細_01.csv", "data/明細_02.csv"}
    for name, (_, rows) in (
        ("data/明細_01.csv", messy_export(50, 0)),
        ("data/明細_02.csv", messy_export(30, 1)),
    ):
        part = b.filter(pl.col("__source_file") == name)
        keys = list(dict.fromkeys((r["門市"], r["通路"]) for r in rows))  # 第一次出現的順序
        assert list(zip(part["門市"], part["通路"], strict=True)) == keys
        assert part["筆數"].sum() == len(rows)
        assert part["__line_no"].is_sorted()
    assert b.columns == ["__source_file", "__line_no", "門市", "通路", "合計", "品項", "筆數"]
    assert eager.steps[-2].warning_count == streamed.steps[-2].warning_count == 70


def test_stream_matches_eager_with_pivot_and_unpivot_after_merge(workdir):
    """合併來源檔之後樞紐再取消樞紐：整份一起處理；樞紐的警告在取消樞紐之前就算好。"""
    steps = [
        {"id": "m", "type": "merge_files"},
        AMOUNT,
        {
            "id": "p",
            "type": "pivot",
            "index": ["付款方式"],
            "on": "通路",
            "values": "金額",
            "columns": ["門市", "網路"],  # 沒列「電話」：每一組都有警告
        },
        {"id": "u", "type": "unpivot", "columns": ["門市", "網路"], "name_column": "通路"},
    ]
    b, _, streamed = run_both(workdir, steps, "pivot")
    assert set(b["__source_file"]) == {"合併"} and b.height == 6
    assert b.columns == [
        "__source_file",
        "__line_no",
        "__orig_source_file",
        "__orig_line_no",
        "付款方式",
        "通路",
        "值",
    ]
    # 合併前的位置：每一組第一筆的原始來源檔與行號
    assert b["__orig_source_file"].is_in(["data/明細_01.csv", "data/明細_02.csv"]).all()
    assert b["通路"].to_list() == ["門市", "網路"] * 3
    pivot = next(s for s in streamed.steps if s.id == "p")
    assert pivot.warning_count == 3
    assert {w.message for w in pivot.warnings} == {"這一組有沒轉成欄的「通路」：電話"}


def test_stream_warnings_come_from_staged_segments(workdir, monkeypatch):
    """需要先算警告的地方（合併來源檔、群組彙總之前）先寫成一段暫存檔，警告從暫存檔算：
    不會每項警告條件都從原始檔重跑整條流程；結果、警告都和整份讀進記憶體一樣。"""
    plans: list[str] = []
    original = engine_run.collect_warnings

    def spy(items, frame, limit, progress=None):
        if isinstance(frame, pl.LazyFrame):  # 串流執行時（整份讀進記憶體時是 DataFrame）
            plans.append(frame.explain())
        return original(items, frame, limit, progress)

    monkeypatch.setattr(engine_run, "collect_warnings", spy)
    steps = [
        AMOUNT,
        BAD_NUMBER,
        {"id": "m", "type": "merge_files"},
        {"id": "bad2", "type": "parse_number", "columns": ["品名"]},  # 合併之後的警告
        {
            "id": "g",
            "type": "group_by",
            "by": ["門市"],
            "aggregations": [{"column": "金額", "op": "sum", "name": "合計"}],
        },
    ]
    b, _, streamed = run_both(workdir, steps, "segments")
    assert b.height == 3
    assert next(s for s in streamed.steps if s.id == "bad").warning_count == 70
    assert next(s for s in streamed.steps if s.id == "bad2").warning_count == 70
    # 每次算警告都是讀暫存的 Parquet：兩段（合併、群組彙總之前）加上最後的結果
    assert len(plans) == 3 and all(p.startswith("Parquet SCAN") for p in plans)
    assert [("segment-1" in p, "segment-2" in p) for p in plans] == [
        (True, False),
        (False, True),
        (False, False),
    ]
    # 進度：分成 3 段寫，每段各自從 0 到全部
    seen: list[dict] = []
    path = workdir / "segments.recipe.json"
    execute(
        parse_recipe(load_json(path)),
        workdir,
        output=workdir / "p.parquet",
        mode="stream",
        progress=seen.append,
    )
    labels = list(dict.fromkeys(p["stage"] for p in seen if p["stage"].startswith("執行")))
    assert labels == [f"執行（串流，第 {n} / 3 段）" for n in (1, 2, 3)]


def test_stream_segment_with_no_rows(workdir):
    """寫成一段暫存檔時沒有任何列：後面照樣接得下去。"""
    steps = [
        {"id": "z", "type": "remove_top_rows", "n": 1_000_000},
        {"id": "m", "type": "merge_files"},
    ]
    b, _, streamed = run_both(workdir, steps, "empty_segment")
    assert b.height == 0 and streamed.rows == 0 and "金額" in b.columns


def test_stream_matches_eager_with_dedupe_then_whole_file_summary(workdir):
    """移除重複每個來源檔各自找；合併之後不指定分組欄就是整份彙總成一列。"""
    steps = [
        BAD_NUMBER,
        {"id": "d", "type": "dedupe", "columns": ["門市"]},
        {"id": "m", "type": "merge_files"},
        {
            "id": "g",
            "type": "group_by",
            "aggregations": [{"column": "門市", "op": "n_unique", "name": "門市數"}],
        },
    ]
    b, eager, streamed = run_both(workdir, steps, "dedupe", same_warnings=False)
    data_only = b.select([c for c in b.columns if not c.startswith("__")])
    assert data_only.to_dicts() == [{"門市數": 3, "筆數": 6}]
    assert b["__orig_source_file"].to_list() == ["data/明細_01.csv"]
    assert b["__line_no"].to_list() == [1]
    # 移除重複之前的警告：串流時只算移除重複之後還在的列（每個檔 3 列，和篩選一樣），
    # 而且在合併來源檔之前就算好，指到原本的來源檔與行號
    pick = lambda rep: {(w.source_file, w.line_no, w.message) for w in rep.steps[-4].warnings}  # noqa: E731
    assert streamed.steps[-4].warning_count == 6 and eager.steps[-4].warning_count == 70
    assert pick(streamed) <= pick(eager)
    assert {f for f, _, _ in pick(streamed)} == {"data/明細_01.csv", "data/明細_02.csv"}


def test_auto_mode_samples_dry_runs_and_streams_exports(workdir, monkeypatch):
    monkeypatch.setattr(engine_run, "SAMPLE_THRESHOLD", 10)
    monkeypatch.setattr(engine_run, "SAMPLE_HEAD", 20)
    monkeypatch.setattr(engine_run, "SAMPLE_TAIL", 3)
    _, dry = execute(recipe(workdir), workdir)
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
    result = s.export()
    assert result["mode"] == "stream" and result["rows"] == 70
    assert pl.read_parquet(result["path"]).drop("__source_file", "__line_no").to_dicts() == EXPECTED


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
    assert units == {
        "檢查編碼": "bytes",
        "轉碼": "bytes",
        "執行（串流）": "rows",
        "檢查警告": "checks",  # 範例有 3 項警告條件：第幾項
    }
    assert not [p for p in seen if p["stage"].startswith("準備")]  # 排進流程很快，不回報進度
    checks = [p["done"] for p in seen if p["stage"] == "檢查警告"]
    assert checks == [0, 1, 2, 3] and all(p["total"] == 3 for p in seen if p["stage"] == "檢查警告")
    assert (
        pl.read_parquet(workdir / "s.parquet").drop("__source_file", "__line_no").to_dicts()
        == EXPECTED
    )
    assert not list(sources.staging_dir().glob("run-*"))  # 分檔寫的暫存結果刪掉了


def test_stream_handles_an_empty_result(workdir):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["stages"][0]["steps"].append({"id": "z", "type": "remove_top_rows", "n": 1_000_000})
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


# ── 多個階段 ─────────────────────────────────────────────────────

COMBINED = ROOT / "examples" / "sales_combined.recipe.json"


def run_stages(workdir: Path, mutate=None):
    data = json.loads(COMBINED.read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    path = workdir / "stages.recipe.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    r = parse_recipe(load_json(path))
    _, eager = execute(r, workdir, output=workdir / "e.parquet", mode="eager")
    _, streamed = execute(r, workdir, output=workdir / "s.parquet", mode="stream")
    a, b = pl.read_parquet(workdir / "e.parquet"), pl.read_parquet(workdir / "s.parquet")
    assert a.equals(b)
    pick = lambda rep: sorted(  # noqa: E731
        (s.id, w.source_file, w.line_no, w.stage, w.orig_source_file, w.orig_line_no, w.message)
        for s in rep.steps
        for w in s.warnings
    )
    assert pick(streamed) == pick(eager)
    assert [(s.name, s.rows) for s in streamed.stages] == [(s.name, s.rows) for s in eager.stages]
    return b, eager, streamed


def test_stream_matches_eager_with_stages_append_and_join(workdir):
    """附加（讀兩個階段＋合併來源檔）再結合：結果、警告（含原始位置）都和整份讀進記憶體一樣。"""
    b, _, streamed = run_stages(workdir)
    assert b.height == 90 and "類別" in b.columns
    assert set(b["__orig_stage"]) == {"文字明細", "Excel明細"}
    assert b.filter(pl.col("品號").str.starts_with("D"))["類別"].null_count() == 18
    assert streamed.warning_count == 18
    first = next(w for s in streamed.steps for w in s.warnings)
    assert (first.source_file, first.orig_stage) == ("全部明細", "文字明細")
    manifest = json.loads((workdir / "log" / "s.manifest.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in manifest["stages"]] == ["品項對照", "文字明細", "Excel明細"]
    assert manifest["warnings"]["items"][0]["original"]["from_stage"] == "文字明細"
    assert {i["stage"] for i in manifest["inputs"]} == {"品項對照", "文字明細", "Excel明細"}


def test_middle_stage_outputs_and_all_or_nothing_writes(workdir):
    """中間的階段也可以寫檔（兩種執行方式結果一樣）；之後的階段失敗時，已經存在的輸出檔都維持原樣。"""
    data = json.loads(COMBINED.read_text(encoding="utf-8"))
    r = parse_recipe(data)
    written = {}
    for mode in ("eager", "stream"):
        folder = workdir / mode
        targets = {
            "文字明細": folder / "output" / "文字.parquet",
            "預設階段": folder / "output" / "全部.parquet",
        }
        _, report = execute(r, workdir, outputs=targets, log_dir=folder / "log", mode=mode)
        assert [(o.stage, o.rows) for o in report.outputs] == [("文字明細", 70), ("預設階段", 90)]
        assert report.output == targets["預設階段"].as_posix() and report.rows == 90
        assert (folder / "log" / "文字.manifest.json").is_file()
        assert report.manifest == (folder / "log" / "全部.manifest.json").as_posix()
        written[mode] = {name: pl.read_parquet(path) for name, path in targets.items()}
        assert not list((folder / "output").glob(".*"))  # 沒有留下暫存檔
    for name, frame in written["eager"].items():
        assert frame.equals(written["stream"][name])

    broken = json.loads(COMBINED.read_text(encoding="utf-8"))
    broken["stages"][3]["steps"].append({"id": "x", "type": "parse_number", "columns": ["不存在"]})
    for mode in ("eager", "stream"):
        folder = workdir / mode
        targets = {
            "文字明細": folder / "output" / "文字.parquet",
            "預設階段": folder / "output" / "全部.parquet",
        }
        before = {name: path.read_bytes() for name, path in targets.items()}
        with pytest.raises(RunError):
            execute(
                parse_recipe(broken), workdir, outputs=targets, log_dir=folder / "log", mode=mode
            )
        assert {name: path.read_bytes() for name, path in targets.items()} == before
        assert not list((folder / "output").glob(".*"))


def test_stream_matches_eager_with_join_before_merge(workdir):
    """合併來源檔之前 join：每個來源檔各自和整張右表 join；讀前面階段時每個來源各自處理。"""

    def join_in_text_stage(data):
        stages = data["stages"]
        stages[1]["steps"].append(
            {
                "id": "tj",
                "type": "join",
                "right": "品項對照",
                "on": [{"left": "品號", "right": "品號"}],
                "columns": ["建議售價"],
            }
        )
        stages[3]["steps"] = [
            {"id": "d", "type": "dedupe", "columns": ["門市"]},  # 每個來源各自去重
        ]

    b, _, streamed = run_stages(workdir, join_in_text_stage)
    # 3 個來源（@文字明細 的兩個檔＋@Excel明細）各自去重：每個來源最多 3 個門市
    assert b.group_by("__stage", "__source_file").len()["len"].max() <= 3
    assert set(b["__stage"]) == {"文字明細", "Excel明細"}
    text_rows = b.filter(pl.col("__stage") == "文字明細")
    assert text_rows["建議售價"].drop_nulls().len() > 0
    tj = next(s for s in streamed.steps if s.id == "tj")
    assert tj.warning_count > 0 and tj.stage == "文字明細"


def stage_events(seen: list[dict]) -> list[list[tuple]]:
    return [
        [(s["name"], s["status"], s["rows"]) for s in p["stages"]] for p in seen if "stages" in p
    ]


IDLE = {"stage": "", "file": "", "done": 0, "total": 0}


def test_tracker_clears_the_activity_when_something_new_starts():
    """開始新的 phase、檔案或階段時先送一則空白進度：畫面上不會還寫著上一段的進度。"""
    seen: list[dict] = []
    t = engine_run.StageTracker(["a", "b"], seen.append, [engine_run.PREPARE])
    steps = [
        lambda: t.begin(engine_run.PREPARE),
        lambda: t.files(0, 2, "x.csv"),
        lambda: t.end(engine_run.PREPARE),
        lambda: t.start(0),
        lambda: t.done(0, 3),
        lambda: t.start(1),
    ]
    for step, clears in zip(steps, [True, True, False, True, False, True], strict=True):
        seen.clear()
        step()
        assert (seen[-1] == IDLE) is clears
        assert "stages" in seen[0]  # 先送階段的狀態


def test_each_stage_starts_with_a_blank_activity(workdir):
    """串流執行時，每個階段開始的下一則就是空白進度（之後才是這個階段自己的進度）。"""
    seen: list[dict] = []
    r = parse_recipe(load_json(COMBINED))
    execute(r, workdir, output=workdir / "s.parquet", mode="stream", progress=seen.append)
    starts = [
        i
        for i, p in enumerate(seen)
        if "stages" in p and any(s["status"] == "running" for s in p["stages"])
    ]
    assert len(starts) >= 4
    assert all(seen[i + 1] == IDLE for i in starts)


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_stage_progress_is_reported_in_order(workdir, mode):
    """每個階段依序回報：處理中 → 完成（列數），回報的事件和一般進度同一個格式。"""
    seen: list[dict] = []
    r = parse_recipe(load_json(COMBINED))
    execute(r, workdir, output=workdir / "s.parquet", mode=mode, progress=seen.append)
    events = stage_events(seen)
    names = ["品項對照", "文字明細", "Excel明細", "預設階段"]
    assert events[0] == [(n, "waiting", None) for n in names]
    assert events[-1] == [
        ("品項對照", "done", 6),
        ("文字明細", "done", 70),
        ("Excel明細", "done", 20),
        ("預設階段", "done", 90),
    ]
    running = [
        next(n for n, status, _ in e if status == "running")
        for e in events
        if any(s == "running" for _, s, _ in e)
    ]
    assert running == names  # 一次只有一個階段在處理，依序
    assert all({"stage", "file", "done", "total"} <= p.keys() for p in seen)


def test_failed_stage_is_marked(workdir):
    data = json.loads(COMBINED.read_text(encoding="utf-8"))
    data["stages"][1]["steps"].append(
        {"id": "bad", "type": "trim_clean", "columns": ["金額"]}
    )  # 金額已經是數字
    path = workdir / "bad.recipe.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    seen: list[dict] = []
    with pytest.raises(engine_run.RunError):
        execute(
            parse_recipe(load_json(path)),
            workdir,
            output=workdir / "s.parquet",
            progress=seen.append,
        )
    last = stage_events(seen)[-1]
    assert [s for _, s, _ in last] == ["done", "error", "waiting", "waiting"]
