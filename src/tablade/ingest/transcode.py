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

Stats = tuple[int, tuple[int, ...], int, tuple[int, int, int], int, tuple[int, ...]]
"""轉一段的統計：（寬鬆解碼換掉的字數, 前 ``REPLACED_LINES`` 個有換字的行之前各有幾個換行, 這一段共有幾個換行,
（``\\r\\n``, 單獨的 ``\\n``, 單獨的 ``\\r``）各有幾個, 「只認 CRLF」時留在行裡的換行有幾個,
前 ``REPLACED_LINES`` 個留著換行的行之前各有幾個換行）。

換行和逐行讀取一樣算（``LineCounter``）：「都算換行」是 ``\\n`` 與單獨的 ``\\r``，「只認 CRLF」只算 ``\\r\\n``。
行號從段的開頭算（第 1 行是 0），同一行只記一次；呼叫的人加上之前的段的換行數。都是基本型別，可以跨程序傳。"""

ANY = "any"
CRLF = "crlf"
"""換行的認法（Recipe 的 ``line_breaks``）：都算換行，或只認 ``\\r\\n``（docs/line-breaks-plan.md）。"""
PLACEHOLDER = "\ufdd0"
"""「只認 CRLF」時，行裡單獨的 ``\\n`` 暫時換成這個字（Unicode 的非字元，正常的文字不會用到），
``scan_lines`` 才不會在那裡切行；讀進來之後再換回 ``\\n``（``sources._scan_one``）。"""

_BREAK = re.compile(rb"\n|\r(?!\n)")
_CRLF = re.compile(rb"\r\n")
_LONE_LF = re.compile(rb"(?<!\r)\n")
_LONE_LF_TEXT = re.compile("(?<!\r)\n")
_LONE_CR_BYTES = re.compile(rb"\r(?!\n)")


class PlaceholderError(ValueError):
    """「只認 CRLF」：原檔裡已經有 ``PLACEHOLDER`` 這個字，不能拿它暫存行裡的換行。"""


class LineCounter:
    """數換行，和逐行讀取一樣：「都算換行」時 ``\\n`` 與單獨的 ``\\r`` 都算（轉碼時單獨的 ``\\r`` 會改成 ``\\n``，
    ``__line_no`` 也是這樣編的）；``crlf`` 時只算 ``\\r\\n``。一塊一塊餵進來；塊結尾的 ``\\r`` 要看下一塊開頭
    是不是 ``\\n`` 才知道。"""

    def __init__(self, crlf: bool = False) -> None:
        self.count = 0
        """已經確定的換行數（不含塊結尾還沒確定的 ``\\r``）。"""
        self._cr = False
        self._crlf = crlf
        self._pattern = _CRLF if crlf else _BREAK

    def _pending(self, chunk: bytes) -> int:
        """上一塊結尾的 ``\\r``：都算換行時，這一塊不是 ``\\n`` 開頭就是單獨的 ``\\r``，算一個換行；
        只認 CRLF 時，這一塊是 ``\\n`` 開頭才和它是一組 ``\\r\\n``。"""
        if not self._cr:
            return 0
        starts = chunk[:1] == b"\n"
        return 1 if starts == self._crlf else 0

    def before(self, chunk: bytes, pos: int) -> int:
        """``chunk[pos]`` 之前共有幾個換行；``chunk`` 是還沒 ``feed`` 的這一塊。"""
        return self.count + self._pending(chunk) + len(self._pattern.findall(chunk, 0, max(pos, 0)))

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        found = self._pending(chunk) + len(self._pattern.findall(chunk))
        self._cr = chunk.endswith(b"\r")
        # 都算換行時，結尾的 \r 被當成單獨的 \r 數到了：先不算，看下一塊
        self.count += found - (1 if self._cr and not self._crlf else 0)

    def finish(self) -> int:
        """到結尾了：最後留著的 ``\\r`` 是單獨的 ``\\r``（只認 CRLF 時不算）。回傳全部的換行數。"""
        if self._cr and not self._crlf:
            self.count += 1
        self._cr = False
        return self.count


def line_breaks(data: bytes, crlf: bool = False) -> int:
    """這段位元組有幾個換行（和逐行讀取一樣算；都算換行時，最後一個位元組是 ``\\r`` 算單獨的 ``\\r``）。"""
    counter = LineCounter(crlf)
    counter.feed(data)
    return counter.finish()


class EndingCounter:
    """數 ``\\r\\n``、單獨的 ``\\n``、單獨的 ``\\r`` 各有幾個（一塊一塊餵；跨兩塊的 ``\\r\\n`` 算一組）。

    ``crlf`` 時順便記下單獨的 ``\\n``、``\\r``（留在行裡的換行）各在第幾行之前（``LineCounter(crlf=True)``）。
    """

    def __init__(self, crlf: bool = False, lines: LineCounter | None = None) -> None:
        self.crlf = self.lf = self.cr = 0
        self._cr = False
        self._lines = lines
        self.inner: list[int] = []
        """留著換行的行（之前有幾個換行，同一行只記一次），最多 ``REPLACED_LINES`` 個。"""
        self._track = crlf

    def _mark(self, line: int) -> None:
        if (
            self._track
            and len(self.inner) < REPLACED_LINES
            and (not self.inner or self.inner[-1] != line)
        ):
            self.inner.append(line)

    def feed(self, chunk: bytes) -> None:
        """在 ``lines.feed(chunk)`` 之前呼叫（行號用還沒餵這一塊的 ``lines``）。"""
        if not chunk:
            return
        joined = self._cr and chunk[:1] == b"\n"  # 上一塊結尾的 \r 和這一塊開頭的 \n 是一組
        if self._cr and not joined:
            self.cr += 1
            if self._lines is not None:
                self._mark(self._lines.count)
        self.crlf += chunk.count(b"\r\n") + (1 if joined else 0)
        lone_lf = [m.start() for m in _LONE_LF.finditer(chunk)]
        if joined:
            lone_lf = lone_lf[1:]
        last = (
            len(chunk) - 1
        )  # 結尾的 \r 要看下一塊（finditer 給 endpos 的話，後面的 \n 會被看不見）
        lone_cr = [m.start() for m in _LONE_CR_BYTES.finditer(chunk) if m.start() != last]
        self.lf += len(lone_lf)
        self.cr += len(lone_cr)
        self._cr = chunk.endswith(b"\r")
        if self._track and self._lines is not None:
            for pos in sorted(lone_lf + lone_cr):
                if len(self.inner) >= REPLACED_LINES:
                    break
                self._mark(self._lines.before(chunk, pos))

    def finish(self) -> tuple[int, int, int]:
        if self._cr:
            self.cr += 1
            if self._lines is not None:
                self._mark(self._lines.count)
        self._cr = False
        return self.crlf, self.lf, self.cr


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
    breaks: str = ANY,
) -> tuple[Failure, Stats]:
    """把 f 的 [start, end) 用 encoding 解碼後寫成 UTF-8；\\r\\n 原樣保留。

    - ``breaks`` 是 ``ANY``（都算換行）：單獨的 \\r 換行改成 \\n。
    - ``CRLF``（只認 CRLF）：單獨的 \\r 不動，單獨的 \\n 換成 ``PLACEHOLDER``（``scan_lines`` 只在 \\n 切行）；
      原檔裡已經有 ``PLACEHOLDER`` 時丟出 ``PlaceholderError``。

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
    crlf = breaks == CRLF
    replaced, lines, counter = 0, [], LineCounter(crlf)
    endings = EndingCounter(crlf, counter)

    def stats(newlines: int) -> Stats:
        found = endings.finish()
        inner = found[1] + found[2] if crlf else 0
        return replaced, tuple(lines), newlines, found, inner, tuple(endings.inner)

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
            line = counter.before(chunk, hit - pending)
            if not lines or lines[-1] != line:  # 同一行有好幾個字解不開：只記一次
                lines.append(line)
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
            return (pos + i, chunk[i]), stats(counter.count)
        endings.feed(chunk)
        counter.feed(chunk)
        carry = ""
        if text.endswith("\r"):  # 可能是跨兩塊的 \r\n，留到下一塊再處理
            text, carry = text[:-1], "\r"
        out.write(_breaks(text, crlf).encode("utf-8"))
        pos += len(chunk)
    try:
        tail = carry + decode(b"", final=True)
    except UnicodeDecodeError:
        return (pos, -1), stats(counter.finish())
    out.write(_breaks(tail, crlf).encode("utf-8"))
    return None, stats(counter.finish())


def _breaks(text: str, crlf: bool) -> str:
    """寫出前整理換行：都算換行時單獨的 \\r 改成 \\n；只認 CRLF 時單獨的 \\n 換成 ``PLACEHOLDER``。
    ``text`` 結尾的 \\r 已經先拿掉（留到下一塊），所以開頭的 \\n 前面如果是 \\r，``text`` 裡看得到。"""
    if crlf:
        if PLACEHOLDER in text:
            raise PlaceholderError(PLACEHOLDER)
        return _LONE_LF_TEXT.sub(PLACEHOLDER, text) if "\n" in text else text
    return LONE_CR.sub("\n", text) if "\r" in text else text


def _after_newline(f: BinaryIO, pos: int, crlf: bool = False) -> int | None:
    """pos 之後第一個換行的下一個位置（``crlf`` 時是 \\r\\n：在單獨的 \\n 後面切會把一行切成兩段）；
    附近找不到換行就回傳 None。"""
    f.seek(pos)
    data = f.read(NEWLINE_SEARCH)
    mark = b"\r\n" if crlf else b"\n"
    i = data.find(mark)
    return None if i < 0 else pos + i + len(mark)


def segment_bounds(path: str, size: int, segment: int, crlf: bool = False) -> list[int]:
    """在換行後面切段，每段大約 segment bytes；回傳 [0, 切點…, size]。"""
    cuts = [0]
    with open(path, "rb") as f:
        pos = segment
        while pos < size:
            cut = _after_newline(f, pos, crlf)
            if cut is None:
                pos += segment
                continue
            if cut >= size:
                break
            cuts.append(cut)
            pos = cut + segment
    return [*cuts, size]


def transcode_bytes(job: tuple[bytes, str, int, str, str, str]) -> tuple[Failure, Stats]:
    """工作程序執行的部分（zip 裡的檔）：把主程序依序解壓出來的一段位元組轉成 UTF-8，寫到自己的暫存檔。

    job 是（這一段的位元組, 編碼, 這一段在檔案裡的起點, 暫存檔, errors, breaks）；失敗時的位置是在檔案裡的位置。
    """
    data, encoding, base, dst, errors, breaks = job
    with open(dst, "wb") as out:
        failure, stats = decode_range(io.BytesIO(data), out, encoding, errors=errors, breaks=breaks)
    return (None if failure is None else (base + failure[0], failure[1])), stats


def transcode_segment(job: tuple[str, str, int, int, str, str, str]) -> tuple[Failure, Stats]:
    """工作程序執行的部分：把一段轉成 UTF-8 寫到自己的暫存檔（job 最後兩項是 errors、breaks）。"""
    src, encoding, start, end, dst, errors, breaks = job
    with open(src, "rb") as f, open(dst, "wb") as out:
        return decode_range(f, out, encoding, start, end, errors=errors, breaks=breaks)
