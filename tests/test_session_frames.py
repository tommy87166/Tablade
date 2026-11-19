"""預覽只存數字、不存表（docs/memory-improve.md 第 2 部分）：放掉的表要用時重算，結果和全部留著時一樣。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from polars.testing import assert_frame_equal

from tablade.fixtures import write_all
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.recipe.json"))


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("frames")
    write_all(base / "data")
    for recipe in EXAMPLES:
        shutil.copy(recipe, base / recipe.name)
    return base


def keep_everything(s: Session) -> Session:
    """對照組：每一步的表都留著（原本的做法）。"""
    s._kept_steps = lambda si: set(range(len(s.stages[si]["steps"])))  # type: ignore[method-assign]
    return s


def open_pair(folder: Path, name: str) -> tuple[Session, Session]:
    pair = []
    for lean in (True, False):
        s = Session()
        s.load_recipe(folder / name)
        if not lean:
            keep_everything(s)
        s.load_data()
        pair.append(s)
    return pair[0], pair[1]


def view(s: Session) -> str:
    """畫面上看得到的：步驟清單、階段、預覽（不含記憶體這類每次不一樣的數字）。"""
    state = s.state()
    return json.dumps({k: state[k] for k in ("steps", "stages", "preview", "current")}, default=str)


def same(lean: Session, full: Session) -> None:
    assert view(lean) == view(full)
    current = lean.current
    for k in range(len(lean.stages)):
        lean.select_stage(k)
        full.select_stage(k)
        for i in range(-1, len(lean.steps)):
            a, b = lean.frame_at(i), full.frame_at(i)
            assert (a is None) == (b is None), (k, i)
            if a is not None:
                assert_frame_equal(a, b)
    lean.select_stage(current)
    full.select_stage(current)


def kept(s: Session) -> list[int]:
    return [i for i, c in enumerate(s._computed[s.stage["uid"]]) if c.frame is not None]


@pytest.mark.parametrize("recipe", [p.name for p in EXAMPLES])
def test_rebuilt_frames_match_kept_frames(folder, recipe):
    lean, full = open_pair(folder, recipe)
    same(lean, full)
    for k in range(len(lean.stages)):
        lean.select_stage(k)
        full.select_stage(k)
        for i in range(len(lean.steps)):
            lean.select(i)
            full.select(i)
            same(lean, full)


def test_only_the_needed_frames_are_kept(folder):
    lean, full = open_pair(folder, "sales_report.recipe.json")
    n = len(lean.steps)
    assert n >= 6
    lean.select(2)
    lean.state()
    assert kept(lean) == [n - 1]  # 先放掉：畫面上那一步等資料格要用時才重算
    lean.frame_at(2)
    assert kept(lean) == [1, 2, n - 1]  # 重算時順便留著它的輸入
    lean.frame_at(4)  # 重算出來，但不留著
    assert kept(lean) == [1, 2, n - 1]
    lean.select(-1)
    assert kept(lean) == [n - 1]
    lean.select(n)  # 輸出資料：最後一步
    lean.frame_at(n - 1)
    assert kept(lean) == [n - 1]
    full.select(n)
    assert view(lean) == view(full)


def test_paging_columns_and_rows_on_a_released_step(folder):
    lean, full = open_pair(folder, "sales_report.recipe.json")
    for s in (lean, full):
        s.select(0)
    assert lean._computed[lean.stage["uid"]][3].frame is None
    assert lean.rows(3, 0, 20) == full.rows(3, 0, 20)
    assert lean.column_info(3) == full.column_info(3)
    first = full.frame_at(3).row(0, named=True)
    args = (3, first["__source_file"], first["__line_no"])
    assert lean.find_row(*args) == full.find_row(*args)


def test_edits_after_releasing_frames(folder):
    lean, full = open_pair(folder, "sales_report.recipe.json")
    n = len(lean.steps)
    for s in (lean, full):
        s.select(0)
        s.state()
    edits = [
        lambda s: s.select(3),
        lambda s: s.add_step("trim_clean"),
        lambda s: s.toggle_step(2),
        lambda s: s.toggle_step(2),
        lambda s: s.move_step(5, 6),
        lambda s: s.select(1),
        lambda s: s.delete_step(n),
        lambda s: s.undo(),
        lambda s: s.undo(),
        lambda s: s.redo(),
        lambda s: s.select(n - 1),
        lambda s: s.update_step(1, {"columns": ["門市"]}),
    ]
    for edit in edits:
        for s in (lean, full):
            edit(s)
        same(lean, full)


def test_steps_after_an_error_stay_blocked(folder):
    lean, full = open_pair(folder, "sales_report.recipe.json")
    for s in (lean, full):
        s.select(0)
        s.update_step(1, {"columns": ["沒有這一欄"]})
    same(lean, full)
    steps = lean.state()["steps"]
    assert steps[1]["status"] == "error" and steps[2]["blocked_by"] == 1


def test_stages_and_joins_after_releasing(folder):
    lean, full = open_pair(folder, "sales_combined.recipe.json")
    assert len(lean.stages) > 1
    for k in range(len(lean.stages)):
        for s in (lean, full):
            s.select_stage(k)
            s.select(0)
        same(lean, full)
    for s in (lean, full):
        s.select_stage(len(s.stages) - 1)
        s.full_run(0)
    same(lean, full)


@pytest.mark.parametrize("recipe", [p.name for p in EXAMPLES])
def test_apply_step_without_warnings_gives_the_same_frame(folder, recipe):
    from tablade.engine.run import apply_step
    from tablade.steps.registry import get

    _, full = open_pair(folder, recipe)
    checked = 0
    for si, stage in enumerate(full.stages):
        full.select_stage(si)
        for i, raw in enumerate(stage["steps"]):
            source = full.frame_at(i - 1)
            if source is None or raw.get("enabled", True) is False:
                continue
            model = get(raw["type"]).model_validate(raw)
            tables = {n: r[0] for n, r in full._step_refs(si, raw).items()}
            frame = apply_step(model, source, tables=tables)[0]
            quiet = apply_step(model, source, tables=tables, warnings=False)
            assert_frame_equal(quiet[0], frame)
            assert quiet[1:] == (0, [])
            checked += 1
    assert checked > 0
