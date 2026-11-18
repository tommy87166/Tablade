"""解析 recipe 的 inputs：glob 找檔案、轉碼、lazy 掃描、多檔合併（DESIGN.md §5、§8）。

幾 GB 的檔案也要在固定記憶體內處理：
- **不把整個檔案讀進記憶體。** 以 16 MB 為單位分塊讀取，同時算 SHA-256、檢查是不是 UTF-8。
- UTF-8 檔直接交給 Polars 的 ``scan_lines`` / ``scan_csv``（lazy、可串流）。
- 其他編碼（cp950 等）或有單獨 ``\\r`` 換行的檔案，先分塊轉成 UTF-8 暫存檔再掃描。
  暫存檔放在暫存資料夾，依「路徑＋大小＋修改時間＋編碼」快取，關閉視窗或程式結束時刪除；
  當掉或被強制結束時留下的資料夾，下次啟動時清掉（資料夾裡的鎖檔沒有人鎖著，就代表沒人在用）。
- 每個來源檔各自一個 LazyFrame（``scan_parts``，依路徑排序、行號排好），欄位對齊成同一組；
  引擎讓每個檔各自跑步驟，到「合併來源檔」或寫出前才接起來（DESIGN.md §8）。
"""

from __future__ import annotations

import atexit
import codecs
import contextlib
import glob
import hashlib
import io
import multiprocessing
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from tablade.dtypes import type_label
from tablade.usage import worker_memory

from . import excel
from .encoding import AUTO_ORDER, SUPPORTED, DecodeError
from .transcode import CHUNK, decode_range, segment_bounds, transcode_segment

SOURCE_FILE, LINE_NO, SHEET, STAGE = "__source_file", "__line_no", "__sheet", "__stage"
ORDER = (SOURCE_FILE, LINE_NO)
SYSTEM = (STAGE, SOURCE_FILE, SHEET, LINE_NO)
"""讀檔產生的系統欄位（依這個順序）；__sheet 只有讀 Excel 全部工作表時才有，
__stage 只有讀前面階段的輸出時才有。其他系統欄位（合併來源檔留下的 __orig_*）排在後面。"""
PARQUET_LABELS = {
    "source_file": "來源檔",
    "line_no": "行號",
    "sheet": "工作表",
    "stage": "階段",
    "orig_source_file": "合併前來源檔",
    "orig_line_no": "合併前行號",
    "orig_sheet": "合併前工作表",
    "orig_stage": "合併前階段",
}
"""讀 parquet 檔時，檔案自己帶的 __ 開頭欄位改名成「原始…」的一般欄保留（例如 __line_no → 原始行號）。"""


def part_name(file: str, sheet: str | None = None, stage: str | None = None) -> str:
    """一個來源的名稱：檔名；讀 Excel 全部工作表時每個工作表各自是一個來源，名稱是「檔名［工作表］」；
    從前面的階段讀進來時前面加「@階段／」。"""
    name = file if sheet is None else f"{file}［{sheet}］"
    return name if stage is None else f"@{stage}／{name}"


Progress = Callable[[dict[str, Any]], None] | None
Part = tuple[str, pl.LazyFrame]
"""一個來源檔（或合併後的一份資料）：（名稱, LazyFrame）。"""


class InputError(ValueError):
    pass


class PartsMismatch(ValueError):
    """要接起來的 parts 欄位不一樣。"""


@dataclass(frozen=True)
class SourceFile:
    path: str
    """相對於 recipe 所在資料夾的路徑（/ 分隔）。"""
    bytes: int
    sha256: str
    encoding: str
    rows: int
    sheet: str | None = None
    """讀 Excel 全部工作表時，這一個來源是哪個工作表。"""
    stage: str | None = None
    """這個檔是哪個階段讀的（處理紀錄用；不影響來源名稱）。"""

    @property
    def name(self) -> str:
        """這個來源的名稱（和 part 的名稱相同）。"""
        return part_name(self.path, self.sheet)


@dataclass(frozen=True)
class Prepared:
    effective: Path
    """要交給 Polars 掃描的 UTF-8 檔（原檔或暫存檔）。"""
    encoding: str
    sha256: str
    bytes: int
    bom: bool
    lines: int


_cache: dict[tuple[str, int, int, str], Prepared] = {}
_lock = threading.Lock()
_staging: Path | None = None
_staging_lock: Any = None
"""暫存資料夾裡鎖檔的檔案代號；開著並鎖住，代表這個程序還在用這個資料夾。"""
STAGING_PREFIX = "tablade-stage-"
LOCK_NAME = ".in-use"


def _try_lock(handle: Any) -> bool:
    """不等待地鎖住檔案；別的程序已經鎖著就回傳 False。"""
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def sweep_stale_staging(root: Path | None = None) -> list[Path]:
    """刪掉沒有人在用的暫存資料夾（上次當掉或被強制結束時留下的轉碼副本）。"""
    removed = []
    for folder in Path(root or tempfile.gettempdir()).glob(f"{STAGING_PREFIX}*"):
        if not folder.is_dir() or folder == _staging:
            continue
        try:
            with (folder / LOCK_NAME).open("a+b") as handle:
                if not _try_lock(handle):
                    continue  # 另一個 Tablade 還開著，正在用
        except OSError:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder)
    return removed


def staging_dir() -> Path:
    global _staging, _staging_lock
    with _lock:
        if _staging is None or not _staging.is_dir():
            sweep_stale_staging()
            _staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))
            _staging_lock = (_staging / LOCK_NAME).open("a+b")
            _try_lock(_staging_lock)
            atexit.register(clear_staging)
        return _staging


def staging_info() -> tuple[Path, int]:
    """這次開著的暫存資料夾（轉碼後的檔、處理中的暫存檔）與裡面的檔加起來多大（bytes）。"""
    folder = staging_dir()
    return folder, _folder_size(folder)


def staging_size() -> int:
    """暫存資料夾目前多大（bytes，畫面右下角定時顯示）；還沒建立時是 0，不會為了算大小去建資料夾。"""
    folder = _staging
    return _folder_size(folder) if folder is not None else 0


def _folder_size(folder: Path) -> int:
    """資料夾裡的檔加起來多大；處理中的暫存檔可能邊算邊被刪掉，找不到的就跳過。"""
    total = 0
    for root, _, names in os.walk(folder):
        for name in names:
            with contextlib.suppress(OSError):
                total += os.stat(os.path.join(root, name)).st_size
    return total


def clear_staging() -> None:
    """刪除所有暫存檔（暫存檔是原始資料的副本，不要留在電腦上）。關閉視窗與程式結束時都會呼叫。"""
    global _staging, _staging_lock
    with _lock:
        _cache.clear()
        if _staging_lock is not None:
            _staging_lock.close()  # 先放開鎖檔，Windows 才刪得掉
            _staging_lock = None
        if _staging is not None:
            shutil.rmtree(_staging, ignore_errors=True)
            _staging = None


def is_glob(entry: str) -> bool:
    return any(c in entry for c in "*?[")


def expand(entry: str, base_dir: Path) -> list[Path]:
    """檔案清單的一項找到的檔：相對路徑以 recipe 資料夾為基準；有 * 才當成 glob（依路徑排序）。"""
    full = Path(entry) if Path(entry).is_absolute() else base_dir / entry
    if is_glob(entry):
        return sorted(Path(p) for p in glob.glob(str(full), recursive=True) if Path(p).is_file())
    return [full] if full.is_file() else []


def check_entries(entries: list[str], base_dir: Path) -> list[dict[str, Any]]:
    """每一項找到幾個檔（給畫面顯示，不讀內容）。"""
    return [{"path": entry, "found": len(expand(entry, base_dir))} for entry in entries]


def resolve(entries: str | list[str], base_dir: Path) -> list[Path]:
    """依檔案清單找出所有檔：依清單順序，同一項裡依路徑排序，同一個實際的檔只留一次。"""
    if isinstance(entries, str):
        entries = [entries]
    if not entries:
        raise InputError("還沒有加入資料檔")
    seen: set[Path] = set()
    found = []
    for entry in entries:
        matches = expand(entry, base_dir)
        if not matches:
            what = f"符合「{entry}」的檔案" if is_glob(entry) else f"檔案「{entry}」"
            raise InputError(f"找不到{what}（相對於 {base_dir}）")
        for path in matches:
            real = path.resolve()  # 符號連結可能讓同一個檔出現兩次
            if real not in seen:
                seen.add(real)
                found.append(path)
    return found


def display_name(path: Path, base_dir: Path) -> str:
    """來源檔的名稱：recipe 資料夾裡的檔用相對路徑，其他用整理過的絕對路徑。"""
    real = path.resolve()
    try:
        return real.relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return real.as_posix()


def _report(progress: Progress, stage: str, name: str, done: int, total: int) -> None:
    if progress is not None:
        progress({"stage": stage, "file": name, "done": done, "total": total, "unit": "bytes"})


def _failure(encoding: str, offset: int, newlines: int, byte: int) -> str:
    return f"{encoding} 在第 {offset} 個位元組（第 {newlines + 1} 行，0x{byte:02X}）無法解碼"


_LONE_CR = re.compile(rb"\r(?!\n)")


def _check_utf8(path: Path, progress: Progress) -> tuple[str, bool, bool, str | None]:
    """一次讀完：回傳（sha256, 是否有 BOM, 是否有單獨的 \\r 換行, UTF-8 錯誤訊息或 None）。"""
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    total, done, newlines = path.stat().st_size, 0, 0
    bom, lone_cr, error, carry = False, False, None, b""
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            if done == 0:
                bom = chunk.startswith(codecs.BOM_UTF8)
            digest.update(chunk)
            # 區塊結尾的 \r 可能和下一塊開頭的 \n 是一組，留到下一塊再判斷
            data = carry + chunk
            carry = b"\r" if data.endswith(b"\r") else b""
            if not lone_cr and _LONE_CR.search(data[:-1] if carry else data):
                lone_cr = True
            if error is None:
                try:
                    decoder.decode(chunk)
                except UnicodeDecodeError as err:
                    pos = min(max(err.start, 0), len(chunk) - 1)
                    error = _failure(
                        "utf-8", done + pos, newlines + chunk.count(b"\n", 0, pos), chunk[pos]
                    )
            newlines += chunk.count(b"\n")
            done += len(chunk)
            _report(progress, "檢查編碼", path.name, done, total)
            if error is not None:
                break  # 不是 UTF-8：改用其他編碼轉碼時會重新讀一次
    if carry:
        lone_cr = True
    if error is None:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            error = f"utf-8 在檔案結尾（第 {newlines + 1} 行）有不完整的字元"
    return digest.hexdigest(), bom, lone_cr, error


PARALLEL_MIN = 64 << 20
"""檔案至少這麼大才平行轉碼（小檔開工作程序反而比較慢）。"""
SEGMENT = 32 << 20
"""平行轉碼時每一段的大小。"""
_pool: ProcessPoolExecutor | None = None
_pool_failed = False
"""開工作程序失敗過一次，這次執行就都用單核心，不再重試。"""


def _workers() -> int:
    """平行轉碼用幾個工作程序：留一個核心給畫面，最多 8 個；核心太少或開不了程序就不平行。"""
    cores = os.cpu_count() or 1
    return min(8, cores - 1) if cores > 2 and not _pool_failed else 0


def new_pool(workers: int) -> ProcessPoolExecutor:
    """開工作程序。spawn：各平台行為一致，打包後的 App 也能用（__main__.py 呼叫了 freeze_support）。"""
    return ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"))


def _get_pool() -> ProcessPoolExecutor:
    """平行轉碼的工作程序：第一次要用時才開，載入、匯出跑完就關掉（``release_workers``）。"""
    global _pool, _atexit_registered
    if _pool is None:
        _pool = new_pool(_workers())
        if not _atexit_registered:
            atexit.register(_shutdown_pool)
            _atexit_registered = True
    return _pool


_atexit_registered = False


def _shutdown_pool(wait: bool = False) -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=wait, cancel_futures=True)
        _pool = None


def release_workers() -> None:
    """關掉平行轉碼的工作程序（「載入資料及預覽」「處理並匯出資料」跑完時）：不用時不佔記憶體
    （8 個工作程序合計約 260 MB），下次要轉碼再開。關掉之前先讀一次它們用到多少（狀態列的「最高」）。"""
    if _pool is None:
        return
    worker_memory()
    _shutdown_pool(wait=True)


def _line_of(path: Path, offset: int) -> int:
    """offset 之前有幾個換行（只在解碼失敗、要寫錯誤訊息時才算）。"""
    count, done = 0, 0
    with path.open("rb") as f:
        while done < offset and (chunk := f.read(min(CHUNK, offset - done))):
            count += chunk.count(b"\n")
            done += len(chunk)
    return count


def _decode_error(path: Path, encoding: str, failure: tuple[int, int]) -> DecodeError:
    offset, byte = failure
    newlines = _line_of(path, offset)
    if byte < 0:
        return DecodeError(f"{encoding} 在檔案結尾（第 {newlines + 1} 行）有不完整的字元")
    return DecodeError(_failure(encoding, offset, newlines, byte))


def _transcode_single(path: Path, encoding: str, target: Path, progress: Progress) -> str:
    digest = hashlib.sha256()
    total, done = path.stat().st_size, 0

    def on_chunk(chunk: bytes) -> None:
        nonlocal done
        digest.update(chunk)
        done += len(chunk)
        _report(progress, "轉碼", path.name, done, total)

    with path.open("rb") as f, target.open("wb") as out:
        failure = decode_range(f, out, encoding, on_chunk=on_chunk)
    if failure is not None:
        raise _decode_error(path, encoding, failure)
    return digest.hexdigest()


def _transcode_parallel(
    path: Path,
    encoding: str,
    target: Path,
    progress: Progress,
    segment: int,
    pool: ProcessPoolExecutor | None = None,
) -> str | None:
    """在換行處切段、多個程序同時轉碼，再依序接起來；切不出兩段時回傳 None（改用單核心）。

    ``pool`` 預設是共用的工作程序；自我檢測會傳自己開的（不管核心數多少都實際開程序）。
    """
    total = path.stat().st_size
    cuts = segment_bounds(str(path), total, segment)
    if len(cuts) <= 2:
        return None
    # 中間的段不是檔案開頭：utf-8-sig 不能在那裡拿掉 BOM
    middle = "utf-8" if encoding == "utf-8-sig" else encoding
    parts = [target.with_name(f"{target.name}.part{i}") for i in range(len(cuts) - 1)]
    jobs = [
        (str(path), encoding if i == 0 else middle, cuts[i], cuts[i + 1], str(parts[i]))
        for i in range(len(parts))
    ]
    digest = hashlib.sha256()  # SHA-256 一定要從頭讀到尾：主程序另外用一條執行緒算

    def hash_file() -> None:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)

    hasher = threading.Thread(target=hash_file, daemon=True)
    hasher.start()
    try:
        workers = pool or _get_pool()
        futures = [workers.submit(transcode_segment, job) for job in jobs]
        done = 0
        for i, future in enumerate(futures):  # 依序等：第一個失敗的就是檔案裡最前面的錯誤
            failure = future.result()
            if failure is not None:
                for rest in futures[i + 1 :]:
                    rest.cancel()
                raise _decode_error(path, encoding, failure)
            done += cuts[i + 1] - cuts[i]
            _report(progress, "轉碼", path.name, done, total)
        with target.open("wb") as out:
            for part in parts:
                with part.open("rb") as f:
                    shutil.copyfileobj(f, out, CHUNK)
                part.unlink()
    finally:
        for part in parts:
            part.unlink(missing_ok=True)
        hasher.join()
    return digest.hexdigest()


def _transcode(
    path: Path,
    encoding: str,
    target: Path,
    progress: Progress,
    *,
    parallel_min: int | None = None,
    segment: int | None = None,
) -> str:
    """轉成 UTF-8 暫存檔；回傳原檔的 sha256。解碼失敗丟出 DecodeError。

    大檔用多個程序同時轉（結果和單核心完全相同）；開不了工作程序時改用單核心。
    \r\n 原樣保留（scan_lines 會處理），只有單獨的 \r 換行才改成 \n。
    """
    minimum = PARALLEL_MIN if parallel_min is None else parallel_min
    if _workers() and path.stat().st_size >= minimum:
        try:
            digest = _transcode_parallel(path, encoding, target, progress, segment or SEGMENT)
            if digest is not None:
                return digest
        except (OSError, RuntimeError, BrokenProcessPool):  # 例如公司電腦不讓開新程序
            global _pool_failed
            _pool_failed = True
            _shutdown_pool()
    return _transcode_single(path, encoding, target, progress)


def _count_lines(path: Path) -> int:
    return pl.scan_lines(path).select(pl.len()).collect(engine="streaming").item()


def prepare(path: Path, encoding: str = "auto", progress: Progress = None) -> Prepared:
    """確保檔案可以用 UTF-8 掃描：必要時轉碼成暫存檔。結果會快取。"""
    if encoding not in SUPPORTED:
        raise DecodeError(f"不支援的編碼：{encoding}（可用：{', '.join(SUPPORTED)}）")
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, encoding)
    with _lock:
        if key in _cache and _cache[key].effective.exists():
            return _cache[key]

    staged = staging_dir() / f"{hashlib.sha256(repr(key).encode()).hexdigest()[:24]}.txt"
    if stat.st_size == 0:
        prepared = Prepared(path, "utf-8", hashlib.sha256().hexdigest(), 0, False, 0)
    elif encoding in ("auto", "utf-8", "utf-8-sig"):
        digest, bom, lone_cr, error = _check_utf8(path, progress)
        if error is None and not lone_cr:
            detected = "utf-8-sig" if bom else "utf-8"
            prepared = Prepared(path, detected, digest, stat.st_size, bom, _count_lines(path))
        elif error is None:  # UTF-8，但有單獨的 \r 換行：轉成暫存檔統一換行
            _transcode(path, "utf-8-sig", staged, progress)
            prepared = Prepared(
                staged,
                "utf-8-sig" if bom else "utf-8",
                digest,
                stat.st_size,
                False,
                _count_lines(staged),
            )
        elif encoding != "auto":
            raise DecodeError(error)
        else:
            failures = [error]
            for candidate in AUTO_ORDER[1:]:
                try:
                    digest = _transcode(path, candidate, staged, progress)
                except DecodeError as err:
                    failures.append(str(err))
                    continue
                prepared = Prepared(
                    staged, candidate, digest, stat.st_size, False, _count_lines(staged)
                )
                break
            else:
                raise DecodeError("無法判斷編碼：" + "；".join(failures))
    else:
        digest = _transcode(path, encoding, staged, progress)
        prepared = Prepared(staged, encoding, digest, stat.st_size, False, _count_lines(staged))

    with _lock:
        _cache[key] = prepared
    return prepared


def _scan_one(prepared: Prepared, reader: str, name: str) -> tuple[pl.LazyFrame, int]:
    if prepared.lines == 0:
        empty = {SOURCE_FILE: pl.String, LINE_NO: pl.Int64}
        return pl.LazyFrame(schema=empty if reader == "csv" else {**empty, "line": pl.String}), 0
    if reader == "text_lines":
        lf = pl.scan_lines(
            prepared.effective, name="line", row_index_name=LINE_NO, row_index_offset=1
        )
        line = pl.col("line")
        if prepared.bom:
            line = pl.when(pl.col(LINE_NO) == 1).then(line.str.strip_prefix("﻿")).otherwise(line)
        lf = lf.select(
            pl.lit(name).alias(SOURCE_FILE), pl.col(LINE_NO).cast(pl.Int64), line.alias("line")
        )
        return lf, prepared.lines
    if reader == "csv":
        # 假設沒有跨行的欄位：標題在第 1 行，資料從第 2 行開始（有跨行欄位請改用 text_lines）
        lf = pl.scan_csv(
            prepared.effective,
            infer_schema=False,
            row_index_name=LINE_NO,
            row_index_offset=2,
            encoding="utf8",
        )
        columns = [c for c in lf.collect_schema().names() if c != LINE_NO]
        renamed = {c: c.removeprefix("﻿") for c in columns}
        lf = lf.select(
            pl.lit(name).alias(SOURCE_FILE),
            pl.col(LINE_NO).cast(pl.Int64),
            *[pl.col(c).alias(renamed[c]) for c in columns],
        )
        return lf, max(prepared.lines - 1, 0)
    raise InputError(f"不支援的讀取方式：{reader}")


def _common_type(column: str, found: list[tuple[str, pl.DataType]]) -> pl.DataType:
    """同名欄在各來源的型別要一樣：整欄都是空值的不算，小數位數不同時取位數多的；其他不一樣就報錯。"""
    typed = [(name, t) for name, t in found if t != pl.Null]
    if not typed:
        return found[0][1]
    first = typed[0][1]
    if all(t == first for _, t in typed):
        return first
    if all(isinstance(t, pl.Decimal) for _, t in typed):
        return pl.Decimal(38, max(t.scale for _, t in typed))  # type: ignore[attr-defined]
    shown = "、".join(f"{name}：{type_label(t)}" for name, t in typed)
    raise InputError(f"「{column}」在各來源的型別不一樣（{shown}），請先轉成一樣的型別")


def align(parts: list[Part]) -> list[Part]:
    """各來源的欄位可能不同：補成同一組欄位（依第一次出現的順序），缺的欄是空值；同名欄的型別要一樣。"""
    schemas = [lf.collect_schema() for _, lf in parts]
    seen: list[str] = []
    for schema in schemas:
        seen += [c for c in schema.names() if c not in seen]
    system = [c for c in SYSTEM if c in seen]
    system += [c for c in seen if c.startswith("__") and c not in SYSTEM]
    columns = system + [c for c in seen if not c.startswith("__")]
    target = {
        c: _common_type(
            c, [(name, s[c]) for (name, _), s in zip(parts, schemas, strict=True) if c in s]
        )
        for c in columns
    }
    if all(s.names() == columns and all(s[c] == target[c] for c in columns) for s in schemas):
        return parts
    return [
        (
            name,
            lf.select(
                [
                    (pl.col(c) if s[c] == target[c] else pl.col(c).cast(target[c]))
                    if c in s
                    else pl.lit(None, dtype=target[c]).alias(c)
                    for c in columns
                ]
            ),
        )
        for (name, lf), s in zip(parts, schemas, strict=True)
    ]


def scan_parts(
    entries: str | list[str],
    reader: str,
    encoding: str,
    base_dir: Path,
    progress: Progress = None,
    sheet: str = "",
    on_file: Callable[[int, str], None] | None = None,
) -> tuple[list[Part], list[SourceFile]]:
    """每個來源檔一個 LazyFrame（依檔案清單的順序），欄位對齊成同一組。

    ``sheet`` 只有 ``reader="excel"`` 時用：工作表名稱，空白代表第一個工作表。
    ``on_file(i, 名稱)`` 在開始讀第 i 個檔（從 0 起）之前呼叫（畫面上的「已處理幾個檔」）。
    """
    parts, files = [], []
    for i, path in enumerate(resolve(entries, base_dir)):
        name = display_name(path, base_dir)
        if on_file is not None:
            on_file(i, name)
        if reader == "excel":
            for part, info in _scan_excel(path, sheet, name, progress):
                parts.append(part)
                files.append(info)
            continue
        if excel.is_excel(path):
            raise InputError(f"「{name}」是 Excel 檔：讀取方式請選「Excel 工作表」")
        if reader == "parquet":
            lf, info = _scan_parquet(path, name, progress)
            parts.append((name, lf))
            files.append(info)
            continue
        prepared = prepare(path, encoding, progress)
        lf, rows = _scan_one(prepared, reader, name)
        parts.append((name, lf))
        files.append(SourceFile(name, prepared.bytes, prepared.sha256, prepared.encoding, rows))
    return align(parts), files


def parquet_names(columns: list[str]) -> dict[str, str]:
    """parquet 檔的欄名 → 讀進來的欄名：__ 開頭的改名成「原始…」（例如 __line_no → 原始行號）。"""
    renamed = {
        c: f"原始{PARQUET_LABELS.get(c[2:], c[2:])}" if c.startswith("__") else c for c in columns
    }
    values = list(renamed.values())
    clash = sorted({v for v in values if values.count(v) > 1})
    if clash:
        raise InputError(f"改名後欄名重複：{'、'.join(clash)}")
    return renamed


def _scan_parquet(path: Path, name: str, progress: Progress) -> tuple[pl.LazyFrame, SourceFile]:
    """一個 parquet 檔是一個來源：欄位與型別照檔案；__source_file 是檔名、__line_no 是第幾列（從 1 起）。"""
    _report(progress, "讀取 Parquet", name, 0, 0)
    try:
        raw = pl.scan_parquet(path)
        renamed = parquet_names(raw.collect_schema().names())
        rows = raw.select(pl.len()).collect().item()
    except (OSError, pl.exceptions.PolarsError) as err:
        raise InputError(f"「{name}」讀不了（不是 Parquet 檔？）：{err}") from None
    lf = (
        raw.select([pl.col(c).alias(n) for c, n in renamed.items()])
        .with_row_index(LINE_NO, offset=1)
        .select(
            pl.lit(name).alias(SOURCE_FILE),
            pl.col(LINE_NO).cast(pl.Int64),
            *[pl.col(n) for n in renamed.values()],
        )
    )
    info = SourceFile(name, path.stat().st_size, excel.sha256(path), "parquet", rows)
    return lf, info


def _scan_excel(
    path: Path, sheet: str, name: str, progress: Progress
) -> list[tuple[Part, SourceFile]]:
    """一個 Excel 檔：一個工作表一個來源；工作表是 ``*`` 時讀全部工作表，多一個 __sheet 欄。"""
    _report(progress, "讀取 Excel", name, 0, 0)
    size, digest = path.stat().st_size, excel.sha256(path)
    try:
        if sheet != excel.ALL_SHEETS:
            frame = excel.read_sheet(path, sheet, name)
            return [((name, frame.lazy()), SourceFile(name, size, digest, "excel", frame.height))]
        out = []
        for one in excel.sheet_names(path):
            frame = excel.read_sheet(path, one, name, with_sheet=True)
            info = SourceFile(name, size, digest, "excel", frame.height, sheet=one)
            out.append(((info.name, frame.lazy()), info))
        return out
    except excel.ExcelError as err:
        raise InputError(str(err)) from None


def _schema_text(schema: pl.Schema) -> str:
    return "、".join(f"{name}（{dtype}）" for name, dtype in schema.items())


def concat(parts: list[Part]) -> pl.LazyFrame:
    """把 parts 依序接成一個 LazyFrame。欄位不一樣時丟出 ``PartsMismatch``（不要等到執行到一半才失敗）。"""
    frames = [lf for _, lf in parts]
    if len(frames) == 1:
        return frames[0]
    first_name, first = parts[0]
    expected = first.collect_schema()
    for name, lf in parts[1:]:
        schema = lf.collect_schema()
        if schema != expected:
            raise PartsMismatch(
                f"來源檔「{name}」的欄位（{_schema_text(schema)}）"
                f"和「{first_name}」（{_schema_text(expected)}）不同"
            )
    return pl.concat(frames, how="vertical")


def scan(
    entries: str | list[str],
    reader: str,
    encoding: str,
    base_dir: Path,
    progress: Progress = None,
) -> tuple[pl.LazyFrame, list[SourceFile]]:
    """所有來源檔接成一個 LazyFrame（測試與小工具用；引擎用 ``scan_parts``）。"""
    parts, files = scan_parts(entries, reader, encoding, base_dir, progress)
    return concat(parts), files


def load(
    entries: str | list[str],
    reader: str,
    encoding: str,
    base_dir: Path,
    progress: Progress = None,
) -> tuple[pl.DataFrame, list[SourceFile]]:
    """把整份資料讀進記憶體（只適合小檔；大檔請用 ``scan`` ＋ ``sample``）。"""
    lf, files = scan(entries, reader, encoding, base_dir, progress)
    return lf.collect(engine="streaming"), files


def sample(
    parts: list[Part], files: list[SourceFile], reader: str, head: int, tail: int
) -> pl.DataFrame:
    """每個來源檔的前 ``head`` 行與最後 ``tail`` 行（預覽大檔用；每個檔串流掃描一次）。"""
    first = 2 if reader == "csv" else 1
    rows = {f.name: f.rows for f in files}
    kept = []
    for name, lf in parts:
        last = first + rows.get(name, 0) - 1
        kept.append(
            (name, lf.filter((pl.col(LINE_NO) < first + head) | (pl.col(LINE_NO) > last - tail)))
        )
    return concat(kept).collect(engine="streaming")


def peek_columns(
    entries: str | list[str], reader: str, encoding: str, base_dir: Path, sheet: str = ""
) -> list[str] | None:
    """不讀整個檔案，只取得 input 的資料欄位（給驗證器檢查欄位引用）。"""
    first = resolve(entries, base_dir)[0]  # 找不到檔案時丟出 InputError
    if reader == "text_lines":
        return ["line"]
    if reader == "parquet":
        try:
            names = list(parquet_names(pl.read_parquet_schema(first).keys()).values())
        except (OSError, pl.exceptions.PolarsError) as err:
            raise InputError(f"「{first.name}」讀不了（不是 Parquet 檔？）：{err}") from None
        return names
    if reader == "excel":
        try:
            return excel.peek_columns(first, sheet)
        except excel.ExcelError as err:
            raise InputError(str(err)) from None
    head = first.read_bytes()[: 1 << 16].split(b"\n", 1)[0].rstrip(b"\r")
    text = _lenient(head) if encoding == "auto" else head.decode(encoding, errors="replace")
    text = text.removeprefix("﻿")
    return pl.read_csv(io.StringIO(text + "\n"), infer_schema=False).columns if text else []


def _lenient(raw: bytes) -> str:
    for candidate in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
