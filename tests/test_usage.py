"""記憶體與暫存資料夾的用量（狀態列、進度視窗）；平行轉碼的工作程序跑完就關掉。"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tablade import usage
from tablade.api import Api
from tablade.ingest import sources
from tablade.usage import process_memory, worker_memory

MB = 1 << 20
ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = sys.platform in ("win32", "darwin") or sys.platform.startswith("linux")
needs_memory = pytest.mark.skipif(not SUPPORTED, reason="只支援 Windows、macOS、Linux")


@needs_memory
def test_process_memory_follows_what_the_program_uses():
    """讀得到目前與最高的用量；多用 200 MB，兩個數字都跟著變大（結構欄位對的話才會這樣）。"""
    before = process_memory()
    assert before is not None and 0 < before.current <= before.peak
    data = b"x" * (200 * MB)  # 真的寫進記憶體（不是只保留位址）
    after = process_memory()
    assert after is not None
    assert after.current - before.current > 150 * MB
    assert after.peak >= after.current
    assert len(data) == 200 * MB


@needs_memory
def test_workers_are_counted_and_released(monkeypatch):
    """平行轉碼的工作程序算進「轉碼」的用量；關掉之後目前是 0，最高值留著（關掉前先讀一次）。"""
    monkeypatch.setattr(usage, "_workers_peak", 0)
    pool = sources.new_pool(2)
    monkeypatch.setattr(sources, "_pool", pool)
    assert pool.submit(os.getpid).result(timeout=60) > 0  # 讓工作程序真的開起來
    children = multiprocessing.active_children()
    assert children
    one = process_memory(children[0].pid)
    assert one is not None and 0 < one.current <= one.peak
    running = worker_memory()
    assert running is not None and running.current > 0
    sources.release_workers()
    assert sources._pool is None and not multiprocessing.active_children()
    after = worker_memory()
    assert after is not None and after.current == 0 and after.peak >= running.current


def test_release_workers_without_a_pool_does_nothing(monkeypatch):
    monkeypatch.setattr(sources, "_pool", None)
    sources.release_workers()
    assert sources._pool is None


def test_load_and_export_release_the_workers(tmp_path, monkeypatch):
    """「載入資料及預覽」「處理並匯出資料」跑完就關掉工作程序（不用時不佔記憶體）。"""
    import shutil

    from tablade.fixtures import write_all
    from tablade.session import Session

    write_all(tmp_path / "data")
    example = ROOT / "examples" / "sales_detail.recipe.json"
    released: list[str] = []
    monkeypatch.setattr("tablade.session.release_workers", lambda: released.append("關掉"))
    shutil.copy(example, tmp_path / example.name)
    s = Session()
    s.load_recipe(tmp_path / example.name)
    s.load_data()
    assert released == ["關掉"]
    s.export()
    assert released == ["關掉", "關掉"]


def test_staging_size_does_not_create_the_folder(monkeypatch, tmp_path):
    """還沒建立暫存資料夾時是 0，而且不會為了算大小去建；建了之後是裡面所有檔加起來。"""
    monkeypatch.setattr(sources, "_staging", None)
    assert sources.staging_size() == 0
    assert sources._staging is None
    folder = tmp_path / "stage"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_bytes(b"x" * 1000)
    (folder / "sub" / "b.parquet").write_bytes(b"y" * 234)
    monkeypatch.setattr(sources, "_staging", folder)
    assert sources.staging_size() == 1234


def test_usage_is_pushed_to_the_window(monkeypatch):
    """api.py 把用量推到 window.state.usage；視窗還沒載入或關閉中（設定失敗）時不丟例外。"""
    api = Api()
    api._window = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(sources, "_staging", None)
    api._push_usage()
    pushed = api._window.state.usage
    assert set(pushed) == {"memory", "peak", "workers", "workers_peak", "staging"}
    assert pushed["staging"] == 0
    if SUPPORTED:
        assert pushed["memory"] > 0 and pushed["peak"] >= pushed["memory"]
        assert pushed["workers"] >= 0 and pushed["workers_peak"] >= pushed["workers"]

    class Closing:
        def __setattr__(self, name, value):
            raise RuntimeError("視窗關閉中")

    api._window = SimpleNamespace(state=Closing())
    api._push_usage()


def test_system_memory_reads_total_and_available():
    memory = usage.system_memory()
    if sys.platform not in ("win32", "darwin") and not sys.platform.startswith("linux"):
        assert memory is None
        return
    assert memory is not None
    total, available = memory
    assert total > 0 and 0 < available <= total


def test_system_memory_returns_none_when_the_call_fails(monkeypatch):
    def broken() -> tuple[int, int]:
        raise OSError("boom")

    for name in ("_windows_system", "_macos_system", "_linux_system"):
        monkeypatch.setattr(usage, name, broken)
    assert usage.system_memory() is None
