"""把非 UTF-8 的檔轉成 UTF-8（DESIGN.md §5.4、§8）。

逐行讀取時每段轉好的文字由主程序寫成 Parquet（``sources._stage_lines``），CSV 時接成一整份文字
（``sources._transcode``）；這裡只負責「把一段轉成 UTF-8 文字」，兩種都用。

只用標準函式庫、不載入 Polars：平行轉碼的工作程序也載入這個檔，要啟動得快、佔的記憶體少。

大檔在換行處切成幾段，交給多個程序同時轉碼，接起來的結果和一個一個轉完全相同：
cp950、Big5-HKSCS、UTF-8 的多位元組字元裡都不會出現 0x0A，所以 ``\\n`` 後面一定是一個字元的開頭；
每段都在 ``\\n`` 後面結束，``\\r\\n`` 也不會被切開。

寬鬆解碼（``errors=LENIENT``，docs/eudc-lenient-plan.md）：解不開的字換成 ``�``，照樣轉下去，並數換了幾個字、
前幾個在第幾行。cp950、Big5-HKSCS 的 2 位元組字整個換成兩個 ``�``（Python 內建的 ``replace`` 只換掉前半個，
後半個會漏出來，例如 ``8F 7C`` 變成 ``�|``）；兩個 ``�`` 剛好佔原本的 2 格，固定位置的報表不會偏。
"""

from __future__ import annotations

import codecs
import io
import re
import threading
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

Stats = tuple[int, tuple[int, ...], int]
"""寬鬆解碼的統計：（換掉的字數, 前 ``REPLACED_LINES`` 個換字之前各有幾個 ``\\n``, 這一段共有幾個 ``\\n``）。
行號從段的開頭算（第 1 行是 0）；呼叫的人加上之前的段的換行數。都是基本型別，可以跨程序傳。"""

LENIENT = "tablade-lenient"
"""寬鬆解碼的錯誤處理器名稱（``bytes.decode(encoding, LENIENT)``）。"""
REPLACED_LINES = 10
"""每個檔記前幾個換字的行號。"""
_hits = threading.local()
"""``decode_range`` 解每一塊時掛一個清單上去，處理器把解不開的位置（``err.start``）記進去。"""


def _is_trail(byte: int) -> bool:
    """cp950、Big5-HKSCS 的 2 位元組字的第 2 個位元組（0x0A 換行不是，所以換字不會吃掉換行）。"""
    return 0x40 <= byte <= 0x7E or 0xA1 <= byte <= 0xFE


def _lenient(err: UnicodeError) -> tuple[str, int]:
    """解不開的字換成 ``�``：多位元組編碼的 2 位元組字（lead byte 後面接合法的 trail）一起換成兩個。"""
    if not isinstance(err, UnicodeDecodeError):
        raise err
    hits = getattr(_hits, "positions", None)
    if hits is not None:
        hits.append(err.start)
    data, start = err.object, err.start
    if err.encoding.replace("_", "-").lower().startswith("utf"):
        return "�", err.end  # UTF-8 照內建的範圍換：壞掉的位元組不會吞掉後面的 ASCII
    if 0x81 <= data[start] <= 0xFE and start + 1 < len(data) and _is_trail(data[start + 1]):
        return "��", start + 2
    return "�", start + 1


codecs.register_error(LENIENT, _lenient)


def count_replaced(data: bytes, encoding: str) -> int:
    """這段位元組用 ``encoding`` 寬鬆解碼會換掉幾個字（只在記憶體裡解，``auto``＋寬鬆取樣比較時用）。"""
    _hits.positions = []
    try:
        data.decode(encoding, LENIENT)
        return len(_hits.positions)
    finally:
        _hits.positions = None


def decode_range(
    f: BinaryIO,
    out: BinaryIO,
    encoding: str,
    start: int = 0,
    end: int | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
    errors: str = "strict",
) -> tuple[Failure, Stats]:
    """把 f 的 [start, end) 用 encoding 解碼後寫成 UTF-8；單獨的 \\r 換行改成 \\n，\\r\\n 原樣保留。

    回傳（失敗, 統計）；``errors`` 是 ``LENIENT`` 時不會失敗，解不開的字換成 ``�`` 並記在統計裡。
    """
    f.seek(start)
    if encoding == "utf-8-sig":  # 自己拿掉 BOM：utf-8-sig 回報的錯誤位置不算 BOM 那 3 個位元組
        encoding = "utf-8"
        head = f.read(3 if end is None else min(3, end - start))
        if head == codecs.BOM_UTF8:
            if on_chunk is not None:
                on_chunk(head)
            start += 3
        f.seek(start)
    decoder = codecs.getincrementaldecoder(encoding)(errors)
    lenient = errors == LENIENT
    pos, carry = start, ""
    replaced, lines, newlines = 0, [], 0

    def decode(chunk: bytes, final: bool = False) -> str:
        nonlocal replaced
        if not lenient:
            return decoder.decode(chunk, final)
        pending = len(decoder.getstate()[0])  # 上一塊結尾留著的半個字（err.object 是它接著這一塊）
        _hits.positions = []
        try:
            text = decoder.decode(chunk, final)
            found = _hits.positions
        finally:
            _hits.positions = None
        replaced += len(found)
        for hit in found:
            if len(lines) >= REPLACED_LINES:
                break
            lines.append(newlines + chunk.count(b"\n", 0, max(hit - pending, 0)))
        return text

    while True:
        size = CHUNK if end is None else min(CHUNK, end - pos)
        chunk = f.read(size) if size > 0 else b""
        if not chunk:
            break
        if on_chunk is not None:
            on_chunk(chunk)
        try:
            text = carry + decode(chunk)
        except UnicodeDecodeError as err:
            i = min(max(err.start, 0), len(chunk) - 1)
            return (pos + i, chunk[i]), (replaced, tuple(lines), newlines)
        newlines += chunk.count(b"\n")
        carry = ""
        if text.endswith("\r"):  # 可能是跨兩塊的 \r\n，留到下一塊再處理
            text, carry = text[:-1], "\r"
        if "\r" in text:
            text = LONE_CR.sub("\n", text)
        out.write(text.encode("utf-8"))
        pos += len(chunk)
    try:
        tail = carry + decode(b"", final=True)
    except UnicodeDecodeError:
        return (pos, -1), (replaced, tuple(lines), newlines)
    out.write(LONE_CR.sub("\n", tail).encode("utf-8"))
    return None, (replaced, tuple(lines), newlines)


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


def transcode_bytes(job: tuple[bytes, str, int, str, str]) -> tuple[Failure, Stats]:
    """工作程序執行的部分（zip 裡的檔）：把主程序依序解壓出來的一段位元組轉成 UTF-8，寫到自己的暫存檔。

    job 是（這一段的位元組, 編碼, 這一段在檔案裡的起點, 暫存檔, errors）；失敗時的位置是在檔案裡的位置。
    """
    data, encoding, base, dst, errors = job
    with open(dst, "wb") as out:
        failure, stats = decode_range(io.BytesIO(data), out, encoding, errors=errors)
    return (None if failure is None else (base + failure[0], failure[1])), stats


def transcode_segment(job: tuple[str, str, int, int, str, str]) -> tuple[Failure, Stats]:
    """工作程序執行的部分：把一段轉成 UTF-8 寫到自己的暫存檔（job 最後一項是 errors）。"""
    src, encoding, start, end, dst, errors = job
    with open(src, "rb") as f, open(dst, "wb") as out:
        return decode_range(f, out, encoding, start, end, errors=errors)
