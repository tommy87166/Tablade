"""寫出 Parquet。先寫到同目錄的暫存檔再改名，避免中途失敗留下半個檔案。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import polars as pl


def write_parquet(frame: pl.DataFrame, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 不用 mkstemp：它建立的檔案權限是 600，改名後輸出檔就只有自己能讀
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    try:
        frame.write_parquet(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
