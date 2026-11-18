"""把非 UTF-8 的檔轉成 UTF-8（DESIGN.md §5.4、§8）。

逐行讀取時每段轉好的文字由主程序寫成 Parquet（``sources._stage_lines``），CSV 時接成一整份文字
（``sources._transcode``）；這裡只負責「把一段轉成 UTF-8 文字」，兩種都用。

只用標準函式庫、不載入 Polars：平行轉碼的工作程序也載入這個檔，要啟動得快、佔的記憶體少。

大檔在換行處切成幾段，交給多個程序同時轉碼，接起來的結果和一個一個轉完全相同：
cp950、Big5-HKSCS、UTF-8 的多位元組字元裡都不會出現 0x0A，所以 ``\\n`` 後面一定是一個字元的開頭；
每段都在 ``\\n`` 後面結束，``\\r\\n`` 也不會被切開。
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable
from typing import BinaryIO

CHUNK = 1 << 20
"""一次讀 1 MB：大區塊（例如 16 MB）解碼後釋放的記憶體，macOS 的配置器會留著不還，
每個程序的記憶體會一路長到幾百 MB 到 1.5 GB；1 MB 時固定在幾十 MB，速度一樣。"""
LONE_CR = re.compile("\r(?!\n)")
NEWLINE_SEARCH = 4 << 20
"""找切點時，往後最多找這麼遠的換行；找不到（例如整個檔只用 \\r 換行）就不切。"""

Failure = tuple[int, int] | None
"""None：成功；否則是（解碼失敗的檔案位置, 那個 byte 的值；檔尾字元不完整時是 -1）。"""


def decode_range(
    f: BinaryIO,
    out: BinaryIO,
    encoding: str,
    start: int = 0,
    end: int | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
) -> Failure:
    """把 f 的 [start, end) 用 encoding 解碼後寫成 UTF-8；單獨的 \\r 換行改成 \\n，\\r\\n 原樣保留。"""
    decoder = codecs.getincrementaldecoder(encoding)()
    f.seek(start)
    pos, carry = start, ""
    while True:
        size = CHUNK if end is None else min(CHUNK, end - pos)
        chunk = f.read(size) if size > 0 else b""
        if not chunk:
            break
        if on_chunk is not None:
            on_chunk(chunk)
        try:
            text = carry + decoder.decode(chunk)
        except UnicodeDecodeError as err:
            i = min(max(err.start, 0), len(chunk) - 1)
            return pos + i, chunk[i]
        carry = ""
        if text.endswith("\r"):  # 可能是跨兩塊的 \r\n，留到下一塊再處理
            text, carry = text[:-1], "\r"
        if "\r" in text:
            text = LONE_CR.sub("\n", text)
        out.write(text.encode("utf-8"))
        pos += len(chunk)
    try:
        tail = carry + decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return pos, -1
    out.write(LONE_CR.sub("\n", tail).encode("utf-8"))
    return None


def _after_newline(f: BinaryIO, pos: int) -> int | None:
    """pos 之後第一個換行的下一個位置；附近找不到換行就回傳 None。"""
    f.seek(pos)
    data = f.read(NEWLINE_SEARCH)
    i = data.find(b"\n")
    return None if i < 0 else pos + i + 1


def segment_bounds(path: str, size: int, segment: int) -> list[int]:
    """在換行後面切段，每段大約 segment bytes；回傳 [0, 切點…, size]。"""
    cuts = [0]
    with open(path, "rb") as f:
        pos = segment
        while pos < size:
            cut = _after_newline(f, pos)
            if cut is None:
                pos += segment
                continue
            if cut >= size:
                break
            cuts.append(cut)
            pos = cut + segment
    return [*cuts, size]


def transcode_segment(job: tuple[str, str, int, int, str]) -> Failure:
    """工作程序執行的部分：把一段轉成 UTF-8 寫到自己的暫存檔。"""
    src, encoding, start, end, dst = job
    with open(src, "rb") as f, open(dst, "wb") as out:
        return decode_range(f, out, encoding, start, end)
