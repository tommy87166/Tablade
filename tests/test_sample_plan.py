"""預覽與試跑的樣本：額度（``sample_budget``）與每個檔取幾行（``sample_plan``，docs/memory-improve.md）。"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from tablade import fixtures
from tablade.engine import run
from tablade.engine.check import format_report
from tablade.engine.run import execute, sample_budget
from tablade.ingest import sources
from tablade.ingest.sources import SamplePlan, sample, sample_plan, scan_parts
from tablade.recipe.schema import parse_recipe
from tablade.steps.base import LINE_NO, SOURCE_FILE

MB = 1 << 20
GB = 1 << 30
MEMBERS = "SALES_*.TXT"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(run.SAMPLE_MB_ENV, raising=False)


def zips(tmp_path: Path, name: str, files: int, rows: int, seed: int = 0) -> list[Path]:
    return fixtures.write_many_zips(tmp_path / name, files=files, rows=rows, seed=seed)


def scan(paths: list[Path], base: Path):
    return scan_parts([str(p) for p in paths], "zip_lines", "cp950", base, members=MEMBERS)


# ── 額度 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("memory", "threads", "expected"),
    [
        ((8 * GB, 1 * GB), 4, int(8 * GB * 0.01)),  # 可用很少：實體記憶體 × 1%（約 82 MB）
        ((8 * GB, 4 * GB), 4, 128 * MB),  # 流暢上限：4 × 32 MB
        ((32 * GB, 15 * GB), 16, 256 * MB),  # 流暢上限：最多 256 MB
        ((8 * GB, 2 * GB), 8, int(2 * GB * 0.05)),  # 中間：可用 × 5%（約 102 MB）
    ],
)
def test_budget_follows_memory_and_threads(memory, threads, expected):
    assert sample_budget(memory, threads) == expected


@pytest.mark.parametrize(("threads", "smooth"), [(1, 32), (4, 128), (8, 256), (16, 256)])
def test_smooth_limit_follows_threads(threads, smooth):
    assert sample_budget((256 * GB, 200 * GB), threads) == smooth * MB


def test_budget_without_system_memory():
    assert sample_budget(None, 16) == 128 * MB
    assert sample_budget(None, 2) == 64 * MB


def test_budget_reads_this_computer_by_default():
    assert 0 < sample_budget() <= run.SAMPLE_BYTES_MAX


@pytest.mark.parametrize(("value", "expected"), [("300", 300 * MB), (" 64 ", 64 * MB)])
def test_override_with_environment(monkeypatch, value, expected):
    monkeypatch.setenv(run.SAMPLE_MB_ENV, value)
    assert sample_budget((8 * GB, 1 * GB), 4) == expected


@pytest.mark.parametrize("value", ["", "abc", "0", "-5", "1.5", "１２"])
def test_bad_override_is_ignored(monkeypatch, value):
    monkeypatch.setenv(run.SAMPLE_MB_ENV, value)
    assert sample_budget((32 * GB, 15 * GB), 16) == 256 * MB


# ── 每個檔取幾行 ─────────────────────────────────────────────


def test_few_files_keep_the_per_file_limit(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=2, rows=100), tmp_path)
    plan = sample_plan(parts, files, 256 * MB, 20, 3)
    assert set(plan.lines.values()) == {(20, 3)}
    assert plan.rows == 46 and plan.truncated and (plan.head, plan.tail) == (20, 3)


def test_many_files_stay_within_the_budget(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=40, rows=3_000), tmp_path)
    budget = 4 * MB
    plan = sample_plan(parts, files, budget, 100_000, 1_000)
    frame = sample(parts, files, "zip_lines", plan)
    assert frame.height == plan.rows and plan.truncated
    assert frame.estimated_size() <= budget * 1.05
    assert 40 * 220 <= plan.rows < 40 * 3_000
    by_file = frame.group_by(SOURCE_FILE).agg(
        pl.col(LINE_NO).min().alias("first"), pl.col(LINE_NO).max().alias("last")
    )
    assert by_file.height == 40
    assert by_file["first"].to_list() == [1] * 40 and by_file["last"].to_list() == [3_000] * 40
    for head, tail in plan.lines.values():
        assert head >= sources.SAMPLE_MIN_HEAD and tail >= sources.SAMPLE_MIN_TAIL


def test_minimum_shrinks_when_there_are_too_many_files(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=30, rows=500), tmp_path)
    plan = sample_plan(parts, files, 10_000, 100_000, 1_000)
    assert set(plan.lines.values()) == {(sources.SAMPLE_FLOOR_HEAD, sources.SAMPLE_FLOOR_TAIL)}


def test_minimum_never_exceeds_the_maximum(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=5, rows=500), tmp_path)
    plan = sample_plan(parts, files, 1_000, 10, 2)
    assert set(plan.lines.values()) == {(10, 2)}


def test_small_files_are_taken_whole_and_give_the_rest_away(tmp_path):
    small = zips(tmp_path, "small", files=1, rows=50, seed=1)
    big = zips(tmp_path, "big", files=3, rows=4_000, seed=2)
    parts, files = scan(small + big, tmp_path)
    budget = 400_000
    plan = sample_plan(parts, files, budget, 100_000, 1_000)
    small_name = files[0].name
    assert plan.lines[small_name] == (50, 0)
    others = [h + t for n, (h, t) in plan.lines.items() if n != small_name]
    equal_share = sample_plan(parts[1:], files[1:], budget * 3 // 4, 100_000, 1_000)
    assert min(others) > max(h + t for h, t in equal_share.lines.values())
    for count in others:
        assert count < 4_000


def test_tail_is_a_tenth_of_what_a_file_gets(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=10, rows=20_000), tmp_path)
    plan = sample_plan(parts, files, 2 * MB, 100_000, 1_000)
    for head, tail in plan.lines.values():
        assert tail == min(1_000, max(sources.SAMPLE_MIN_TAIL, (head + tail) // 10))


def test_not_truncated_when_every_file_fits(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=3, rows=200), tmp_path)
    plan = sample_plan(parts, files, 256 * MB, 100_000, 1_000)
    assert not plan.truncated and plan.rows == 600
    assert sample(parts, files, "zip_lines", plan).height == 600


def test_fixed_plan():
    files = [_file("a", 100), _file("b", 10)]
    plan = SamplePlan.fixed(files, 20, 3)
    assert plan.lines == {"a": (20, 3), "b": (20, 3)}
    assert plan.rows == 23 + 10 and plan.truncated and plan.budget is None
    assert not SamplePlan.fixed([_file("b", 10)], 20, 3).truncated


def _file(name: str, rows: int) -> sources.SourceFile:
    return sources.SourceFile(name, 0, "", "", rows)


def _filled(parts, files, reader: str, budget: int) -> float:
    """好幾個檔依額度取樣：樣本實際的 ``estimated_size()`` 是額度的幾倍（應該接近 1）。"""
    plan = sample_plan(parts, files, budget, 100_000, 1_000)
    assert plan.truncated
    return sample(parts, files, reader, plan).estimated_size() / budget


def _csvs(folder: Path, names: list[str], rows: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return [
        fixtures.write_simple_csv(folder / name, n=rows, seed=i) for i, name in enumerate(names)
    ]


def test_text_sample_fills_the_budget(tmp_path):
    parts, files = scan(zips(tmp_path, "z", files=12, rows=5_000), tmp_path)
    assert 0.9 <= _filled(parts, files, "zip_lines", 4 * MB) <= 1.05


def test_csv_and_parquet_samples_fill_the_budget(tmp_path):
    csvs = _csvs(tmp_path / "csv", [f"門市銷售明細_{i:02d}.csv" for i in range(6)], 10_000)
    parts, files = scan_parts([str(c) for c in csvs], "csv", "cp950", tmp_path)
    assert 0.9 <= _filled(parts, files, "csv", 2 * MB) <= 1.05

    folder = tmp_path / "parquet"
    folder.mkdir()
    for c in csvs:
        pl.read_csv(c, encoding="cp950").write_parquet(folder / f"{c.stem}.parquet")
    parquets = sorted(str(f) for f in folder.glob("*.parquet"))
    parts, files = scan_parts(parquets, "parquet", "auto", tmp_path)
    assert 0.9 <= _filled(parts, files, "parquet", 2 * MB) <= 1.05


@pytest.mark.parametrize("stem", ["a", "門市" + "銷售明細資料" * 6 + "長檔名測試"])
def test_long_and_short_file_names_fill_the_budget(tmp_path, stem):
    csvs = _csvs(tmp_path / "csv", [f"{stem}_{i}.csv" for i in range(6)], 10_000)
    parts, files = scan_parts([str(c) for c in csvs], "csv", "cp950", tmp_path)
    assert 0.9 <= _filled(parts, files, "csv", 2 * MB) <= 1.05


def test_csv_sample_starts_at_the_first_data_line(tmp_path):
    csv = fixtures.write_simple_csv(tmp_path / "simple.csv", n=3_000)
    parts, files = scan_parts([str(csv)], "csv", "cp950", tmp_path)
    plan = sample_plan(parts, files, 20_000, 100_000, 1_000)
    head, tail = plan.lines[files[0].name]
    frame = sample(parts, files, "csv", plan)
    assert plan.truncated and frame.height == head + tail
    numbers = frame[LINE_NO].to_list()
    assert numbers[0] == 2 and numbers[head - 1] == head + 1 and numbers[-1] == 3_001


# ── 試跑（execute 的 sample 模式）───────────────────────────────


def recipe_for(tmp_path: Path, paths: list[Path]) -> dict:
    return {
        "tablade_recipe": "1.1",
        "name": "many",
        "stages": [
            {
                "name": "s",
                "output_format": "parquet",
                "input": {
                    "files": [p.relative_to(tmp_path).as_posix() for p in paths],
                    "reader": "zip_lines",
                    "members": MEMBERS,
                    "encoding": "cp950",
                },
                "steps": [
                    {"id": "a", "type": "split_delimited", "delimiter": "|", "columns": ["日期"]}
                ],
            }
        ],
        "output": {},
    }


def test_trial_run_uses_the_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "SAMPLE_THRESHOLD", 1_000)
    paths = zips(tmp_path, "z", files=20, rows=2_000)
    recipe = parse_recipe(recipe_for(tmp_path, paths))
    _, report = execute(recipe, tmp_path, mode="sample", budget=200_000)
    assert report.mode == "sample"
    assert 20 * 25 <= report.sample_rows < 20 * 2_000
    text = format_report(report)
    assert f"共 {report.sample_rows:,} 行" in text and "10 萬" not in text and "100,000" not in text


def test_trial_run_is_not_a_sample_when_everything_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "SAMPLE_THRESHOLD", 1_000)
    paths = zips(tmp_path, "z", files=3, rows=500)
    recipe = parse_recipe(recipe_for(tmp_path, paths))
    _, report = execute(recipe, tmp_path, mode="sample", budget=256 * MB)
    assert report.mode == "eager" and report.sample_rows is None
    assert "試跑只用" not in format_report(report)


def test_fixed_head_and_tail_still_work(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "SAMPLE_THRESHOLD", 1_000)
    paths = zips(tmp_path, "z", files=3, rows=500)
    recipe = parse_recipe(recipe_for(tmp_path, paths))
    _, report = execute(recipe, tmp_path, mode="sample", head=10, tail=2)
    assert report.mode == "sample" and report.sample_rows == 36


# ── 預覽（Session）──────────────────────────────────────────


def preview_session(tmp_path: Path, paths: list[Path], monkeypatch, budget: int):
    from tablade import session as session_module
    from tablade.session import Session

    monkeypatch.setattr(session_module, "SAMPLE_THRESHOLD", 1_000)
    monkeypatch.setattr(session_module, "sample_budget", lambda: budget)
    s = Session()
    s.new(tmp_path / "many.recipe.json")
    s.add_files(paths)
    s.set_input({"reader": "zip_lines", "encoding": "cp950", "members": MEMBERS})
    return s


def test_preview_sample_stays_within_the_budget(tmp_path, monkeypatch):
    paths = zips(tmp_path, "z", files=20, rows=2_000)
    s = preview_session(tmp_path, paths, monkeypatch, 400_000)
    s.load_data()
    state = s.state()
    sample_info = state["input"]["sample"]
    assert state["input"]["sampled"] and state["preview"]["mode"] == "sample"
    assert sample_info["files"] == 20 and (sample_info["head"], sample_info["tail"]) == (
        100_000,
        1_000,
    )
    assert sample_info["rows"] == state["input"]["rows"] < 20 * 2_000
    frame = s.frame_at(-1)
    assert frame.estimated_size() <= 400_000 * 1.05
    ends = frame.group_by(SOURCE_FILE).agg(pl.col(LINE_NO).min(), pl.col(LINE_NO).max().alias("m"))
    assert ends.height == 20 and set(ends[LINE_NO]) == {1} and set(ends["m"]) == {2_000}


def test_preview_is_not_a_sample_when_every_file_fits(tmp_path, monkeypatch):
    paths = zips(tmp_path, "z", files=3, rows=500)
    s = preview_session(tmp_path, paths, monkeypatch, 256 * MB)
    s.load_data()
    state = s.state()
    assert not state["input"]["sampled"] and state["input"]["sample"] is None
    assert state["preview"]["mode"] == "full" and state["input"]["rows"] == 1_500


def test_reloading_unchanged_files_keeps_the_sample(tmp_path, monkeypatch):
    from tablade import session as session_module

    paths = zips(tmp_path, "z", files=10, rows=2_000)
    s = preview_session(tmp_path, paths, monkeypatch, 200_000)
    s.load_data()
    before = s.state()["input"]["sample"]["rows"]
    monkeypatch.setattr(session_module, "sample_budget", lambda: 50_000)
    s.load_data()
    assert s.state()["input"]["sample"]["rows"] == before


def test_stages_that_read_files_share_the_budget(tmp_path, monkeypatch):
    from tablade import session as session_module

    seen = []
    original = session_module.Session._load_stage

    def spy(self, stage, base, multi, tracker, budget):
        seen.append(budget)
        return original(self, stage, base, multi, tracker, budget)

    monkeypatch.setattr(session_module.Session, "_load_stage", spy)
    paths = zips(tmp_path, "z", files=4, rows=2_000)
    s = preview_session(tmp_path, paths, monkeypatch, 600_000)
    s.add_stage("第二", before=False)
    s.add_files(paths[:2])
    s.set_input({"reader": "zip_lines", "encoding": "cp950", "members": MEMBERS})
    s.load_data()
    assert seen == [300_000, 300_000]


def test_trial_before_export_has_a_budget(tmp_path, monkeypatch):
    from tablade import session as session_module

    calls = []
    original = session_module.execute

    def spy(*args, **kwargs):
        frame, report = original(*args, **kwargs)
        calls.append((kwargs.get("mode"), kwargs.get("budget"), report))
        return frame, report

    monkeypatch.setattr(session_module, "execute", spy)
    monkeypatch.setattr(run, "SAMPLE_THRESHOLD", 1_000)
    monkeypatch.setattr(session_module, "TRIAL_BYTES", 100_000)
    paths = zips(tmp_path, "z", files=20, rows=3_000)
    s = preview_session(tmp_path, paths, monkeypatch, 256 * MB)
    s.export()
    trial = [report for mode, budget, report in calls if mode == "sample"]
    assert len(trial) == 1 and [b for m, b, _ in calls if m == "sample"] == [100_000]
    assert trial[0].sample_rows < 20 * session_module.TRIAL_HEAD


# ── 檢查檔案有沒有變：一次呼叫只做一次 ───────────────────────────


def count_fingerprints(monkeypatch) -> list[int]:
    from tablade.session import Session

    calls = [0]
    original = Session._compute_source_key

    def counted(self, stage):
        calls[0] += 1
        return original(self, stage)

    monkeypatch.setattr(Session, "_compute_source_key", counted)
    return calls


def test_file_fingerprint_is_checked_once_per_call(tmp_path, monkeypatch):
    paths = zips(tmp_path, "z", files=20, rows=300)
    s = preview_session(tmp_path, paths, monkeypatch, 256 * MB)
    s.add_step("trim_clean")
    s.load_data()
    calls = count_fingerprints(monkeypatch)
    s.state()
    assert calls[0] == 1  # 之前一次 state() 會檢查 10 次以上
    calls[0] = 0
    s.select(0)
    s.state()
    assert calls[0] == 1  # select 沒有用到；state 一次


def test_changes_are_still_noticed_on_the_next_call(tmp_path, monkeypatch):
    import os

    paths = zips(tmp_path, "z", files=3, rows=300)
    s = preview_session(tmp_path, paths, monkeypatch, 256 * MB)
    s.load_data()
    assert s.state()["input"]["loaded"]
    stat = paths[1].stat()
    os.utime(paths[1], ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    state = s.state()
    assert not state["input"]["loaded"] and state["input"]["stale"]


def test_only_the_changed_stage_needs_another_load(tmp_path, monkeypatch):
    import os

    first = zips(tmp_path, "a", files=2, rows=300, seed=1)
    second = zips(tmp_path, "b", files=2, rows=300, seed=2)
    s = preview_session(tmp_path, first, monkeypatch, 256 * MB)
    s.add_stage("第二", before=False)
    s.add_files(second)
    s.set_input({"reader": "zip_lines", "encoding": "cp950", "members": MEMBERS})
    s.load_data()
    stat = second[0].stat()
    os.utime(second[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert s.state()["input"]["stale"]
    s.select_stage(0)
    state = s.state()
    assert state["input"]["loaded"] and not state["input"]["stale"]


def test_opening_another_recipe_does_not_reuse_fingerprints(tmp_path, monkeypatch):
    from tablade.session import Session

    a = zips(tmp_path, "a", files=2, rows=300, seed=1)
    b = zips(tmp_path, "b", files=2, rows=500, seed=2)
    one = preview_session(tmp_path, a, monkeypatch, 256 * MB)
    one.save_recipe()
    two = Session()
    two.new(tmp_path / "two.recipe.json")
    two.add_files(b)
    two.set_input({"reader": "zip_lines", "encoding": "cp950", "members": MEMBERS})
    two.save_recipe()
    s = Session()
    s.load_recipe(tmp_path / "many.recipe.json")
    s.load_data()
    assert s.state()["input"]["rows"] == 600
    s.load_recipe(tmp_path / "two.recipe.json")
    assert not s.state()["input"]["loaded"]
    s.load_data()
    assert s.state()["input"]["rows"] == 1_000
