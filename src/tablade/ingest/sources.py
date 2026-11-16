"""解析 recipe 的 inputs：glob 找檔案、轉碼、lazy 掃描、多檔合併（DESIGN.md §5、§8）。

幾 GB 的檔案也要在固定記憶體內處理：
- **不把整個檔案讀進記憶體。** 以 16 MB 為單位分塊讀取，同時算 SHA-256、檢查是不是 UTF-8。
- UTF-8 檔直接交給 Polars 的 ``scan_lines`` / ``scan_csv``（lazy、可串流）。
- 其他編碼（cp950 等）或有單獨 ``\\r`` 換行的檔案，先分塊轉成 UTF-8 暫存檔再掃描。
  暫存檔放在暫存資料夾，依「路徑＋大小＋修改時間＋編碼」快取，程式結束時刪除。
- 每個來源檔各自一個 LazyFrame（``scan_parts``，依路徑排序、行號排好），欄位對齊成同一組；
  引擎讓每個檔各自跑步驟，到「合併來源檔」或寫出前才接起來（DESIGN.md §8）。
"""

from __future__ import annotations

import atexit
import codecs
import glob
import hashlib
import io
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from .encoding import AUTO_ORDER, SUPPORTED, DecodeError

SOURCE_FILE, LINE_NO = "__source_file", "__line_no"
ORDER = (SOURCE_FILE, LINE_NO)
CHUNK = 16 << 20
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


def staging_dir() -> Path:
    global _staging
    with _lock:
        if _staging is None or not _staging.is_dir():
            _staging = Path(tempfile.mkdtemp(prefix="tablade-stage-"))
            atexit.register(shutil.rmtree, _staging, True)
        return _staging


def clear_staging() -> None:
    """刪除所有暫存檔（暫存檔是原始資料的副本，不要留在電腦上）。"""
    global _staging
    with _lock:
        _cache.clear()
        if _staging is not None:
            shutil.rmtree(_staging, ignore_errors=True)
            _staging = None


def resolve(pattern: str, base_dir: Path) -> list[Path]:
    """依 glob 找出檔案（相對於 base_dir），依路徑排序。"""
    full = pattern if Path(pattern).is_absolute() else str(base_dir / pattern)
    matches = sorted(Path(p) for p in glob.glob(full, recursive=True) if Path(p).is_file())
    if not matches:
        raise InputError(f"找不到符合「{pattern}」的檔案（相對於 {base_dir}）")
    # 符號連結可能讓同一個檔出現兩次；每個實際的檔只留一次
    seen: set[Path] = set()
    unique = []
    for path in matches:
        real = path.resolve()
        if real not in seen:
            seen.add(real)
            unique.append(path)
    return unique


def display_name(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _report(progress: Progress, stage: str, name: str, done: int, total: int) -> None:
    if progress is not None:
        progress({"stage": stage, "file": name, "done": done, "total": total})


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


_LONE_CR_TEXT = re.compile("\r(?!\n)")


def _transcode(path: Path, encoding: str, target: Path, progress: Progress) -> str:
    """分塊轉成 UTF-8；回傳 sha256。解碼失敗丟出 DecodeError。

    \\r\\n 原樣保留（scan_lines 會處理），只有單獨的 \\r 換行才改成 \\n。
    """
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder(encoding)()
    total, done, newlines, carry = path.stat().st_size, 0, 0, ""
    with path.open("rb") as f, target.open("wb") as out:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
            try:
                text = carry + decoder.decode(chunk)
            except UnicodeDecodeError as err:
                pos = min(max(err.start, 0), len(chunk) - 1)
                message = _failure(
                    encoding, done + pos, newlines + chunk.count(b"\n", 0, pos), chunk[pos]
                )
                raise DecodeError(message) from None
            carry = ""
            if text.endswith("\r"):  # 可能是跨兩塊的 \r\n，留到下一塊再處理
                text, carry = text[:-1], "\r"
            if "\r" in text and _LONE_CR_TEXT.search(text):
                text = _LONE_CR_TEXT.sub("\n", text)
            out.write(text.encode("utf-8"))
            newlines += chunk.count(b"\n")
            done += len(chunk)
            _report(progress, "轉碼", path.name, done, total)
        try:
            tail = carry + decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise DecodeError(
                f"{encoding} 在檔案結尾（第 {newlines + 1} 行）有不完整的字元"
            ) from None
        out.write(_LONE_CR_TEXT.sub("\n", tail).encode("utf-8"))
    return digest.hexdigest()


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


def _align(parts: list[Part]) -> list[Part]:
    """csv 各檔欄位可能不同：補成同一組欄位（依第一次出現的順序），缺的欄是 null。"""
    names = [lf.collect_schema().names() for _, lf in parts]
    columns: list[str] = []
    for found in names:
        columns += [c for c in found if c not in ORDER and c not in columns]
    if all(found == [*ORDER, *columns] for found in names):
        return parts
    return [
        (
            name,
            lf.select(
                pl.col(SOURCE_FILE),
                pl.col(LINE_NO),
                *[
                    pl.col(c) if c in found else pl.lit(None, dtype=pl.String).alias(c)
                    for c in columns
                ],
            ),
        )
        for (name, lf), found in zip(parts, names, strict=True)
    ]


def scan_parts(
    pattern: str, reader: str, encoding: str, base_dir: Path, progress: Progress = None
) -> tuple[list[Part], list[SourceFile]]:
    """每個來源檔一個 LazyFrame（依路徑排序），欄位對齊成同一組。"""
    parts, files = [], []
    for path in resolve(pattern, base_dir):
        name = display_name(path, base_dir)
        prepared = prepare(path, encoding, progress)
        lf, rows = _scan_one(prepared, reader, name)
        parts.append((name, lf))
        files.append(SourceFile(name, prepared.bytes, prepared.sha256, prepared.encoding, rows))
    return _align(parts), files


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
    pattern: str, reader: str, encoding: str, base_dir: Path, progress: Progress = None
) -> tuple[pl.LazyFrame, list[SourceFile]]:
    """所有來源檔接成一個 LazyFrame（測試與小工具用；引擎用 ``scan_parts``）。"""
    parts, files = scan_parts(pattern, reader, encoding, base_dir, progress)
    return concat(parts), files


def load(
    pattern: str, reader: str, encoding: str, base_dir: Path, progress: Progress = None
) -> tuple[pl.DataFrame, list[SourceFile]]:
    """把整份資料讀進記憶體（只適合小檔；大檔請用 ``scan`` ＋ ``sample``）。"""
    lf, files = scan(pattern, reader, encoding, base_dir, progress)
    return lf.collect(engine="streaming"), files


def sample(
    parts: list[Part], files: list[SourceFile], reader: str, head: int, tail: int
) -> pl.DataFrame:
    """每個來源檔的前 ``head`` 行與最後 ``tail`` 行（預覽大檔用；每個檔串流掃描一次）。"""
    first = 2 if reader == "csv" else 1
    rows = {f.path: f.rows for f in files}
    kept = []
    for name, lf in parts:
        last = first + rows.get(name, 0) - 1
        kept.append(
            (name, lf.filter((pl.col(LINE_NO) < first + head) | (pl.col(LINE_NO) > last - tail)))
        )
    return concat(kept).collect(engine="streaming")


def peek_columns(pattern: str, reader: str, encoding: str, base_dir: Path) -> list[str] | None:
    """不讀整個檔案，只取得 input 的資料欄位（給驗證器檢查欄位引用）。"""
    first = resolve(pattern, base_dir)[0]  # 找不到檔案時丟出 InputError
    if reader == "text_lines":
        return ["line"]
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
