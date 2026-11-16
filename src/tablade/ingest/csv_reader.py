"""乾淨 CSV 的讀取（DESIGN.md §5.1 的 ``csv`` 模式）。

M0 版本：轉碼後整檔交給 Polars，全部欄位保留為文字，不猜型別。
``__source_file`` / ``__line_no`` 系統欄位在 M1 加入。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from .encoding import to_utf8


@dataclass(frozen=True)
class CsvResult:
    frame: pl.DataFrame
    encoding: str
    bytes_read: int


def read_csv(path: str | Path, encoding: str = "auto", separator: str = ",") -> CsvResult:
    raw = Path(path).read_bytes()
    utf8, detected = to_utf8(raw, encoding)
    frame = pl.read_csv(io.BytesIO(utf8), separator=separator, infer_schema=False)
    return CsvResult(frame=frame, encoding=detected, bytes_read=len(raw))
