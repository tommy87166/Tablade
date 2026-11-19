"""量預覽的記憶體與時間（docs/memory-improve.md 的實測）。Windows、macOS、Linux 都能跑。

用法（在 repo 的資料夾裡；先 ``uv sync``）::

    uv run python docs/memory-improve-measure.py
    uv run python docs/memory-improve-measure.py --files 20     # 記憶體比較小的電腦：樣本小一半
    uv run python docs/memory-improve-measure.py --threads 4    # 限制 Polars 的執行緒數

做的事：

1. 讀系統的記憶體（總量、可用），照計畫的公式算出這台電腦的樣本額度。
2. 用 ``tablade.fixtures.write_many_zips`` 產生假資料（很多個 zip，每個裡面一個 cp950 的文字檔），
   放在系統的暫存資料夾，跑完就刪掉。不讀、不寫 repo 裡的任何檔。
3. 取四種大小的樣本，每種大小、每種做法各開一個新的行程量記憶體與時間：
   - 現在的做法：每一步的結果都留著（6 步與 12 步的 Recipe）
   - 只留輸入與最後一步（12 步）
   - 不存表、連續點 30 次不同的步驟（每次從樣本重算；先放掉舊表、後放掉舊表各一次）
4. 印出一份報告（也存成暫存資料夾裡的 ``result.md``）：把報告整段貼回來就可以。
   報告裡沒有電腦名稱、使用者名稱或路徑。

不連網路、不裝套件。記憶體的數字用 ``tablade.usage.process_memory()``（和 App 狀態列顯示的一樣）。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import gc
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MB = 1 << 20
GB = 1 << 30
HEADS = (12_500, 25_000, 50_000, 100_000)
"""每個檔取前幾行（結尾另外取 1/100）：40 個檔時樣本大約是 50、100、200、400 萬行。"""
CLICKS = 30

# 計畫建議的數字（docs/memory-improve.md「第 1 部分」）
SHARE_AVAILABLE = 0.05
SHARE_TOTAL = 0.01
BYTES_PER_THREAD = 32 * MB
BYTES_MAX = 256 * MB


def recipes() -> dict[int, list[dict]]:
    from tablade.fixtures import MANY_ZIPS_COLUMNS

    split = {
        "type": "split_delimited",
        "column": "line",
        "delimiter": "|",
        "columns": list(MANY_ZIPS_COLUMNS),
    }

    def keep_out(column: str, value: str) -> dict:
        return {
            "type": "filter",
            "conditions": [{"column": column, "op": "not_equals", "value": value}],
        }

    date = {"type": "split_column", "column": "日期", "delimiter": "/", "names": ["年", "月", "日"]}
    rename = {"type": "rename_columns", "names": {"門市代號": "店號"}}
    text = ["門市", "品名", "通路", "付款方式", "單號", "店員", "備註"]
    return {
        6: [
            split,
            {"type": "remove_columns", "columns": ["店員"]},
            rename,
            keep_out("備註", "退貨"),
            keep_out("數量", "12"),
            date,
        ],
        12: [
            split,
            {"type": "trim_clean", "columns": text},
            keep_out("備註", "退貨"),
            {"type": "parse_number", "columns": ["數量", "單價", "金額"]},
            {
                "type": "replace_values",
                "columns": ["通路"],
                "replacements": [{"find": "門市", "replace": "店面"}],
            },
            {"type": "filter", "conditions": [{"column": "數量", "op": "lt", "value": 12}]},
            date,
            keep_out("月", "13"),
            {"type": "trim_clean", "columns": ["年", "月", "日"]},
            keep_out("店員", "無"),
            rename,
            keep_out("付款方式", "禮券"),
        ],
    }


# ── 系統的記憶體（計畫要加進 tablade/usage.py 的 system_memory() 的原型）──────────────


def system_memory() -> tuple[int, int] | None:
    """（實體記憶體總量、目前可用）bytes；讀不到時回傳 None。"""
    try:
        if sys.platform == "win32":
            return _windows_memory()
        if sys.platform == "darwin":
            return _macos_memory()
        if sys.platform.startswith("linux"):
            return _linux_memory()
    except (OSError, AttributeError, ValueError, KeyError):
        return None
    return None


def _windows_memory() -> tuple[int, int] | None:
    from ctypes import wintypes

    class Status(ctypes.Structure):  # MEMORYSTATUSEX
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    query = ctypes.WinDLL("kernel32").GlobalMemoryStatusEx  # type: ignore[attr-defined]
    query.argtypes = [ctypes.POINTER(Status)]
    query.restype = wintypes.BOOL
    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    if not query(ctypes.byref(status)):
        return None
    return status.ullTotalPhys, status.ullAvailPhys


class _VMStatistics64(ctypes.Structure):
    """``<mach/vm_statistics.h>`` 的 vm_statistics64 開頭的欄位（只用 free_count、inactive_count）。"""

    _fields_ = [
        ("free_count", ctypes.c_uint32),
        ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32),
        ("rest", ctypes.c_uint64 * 32),  # 後面的欄位不用，留夠大的空間給系統寫
    ]


def _macos_memory() -> tuple[int, int] | None:
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib")

    def sysctl(name: bytes) -> int:
        value = ctypes.c_uint64()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            raise OSError(name.decode())
        return value.value

    libc.mach_host_self.restype = ctypes.c_uint32
    stat = _VMStatistics64()
    count = ctypes.c_uint32(ctypes.sizeof(stat) // 4)
    host_vm_info64 = 4
    if libc.host_statistics64(
        libc.mach_host_self(), host_vm_info64, ctypes.byref(stat), ctypes.byref(count)
    ):
        return None
    page = sysctl(b"hw.pagesize")
    return sysctl(b"hw.memsize"), (stat.free_count + stat.inactive_count) * page


def _linux_memory() -> tuple[int, int] | None:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0]) * 1024  # kB
    return values["MemTotal"], values["MemAvailable"]


def budget(memory: tuple[int, int] | None, threads: int) -> tuple[int, str]:
    """計畫的公式：min(流暢上限, max(可用 × Y, 總量 × X))；回傳（額度、是哪一項決定的）。"""
    smooth = min(BYTES_MAX, BYTES_PER_THREAD * threads)
    if memory is None:
        return min(smooth, 128 * MB), "讀不到記憶體"
    total, available = memory
    by_available, floor = int(available * SHARE_AVAILABLE), int(total * SHARE_TOTAL)
    if max(by_available, floor) >= smooth:
        return smooth, "流暢上限"
    return (by_available, "可用 × Y") if by_available >= floor else (floor, "下限 X")


# ── 子行程：量一種做法 ───────────────────────────────────────────


def child(mode: str, sample: Path, count: int) -> None:
    import polars as pl

    from tablade.engine.run import apply_step
    from tablade.steps.registry import get
    from tablade.usage import process_memory

    peak = 0
    stop = threading.Event()

    def watch() -> None:
        nonlocal peak
        while not stop.is_set():
            memory = process_memory()
            if memory is not None:
                peak = max(peak, memory.current)
            time.sleep(0.02)

    def now() -> float:
        memory = process_memory()
        return round(memory.current / GB, 2) if memory is not None else -1.0

    steps = [
        get(raw["type"]).model_validate({"id": f"s{i}", **raw})
        for i, raw in enumerate(recipes()[count])
    ]
    out: dict = {"mode": mode, "steps": count, "before": now()}
    frame = pl.read_parquet(sample)
    gc.collect()
    time.sleep(0.3)
    out.update(rows=frame.height, sample_mb=round(frame.estimated_size() / MB), loaded=now())
    threading.Thread(target=watch, daemon=True).start()

    def rebuild(upto: int) -> pl.DataFrame:
        """從樣本一步一步算到第 upto 步，算完下一步就放掉上一步。"""
        current = frame
        for step in steps[: upto + 1]:
            current = apply_step(step, current)[0]
        return current

    if mode in ("keep", "drop"):
        kept = [frame]
        start = time.perf_counter()
        for step in steps:
            result = apply_step(step, kept[-1])[0]
            kept = [*kept, result] if mode == "keep" else [frame, result]
        out["seconds"] = round(time.perf_counter() - start, 2)
        gc.collect()
        out.update(after=now(), result_rows=kept[-1].height)
    else:
        rng = random.Random(0)
        targets = [i for i in (0, 2, 3, 5, 8, 11) if i < len(steps)]
        shown = None
        seconds, seen = [], []
        for _ in range(CLICKS):
            if mode == "clicks_first":
                shown = None  # 先放掉畫面上舊的那張，再重算
            start = time.perf_counter()
            shown = rebuild(rng.choice(targets))
            seconds.append(time.perf_counter() - start)
            gc.collect()
            seen.append(now())
        time.sleep(10)
        out.update(
            click_mean=round(sum(seconds) / len(seconds), 2),
            click_max=round(max(seconds), 2),
            low=min(seen[5:]),
            high=max(seen[5:]),
            idle=now(),
            result_rows=shown.height if shown is not None else 0,
        )
    stop.set()
    memory = process_memory()
    out["peak"] = round(max(peak, memory.peak if memory is not None else 0) / GB, 2)
    print("RESULT " + json.dumps(out))


# ── 主程式 ─────────────────────────────────────────────────


def run_child(mode: str, sample: Path, count: int, threads: int | None) -> dict | None:
    env = {**os.environ, "PYTHONUTF8": "1"}
    if threads:
        env["POLARS_MAX_THREADS"] = str(threads)
    command = [sys.executable, __file__, "--child", mode, "--sample", str(sample)]
    done = subprocess.run(
        [*command, "--steps", str(count)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    for line in done.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    return None


def gb(value: float | None) -> str:
    return "失敗" if value is None else f"{value:.2f} GB"


def main() -> None:
    parser = argparse.ArgumentParser(description="量預覽的記憶體與時間（docs/memory-improve.md）")
    parser.add_argument("--files", type=int, default=40, help="幾個 zip（預設 40）")
    parser.add_argument("--rows", type=int, default=120_000, help="每個檔幾行（預設 12 萬）")
    parser.add_argument("--threads", type=int, default=None, help="限制 Polars 的執行緒數")
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--sample", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--steps", type=int, default=6, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.child, Path(args.sample), args.steps)
        return
    if args.threads:
        os.environ["POLARS_MAX_THREADS"] = str(args.threads)

    import polars as pl

    import tablade
    from tablade.fixtures import write_many_zips
    from tablade.ingest import sources

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    threads = pl.thread_pool_size()
    memory = system_memory()
    report: list[str] = []

    def say(text: str = "") -> None:
        report.append(text)
        print(text, flush=True)

    def note(text: str) -> None:
        print(f"… {text}", flush=True)

    say("## 預覽記憶體的實測")
    say()
    say(f"- 系統：{platform.system()} {platform.release()}（{platform.machine()}）")
    say(f"- CPU：{os.cpu_count()} 個邏輯核心；Polars 用 {threads} 條執行緒")
    say(
        f"- 版本：Tablade {tablade.__version__}、Polars {pl.__version__}、Python {platform.python_version()}"
    )
    if memory is None:
        say("- 系統記憶體：**讀不到**")
    else:
        say(
            f"- 系統記憶體：總量 {memory[0] / GB:.1f} GB，可用 {memory[1] / GB:.1f} GB"
            "（請和工作管理員／活動監視器的數字對一下）"
        )
    size, reason = budget(memory, threads)
    say(f"- 照計畫的公式，這台電腦現在的樣本額度：{size / MB:.0f} MB（{reason}）")
    say(f"- 假資料：{args.files} 個 zip，每個 {args.rows:,} 行")
    say()

    work = Path(tempfile.mkdtemp(prefix="tablade-measure-"))
    try:
        note("產生假資料")
        start = time.perf_counter()
        write_many_zips(work / "data", args.files, args.rows)
        made = time.perf_counter() - start
        note("讀檔、轉碼")
        start = time.perf_counter()
        parts, files = sources.scan_parts(
            ["data/*.zip"], "zip_lines", "auto", work, members="*.TXT"
        )
        scanned = time.perf_counter() - start
        samples = []
        for head in HEADS:
            if head > args.rows:
                continue
            start = time.perf_counter()
            frame = sources.sample(parts, files, "zip_lines", head, head // 100)
            seconds = time.perf_counter() - start
            path = work / f"sample-{head}.parquet"
            frame.write_parquet(path)
            samples.append((path, frame.height, frame.estimated_size(), seconds))
            del frame
        sources.release_workers()
        say(
            f"- 產生假資料 {made:.0f} 秒；讀檔、轉碼 {scanned:.0f} 秒；取樣本 "
            + "、".join(f"{s[3]:.2f}" for s in samples)
            + " 秒"
        )
        say()

        def fits(estimated: int) -> bool:
            """預估的峰值（樣本的 10 倍）超過可用記憶體的 7 成就不跑。"""
            now = system_memory()
            return now is None or estimated * 10 < now[1] * 0.7

        results: dict[tuple[str, int, int], dict | None] = {}
        plan = [("keep", 6), ("keep", 12), ("drop", 12)]
        for index, (path, rows, estimated, _) in enumerate(samples):
            if not fits(estimated):
                note(f"樣本 {rows:,} 行：可用記憶體不夠，跳過")
                continue
            modes = list(plan)
            if index in (1, len(samples) - 1):
                modes += [("clicks_first", 12), ("clicks_after", 12)]
            for mode, count in modes:
                note(f"樣本 {rows:,} 行：{mode}（{count} 步）")
                results[(mode, count, index)] = run_child(mode, path, count, args.threads)

        def cell(mode: str, count: int, index: int, key: str, unit: str = "") -> str:
            result = results.get((mode, count, index))
            if (mode, count, index) not in results:
                return "跳過"
            if result is None:
                return "失敗"
            return f"{result[key]}{unit}"

        say("### 表 1：每一步都存（現在的做法）與只留輸入、最後一步")
        say()
        say(
            "| 樣本行數 | 樣本大小 | 載入後 | 6 步都存：峰值 | 6 步：時間 | 12 步都存：峰值 | 12 步：時間 | 12 步只留頭尾：峰值 | 時間 |"
        )
        say("|---|---|---|---|---|---|---|---|---|")
        for index, (_, rows, estimated, _) in enumerate(samples):
            say(
                f"| {rows:,} | {estimated / MB:.0f} MB | {cell('keep', 6, index, 'loaded', ' GB')} "
                f"| {cell('keep', 6, index, 'peak', ' GB')} | {cell('keep', 6, index, 'seconds', ' 秒')} "
                f"| {cell('keep', 12, index, 'peak', ' GB')} | {cell('keep', 12, index, 'seconds', ' 秒')} "
                f"| {cell('drop', 12, index, 'peak', ' GB')} | {cell('drop', 12, index, 'seconds', ' 秒')} |"
            )
        say()
        say(
            f"### 表 2：不存表，連續點 {CLICKS} 次不同的步驟（12 步；每次從樣本用 apply_step 重算，含警告）"
        )
        say()
        say("| 樣本行數 | 順序 | 峰值 | 第 6 次之後 | 停 10 秒後 | 每次平均 | 最久一次 |")
        say("|---|---|---|---|---|---|---|")
        for index, (_, rows, _, _) in enumerate(samples):
            for mode, title in (("clicks_first", "先放掉舊表"), ("clicks_after", "後放掉舊表")):
                result = results.get((mode, 12, index))
                if (mode, 12, index) not in results:
                    continue
                if result is None:
                    say(f"| {rows:,} | {title} | 失敗 | | | | |")
                    continue
                say(
                    f"| {rows:,} | {title} | {result['peak']} GB | {result['low']}～{result['high']} GB "
                    f"| {result['idle']} GB | {result['click_mean']} 秒 | {result['click_max']} 秒 |"
                )
        say()
        base = next((r["before"] for r in results.values() if r), None)
        say(f"- 行程在載入樣本之前：{gb(base)}")
        again = system_memory()
        if again is not None:
            say(f"- 跑完後系統可用記憶體：{again[1] / GB:.1f} GB")
    finally:
        shutil.rmtree(work / "data", ignore_errors=True)
        for path in work.glob("sample-*.parquet"):
            path.unlink(missing_ok=True)
        sources.clear_staging()
    target = work / "result.md"
    target.write_text("\n".join(report) + "\n", encoding="utf-8")
    print()
    print(f"報告也存在：{target}（貼報告的內容就好，不用貼這個路徑）")


if __name__ == "__main__":
    main()
