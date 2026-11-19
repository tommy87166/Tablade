"""解析 recipe 的 inputs：glob 找檔案、轉碼、lazy 掃描、多檔合併（DESIGN.md §5、§8）。

幾 GB 的檔案也要在固定記憶體內處理：
- **不把整個檔案讀進記憶體。** 以 1 MB 為單位分塊讀取（``transcode.CHUNK``），同時算 SHA-256、檢查是不是 UTF-8。
- UTF-8 檔直接交給 Polars 的 ``scan_lines`` / ``scan_csv``（lazy、可串流），不寫暫存檔。
- 其他編碼（cp950 等）或有單獨 ``\\r`` 換行的檔案要先轉碼：
  - 逐行讀取：在換行處切段、轉成 UTF-8，一段一段寫成 zstd 壓縮的 Parquet（``_stage_lines``），
    暫存大約只有原檔的 1/10；轉碼途中的文字段最多「工作程序數 × 2」段（``_segments``）。
  - CSV：轉成一整份 UTF-8 的文字暫存檔再 ``scan_csv``（Polars 一定要讀一整份檔；
    docs/parquet-staging-plan.md 的「CSV 為什麼不改」）。
  暫存檔放在暫存資料夾，依「路徑＋大小＋修改時間＋編碼＋讀取方式」快取，關閉視窗或程式結束時刪除；
  當掉或被強制結束時留下的資料夾，下次啟動時清掉（資料夾裡的鎖檔沒有人鎖著，就代表沒人在用）。
- 每個來源檔各自一個 LazyFrame（``scan_parts``，依路徑排序、行號排好），欄位對齊成同一組；
  引擎讓每個檔各自跑步驟，到「合併來源檔」或寫出前才接起來（DESIGN.md §8）。
"""

from __future__ import annotations

import atexit
import codecs
import collections
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
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import wait as wait_futures
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import polars as pl

from tablade.dtypes import type_label
from tablade.usage import worker_memory

from ..i18n import t
from . import excel, zip_lines
from .encoding import AUTO_ORDER, SUPPORTED, DecodeError
from .transcode import (
    CHUNK,
    LENIENT,
    REPLACED_LINES,
    LineCounter,
    Stats,
    count_replaced,
    decode_range,
    line_breaks,
    segment_bounds,
    transcode_bytes,
    transcode_segment,
)

SOURCE_FILE, LINE_NO, SHEET, STAGE = "__source_file", "__line_no", "__sheet", "__stage"
ORDER = (SOURCE_FILE, LINE_NO)
SYSTEM = (STAGE, SOURCE_FILE, SHEET, LINE_NO)
"""讀檔產生的系統欄位（依這個順序）；__sheet 只有讀 Excel 全部工作表時才有，
__stage 只有讀前面階段的輸出時才有。其他系統欄位（合併來源檔留下的 __orig_*）排在後面。"""
PARQUET_LABELS = {
    "source_file": "來源檔",  # i18n-ignore：程式產生的欄名是資料，Recipe 會引用
    "line_no": "行號",  # i18n-ignore：同上
    "sheet": "工作表",  # i18n-ignore：同上
    "stage": "階段",  # i18n-ignore：同上
    "orig_source_file": "合併前來源檔",  # i18n-ignore：同上
    "orig_line_no": "合併前行號",  # i18n-ignore：同上
    "orig_sheet": "合併前工作表",  # i18n-ignore：同上
    "orig_stage": "合併前階段",  # i18n-ignore：同上
}
"""讀 parquet 檔時，檔案自己帶的 __ 開頭欄位改名成「原始…」的一般欄保留（例如 __line_no → 原始行號）。"""


def part_name(file: str, sheet: str | None = None, stage: str | None = None) -> str:
    """一個來源的名稱：檔名；讀 Excel 全部工作表時每個工作表各自是一個來源，名稱是「檔名［工作表］」；
    從前面的階段讀進來時前面加「@階段／」。"""
    name = file if sheet is None else f"{file}［{sheet}］"  # i18n-ignore：來源名稱是資料
    return name if stage is None else f"@{stage}／{name}"  # i18n-ignore：來源名稱是資料


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
    archive: str | None = None
    """zip 裡的檔（讀取方式 ``zip_lines``）：zip 檔的路徑（和 ``path`` 一樣的寫法）。"""
    archive_bytes: int | None = None
    archive_sha256: str | None = None
    """zip 檔本身的大小與 SHA-256（``bytes``、``sha256`` 是 zip 裡這個檔解開後的）。"""
    replaced: int = 0
    """寬鬆解碼（``decode_errors: "replace"``）換成 ``�`` 的字數（一個 2 位元組的字算 1 個）。"""
    replaced_lines: tuple[int, ...] = ()
    """前 ``REPLACED_LINES`` 個有換字的行（同一行只記一次）。"""
    auto_picked: bool = False
    """``auto``＋寬鬆時 UTF-8、cp950 都解不開，取樣比較後選了 ``encoding``（警告裡寫出來）。"""

    @property
    def name(self) -> str:
        """這個來源的名稱（和 part 的名稱相同）。"""
        return part_name(self.path, self.sheet)


@dataclass(frozen=True)
class Prepared:
    effective: Path
    """要交給 Polars 掃描的 UTF-8 檔（原檔或暫存檔）；暫存成 Parquet 時是放那些 Parquet 的資料夾。"""
    encoding: str
    sha256: str
    bytes: int
    bom: bool
    lines: int
    parquet: tuple[Path, ...] = ()
    """逐行讀取、要轉碼的檔：依序的暫存 Parquet（一段一個，``line`` 一欄）；其他情況是空的。"""
    replaced: int = 0
    replaced_lines: tuple[int, ...] = ()
    auto_picked: bool = False
    """寬鬆解碼的結果，見 ``SourceFile`` 同名的欄位。"""


@dataclass
class Replaced:
    """寬鬆解碼的統計，照檔案順序一段一段加進來（平行轉碼時段也是照順序收的，行號才對）。"""

    count: int = 0
    """換成 ``�`` 的字數。"""
    lines: list[int] = field(default_factory=list)
    """前 ``REPLACED_LINES`` 個有換字的行（從 1 算，同一行只記一次）。"""
    newlines: int = 0
    """已經收到的段共有幾個 ``\\n``。"""

    def add(self, stats: Stats) -> None:
        count, lines, newlines = stats
        self.count += count
        for before in lines:
            line = self.newlines + before + 1
            if len(self.lines) < REPLACED_LINES and (not self.lines or self.lines[-1] != line):
                self.lines.append(line)
        self.newlines += newlines

    def clear(self) -> None:
        self.count, self.newlines = 0, 0
        self.lines.clear()


def _alive(prepared: Prepared) -> bool:
    """快取的暫存檔還在（暫存資料夾可能被清掉）。"""
    return prepared.effective.exists() and all(f.exists() for f in prepared.parquet)


_cache: dict[tuple[str, int, int, str, bool], Prepared] = {}
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
    """有 ``*``、``?``、``[`` 時可能是 glob（``expand`` 會先看這個路徑本身是不是檔案）。"""
    return any(c in entry for c in "*?[")


def expand(entry: str, base_dir: Path) -> list[Path]:
    """檔案清單的一項找到的檔：相對路徑以 recipe 資料夾為基準。

    這個路徑本身就是檔案時直接用（檔名、資料夾名稱可以有 ``[``、``?``，例如「明細[1].csv」）；
    不是檔案、又有 ``*``、``?``、``[`` 時才當成 glob（依路徑排序）。
    """
    full = Path(entry) if Path(entry).is_absolute() else base_dir / entry
    if full.is_file():
        return [full]
    if is_glob(entry):
        return sorted(Path(p) for p in glob.glob(str(full), recursive=True) if Path(p).is_file())
    return []


def check_entries(entries: list[str], base_dir: Path) -> list[dict[str, Any]]:
    """每一項找到幾個檔（給畫面顯示，不讀內容）。"""
    return [{"path": entry, "found": len(expand(entry, base_dir))} for entry in entries]


def resolve(entries: str | list[str], base_dir: Path) -> list[Path]:
    """依檔案清單找出所有檔：依清單順序，同一項裡依路徑排序，同一個實際的檔只留一次。"""
    if isinstance(entries, str):
        entries = [entries]
    if not entries:
        raise InputError(t("還沒有加入資料檔", "No data files added yet"))
    seen: set[Path] = set()
    found = []
    for entry in entries:
        matches = expand(entry, base_dir)
        if not matches:
            if is_glob(entry):
                message = t(
                    f"找不到符合「{entry}」的檔案（相對於 {base_dir}）",
                    f'No files match "{entry}" (relative to {base_dir})',
                )
            else:
                message = t(
                    f"找不到檔案「{entry}」（相對於 {base_dir}）",
                    f'File "{entry}" not found (relative to {base_dir})',
                )
            raise InputError(message)
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
    return t(
        f"{encoding} 在第 {offset} 個位元組（第 {newlines + 1} 行，0x{byte:02X}）無法解碼",
        f"{encoding} can't decode byte 0x{byte:02X} at offset {offset} (line {newlines + 1})",
    )


_LONE_CR = re.compile(rb"\r(?!\n)")


def _check_utf8(path: Path, progress: Progress) -> tuple[str, bool, bool, str | None, int]:
    """一次讀完：回傳（sha256, 是否有 BOM, 是否有單獨的 \\r 換行, UTF-8 錯誤訊息或 None,
    第一個不合 UTF-8 的位置；``auto``＋寬鬆從這裡取樣）。"""
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    total, done, breaks = path.stat().st_size, 0, LineCounter()
    bom, lone_cr, error, carry, bad = False, False, None, b"", 0
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
                    bad = done + pos
                    error = _failure("utf-8", bad, breaks.before(chunk, pos), chunk[pos])
            breaks.feed(chunk)
            done += len(chunk)
            _report(progress, t("檢查編碼", "Checking encoding"), path.name, done, total)
            if error is not None:
                break  # 不是 UTF-8：改用其他編碼轉碼時會重新讀一次
    if carry:
        lone_cr = True
    if error is None:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            bad = max(total - 1, 0)
            line = breaks.finish() + 1
            error = t(
                f"utf-8 在檔案結尾（第 {line} 行）有不完整的字元",
                f"utf-8: incomplete character at the end of the file (line {line})",
            )
    return digest.hexdigest(), bom, lone_cr, error, bad


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
    """offset 之前有幾個換行（只在解碼失敗、要寫錯誤訊息時才算；和逐行讀取一樣，單獨的 ``\\r`` 也算）。"""
    breaks, done = LineCounter(), 0
    with path.open("rb") as f:
        while done < offset and (chunk := f.read(min(CHUNK, offset - done))):
            breaks.feed(chunk)
            done += len(chunk)
    return breaks.finish()  # offset 那個位元組解不開、不會是換行：最後的 \r 是單獨的


def _decode_error(
    path: Path,
    encoding: str,
    failure: tuple[int, int],
    newlines_before: Callable[[int], int] | None = None,
) -> DecodeError:
    """轉不了的字的訊息（第幾行）；``newlines_before(offset)`` 是那個位置之前有幾個換行（預設讀原檔來數）。"""
    offset, byte = failure
    newlines = (newlines_before or (lambda pos: _line_of(path, pos)))(offset)
    if byte < 0:
        return DecodeError(
            t(
                f"{encoding} 在檔案結尾（第 {newlines + 1} 行）有不完整的字元",
                f"{encoding}: incomplete character at the end of the file (line {newlines + 1})",
            )
        )
    return DecodeError(_failure(encoding, offset, newlines, byte))


def _transcode_single(path: Path, encoding: str, target: Path, progress: Progress) -> str:
    digest = hashlib.sha256()
    total, done = path.stat().st_size, 0

    def on_chunk(chunk: bytes) -> None:
        nonlocal done
        digest.update(chunk)
        done += len(chunk)
        _report(progress, t("轉碼", "Transcoding"), path.name, done, total)

    with path.open("rb") as f, target.open("wb") as out:
        failure, _ = decode_range(f, out, encoding, on_chunk=on_chunk)
    if failure is not None:
        raise _decode_error(path, encoding, failure)
    return digest.hexdigest()


class PoolUnavailable(RuntimeError):
    """開不了工作程序（例如權限受管控的電腦不讓開新程序），或工作程序意外結束：改用單核心轉碼。

    讀寫檔的錯誤（暫存碟空間不足、檔案被鎖住…）不是這種，照原樣往外丟。
    """


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
    開不了工作程序、工作程序意外結束時丟出 ``PoolUnavailable``；其他錯誤（例如讀寫檔失敗）照原樣丟出。
    """
    total = path.stat().st_size
    cuts = segment_bounds(str(path), total, segment)
    if len(cuts) <= 2:
        return None
    # 中間的段不是檔案開頭：utf-8-sig 不能在那裡拿掉 BOM
    middle = "utf-8" if encoding == "utf-8-sig" else encoding
    parts = [target.with_name(f"{target.name}.part{i}") for i in range(len(cuts) - 1)]
    jobs = [
        (str(path), encoding if i == 0 else middle, cuts[i], cuts[i + 1], str(parts[i]), "strict")
        for i in range(len(parts))
    ]
    digest = hashlib.sha256()  # SHA-256 一定要從頭讀到尾：主程序另外用一條執行緒算

    def hash_file() -> None:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)

    hasher = threading.Thread(target=hash_file, daemon=True)
    hasher.start()
    futures: list[Any] = []
    try:
        try:
            workers = pool or _get_pool()
            futures = [workers.submit(transcode_segment, job) for job in jobs]
        except (OSError, RuntimeError, BrokenProcessPool) as err:  # 開不了工作程序
            raise PoolUnavailable(str(err)) from err
        done = 0
        for i, future in enumerate(futures):  # 依序等：第一個失敗的就是檔案裡最前面的錯誤
            try:
                failure, _ = future.result()
            except BrokenProcessPool as err:  # 工作程序意外結束（例如被系統擋掉）
                raise PoolUnavailable(str(err)) from err
            if failure is not None:
                raise _decode_error(path, encoding, failure)
            done += cuts[i + 1] - cuts[i]
            _report(progress, t("轉碼", "Transcoding"), path.name, done, total)
        with target.open("wb") as out:
            for part in parts:
                with part.open("rb") as f:
                    shutil.copyfileobj(f, out, CHUNK)
                part.unlink()
    finally:
        for rest in futures:  # 失敗時還沒開始的段不用做了
            rest.cancel()
        for part in parts:
            with contextlib.suppress(OSError):  # 還在寫的段（Windows 上刪不掉）留給暫存資料夾清
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

    大檔用多個程序同時轉（結果和單核心完全相同）；開不了工作程序時改用單核心，這次開著的期間都不再試。
    讀寫檔的錯誤（暫存碟空間不足之類）照原樣丟出，不算「開不了工作程序」。
    \r\n 原樣保留（scan_lines 會處理），只有單獨的 \r 換行才改成 \n。
    """
    minimum = PARALLEL_MIN if parallel_min is None else parallel_min
    if _workers() and path.stat().st_size >= minimum:
        try:
            digest = _transcode_parallel(path, encoding, target, progress, segment or SEGMENT)
            if digest is not None:
                return digest
        except PoolUnavailable:  # 例如公司電腦不讓開新程序
            global _pool_failed
            _pool_failed = True
            _shutdown_pool()
    return _transcode_single(path, encoding, target, progress)


def _count_lines(path: Path) -> int:
    return pl.scan_lines(path).select(pl.len()).collect(engine="streaming").item()


LINES_ROW_GROUP = 8_192
"""逐行讀取的暫存 Parquet 每個 row group 最多幾行：讀回來時一次解開一個 row group，記憶體才固定。"""


def _give_up_pool() -> None:
    """開不了工作程序（或工作程序意外結束）：這次開著的期間都用單核心，不再重試。"""
    global _pool_failed
    _pool_failed = True
    _shutdown_pool()


def _hash_in_background(path: Path) -> tuple[threading.Thread, Any]:
    """另開一條執行緒從頭讀到尾算 SHA-256（轉碼是分段做的，SHA-256 一定要依序讀整個檔）。"""
    digest = hashlib.sha256()

    def run() -> None:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, digest


def _segments(
    path: Path,
    encoding: str,
    folder: Path,
    progress: Progress,
    segment: int,
    pool: ProcessPoolExecutor | None,
    ahead: int,
    errors: str = "strict",
    replaced: Replaced | None = None,
) -> Iterator[Path]:
    """在換行處切段、轉成 UTF-8，依檔案順序一段一段交出（每段一個文字檔，呼叫的人用完就刪）。

    ``pool`` 是工作程序時同時轉好幾段，但在路上（轉碼中，或轉好了還沒交出）的段最多 ``ahead`` 個：
    前面的段交出去了才送下一段，轉碼途中的暫存文字不會累積成一整份。``pool`` 是 None 時在這個程序依序轉。

    轉不了的字依檔案順序回報第一個（和單核心相同的訊息）；開不了工作程序、工作程序意外結束時丟出
    ``PoolUnavailable``。結束時（包括出錯、呼叫的人提早停下）刪掉還沒交出的段。
    ``errors`` 是 ``LENIENT`` 時解不開的字換成 ``�``，每段的統計照檔案順序加進 ``replaced``。
    """
    total = path.stat().st_size
    cuts = segment_bounds(str(path), total, segment)
    # 中間的段不是檔案開頭：utf-8-sig 不能在那裡拿掉 BOM
    middle = "utf-8" if encoding == "utf-8-sig" else encoding
    jobs = [
        (
            str(path),
            encoding if i == 0 else middle,
            cuts[i],
            cuts[i + 1],
            str(folder / f"{i:06d}.txt"),
            errors,
        )
        for i in range(len(cuts) - 1)
    ]
    label = t("轉碼", "Transcoding")
    pending: collections.deque[tuple[int, Any]] = collections.deque()

    def submit(i: int) -> None:
        try:
            pending.append((i, pool.submit(transcode_segment, jobs[i])))  # type: ignore[union-attr]
        except (OSError, RuntimeError, BrokenProcessPool) as err:  # 開不了工作程序
            raise PoolUnavailable(str(err)) from err

    def received(failure: Any, stats: Stats) -> None:
        if failure is not None:
            raise _decode_error(path, encoding, failure)
        if replaced is not None:
            replaced.add(stats)

    try:
        if pool is None:
            for i, job in enumerate(jobs):
                received(*transcode_segment(job))
                _report(progress, label, path.name, cuts[i + 1], total)
                yield Path(job[4])
            return
        following = 0
        while following < len(jobs) and len(pending) < ahead:
            submit(following)
            following += 1
        while pending:
            i, future = pending.popleft()
            try:
                outcome = future.result()
            except BrokenProcessPool as err:  # 工作程序意外結束（例如被系統擋掉）
                raise PoolUnavailable(str(err)) from err
            received(*outcome)
            _report(progress, label, path.name, cuts[i + 1], total)
            yield Path(jobs[i][4])
            if following < len(jobs):
                submit(following)
                following += 1
    finally:
        for _, future in pending:  # 還沒開始的段不用做了；正在轉的等它寫完再刪
            future.cancel()
        with contextlib.suppress(Exception):
            wait_futures([future for _, future in pending])
        for job in jobs:
            with contextlib.suppress(OSError):  # Windows 上刪不掉的留給暫存資料夾清
                Path(job[4]).unlink(missing_ok=True)


def _write_lines(segments: Iterator[Path], folder: Path, group: int = 1) -> list[Path]:
    """每取到 ``group`` 段轉好的文字，就寫成一個 Parquet（``line`` 一欄）並刪掉那些文字。

    zip 裡的檔段比較小（主程序記憶體裡的位元組），幾段合寫成一個 Parquet，檔數才和一般的檔差不多。
    """
    folder.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    batch: list[Path] = []

    def flush() -> None:
        target = folder / f"{len(files):06d}.parquet"
        pl.scan_lines(list(batch), name="line").sink_parquet(
            target, compression="zstd", row_group_size=LINES_ROW_GROUP
        )
        for text in batch:
            text.unlink()
        batch.clear()
        files.append(target)

    with contextlib.closing(segments):  # type: ignore[type-var]
        for text in segments:
            batch.append(text)
            if len(batch) >= group:
                flush()
        if batch:
            flush()
    return files


def _pick_pool(size: int, minimum: int) -> ProcessPoolExecutor | None:
    """這麼大的檔要不要用工作程序（開不了時這次開著的期間都改用單核心）。"""
    if not (_workers() and size >= minimum):
        return None
    try:
        return _get_pool()
    except (OSError, RuntimeError, BrokenProcessPool):  # 例如公司電腦不讓開新程序
        _give_up_pool()
        return None


def _stage_lines(
    path: Path,
    encoding: str,
    folder: Path,
    progress: Progress,
    *,
    pool: ProcessPoolExecutor | None = None,
    parallel_min: int | None = None,
    segment: int | None = None,
    errors: str = "strict",
    replaced: Replaced | None = None,
) -> tuple[str, tuple[Path, ...], int]:
    """逐行讀取、要轉碼的檔：轉成 UTF-8 後一段一段寫成 zstd 壓縮的 Parquet，放在 ``folder``。

    回傳（原檔的 sha256, 依序的 Parquet 檔, 行數）。讀出來的 ``line`` 和把整份轉成 UTF-8 文字再
    ``scan_lines`` 完全一樣（每段都在 ``\\n`` 後面結束）；行數直接看 Parquet 記的列數，不用再讀一遍。

    大檔用多個工作程序同時轉（``pool`` 預設是共用的工作程序；自我檢測傳自己開的）；開不了工作程序時
    改用單核心重來，這次開著的期間都不再試（自我檢測傳了 ``pool`` 時照原樣丟出 ``PoolUnavailable``）。
    解碼失敗丟出 ``DecodeError``；出錯時 ``folder`` 整個刪掉，不留半套暫存。
    ``errors`` 是 ``LENIENT`` 時不會解碼失敗，換字的統計記在 ``replaced``。
    """
    size = path.stat().st_size
    minimum = PARALLEL_MIN if parallel_min is None else parallel_min
    step = segment or SEGMENT
    workers = pool or _pick_pool(size, minimum)
    count = getattr(workers, "_max_workers", 1) if workers is not None else 1
    ahead = 2 * count
    hasher, digest = _hash_in_background(path)
    try:
        try:
            files = _write_lines(
                _segments(path, encoding, folder, progress, step, workers, ahead, errors, replaced),
                folder,
            )
        except PoolUnavailable:
            if pool is not None:
                raise
            _give_up_pool()
            shutil.rmtree(folder, ignore_errors=True)
            if replaced is not None:
                replaced.clear()
            files = _write_lines(
                _segments(path, encoding, folder, progress, step, None, 2, errors, replaced),
                folder,
            )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        hasher.join()
    lines = pl.scan_parquet(files).select(pl.len()).collect().item() if files else 0
    return digest.hexdigest(), tuple(files), lines


ZIP_SEGMENT = 4 << 20
"""zip 裡的檔每段多大：段是主程序解壓後用位元組交給工作程序的。比原檔的段（32 MB）小：
實測（2 個 1 GB 的 cp950 檔、8 個工作程序）每段 16 MB、8 MB 時工作程序合計最高約 2.6 GB（大塊記憶體釋放後
macOS 的配置器留著不還），4 MB 時約 540 MB，速度一樣。"""
ZIP_GROUP = 8
"""zip 裡的檔幾段合寫成一個 Parquet（8 × 4 MB ≈ 一般檔的一段 32 MB）。"""
_LONE_CR_TEXT = re.compile(rb"\r(?!\n)")


def _cut(buffer: bytes) -> int:
    """可以切段的位置（之後的下一段從這裡開始）：最後一個 ``\\n`` 後面；沒有時最後一個單獨的 ``\\r`` 後面
    （緊接著 ``\\n`` 的不算，``\\r\\n`` 才不會被切開）；都沒有時是 0（還不能切）。
    cp950、Big5-HKSCS、UTF-8 的多位元組字元裡都沒有 0x0A、0x0D，所以這裡一定是一個字元的開頭。"""
    i = buffer.rfind(b"\n")
    if i >= 0:
        return i + 1
    last = 0
    for found in _LONE_CR_TEXT.finditer(buffer, 0, len(buffer) - 1):  # 最後一個位元組後面可能接 \n
        last = found.end()
    return last


SAMPLE = 4 << 20
"""``auto``＋寬鬆：UTF-8、cp950 都解不開時，取這麼大的一段比較哪個解不開的字比較少。"""


def _sample_lines(data: bytes, start: int) -> bytes:
    """從 ``start``（一行的開頭）起最多 ``SAMPLE`` 個位元組，切在行尾（一整行都超過時就不切）。"""
    piece = data[start : start + SAMPLE]
    if start + SAMPLE < len(data):
        end = piece.rfind(b"\n")
        if end >= 0:
            piece = piece[: end + 1]
    return piece


def _stream_segments(
    read: Callable[[int], bytes],
    total: int,
    name: str,
    encoding: str,
    folder: Path,
    progress: Progress,
    segment: int,
    pool: ProcessPoolExecutor | None,
    ahead: int,
    errors: str = "strict",
    replaced: Replaced | None = None,
) -> Iterator[Path]:
    """依序讀出的位元組（zip 裡的檔）在換行處切段、轉成 UTF-8，依順序一段一段交出（每段一個文字檔）。

    和 ``_segments`` 一樣：``pool`` 是工作程序時同時轉好幾段，在路上的段最多 ``ahead`` 個；``pool`` 是 None 時
    在這個程序依序轉；第一個轉不了的字照檔案順序回報；結束時刪掉還沒交出的段（交出的段歸呼叫的人刪）。
    每段記著它之前有幾個換行：轉不了時直接算出第幾行，不用重新解壓一遍。

    ``errors``、``replaced`` 和 ``_segments`` 一樣。轉不了時丟出的 ``DecodeError`` 帶著 ``sample``：
    從那一行開頭起的一段（還在記憶體裡），``auto``＋寬鬆拿它比較編碼，不用重新解壓。
    """
    middle = "utf-8" if encoding == "utf-8-sig" else encoding
    label = t("解壓與轉碼", "Extracting and transcoding")
    Work = tuple[bytes, str, int, str, str]
    pending: collections.deque[tuple[Any, Work, int]] = collections.deque()
    made: list[Path] = []
    handed: set[Path] = set()
    state = {"start": 0, "newlines": 0}

    def job(data: bytes) -> tuple[Work, int]:
        """這一段的工作（位元組, 編碼, 起點, 暫存檔, errors）與它之前有幾個換行。"""
        dst = folder / f"{len(made):06d}.txt"
        work = (data, encoding if not made else middle, state["start"], str(dst), errors)
        before = state["newlines"]
        made.append(dst)
        state["start"] += len(data)
        state["newlines"] += line_breaks(data)
        return work, before

    def finished(outcome: tuple[Any, Stats], work: Work, before: int) -> Path:
        failure, stats = outcome
        data, _, start, dst, _ = work
        if failure is not None:
            count = lambda offset: before + line_breaks(data[: offset - start])  # noqa: E731
            err = _decode_error(Path(name), encoding, failure, count)
            at = min(max(failure[0] - start, 0), len(data))
            err.sample = _sample_lines(data, data.rfind(b"\n", 0, at) + 1)  # type: ignore[attr-defined]
            raise err
        if replaced is not None:
            replaced.add(stats)
        _report(progress, label, name, start + len(data), total)
        handed.add(Path(dst))
        return Path(dst)

    def pieces() -> Iterator[bytes]:
        buffer = b""
        while chunk := read(min(CHUNK, segment)):
            buffer += chunk
            if len(buffer) >= segment:
                cut = _cut(buffer)
                if cut:
                    yield buffer[:cut]
                    buffer = buffer[cut:]
        if buffer:
            yield buffer

    def result(future: Any) -> Any:
        try:
            return future.result()
        except BrokenProcessPool as err:  # 工作程序意外結束
            raise PoolUnavailable(str(err)) from err

    try:
        if pool is None:
            for data in pieces():
                work, before = job(data)
                yield finished(transcode_bytes(work), work, before)
            return
        for data in pieces():
            work, before = job(data)
            try:
                future = pool.submit(transcode_bytes, work)
            except (OSError, RuntimeError, BrokenProcessPool) as err:  # 開不了工作程序
                raise PoolUnavailable(str(err)) from err
            pending.append((future, work, before))
            while len(pending) >= ahead:
                future, work, before = pending.popleft()
                yield finished(result(future), work, before)
        while pending:
            future, work, before = pending.popleft()
            yield finished(result(future), work, before)
    finally:
        for future, _, _ in pending:
            future.cancel()
        with contextlib.suppress(Exception):
            wait_futures([future for future, _, _ in pending])
        for dst in made:
            if dst not in handed:
                with contextlib.suppress(OSError):
                    dst.unlink(missing_ok=True)


def _stage_member(
    member: zip_lines.Member,
    name: str,
    encoding: str,
    folder: Path,
    progress: Progress,
    *,
    pool: ProcessPoolExecutor | None = None,
    parallel_min: int | None = None,
    segment: int | None = None,
    errors: str = "strict",
    replaced: Replaced | None = None,
) -> tuple[str, tuple[Path, ...], int, bool]:
    """zip 裡的一個檔：一邊解壓一邊切段、轉成 UTF-8，一段一段寫成 Parquet（和 ``_stage_lines`` 同一種暫存）。

    回傳（解開後內容的 sha256, 依序的 Parquet 檔, 行數, 開頭有沒有 BOM）。轉不了丟出 ``DecodeError``，
    zip 壞了丟出 ``zip_lines.ZipError``；出錯時 ``folder`` 整個刪掉。
    ``errors`` 是 ``LENIENT`` 時不會轉不了，換字的統計記在 ``replaced``。
    """
    minimum = PARALLEL_MIN if parallel_min is None else parallel_min
    step = segment or ZIP_SEGMENT
    workers = pool or _pick_pool(member.size, minimum)

    def run(executor: ProcessPoolExecutor | None) -> tuple[str, list[Path], bool]:
        count = getattr(executor, "_max_workers", 1) if executor is not None else 1
        digest = hashlib.sha256()
        first = {"bom": None}
        with zip_lines.open_member(member) as stream:

            def read(size: int) -> bytes:
                chunk = stream.read(size)
                if first["bom"] is None and chunk:
                    first["bom"] = chunk.startswith(codecs.BOM_UTF8)
                digest.update(chunk)
                return chunk

            segments = _stream_segments(
                read,
                member.size,
                name,
                encoding,
                folder,
                progress,
                step,
                executor,
                2 * count,
                errors,
                replaced,
            )
            try:
                files = _write_lines(segments, folder, ZIP_GROUP)
            except DecodeError as err:  # auto＋寬鬆取樣時要知道檔案開頭有沒有 BOM（樣本看不到開頭）
                err.bom = bool(first["bom"])  # type: ignore[attr-defined]
                raise
        return digest.hexdigest(), files, bool(first["bom"])

    try:
        try:
            sha, files, bom = run(workers)
        except PoolUnavailable:
            if pool is not None:
                raise
            _give_up_pool()
            shutil.rmtree(folder, ignore_errors=True)
            if replaced is not None:
                replaced.clear()
            sha, files, bom = run(None)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    lines = pl.scan_parquet(files).select(pl.len()).collect().item() if files else 0
    return sha, tuple(files), lines, bom


def _lenient_hint(message: str) -> DecodeError:
    """逐行讀取解碼失敗：訊息後面提示可以打開寬鬆解碼（CSV 不能用，所以不加）。"""
    return DecodeError(
        message
        + t(
            '。可以把「解不開的字」改成「換成 �」（Recipe：input 的 "decode_errors": "replace"）',
            '. You can set "Undecodable characters" to "Replace with �" '
            '(Recipe: "decode_errors": "replace" in input)',
        )
    )


def _pick(sample: bytes, bom: bool) -> str:
    """``auto``＋寬鬆、UTF-8 與 cp950 都解不開：樣本裡解不開的字比較少的那個。

    通常差很多：有造字的 cp950 檔當 UTF-8 解，幾乎每個中文字都解不開；只壞了幾個位元組的 UTF-8 檔當 cp950 解也是。
    一樣多時（例如只有英數字和造字的檔：``8F 46`` 當 UTF-8 解也只換 1 個，但第 2 個位元組 ``F`` 會漏出來）選 cp950，
    只有檔案開頭是 UTF-8 的 BOM 時選 UTF-8（用 cp950 讀的話 BOM 會變成亂字）。
    """
    fallback = AUTO_ORDER[1]
    utf8, other = count_replaced(sample, "utf-8"), count_replaced(sample, fallback)
    return "utf-8-sig" if utf8 < other or (utf8 == other and bom) else fallback


def _file_sample(path: Path, bad: int) -> bytes:
    """從 ``bad``（第一個不合 UTF-8 的位置）那一行的開頭起，最多 ``SAMPLE`` 個位元組，切在行尾。"""
    with path.open("rb") as f:
        back = max(bad - (64 << 10), 0)  # 往前找那一行的開頭（找不到就從這裡開始）
        f.seek(back)
        start = back + f.read(bad - back).rfind(b"\n") + 1
        f.seek(start)
        return _sample_lines(f.read(SAMPLE + 1), 0)


def _with_replaced(prepared: Prepared, replaced: Replaced, picked: bool = False) -> Prepared:
    return replace(
        prepared,
        replaced=replaced.count,
        replaced_lines=tuple(replaced.lines),
        auto_picked=picked,
    )


def prepare_member(
    member: zip_lines.Member,
    name: str,
    encoding: str = "auto",
    progress: Progress = None,
    errors: str = "strict",
) -> Prepared:
    """zip 裡的一個檔：暫存成一段一段的 Parquet（zip 裡的檔不能直接交給 Polars 讀，UTF-8 的也一樣）。結果會快取。

    ``auto``：先當 UTF-8 一邊解壓一邊寫暫存，UTF-8 的檔只解壓一遍；遇到不合 UTF-8 的位元組就丟掉已寫的暫存，
    改用 cp950 從頭再解壓一遍（cp950 的檔通常前幾行就有中文，浪費的只有開頭一點點）。

    ``errors`` 是 ``LENIENT``（寬鬆解碼）時解不開的字換成 ``�``。``auto`` 時先照上面嚴格地試，
    兩種都解不開才用 UTF-8 失敗那一段當樣本（還在記憶體裡，不用重新解壓）選編碼，再寬鬆地轉。
    """
    if encoding not in SUPPORTED:
        raise DecodeError(
            t(
                f"不支援的編碼：{encoding}（可用：{', '.join(SUPPORTED)}）",
                f"Unsupported encoding: {encoding} (available: {', '.join(SUPPORTED)})",
            )
        )
    stat = member.archive.stat()
    key = (
        "zip",
        str(member.archive.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
        member.path,
        member.crc,
        member.compressed,
        encoding,
        errors,
    )
    with _lock:
        if key in _cache and _alive(_cache[key]):  # type: ignore[index]
            return _cache[key]  # type: ignore[index]
    folder = staging_dir() / hashlib.sha256(repr(key).encode()).hexdigest()[:24]

    def stage(candidate: str, mode: str = "strict", picked: bool = False) -> Prepared:
        replaced = Replaced()
        sha, files, lines, bom = _stage_member(
            member, name, candidate, folder, progress, errors=mode, replaced=replaced
        )
        label = ("utf-8-sig" if bom else "utf-8") if candidate.startswith("utf-8") else candidate
        prepared = Prepared(folder, label, sha, member.size, False, lines, files)
        return _with_replaced(prepared, replaced, picked)

    if member.size == 0:
        prepared = Prepared(folder, "utf-8", hashlib.sha256().hexdigest(), 0, False, 0)
        folder.mkdir(parents=True, exist_ok=True)
    elif encoding == "auto":
        failures, sample, bom = [], b"", False
        for candidate in AUTO_ORDER:
            try:
                prepared = stage(candidate)
            except DecodeError as err:
                failures.append(str(err))
                if not sample:  # UTF-8 失敗的那一段，與檔案開頭有沒有 BOM
                    sample, bom = getattr(err, "sample", b""), getattr(err, "bom", False)
                continue
            break
        else:
            if errors != LENIENT:
                raise _lenient_hint(
                    t(f"「{name}」無法判斷編碼：", f'"{name}": couldn\'t detect the encoding: ')
                    + t("；", "; ").join(failures)
                )
            prepared = stage(_pick(sample, bom), LENIENT, picked=True)
    else:
        try:
            prepared = stage("utf-8-sig" if encoding == "utf-8" else encoding, errors)
        except DecodeError as err:  # zip 裡有很多檔：訊息前面寫是哪一個
            raise _lenient_hint(t(f"「{name}」：{err}", f'"{name}": {err}')) from None

    with _lock:
        _cache[key] = prepared  # type: ignore[index]
    return prepared


_archive_digests: dict[tuple[str, int, int], str] = {}


class _ArchiveDigest:
    """zip 檔本身的 SHA-256：另開一條執行緒從頭讀一遍（跟著解壓同時做），同一個 zip 只算一次。"""

    def __init__(self, path: Path) -> None:
        stat = path.stat()
        self.key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        self.bytes = stat.st_size
        self._thread: threading.Thread | None = None
        self._digest = hashlib.sha256()
        with _lock:
            self.value = _archive_digests.get(self.key)
        if self.value is None:
            self._thread, self._digest = _hash_in_background(path)

    def result(self) -> str:
        if self.value is None:
            assert self._thread is not None
            self._thread.join()
            self.value = self._digest.hexdigest()
            with _lock:
                _archive_digests[self.key] = self.value
        return self.value


@dataclass(frozen=True)
class ZipEntry:
    """掃描結果的一個 zip：符合的檔（依路徑排序）。"""

    path: str
    """zip 檔（和來源檔一樣的寫法：Recipe 資料夾裡的是相對路徑）。"""
    archive: Path
    members: list[zip_lines.Member]


@dataclass(frozen=True)
class ZipScan:
    """讀取方式 ``zip_lines``：每個 zip 裡符合的檔（只讀 zip 的目錄，不解壓）。"""

    zips: list[ZipEntry]
    estimate: int
    """暫存大約要多少空間（bytes）。"""
    free: int | None
    """暫存資料夾所在磁碟剩多少（bytes；讀不到時是 None）。"""

    @property
    def members(self) -> list[tuple[str, zip_lines.Member]]:
        return [(z.path, m) for z in self.zips for m in z.members]

    @property
    def empty(self) -> list[str]:
        """沒有符合的檔的 zip（略過，掃描清單提醒、處理紀錄記下來）。"""
        return [z.path for z in self.zips if not z.members]

    def problems(self, members: str) -> list[str]:
        """不能讀的原因（有任何一項就不能載入或匯出）。"""
        out = [
            t(f"「{zpath}」裡的「{m.path}」：{why}", f'"{m.path}" in "{zpath}": {why}')
            for zpath, m in self.members
            if (why := m.problem()) is not None
        ]
        if self.zips and not self.members:
            out.append(
                t(
                    f"所有 zip 裡都沒有符合「{members}」的檔",
                    f'No file in any zip matches "{members}"',
                )
            )
        if self.free is not None and self.estimate > self.free:
            out.append(
                t(
                    f"暫存空間不夠：大約要 {_size_text(self.estimate)}，暫存資料夾所在的磁碟只剩 "
                    f"{_size_text(self.free)}",
                    f"Not enough temp space: about {_size_text(self.estimate)} is needed, "
                    f"but only {_size_text(self.free)} is free where the temp folder is",
                )
            )
        return out


def _size_text(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def scan_zips(entries: str | list[str], members: str, base_dir: Path) -> ZipScan:
    """讀取方式 ``zip_lines`` 的掃描：用檔案清單找出 zip，讀每個 zip 的目錄，列出符合 ``members`` 的檔。"""
    if not (members or "").strip():
        raise InputError(
            t(
                "要填「zip 裡的檔」（例如 2026*/HEADER*.TXT）",
                'Fill in "Files in the zip" (for example 2026*/HEADER*.TXT)',
            )
        )
    zips = []
    for path in resolve(entries, base_dir):
        try:
            found = zip_lines.matching(path, members)
        except zip_lines.ZipError as err:
            raise InputError(str(err)) from None
        zips.append(ZipEntry(display_name(path, base_dir), path, found))
    every = [m for z in zips for m in z.members]
    estimate = zip_lines.estimate_staging(every, _workers(), ZIP_SEGMENT, ZIP_GROUP)
    try:
        free: int | None = shutil.disk_usage(staging_dir()).free
    except OSError:
        free = None
    return ZipScan(zips, estimate, free)


def _scan_zip_parts(
    entries: str | list[str],
    members: str,
    encoding: str,
    base_dir: Path,
    progress: Progress,
    on_file: Callable[[int, str], None] | None,
    errors: str = "strict",
) -> tuple[list[Part], list[SourceFile]]:
    """讀取方式 ``zip_lines``：zip 裡每個符合的檔是一個來源（名稱是「zip 的路徑/zip 裡的路徑」）。"""
    scan = scan_zips(entries, members, base_dir)
    problems = scan.problems(members)
    if problems:
        raise InputError(t("；", "; ").join(problems))
    parts, files = [], []
    i = 0
    for entry in scan.zips:
        if not entry.members:
            continue
        digest = _ArchiveDigest(entry.archive)
        found = []
        for member in entry.members:
            name = f"{entry.path}/{member.path}"
            if on_file is not None:
                on_file(i, name)
            i += 1
            try:
                prepared = prepare_member(member, name, encoding, progress, errors)
            except zip_lines.ZipError as err:
                raise InputError(str(err)) from None
            lf, rows = _scan_one(prepared, "text_lines", name)
            parts.append((name, lf))
            found.append(_source_file(name, prepared, rows))
        sha = digest.result()
        files += [
            replace(f, archive=entry.path, archive_bytes=digest.bytes, archive_sha256=sha)
            for f in found
        ]
    return parts, files


def source_count(entries: str | list[str], reader: str, base_dir: Path, members: str = "") -> int:
    """這個讀取設定有幾個來源檔（進度的「已處理 N / M 個檔」）；zip 是裡面符合的檔數。"""
    if reader == "zip_lines":
        return len(scan_zips(entries, members, base_dir).members)
    return len(resolve(entries, base_dir))


def prepare(
    path: Path,
    encoding: str = "auto",
    progress: Progress = None,
    lines: bool = False,
    errors: str = "strict",
) -> Prepared:
    """確保檔案可以用 UTF-8 掃描：必要時轉碼成暫存檔。結果會快取。

    ``lines``：逐行讀取用。要轉碼時暫存成一段一段的 Parquet（``_stage_lines``，小很多）；
    否則（CSV）暫存成一整份 UTF-8 文字。不用轉碼的 UTF-8 檔兩種都直接讀原檔。

    ``errors`` 是 ``LENIENT``（寬鬆解碼，只有逐行讀取能用）時解不開的字換成 ``�``：
    合法的 UTF-8 檔照樣直接讀原檔；``auto`` 時先照原本的流程嚴格地試 UTF-8 → cp950，兩種都解不開
    才從第一個不合 UTF-8 的那一行取樣選編碼（``_pick``），再寬鬆地轉整個檔。
    """
    if encoding not in SUPPORTED:
        raise DecodeError(
            t(
                f"不支援的編碼：{encoding}（可用：{', '.join(SUPPORTED)}）",
                f"Unsupported encoding: {encoding} (available: {', '.join(SUPPORTED)})",
            )
        )
    lenient = lines and errors == LENIENT
    stat = path.stat()
    key = (
        str(path.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
        encoding,
        lines,
        LENIENT if lenient else "strict",
    )
    with _lock:
        if key in _cache and _alive(_cache[key]):
            return _cache[key]

    name = hashlib.sha256(repr(key).encode()).hexdigest()[:24]
    hint = _lenient_hint if lines else DecodeError

    def convert(candidate: str, mode: str = "strict", picked: bool = False) -> Prepared:
        """用 candidate 轉碼成暫存：逐行讀取是 Parquet，CSV 是 UTF-8 文字。"""
        if lines:
            folder = staging_dir() / name
            replaced = Replaced()
            digest, files, count = _stage_lines(
                path, candidate, folder, progress, errors=mode, replaced=replaced
            )
            prepared = Prepared(folder, candidate, digest, stat.st_size, False, count, files)
            return _with_replaced(prepared, replaced, picked)
        staged = staging_dir() / f"{name}.txt"
        digest = _transcode(path, candidate, staged, progress)
        return Prepared(staged, candidate, digest, stat.st_size, False, _count_lines(staged))

    if stat.st_size == 0:
        prepared = Prepared(path, "utf-8", hashlib.sha256().hexdigest(), 0, False, 0)
    elif encoding in ("auto", "utf-8", "utf-8-sig"):
        digest, bom, lone_cr, error, bad = _check_utf8(path, progress)
        utf8 = "utf-8-sig" if bom else "utf-8"
        if error is None and not lone_cr:
            prepared = Prepared(path, utf8, digest, stat.st_size, bom, _count_lines(path))
        elif error is None:  # UTF-8，但有單獨的 \r 換行：轉成暫存檔統一換行
            prepared = replace(convert("utf-8-sig"), encoding=utf8, sha256=digest)
        elif encoding != "auto":
            if not lenient:
                raise hint(error)
            prepared = replace(convert("utf-8-sig", LENIENT), encoding=utf8)
        else:
            failures = [error]
            for candidate in AUTO_ORDER[1:]:
                try:
                    prepared = convert(candidate)
                except DecodeError as err:
                    failures.append(str(err))
                    continue
                break
            else:
                if not lenient:
                    raise hint(
                        t("無法判斷編碼：", "Couldn't detect the encoding: ")
                        + t("；", "; ").join(failures)
                    )
                picked = _pick(_file_sample(path, bad), bom)
                prepared = convert(picked, LENIENT, picked=True)
                if picked == "utf-8-sig":
                    prepared = replace(prepared, encoding=utf8)
    else:
        try:
            prepared = convert(encoding, LENIENT if lenient else "strict")
        except DecodeError as err:
            raise hint(str(err)) from None

    with _lock:
        _cache[key] = prepared
    return prepared


def _scan_one(prepared: Prepared, reader: str, name: str) -> tuple[pl.LazyFrame, int]:
    if prepared.lines == 0:
        empty = {SOURCE_FILE: pl.String, LINE_NO: pl.Int64}
        return pl.LazyFrame(schema=empty if reader == "csv" else {**empty, "line": pl.String}), 0
    if reader == "text_lines":
        if prepared.parquet:  # 轉碼後的暫存：一段一個 Parquet，依序接起來編行號
            lf = pl.scan_parquet(list(prepared.parquet), row_index_name=LINE_NO, row_index_offset=1)
        else:
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
    raise InputError(t(f"不支援的讀取方式：{reader}", f"Unsupported reader: {reader}"))


def _common_type(column: str, found: list[tuple[str, pl.DataType]]) -> pl.DataType:
    """同名欄在各來源的型別要一樣：整欄都是空值的不算，小數位數不同時取位數多的；其他不一樣就報錯。"""
    typed = [(name, dtype) for name, dtype in found if dtype != pl.Null]
    if not typed:
        return found[0][1]
    first = typed[0][1]
    if all(dtype == first for _, dtype in typed):
        return first
    if all(isinstance(dtype, pl.Decimal) for _, dtype in typed):
        return pl.Decimal(38, max(dtype.scale for _, dtype in typed))  # type: ignore[attr-defined]
    shown = t("、", ", ").join(
        t(f"{name}：{type_label(dtype)}", f"{name}: {type_label(dtype)}") for name, dtype in typed
    )
    raise InputError(
        t(
            f"「{column}」在各來源的型別不一樣（{shown}），請先轉成一樣的型別",
            f'"{column}" has different types in different sources ({shown}); '
            "convert them to the same type first",
        )
    )


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
    members: str = "",
    decode_errors: str = "error",
) -> tuple[list[Part], list[SourceFile]]:
    """每個來源檔一個 LazyFrame（依檔案清單的順序），欄位對齊成同一組。

    ``sheet`` 只有 ``reader="excel"`` 時用：工作表名稱，空白代表第一個工作表。
    ``members`` 只有 ``reader="zip_lines"`` 時用：zip 裡要讀的檔（檔案清單是 zip）。
    ``decode_errors`` 只有逐行讀取（``text_lines``、``zip_lines``）時用：``"replace"`` 是寬鬆解碼，
    解不開的字換成 ``�``（``SourceFile.replaced`` 記字數）；其他讀取方式一律嚴格。
    ``on_file(i, 名稱)`` 在開始讀第 i 個檔（從 0 起）之前呼叫（畫面上的「已處理幾個檔」）。
    """
    lenient = decode_errors == "replace" and reader in ("text_lines", "zip_lines")
    errors = LENIENT if lenient else "strict"
    if reader == "zip_lines":
        parts, files = _scan_zip_parts(
            entries, members, encoding, base_dir, progress, on_file, errors
        )
        return align(parts), files
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
            raise InputError(
                t(
                    f"「{name}」是 Excel 檔：讀取方式請選「Excel 工作表」",
                    f'"{name}" is an Excel file: set "Read as" to "Excel sheet"',
                )
            )
        if reader == "parquet":
            lf, info = _scan_parquet(path, name, progress)
            parts.append((name, lf))
            files.append(info)
            continue
        prepared = prepare(path, encoding, progress, lines=reader == "text_lines", errors=errors)
        lf, rows = _scan_one(prepared, reader, name)
        parts.append((name, lf))
        files.append(_source_file(name, prepared, rows))
    return align(parts), files


def _source_file(name: str, prepared: Prepared, rows: int) -> SourceFile:
    return SourceFile(
        name,
        prepared.bytes,
        prepared.sha256,
        prepared.encoding,
        rows,
        replaced=prepared.replaced,
        replaced_lines=prepared.replaced_lines,
        auto_picked=prepared.auto_picked,
    )


def decode_warning(file: SourceFile) -> str | None:
    """寬鬆解碼換了字的檔的警告（畫面、命令列、錯誤報告共用）；沒有換字時是 None。"""
    if not file.replaced:
        return None
    where = t("、", ", ").join(str(n) for n in file.replaced_lines)
    if len(file.replaced_lines) >= REPLACED_LINES and file.replaced > len(file.replaced_lines):
        where += "…"  # 只記前幾個有換字的行：記滿了，後面可能還有
    text = t(
        f"有 {file.replaced:,} 個字解不開，換成 �（第 {where} 行）",
        f"{file.replaced:,} undecodable character{'s' if file.replaced != 1 else ''} "
        f"replaced with � ({'line' if len(file.replaced_lines) == 1 else 'lines'} {where})",
    )
    # 編碼對、只是有造字時遠低於行數的一半；選錯編碼時通常是行數的好幾倍
    if file.rows and file.replaced > file.rows / 2:
        text += t("，可能選錯編碼", "; the encoding may be wrong")
    if file.auto_picked:
        text += t(f"（自動選了 {file.encoding}）", f" (auto-detected {file.encoding})")
    return text


def parquet_names(columns: list[str]) -> dict[str, str]:
    """parquet 檔的欄名 → 讀進來的欄名：__ 開頭的改名成「原始…」（例如 __line_no → 原始行號）。"""
    renamed = {
        c: f"原始{PARQUET_LABELS.get(c[2:], c[2:])}"  # i18n-ignore：欄名是資料
        if c.startswith("__")
        else c
        for c in columns
    }
    values = list(renamed.values())
    clash = sorted({v for v in values if values.count(v) > 1})
    if clash:
        raise InputError(
            t(
                f"改名後欄名重複：{'、'.join(clash)}",
                f"Duplicate column names after renaming: {', '.join(clash)}",
            )
        )
    return renamed


def _scan_parquet(path: Path, name: str, progress: Progress) -> tuple[pl.LazyFrame, SourceFile]:
    """一個 parquet 檔是一個來源：欄位與型別照檔案；__source_file 是檔名、__line_no 是第幾列（從 1 起）。"""
    _report(progress, t("讀取 Parquet", "Reading Parquet"), name, 0, 0)
    try:
        raw = pl.scan_parquet(path)
        renamed = parquet_names(raw.collect_schema().names())
        rows = raw.select(pl.len()).collect().item()
    except (OSError, pl.exceptions.PolarsError) as err:
        raise InputError(
            t(
                f"「{name}」讀不了（不是 Parquet 檔？）：{err}",
                f'Cannot read "{name}" (not a Parquet file?): {err}',
            )
        ) from None
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
    _report(progress, t("讀取 Excel", "Reading Excel"), name, 0, 0)
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
    return t("、", ", ").join(
        t(f"{name}（{dtype}）", f"{name} ({dtype})") for name, dtype in schema.items()
    )


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
                t(
                    f"來源檔「{name}」的欄位（{_schema_text(schema)}）"
                    f"和「{first_name}」（{_schema_text(expected)}）不同",
                    f'Source file "{name}" has different columns ({_schema_text(schema)}) '
                    f'from "{first_name}" ({_schema_text(expected)})',
                )
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


SAMPLE_MIN_HEAD = 200
SAMPLE_MIN_TAIL = 20
"""每個檔的樣本至少取這麼多行（開頭的標題、欄名，結尾的合計、頁尾都要在樣本裡）。"""
SAMPLE_FLOOR_HEAD = 20
SAMPLE_FLOOR_TAIL = 5
"""檔非常多、下限加起來超過額度時，下限等比例縮小，最少到這麼多行；再多的檔就讓樣本超過額度。"""
MEASURE_FILES = 8
MEASURE_LINES = 2_000
"""量一行在記憶體裡多大：挑最多這麼多個檔（平均分布在清單裡），各讀開頭這麼多行。"""


@dataclass(frozen=True)
class SamplePlan:
    """樣本的計畫：每個來源檔取前幾行與最後幾行（``sample_plan`` 依額度算，或 ``fixed`` 固定行數）。"""

    lines: dict[str, tuple[int, int]]
    """來源檔名稱 → （前幾行, 最後幾行）；整份取的檔是（行數, 0）。"""
    rows: int
    """樣本共幾行。"""
    truncated: bool
    """有沒有任何一個檔被截掉；沒有時樣本就是全部的資料，不算抽樣。"""
    head: int
    """每檔最多前幾行（畫面與錯誤報告的說明用）。"""
    tail: int
    """每檔最多最後幾行。"""
    budget: int | None = None
    """額度（bytes，樣本在記憶體裡的大小）；固定行數的計畫是 None。"""

    @classmethod
    def fixed(cls, files: list[SourceFile], head: int, tail: int) -> SamplePlan:
        """每個檔都取前 head 行與最後 tail 行（測試、指定行數的試跑）。"""
        lines = {f.name: (head, tail) for f in files}
        return cls(
            lines,
            sum(min(f.rows, head + tail) for f in files),
            any(f.rows > head + tail for f in files),
            head,
            tail,
        )


def _line_sizes(parts: list[Part], files: list[SourceFile]) -> dict[str, float]:
    """每個檔估計一行在記憶體裡多大（bytes）：挑幾個檔讀開頭實際量，換算成「原始檔每行的大小」的比例，
    其他檔用自己的「原始檔大小 ÷ 行數」乘上這個比例（原始檔是壓縮過的 Parquet 時，用公式估會低估很多）。

    量的時候各欄分開算再相加，不量整張表：單一個檔的表裡 ``__source_file`` 整欄是同一個值，整張表的
    ``estimated_size()`` 幾乎不算它；樣本是很多個檔接起來的，這一欄每列都算一份（不這樣量，樣本會比額度大
    2～4 成，檔名越長差越多）。"""
    lazy = dict(parts)
    raw = {f.name: f.bytes / f.rows for f in files if f.rows > 0}
    names = [f.name for f in files if f.name in raw and f.name in lazy]
    if not names:
        return {}
    step = max(1, len(names) / MEASURE_FILES)
    picked = sorted({names[int(i * step)] for i in range(min(MEASURE_FILES, len(names)))})
    measured = raw_total = 0.0
    for name in picked:
        head = lazy[name].head(MEASURE_LINES).collect()
        if head.height:
            measured += sum(column.estimated_size() for column in head.get_columns())
            raw_total += raw[name] * head.height
    ratio = measured / raw_total if raw_total > 0 else 1.0
    return {name: max(size * ratio, 1.0) for name, size in raw.items()}


def sample_plan(
    parts: list[Part],
    files: list[SourceFile],
    budget: int,
    head: int,
    tail: int,
) -> SamplePlan:
    """依額度（bytes，樣本在記憶體裡最多佔多少）算每個檔取幾行（docs/memory-improve.md）。

    - 每檔最多前 head 行＋最後 tail 行；至少前 ``SAMPLE_MIN_HEAD``＋最後 ``SAMPLE_MIN_TAIL`` 行（不超過最多）。
      下限加起來超過額度時等比例縮小，最少 ``SAMPLE_FLOOR_HEAD``＋``SAMPLE_FLOOR_TAIL`` 行。
    - 額度平均分給每個檔；比分到的小的檔整份取，剩下的再平均分給其他檔。
    - 每檔取到的行數裡結尾占 1/10（夾在下限與 tail 之間）。
    """
    sizes = _line_sizes(parts, files)
    fallback = max(sizes.values(), default=1.0)
    size = {f.name: sizes.get(f.name, fallback) for f in files}
    rows = {f.name: max(f.rows, 0) for f in files}
    cap = {n: min(r, head + tail) for n, r in rows.items()}
    min_head, min_tail = min(SAMPLE_MIN_HEAD, head), min(SAMPLE_MIN_TAIL, tail)
    floor = sum(min(rows[n], min_head + min_tail) * size[n] for n in rows)
    if floor > budget and floor > 0:
        scale = budget / floor
        min_head = min(head, max(SAMPLE_FLOOR_HEAD, int(min_head * scale)))
        min_tail = min(tail, max(SAMPLE_FLOOR_TAIL, int(min_tail * scale)))
    low = {n: min(r, min_head + min_tail) for n, r in rows.items()}
    take: dict[str, int] = {}
    left, todo = float(budget), sorted(rows, key=lambda n: cap[n] * size[n])
    while todo:  # water-filling：小的先拿滿，剩下的額度分給後面的
        share = left / len(todo)
        name = todo[0]
        if cap[name] * size[name] <= share:
            take[name] = cap[name]
            left -= cap[name] * size[name]
            todo.pop(0)
            continue
        for name in todo:
            take[name] = max(low[name], min(cap[name], int(share / size[name])))
        break
    lines: dict[str, tuple[int, int]] = {}
    for name, count in take.items():
        if count >= rows[name]:
            lines[name] = (rows[name], 0)
            continue
        end = min(tail, max(min_tail, count // 10))
        lines[name] = (min(head, count - end), end)
    return SamplePlan(
        lines,
        sum(min(rows[n], h + t) for n, (h, t) in lines.items()),
        any(h + t < rows[n] for n, (h, t) in lines.items()),
        head,
        tail,
        budget,
    )


def sample(
    parts: list[Part], files: list[SourceFile], reader: str, plan: SamplePlan
) -> pl.DataFrame:
    """依計畫取每個來源檔的前幾行與最後幾行（預覽大檔用；每個檔串流掃描一次）。"""
    first = 2 if reader == "csv" else 1
    rows = {f.name: f.rows for f in files}
    kept = []
    for name, lf in parts:
        head, tail = plan.lines.get(name, (plan.head, plan.tail))
        last = first + rows.get(name, 0) - 1
        kept.append(
            (name, lf.filter((pl.col(LINE_NO) < first + head) | (pl.col(LINE_NO) > last - tail)))
        )
    return concat(kept).collect(engine="streaming")


PEEK_MAX_FILES = 100
"""檔案清單找到超過這麼多個檔時，``peek_columns`` 不看欄位（回傳 None，驗證器不檢查欄位引用）。"""
PEEK_MAX_EXCEL = 20
"""Excel 檔要打開整個活頁簿才知道有幾欄（比較慢），超過這麼多個就不看。"""
PEEK_BYTES = 1 << 16
"""CSV 只讀開頭這麼多 bytes 找標題列。"""
_FIRST_LINE = re.compile(rb"\r\n|\r|\n")


def peek_columns(
    entries: str | list[str],
    reader: str,
    encoding: str,
    base_dir: Path,
    sheet: str = "",
    members: str = "",
) -> list[str] | None:
    """不讀整個檔案，只取得 input 的資料欄位（給驗證器檢查欄位引用）。

    每個檔都看（只讀標題列），欄位取聯集、依第一次出現的順序：引擎讀多個檔時也是對齊成同一組欄位，
    缺的欄是空值（DESIGN.md §5.3）。檔案太多（``PEEK_MAX_FILES``、Excel 是 ``PEEK_MAX_EXCEL``）時
    回傳 None（不檢查欄位引用）。
    """
    if (
        reader == "zip_lines"
    ):  # 只讀 zip 的目錄：有不能讀的檔、或所有 zip 都沒有符合的檔時丟出 InputError
        problems = scan_zips(entries, members, base_dir).problems(members)
        if problems:
            raise InputError(t("；", "; ").join(problems))
        return ["line"]
    paths = resolve(entries, base_dir)  # 找不到檔案時丟出 InputError
    if reader == "text_lines":
        return ["line"]
    if len(paths) > (PEEK_MAX_EXCEL if reader == "excel" else PEEK_MAX_FILES):
        return None
    seen: dict[str, None] = {}
    for path in paths:
        seen.update(dict.fromkeys(_peek_one(path, reader, encoding, sheet)))
    return list(seen)


def _peek_one(path: Path, reader: str, encoding: str, sheet: str) -> list[str]:
    """一個檔的資料欄位（只讀標題列）。"""
    if reader == "parquet":
        try:
            return list(parquet_names(pl.read_parquet_schema(path).keys()).values())
        except (OSError, pl.exceptions.PolarsError) as err:
            raise InputError(
                t(
                    f"「{path.name}」讀不了（不是 Parquet 檔？）：{err}",
                    f'Cannot read "{path.name}" (not a Parquet file?): {err}',
                )
            ) from None
    if reader == "excel":
        try:
            return excel.peek_columns(path, sheet)
        except excel.ExcelError as err:
            raise InputError(str(err)) from None
    with path.open("rb") as f:  # 只讀開頭（幾 GB 的檔也不整個讀進記憶體）
        head = _FIRST_LINE.split(f.read(PEEK_BYTES), maxsplit=1)[0]
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
