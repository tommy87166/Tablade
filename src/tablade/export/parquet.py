"""寫出 Parquet。先寫到同目錄的暫存檔再改名，避免中途失敗留下半個檔案。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import polars as pl


def write_parquet(frame: pl.DataFrame, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        frame.write_parquet(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
