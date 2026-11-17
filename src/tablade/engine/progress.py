"""串流執行時的進度（DESIGN.md §8）。

Polars 的串流引擎執行中不會告訴 Python 做到哪裡，所以：

1. 讀檔時每一列多一個位置欄 ``POSITION``（在所有輸入裡是第幾行，從 1 起），一路帶到最後；
   它是暫存欄位，寫出最後的結果前會拿掉。
2. 暫存結果用 ``pl.PartitionBy`` 分成大約 ``FILES`` 個檔寫，大約每 1% 一個。
3. 另一條執行緒（``Watcher``）每 0.2 秒看哪些檔寫完了，讀出位置欄，
   每個來源檔取看過的最大位置，加起來就是處理到第幾行。

資料流裡完全沒有 Python：執行緒慢了只會讓進度更新得少一點，不會拖慢 Polars、也不會多用記憶體。
不能用 ``sink_batches`` 把資料交回 Python：Python 每批只要慢 1 毫秒，整個執行就慢一倍、記憶體多好幾 GB。
"""

from __future__ import annotations

import math
import threading
from itertools import accumulate
from pathlib import Path

import polars as pl

from tablade.ingest.sources import Part, Progress, SourceFile
from tablade.steps.base import LINE_NO, POSITION

FILES = 100
MIN_ROWS_PER_FILE = 50_000
POLL_SECONDS = 0.2
STAGE = "執行（串流）"


def rows_per_file(input_rows: int) -> int:
    """暫存結果每個檔最多幾列：大約每 1% 一個檔，太小的檔不值得。"""
    return max(MIN_ROWS_PER_FILE, math.ceil(input_rows / FILES))


def with_positions(parts: list[Part], files: list[SourceFile], reader: str) -> list[Part]:
    """每個 part 加上位置欄：前面的檔共有幾行，加上這一列是這個檔的第幾行。"""
    first_line = 2 if reader == "csv" else 1  # csv 的第 1 行是標題，資料從第 2 行開始
    out, before = [], 0
    for (name, lf), source in zip(parts, files, strict=True):
        shift = before - first_line + 1
        out.append((name, lf.with_columns((pl.col(LINE_NO) + shift).alias(POSITION))))
        before += source.rows
    return out


def with_row_positions(parts: list[Part], rows: list[int]) -> list[Part]:
    """讀前面階段的輸出時：位置是前面的 part 共有幾列，加上這一列是這個 part 的第幾列。"""
    out, before = [], 0
    for (name, lf), count in zip(parts, rows, strict=True):
        indexed = lf.with_row_index(POSITION, offset=1).with_columns(
            (pl.col(POSITION).cast(pl.Int64) + before).alias(POSITION)
        )
        out.append((name, indexed.select([c for c in lf.collect_schema().names()] + [POSITION])))
        before += count
    return out


class Watcher:
    """看暫存資料夾裡寫完了哪些檔，回報處理到第幾行（另一條執行緒，不碰 Polars 的資料流）。"""

    def __init__(
        self, folder: Path, files: list[SourceFile], progress: Progress, label: str = STAGE
    ) -> None:
        self.folder = folder
        self.progress = progress
        self.label = label
        self.rows = [f.rows for f in files]
        self.starts = pl.Series([0, *accumulate(self.rows)][: len(self.rows)], dtype=pl.Int64)
        self.total = sum(self.rows)
        self.best = [0] * len(files)
        self.read = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tablade-progress", daemon=True)

    def __enter__(self) -> Watcher:
        self._report(0)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()

    def done(self) -> int:
        return sum(self.best)

    def _report(self, done: int) -> None:
        if self.progress is not None:
            self.progress(
                {"stage": self.label, "file": "", "done": done, "total": self.total, "unit": "rows"}
            )

    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            if self.poll():
                self._report(self.done())

    def poll(self, finished: bool = False) -> bool:
        """讀新寫完的檔；回傳有沒有讀到新的。還沒寫完時，最新的那個檔可能還在寫，先不讀。"""
        names = sorted(self.folder.glob("*.parquet"))
        ready = names if finished else names[:-1]
        before = self.read
        for path in ready[self.read :]:
            try:
                positions = pl.read_parquet(path, columns=[POSITION])[POSITION]
            except (OSError, pl.exceptions.PolarsError):
                break  # 還在寫：下次再讀
            self._absorb(positions)
            self.read += 1
        return self.read > before

    def _absorb(self, positions: pl.Series) -> None:
        if positions.is_empty():
            return
        index = self.starts.search_sorted(positions, side="left") - 1
        seen = (
            pl.DataFrame({"i": index, "p": positions})
            .group_by("i")
            .agg(pl.col("p").max())
            .iter_rows()
        )
        for i, p in seen:
            if 0 <= i < len(self.best):
                done = min(int(p) - int(self.starts[i]), self.rows[i])
                self.best[i] = max(self.best[i], done)
