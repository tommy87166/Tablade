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

DATA_TYPES = (
    "資料檔 (*.csv;*.txt;*.tsv;*.prn;*.dat;*.xlsx;*.xlsm;*.xls;*.parquet)",
    "Excel (*.xlsx;*.xlsm;*.xlsb;*.xls;*.ods)",
    "Parquet (*.parquet)",
    "所有檔案 (*.*)",
)
RECIPE_TYPES = ("Tablade Recipe (*.json)", "所有檔案 (*.*)")
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
    def check_update(self) -> dict[str, Any]:
        """啟動時查 GitHub 上的最新版號（只讀版號，不下載）；打包時的 smoke test 不連線。"""
        if self._smoke:
            return {"update": None}
        from .update import check_latest

        return {"update": check_latest()}

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
        self._session.load_data()
        return self._state(issues=issues)

    # ── 畫面狀態 ───────────────────────────────────────────────

    @_wrap
    def state(self) -> dict[str, Any]:
        return self._state()

    @_wrap
    def rows(self, step: int, offset: int, limit: int) -> dict[str, Any]:
        return self._session.rows(int(step), int(offset), int(limit))

    @_wrap
    def column_info(self, step: int) -> dict[str, Any]:
        return self._session.column_info(int(step))

    @_wrap
    def find_row(
        self,
        step: int,
        source_file: str,
        line_no: int,
        sheet: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        found = self._session.find_row(
            int(step),
            str(source_file),
            int(line_no),
            str(sheet) if sheet else None,
            str(stage) if stage else None,
        )
        return {"row": found}

    # ── recipe 檔 ──────────────────────────────────────────────

    @_wrap
    def new_recipe(self) -> dict[str, Any]:
        """新增 Recipe：先跳存檔視窗命名，確定後立刻寫出空的 Recipe 檔。"""
        picked = self._dialog("save", save_filename="未命名.recipe.json", file_types=RECIPE_TYPES)
        if not picked:
            return {"cancelled": True, **self._state()}
        target = picked[0]
        if not target.name.endswith(".json"):
            target = target.with_name(target.name + ".recipe.json")
        path = self._session.new(target)
        return {"cancelled": False, "path": str(path), **self._state()}

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
    def add_files(self) -> dict[str, Any]:
        """「瀏覽檔案」：選的檔加進檔案清單（不會自動讀檔）。"""
        picked = self._dialog("open", allow_multiple=True, file_types=DATA_TYPES)
        if not picked:
            return {"cancelled": True, **self._state()}
        self._session.add_files(picked)
        return {"cancelled": False, **self._state()}

    @_wrap
    def add_file_entry(self, text: str) -> dict[str, Any]:
        self._session.add_file_entry(str(text))
        return self._state()

    @_wrap
    def remove_file(self, index: int) -> dict[str, Any]:
        self._session.remove_file(int(index))
        return self._state()

    @_wrap
    def load_data(self) -> dict[str, Any]:
        """「載入資料」：讀檔案清單裡的檔，產生預覽。"""
        self._session.load_data()
        return self._state()

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

    # ── 階段 ───────────────────────────────────────────────────

    @_wrap
    def add_stage(self, name: str, before: bool = True) -> dict[str, Any]:
        """新增階段（一定要命名）：預設插在目前階段的前面。"""
        index = self._session.add_stage(str(name), bool(before))
        return self._state(index=index)

    @_wrap
    def rename_stage(self, index: int, name: str) -> dict[str, Any]:
        self._session.rename_stage(int(index), str(name))
        return self._state()

    @_wrap
    def move_stage(self, index: int, to: int) -> dict[str, Any]:
        self._session.move_stage(int(index), int(to))
        return self._state()

    @_wrap
    def delete_stage(self, index: int) -> dict[str, Any]:
        self._session.delete_stage(int(index))
        return self._state()

    @_wrap
    def select_stage(self, index: int) -> dict[str, Any]:
        self._session.select_stage(int(index))
        return self._state()

    @_wrap
    def full_run(self, index: int) -> dict[str, Any]:
        """「完整跑一次」：用全部資料跑到這個階段，預覽改用完整的結果（結合的右表）。"""
        self._session.full_run(int(index))
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
    def refresh_values(
        self, index: int, field: str, values: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """「重新從資料讀取」：參數的值從資料重新讀出（例如樞紐要變成欄的值）。"""
        found = self._session.refresh_values(int(index), str(field), values)
        return self._state(values=found)

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
        """「處理並匯出資料」：已載入資料時才開存檔對話框選輸出位置，再處理全部資料。"""
        self._session.require_loaded()
        folder, name = self._session.export_suggestion()
        picked = self._dialog(
            "save", directory=str(folder), save_filename=name, file_types=PARQUET_TYPES
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
