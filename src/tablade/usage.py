"""Tablade 目前用了多少記憶體（狀態列與進度視窗顯示）。

只用標準函式庫（``ctypes`` 直接問作業系統，不另外裝套件）：

- Windows：``GetProcessMemoryInfo`` 的 WorkingSetSize／PeakWorkingSetSize
  （跟工作管理員的「記憶體」差不多，略高一點：含跟其他程式共用的部分）
- macOS：``proc_pid_rusage`` 的 phys_footprint／lifetime_max_phys_footprint（活動監視器「記憶體」那一欄）
- Linux：``/proc/<pid>/status`` 的 VmRSS／VmHWM

分兩部分算：主程序（Python＋Polars，資料都在這裡）與平行轉碼的工作程序（``multiprocessing`` 開的子程序，
轉碼時才有，載入、匯出跑完就關掉）。網頁畫面是另外的程序，不算在內。
Polars 用完的記憶體不一定馬上還給作業系統，處理完後數字可能停在高點，不代表外漏。
讀不到（其他作業系統、呼叫失敗）時回傳 None，不丟例外。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import multiprocessing
import os
import sys
import threading
from pathlib import Path
from typing import NamedTuple


class Memory(NamedTuple):
    current: int
    """目前用了多少（bytes）。"""
    peak: int
    """這次開啟以來最多用到多少（bytes）。"""


def process_memory(pid: int | None = None) -> Memory | None:
    """一個程序的記憶體；``pid`` 沒給是這個程序（主程序）。"""
    try:
        if sys.platform == "win32":
            return _windows(pid)
        if sys.platform == "darwin":
            return _macos(os.getpid() if pid is None else pid)
        if sys.platform.startswith("linux"):
            return _linux("self" if pid is None else str(pid))
    except (OSError, AttributeError, ValueError):
        return None
    return None


_workers_peak = 0
_workers_lock = threading.Lock()


def worker_memory() -> Memory | None:
    """平行轉碼的工作程序目前合計用了多少，與這次開啟以來合計最多用到多少。

    工作程序跑完就關掉，事後讀不到它的最高值：每次讀的時候把還開著的工作程序各自的最高值加起來，
    記下看過最大的（關掉工作程序之前也會讀一次，``sources.release_workers``）。讀不到記憶體的作業系統回傳 None。
    """
    global _workers_peak
    if process_memory() is None:
        return None
    current = peak = 0
    for child in multiprocessing.active_children():
        memory = process_memory(child.pid) if child.pid else None
        if memory is not None:
            current += memory.current
            peak += memory.peak
    with _workers_lock:
        _workers_peak = max(_workers_peak, peak, current)
        return Memory(current, _workers_peak)


def _windows(pid: int | None) -> Memory | None:
    from ctypes import wintypes

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    query = kernel32.K32GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    if pid is None:
        handle = kernel32.GetCurrentProcess()  # 不用關
    else:
        query_limited, vm_read = (
            0x1000,
            0x0010,
        )  # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
        handle = kernel32.OpenProcess(query_limited | vm_read, False, pid)
        if not handle:
            return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not query(handle, ctypes.byref(counters), counters.cb):
            return None
        return Memory(counters.WorkingSetSize, counters.PeakWorkingSetSize)
    finally:
        if pid is not None:
            kernel32.CloseHandle(handle)


_RUSAGE_INFO_V4 = 4
_RUSAGE_V4_FIELDS = [
    "user_time",
    "system_time",
    "pkg_idle_wkups",
    "interrupt_wkups",
    "pageins",
    "wired_size",
    "resident_size",
    "phys_footprint",
    "proc_start_abstime",
    "proc_exit_abstime",
    "child_user_time",
    "child_system_time",
    "child_pkg_idle_wkups",
    "child_interrupt_wkups",
    "child_pageins",
    "child_elapsed_abstime",
    "diskio_bytesread",
    "diskio_byteswritten",
    "cpu_time_qos_default",
    "cpu_time_qos_maintenance",
    "cpu_time_qos_background",
    "cpu_time_qos_utility",
    "cpu_time_qos_legacy",
    "cpu_time_qos_user_initiated",
    "cpu_time_qos_user_interactive",
    "billed_system_time",
    "serviced_system_time",
    "logical_writes",
    "lifetime_max_phys_footprint",
    "instructions",
    "cycles",
    "billed_energy",
    "serviced_energy",
    "interval_max_phys_footprint",
    "runnable_time",
]


class _RusageInfoV4(ctypes.Structure):
    """``<sys/resource.h>`` 的 rusage_info_v4（欄位順序照標頭檔）。"""

    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in _RUSAGE_V4_FIELDS]


def _macos(pid: int) -> Memory | None:
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    query = libproc.proc_pid_rusage
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_RusageInfoV4)]
    query.restype = ctypes.c_int
    info = _RusageInfoV4()
    if query(pid, _RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        return None  # 程序剛好結束了
    current = info.phys_footprint
    return Memory(current, max(info.lifetime_max_phys_footprint, current))


def _linux(pid: str) -> Memory | None:
    values = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in ("VmRSS", "VmHWM"):
            values[key] = int(rest.split()[0]) * 1024  # kB
    if "VmRSS" not in values:
        return None
    current = values["VmRSS"]
    return Memory(current, max(values.get("VmHWM", current), current))


def system_memory() -> tuple[int, int] | None:
    """這台電腦的（實體記憶體總量, 目前可用）bytes：預覽的樣本額度用（``engine.run.sample_budget``）。

    - Windows：``GlobalMemoryStatusEx`` 的 ullTotalPhys、ullAvailPhys
    - macOS：``hw.memsize``；可用是 ``host_statistics64`` 的 free＋inactive（只看 free 太小：
      系統會拿空著的記憶體當檔案快取），頁大小讀 ``hw.pagesize``（Apple Silicon 是 16 KB）
    - Linux：``/proc/meminfo`` 的 MemTotal、MemAvailable

    讀不到時回傳 None，不丟例外。
    """
    try:
        if sys.platform == "win32":
            return _windows_system()
        if sys.platform == "darwin":
            return _macos_system()
        if sys.platform.startswith("linux"):
            return _linux_system()
    except (OSError, AttributeError, ValueError, KeyError):
        return None
    return None


def _windows_system() -> tuple[int, int] | None:
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


_HOST_VM_INFO64 = 4


def _macos_system() -> tuple[int, int] | None:
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
    if libc.host_statistics64(
        libc.mach_host_self(), _HOST_VM_INFO64, ctypes.byref(stat), ctypes.byref(count)
    ):
        return None
    page = sysctl(b"hw.pagesize")
    return sysctl(b"hw.memsize"), (stat.free_count + stat.inactive_count) * page


def _linux_system() -> tuple[int, int] | None:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0]) * 1024  # kB
    return values["MemTotal"], values["MemAvailable"]
