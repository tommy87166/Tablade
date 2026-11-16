"""JS bridge：pywebview 把這個類別的公開方法暴露成 ``window.pywebview.api.xxx()``。

只做參數轉換、檔案對話框與錯誤包裝，狀態與邏輯都在 ``session.py``（DESIGN.md §16.4）。
- 每個方法都回傳 ``{"ok": True, ...}`` 或 ``{"ok": False, "error": "...", "issues"?: [...]}``，不讓例外丟到 JS
- 會改變 recipe 的方法都直接回傳新的 ``state``，前端一次呼叫就能更新畫面
- 前端拿不到也不能指定任意檔案路徑：開檔、存檔一律由這裡開對話框（DESIGN.md §16.4）
- 底線開頭的屬性與方法不會暴露給 JS
"""

from __future__ import annotations

import contextlib
import functools
import platform
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from . import __version__
from .recipe.schema import RECIPE_VERSION, RecipeError
from .selftest import run_selftest
from .session import Session, catalog

DATA_TYPES = ("資料檔 (*.csv;*.txt;*.tsv;*.prn;*.dat)", "所有檔案 (*.*)")
RECIPE_TYPES = ("Tablade recipe (*.json)", "所有檔案 (*.*)")
PARQUET_TYPES = ("Parquet (*.parquet)",)


def _wrap(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(self: Api, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return {"ok": True, **fn(self, *args, **kwargs)}
        except RecipeError as exc:
            return {"ok": False, "error": str(exc), "issues": exc.issues}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self._push_progress(None)

    return wrapper


def _paths(picked: Any) -> list[Path]:
    if not picked:
        return []
    if isinstance(picked, str):
        return [Path(picked)]
    return [Path(p) for p in picked]


class Api:
    def __init__(self, smoke: bool = False) -> None:
        self._window: Any = None
        self._smoke = smoke
        self._session = Session()
        self._exit_code = 1 if smoke else 0
        self._done = threading.Event()
        self._smoke_dir: Path | None = None
        self._progress_at = 0.0
        self._progress_stage: str | None = None
        self._session.progress = self._push_progress

    def _attach(self, window: Any) -> None:
        self._window = window

    def _push_progress(self, progress: dict[str, Any] | None) -> None:
        """讀檔、轉碼、匯出的進度推給前端（pywebview 的 window.state；最多每 0.2 秒一次）。"""
        if self._window is None:
            return
        now = time.monotonic()
        stage = None if progress is None else f"{progress['stage']}:{progress['file']}"
        if progress is not None and stage == self._progress_stage and now - self._progress_at < 0.2:
            return
        self._progress_at, self._progress_stage = now, stage
        with contextlib.suppress(Exception):  # 視窗關閉中
            self._window.state.progress = progress

    def _dialog(self, kind: str, **kwargs: Any) -> list[Path]:
        import webview

        dialog = {"open": webview.FileDialog.OPEN, "save": webview.FileDialog.SAVE}[kind]
        base = self._session.base_dir
        if base and "directory" not in kwargs:
            kwargs["directory"] = str(base)
        return _paths(self._window.create_file_dialog(dialog, **kwargs))

    def _state(self, **extra: Any) -> dict[str, Any]:
        return {"state": self._session.state(), **extra}

    # ── 啟動與自我檢測 ─────────────────────────────────────────

    @_wrap
    def ready(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "recipe_version": RECIPE_VERSION,
            "polars": pl.__version__,
            "platform": platform.system(),
            "smoke": self._smoke,
            "catalog": catalog(),
        }

    @_wrap
    def selftest(self) -> dict[str, Any]:
        return {"result": run_selftest()}

    @_wrap
    def smoke_done(self, passed: bool) -> dict[str, Any]:
        """``--smoke`` 模式下由 JS 回報結果後關閉視窗。"""
        if self._smoke:
            self._exit_code = 0 if passed else 1
            self._done.set()
            if self._smoke_dir:
                shutil.rmtree(self._smoke_dir, ignore_errors=True)
            threading.Timer(0.2, self._window.destroy).start()
        return {}

    @_wrap
    def smoke_example(self) -> dict[str, Any]:
        """``--smoke`` 模式專用：用假資料載入範例 recipe，讓前端走一次完整流程。"""
        if not self._smoke:
            raise RuntimeError("只有 --smoke 模式可以使用")
        from .app import resource_dir
        from .fixtures import write_all

        self._smoke_dir = Path(tempfile.mkdtemp(prefix="tablade-smoke-"))
        write_all(self._smoke_dir / "data")
        recipe = resource_dir() / "examples" / "sales_detail.recipe.json"
        shutil.copy(recipe, self._smoke_dir / recipe.name)
        issues = self._session.load_recipe(self._smoke_dir / recipe.name)
        return self._state(issues=issues)

    # ── 畫面狀態 ───────────────────────────────────────────────

    @_wrap
    def state(self) -> dict[str, Any]:
        return self._state()

    @_wrap
    def rows(self, step: int, offset: int, limit: int) -> dict[str, Any]:
        return self._session.rows(int(step), int(offset), int(limit))

    @_wrap
    def find_row(self, step: int, source_file: str, line_no: int) -> dict[str, Any]:
        return {"row": self._session.find_row(int(step), str(source_file), int(line_no))}

    # ── recipe 檔 ──────────────────────────────────────────────

    @_wrap
    def new_recipe(self) -> dict[str, Any]:
        self._session.new()
        return self._state()

    @_wrap
    def open_recipe(self) -> dict[str, Any]:
        picked = self._dialog("open", allow_multiple=False, file_types=RECIPE_TYPES)
        if not picked:
            return {"cancelled": True, **self._state()}
        issues = self._session.load_recipe(picked[0])
        return {"cancelled": False, "issues": issues, **self._state()}

    @_wrap
    def save_recipe(self) -> dict[str, Any]:
        if self._session.recipe_path is None:
            return self.save_recipe_as.__wrapped__(self)
        path = self._session.save_recipe()
        return {"cancelled": False, "path": str(path), **self._state()}

    @_wrap
    def save_recipe_as(self) -> dict[str, Any]:
        name = (self._session.meta.get("name") or "recipe").strip() or "recipe"
        picked = self._dialog("save", save_filename=f"{name}.recipe.json", file_types=RECIPE_TYPES)
        if not picked:
            return {"cancelled": True, **self._state()}
        target = picked[0]
        if not target.name.endswith(".json"):
            target = target.with_name(target.name + ".recipe.json")
        path = self._session.save_recipe(target)
        return {"cancelled": False, "path": str(path), **self._state()}

    # ── 資料 ───────────────────────────────────────────────────

    @_wrap
    def open_data(self) -> dict[str, Any]:
        picked = self._dialog("open", allow_multiple=True, file_types=DATA_TYPES)
        if not picked:
            return {"cancelled": True, **self._state()}
        self._session.open_data(picked)
        return {"cancelled": False, **self._state()}

    @_wrap
    def set_input(self, values: dict[str, Any]) -> dict[str, Any]:
        self._session.set_input(values)
        return self._state()

    @_wrap
    def set_output(self, values: dict[str, Any]) -> dict[str, Any]:
        self._session.set_output(values)
        return self._state()

    @_wrap
    def set_meta(self, values: dict[str, Any]) -> dict[str, Any]:
        self._session.set_meta(values)
        return self._state()

    # ── 步驟 ───────────────────────────────────────────────────

    @_wrap
    def add_step(self, type_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        index = self._session.add_step(type_name, context or {})
        return self._state(index=index)

    @_wrap
    def update_step(self, index: int, values: dict[str, Any]) -> dict[str, Any]:
        self._session.update_step(int(index), values)
        return self._state()

    @_wrap
    def move_step(self, index: int, to: int) -> dict[str, Any]:
        self._session.move_step(int(index), int(to))
        return self._state()

    @_wrap
    def toggle_step(self, index: int) -> dict[str, Any]:
        self._session.toggle_step(int(index))
        return self._state()

    @_wrap
    def delete_step(self, index: int) -> dict[str, Any]:
        self._session.delete_step(int(index))
        return self._state()

    @_wrap
    def select_step(self, index: int) -> dict[str, Any]:
        self._session.select(int(index))
        return self._state()

    @_wrap
    def undo(self) -> dict[str, Any]:
        self._session.undo()
        return self._state()

    @_wrap
    def redo(self) -> dict[str, Any]:
        self._session.redo()
        return self._state()

    # ── 輸出 ───────────────────────────────────────────────────

    @_wrap
    def export_parquet(self) -> dict[str, Any]:
        default = Path(self._session.output.get("path") or "result.parquet")
        base = self._session.base_dir or Path.home()
        folder = base / default.parent if (base / default.parent).is_dir() else base
        picked = self._dialog(
            "save", directory=str(folder), save_filename=default.name, file_types=PARQUET_TYPES
        )
        if not picked:
            return {"cancelled": True}
        target = (
            picked[0]
            if picked[0].suffix.lower() == ".parquet"
            else picked[0].with_suffix(".parquet")
        )
        return {"cancelled": False, **self._session.export(target)}

    @_wrap
    def download_skill(self) -> dict[str, Any]:
        """產生與這個版本一致的 AI skill zip，開存檔對話框存起來（DESIGN.md §17.7）。"""
        from .skill import build_zip, skill_filename

        data = build_zip()
        picked = self._dialog("save", save_filename=skill_filename(), file_types=("zip (*.zip)",))
        if not picked:
            return {"cancelled": True}
        target = picked[0] if picked[0].suffix.lower() == ".zip" else picked[0].with_suffix(".zip")
        target.write_bytes(data)
        return {
            "cancelled": False,
            "path": str(target),
            "bytes": len(data),
            "version": RECIPE_VERSION,
        }

    @_wrap
    def error_report(self) -> dict[str, Any]:
        return {"text": self._session.error_report()}
