"""各檔分開處理（DESIGN.md §8）：分檔步驟一次只看到一個來源檔，串流＝整份讀進記憶體。"""

import json
from pathlib import Path
from typing import Literal

import polars as pl
import pytest

from tablade.engine.run import RunError, apply_parts, apply_step, execute
from tablade.fixtures import write_all, write_many
from tablade.ingest.sources import (
    _scan_one,
    display_name,
    prepare,
    resolve,
    sample,
    scan,
    scan_parts,
)
from tablade.recipe.schema import RECIPE_VERSION, parse_recipe
from tablade.steps import all_steps, get
from tablade.steps.base import LINE_NO, SOURCE_FILE, Step

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"


def frame(data: dict, files: list[str]) -> pl.DataFrame:
    line_no, counter = [], {}
    for f in files:
        counter[f] = counter.get(f, 0) + 1
        line_no.append(counter[f])
    return pl.DataFrame({SOURCE_FILE: files, LINE_NO: line_no, **data}).with_columns(
        pl.col(LINE_NO).cast(pl.Int64)
    )


def make(type_name: str, **params):
    return get(type_name).model_validate({"id": "t", "type": type_name, **params})


def recipe_of(pattern: str, reader: str, steps: list[dict]):
    return parse_recipe(
        {
            "tablade_recipe": RECIPE_VERSION,
            "stages": [
                {
                    "name": "預設階段",
                    "input": {"files": [pattern], "reader": reader, "encoding": "auto"},
                    "steps": steps,
                    "output_format": "parquet",
                }
            ],
            "output": {"keep_system_columns": True},
        }
    )


def both(recipe, base: Path):
    """eager 與 stream 各跑一次，結果必須完全相同。"""
    _, eager = execute(recipe, base, output=base / "e.parquet", mode="eager")
    _, streamed = execute(recipe, base, output=base / "s.parquet", mode="stream")
    a = pl.read_parquet(base / "e.parquet")
    b = pl.read_parquet(base / "s.parquet")
    assert a.equals(b)
    return a, eager, streamed


def write_lines(directory: Path, files: dict[str, list[str] | None]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, lines in files.items():
        path = directory / name
        path.write_bytes(b"" if lines is None else ("\n".join(lines) + "\n").encode())


# ── 規則：分檔步驟一次只看到一個來源檔 ─────────────────────────────

PER_FILE = [
    ("remove_top_rows", {"n": 1}),
    ("remove_bottom_rows", {"n": 1}),
    ("promote_headers", {"names": ["h"]}),
    ("fill_down", {"columns": ["line"]}),
]


@pytest.mark.parametrize(("type_name", "params"), PER_FILE)
def test_per_file_step_equals_each_file_alone(type_name, params):
    files = ["a"] * 3 + ["b"] * 4 + ["c"] * 2
    df = frame({"line": ["h", "", "x", "h", "1", "", "2", "h", "3"]}, files)
    step = make(type_name, **params)
    whole = apply_step(step, df)[0]
    each = pl.concat([apply_step(step, df.filter(pl.col(SOURCE_FILE) == f))[0] for f in "abc"])
    assert whole.equals(each)


def test_per_file_steps_only_ever_see_one_file(tmp_path, monkeypatch):
    seen = []
    for cls in all_steps():
        if cls.per_file and not cls.combines:

            def spy(self, lf, _original=cls.apply):
                seen.append(lf.select(pl.col(SOURCE_FILE).n_unique()).collect().item())
                return _original(self, lf)

            monkeypatch.setattr(cls, "apply", spy)
    write_all(tmp_path / "data")
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["stages"][0]["steps"].insert(2, {"id": "m", "type": "merge_files"})
    recipe = parse_recipe(data)
    both(recipe, tmp_path)
    assert seen and max(seen) == 1


# ── 讀檔：欄位對齊、空檔、符號連結 ────────────────────────────────


def test_csv_files_with_different_columns(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.csv").write_text("x,y\n1,A\n2,\n", encoding="utf-8")
    (d / "b.csv").write_text("y,z\nB,p|q\n,r|s\n", encoding="utf-8")
    (d / "c.csv").write_bytes(b"")
    (d / "d.csv").write_text("x\n", encoding="utf-8")

    parts, _ = scan_parts("data/*.csv", "csv", "auto", tmp_path)
    assert len({tuple(lf.collect_schema().items()) for _, lf in parts}) == 1
    reference = pl.concat(
        [
            _scan_one(prepare(p, "auto"), "csv", display_name(p, tmp_path))[0]
            for p in resolve("data/*.csv", tmp_path)
        ],
        how="diagonal_relaxed",
    ).collect()
    assert scan("data/*.csv", "csv", "auto", tmp_path)[0].collect().equals(reference)

    recipe = recipe_of(
        "data/*.csv",
        "csv",
        [
            {"id": "f", "type": "fill_down", "columns": ["y"]},
            {
                "id": "s",
                "type": "split_column",
                "column": "z",
                "delimiter": "|",
                "names": ["z1", "z2"],
            },
        ],
    )
    out, _, _ = both(recipe, tmp_path)
    assert out["y"].to_list() == ["A", "A", "B", "B"]  # 不會把 a 的值填進 b
    assert out["x"].to_list() == ["1", "2", None, None]
    assert out["z2"].to_list() == [None, None, "q", "s"]


def test_empty_text_files_mixed_in(tmp_path):
    write_lines(
        tmp_path / "data",
        {"a.txt": ["t", "h", "1", "", "2", "end"], "b.txt": None, "c.txt": ["t", "h", "3", "end"]},
    )
    steps = [
        {"id": "s1", "type": "remove_top_rows", "n": 1},
        {"id": "s2", "type": "remove_bottom_rows", "n": 1},
        {"id": "s3", "type": "promote_headers", "names": ["v"]},
        {"id": "s4", "type": "fill_down", "columns": ["v"]},
    ]
    out, _, _ = both(recipe_of("data/*.txt", "text_lines", steps), tmp_path)
    assert out["v"].to_list() == ["1", "1", "2", "3"]
    empty, _, _ = both(recipe_of("data/b.txt", "text_lines", steps), tmp_path)
    assert empty.height == 0 and empty.columns == [SOURCE_FILE, LINE_NO, "v"]


def test_duplicate_paths_are_read_once(tmp_path):
    write_lines(tmp_path, {"a.txt": ["1"]})
    (tmp_path / "b.txt").symlink_to(tmp_path / "a.txt")
    parts, files = scan_parts("*.txt", "text_lines", "auto", tmp_path)
    assert len(parts) == len(files) == 1


def test_sample_takes_head_and_tail_of_many_files(tmp_path):
    write_lines(tmp_path, {f"f{i}.txt": [f"{i}-{n}" for n in range(1, 11)] for i in range(5)})
    parts, files = scan_parts("*.txt", "text_lines", "auto", tmp_path)
    got = sample(parts, files, "text_lines", head=2, tail=1)
    assert got["line"].to_list() == [x for i in range(5) for x in (f"{i}-1", f"{i}-2", f"{i}-10")]


# ── 某個檔中途被篩光、合併來源檔 ─────────────────────────────────


def test_file_emptied_by_filter(tmp_path):
    write_lines(
        tmp_path / "data", {"a.txt": ["keep h", "keep 1", "keep 2"], "b.txt": ["drop h", "drop 1"]}
    )
    steps = [
        {
            "id": "f",
            "type": "filter",
            "conditions": [{"column": "line", "op": "starts_with", "value": "keep"}],
        },
        {"id": "s", "type": "split_delimited", "delimiter": " ", "columns": ["k", "v"]},
        {"id": "h", "type": "promote_headers", "names": ["k", "v"]},
        {"id": "m", "type": "merge_files", "order": ["data/b.txt"]},
    ]
    out, _, _ = both(recipe_of("data/*.txt", "text_lines", steps), tmp_path)
    assert out["v"].to_list() == ["1", "2"] and out[LINE_NO].to_list() == [1, 2]


@pytest.mark.parametrize(
    ("steps", "lines", "sources"),
    [
        # 合併放第一步、順序裡有不存在的檔，之後的「前 N 列」對整份計算
        (
            [
                {"id": "m", "type": "merge_files", "order": ["data/c.txt", "data/x.txt"]},
                {"id": "t", "type": "remove_top_rows", "n": 1},
            ],
            ["c2", "a1", "a2", "b1", "b2"],
            {"合併"},
        ),
        # 連續兩次合併
        (
            [
                {"id": "m", "type": "merge_files"},
                {"id": "n", "type": "merge_files", "name": "全部"},
            ],
            ["a1", "a2", "b1", "b2", "c1", "c2"],
            {"全部"},
        ),
        # 停用的合併：之後仍然分檔
        (
            [
                {"id": "m", "type": "merge_files", "enabled": False},
                {"id": "t", "type": "remove_top_rows", "n": 1},
            ],
            ["a2", "b2", "c2"],
            {"data/a.txt", "data/b.txt", "data/c.txt"},
        ),
    ],
)
def test_merge_variants(tmp_path, steps, lines, sources):
    write_lines(tmp_path / "data", {f"{x}.txt": [f"{x}1", f"{x}2"] for x in "abc"})
    out, _, _ = both(recipe_of("data/*.txt", "text_lines", steps), tmp_path)
    assert out["line"].to_list() == lines
    assert set(out[SOURCE_FILE]) == sources
    if sources == {"合併"} or sources == {"全部"}:
        assert out[LINE_NO].to_list() == sorted(out[LINE_NO].to_list())


def test_reordered_merge_scans_each_file_once(tmp_path):
    write_lines(tmp_path / "data", {f"{x}.txt": [f"{x}1"] for x in "abc"})
    parts, _ = scan_parts("data/*.txt", "text_lines", "auto", tmp_path)
    [(name, merged)] = apply_parts(make("merge_files", order=["data/c.txt"]), parts)
    plan = merged.explain()
    assert name == "合併"
    for x in "abc":
        assert sum(1 for line in plan.splitlines() if "SCAN" in line and f"{x}.txt" in line) == 1


# ── 很多個檔 ───────────────────────────────────────────────────


def test_many_files_stream_equals_eager_and_warns_once(tmp_path):
    paths = write_many(tmp_path / "data", 300, rows=5)
    bad = paths[123]
    bad.write_text(
        bad.read_text(encoding="utf-8").replace("日期,門市", "交易日期,門市", 1), encoding="utf-8"
    )
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["stages"][0]["steps"] = data["stages"][0]["steps"][
        :-1
    ]  # 不篩選，串流模式的警告才看得到同一列
    out, eager, streamed = both(parse_recipe(data), tmp_path)
    keys = out.select(SOURCE_FILE, LINE_NO).rows()
    assert keys == sorted(keys)
    warned = [
        [(w.source_file, w.line_no) for s in r.steps for w in s.warnings] for r in (eager, streamed)
    ]
    assert warned[0] == warned[1] and len(warned[0]) == 1
    assert warned[0][0][0] == display_name(bad, tmp_path)


# ── 各檔處理後欄位不一樣時，錯誤要說清楚 ──────────────────────────


class _Uneven(Step):
    """測試用：只在來源檔 b 多加一欄。"""

    type: Literal["uneven_test"] = "uneven_test"
    per_file = True

    def apply(self, lf):
        name = lf.select(pl.col(SOURCE_FILE).first()).collect().item()
        return lf.with_columns(pl.lit(1).alias("extra")) if name == "b" else lf


def test_parts_with_different_columns_raise_a_clear_error():
    df = frame({"line": ["1", "2"]}, ["a", "b"])
    with pytest.raises(RunError, match="來源檔「b」的欄位"):
        apply_step(_Uneven(id="u"), df)


def test_scan_parts_keep_files_in_name_order(tmp_path):
    write_lines(tmp_path, {"b.txt": ["b"], "a.txt": ["a"], "c.txt": ["c"]})
    parts, files = scan_parts("*.txt", "text_lines", "auto", tmp_path)
    assert [name for name, _ in parts] == [f.path for f in files] == ["a.txt", "b.txt", "c.txt"]
