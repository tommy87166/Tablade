"""JS bridge：pywebview 把這個類別的公開方法暴露成 ``window.pywebview.api.xxx()``。

只做參數轉換與錯誤包裝，邏輯放在引擎（DESIGN.md §4）。
每個方法都回傳 ``{"ok": True, ...}`` 或 ``{"ok": False, "error": "..."}``，不讓例外丟到 JS。
底線開頭的屬性與方法不會暴露給 JS。
"""

from __future__ import annotations

import functools
import platform
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl

from . import __version__
from .export.parquet import write_parquet
from .ingest.csv_reader import read_csv
from .selftest import run_selftest

PREVIEW_ROWS = 100


def _wrap(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return {"ok": True, **fn(*args, **kwargs)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return wrapper


class Api:
    def __init__(self, smoke: bool = False) -> None:
        self._window: Any = None
        self._smoke = smoke
        self._lock = threading.Lock()
        self._frame: pl.DataFrame | None = None
        self._source: Path | None = None
        self._exit_code = 1 if smoke else 0
        self._done = threading.Event()

    def _attach(self, window: Any) -> None:
        self._window = window

    # ── 給 JS 呼叫的方法 ─────────────────────────────────────

    @_wrap
    def ready(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "polars": pl.__version__,
            "platform": platform.system(),
            "smoke": self._smoke,
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
            threading.Timer(0.2, self._window.destroy).start()
        return {}

    @_wrap
    def open_csv(self) -> dict[str, Any]:
        import webview

        picked = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("CSV 檔 (*.csv;*.txt)", "所有檔案 (*.*)"),
        )
        if not picked:
            return {"cancelled": True}
        path = Path(picked[0])
        result = read_csv(path)
        with self._lock:
            self._frame = result.frame
            self._source = path
        return {
            "cancelled": False,
            "file": path.name,
            "encoding": result.encoding,
            "bytes": result.bytes_read,
            **_preview(result.frame),
        }

    @_wrap
    def export_parquet(self) -> dict[str, Any]:
        import webview

        with self._lock:
            frame, source = self._frame, self._source
        if frame is None or source is None:
            raise RuntimeError("請先開啟 CSV")
        picked = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(source.parent),
            save_filename=source.with_suffix(".parquet").name,
        )
        if not picked:
            return {"cancelled": True}
        target = Path(picked if isinstance(picked, str) else picked[0])
        if target.suffix.lower() != ".parquet":
            target = target.with_suffix(".parquet")
        write_parquet(frame, target)
        return {
            "cancelled": False,
            "file": target.name,
            "rows": frame.height,
            "bytes": target.stat().st_size,
        }


def _preview(frame: pl.DataFrame, n: int = PREVIEW_ROWS) -> dict[str, Any]:
    head = frame.head(n)
    return {
        "rows": frame.height,
        "columns": frame.columns,
        "preview": [list(row) for row in head.rows()],
    }
