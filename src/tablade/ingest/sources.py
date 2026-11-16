"""解析 recipe 的 inputs：glob 找檔案、轉碼、讀取、多檔合併（DESIGN.md §5.3）。"""

from __future__ import annotations

import glob
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from .encoding import decode_bytes, to_utf8
from .text_lines import lines_frame

ORDER = ("__source_file", "__line_no")


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: str
    """相對於 recipe 所在資料夾的路徑（/ 分隔）。"""
    bytes: int
    sha256: str
    encoding: str
    rows: int


def resolve(pattern: str, base_dir: Path) -> list[Path]:
    """依 glob 找出檔案（相對於 base_dir），依路徑排序。"""
    full = pattern if Path(pattern).is_absolute() else str(base_dir / pattern)
    matches = sorted(Path(p) for p in glob.glob(full, recursive=True) if Path(p).is_file())
    if not matches:
        raise InputError(f"找不到符合「{pattern}」的檔案（相對於 {base_dir}）")
    return matches


def _display_name(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_one(path: Path, reader: str, encoding: str, name: str) -> tuple[pl.DataFrame, SourceFile]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if reader == "text_lines":
        decoded = decode_bytes(raw, encoding)
        frame = lines_frame(decoded.text, name)
        detected = decoded.encoding
    elif reader == "csv":
        utf8, detected = to_utf8(raw, encoding)
        frame = pl.read_csv(io.BytesIO(utf8), infer_schema=False)
        # 假設沒有跨行的欄位：標題在第 1 行，資料從第 2 行開始（有跨行欄位請改用 text_lines）
        frame = frame.with_columns(
            pl.lit(name).alias("__source_file"),
            (pl.int_range(pl.len(), dtype=pl.Int64) + 2).alias("__line_no"),
        ).select("__source_file", "__line_no", *frame.columns)
    else:
        raise InputError(f"不支援的讀取方式：{reader}")
    return frame, SourceFile(name, len(raw), digest, detected, frame.height)


def load(
    pattern: str, reader: str, encoding: str, base_dir: Path
) -> tuple[pl.DataFrame, list[SourceFile]]:
    frames, files = [], []
    for path in resolve(pattern, base_dir):
        frame, info = _read_one(path, reader, encoding, _display_name(path, base_dir))
        frames.append(frame)
        files.append(info)
    combined = pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]
    return combined.sort(ORDER), files


def peek_columns(pattern: str, reader: str, encoding: str, base_dir: Path) -> list[str] | None:
    """不讀整個檔案，只取得 input 的資料欄位（給驗證器檢查欄位引用）。"""
    first = resolve(pattern, base_dir)[0]  # 找不到檔案時丟出 InputError
    if reader == "text_lines":
        return ["line"]
    head = first.read_bytes()[: 1 << 16].split(b"\n", 1)[0].rstrip(b"\r")
    text = _lenient(head) if encoding == "auto" else head.decode(encoding, errors="replace")
    text = text.removeprefix("\ufeff")
    return pl.read_csv(io.StringIO(text + "\n"), infer_schema=False).columns if text else []


def _lenient(raw: bytes) -> str:
    for candidate in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
