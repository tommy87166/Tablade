"""``tablade profile``：把資料檔整理成給 AI 助理看的精簡摘要（DESIGN.md §17.1）。

大檔或 Big5 檔不適合整個上傳給 AI；摘要只有編碼、行數、前後幾行原文與分隔符號的推測。
檔案一塊一塊讀（和 ``ingest`` 一樣一次 1 MB），只留開頭與結尾幾行，幾 GB 的檔也只用固定的記憶體。

``mask=True`` 時只保留版面：數字換成 ``9``、英文字母換成 ``a``，空白與標點以外的其他字
（中文、全形符號、㈱、㎡、½ 這類）都換成 ``〇``；檔名也一樣遮（副檔名保留）。
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import string
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

from .ingest.encoding import AUTO_ORDER, DecodeError
from .ingest.text_lines import LINE_BREAK
from .ingest.transcode import CHUNK
from .steps.base import guess_delimiter

DELIMITER_NAMES = {",": "逗號", "\t": "Tab", ";": "分號", "|": "直線"}
DELIMITER_SAMPLE = 500
"""用前幾行推測分隔符號。"""
LINE_CHARS = 100_000
"""每一行最多留這麼多字（顯示、推測分隔符號、算欄數用；更長的只記長度）：整個檔沒有換行時
記憶體也是固定的，csv 模組一格最多 128 K 字也不會出錯。"""
_KEEP = set(string.punctuation)


def mask_text(text: str) -> str:
    """數字 → 9、英文字母 → a；空白、標點（含全形標點）與數學符號原樣保留；其他都 → 〇。"""
    out = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch.isspace() or ch in _KEEP or category.startswith("P") or category == "Sm":
            out.append(ch)
        elif ch.isdigit():
            out.append("9")
        elif ch.isascii() and ch.isalpha():
            out.append("a")
        else:
            out.append("〇")
    return "".join(out)


def mask_name(name: str) -> str:
    """遮罩檔名：主檔名照 ``mask_text`` 遮，副檔名保留（看得出是 csv 還是 txt）。"""
    path = Path(name)
    return mask_text(path.stem) + path.suffix


@dataclass
class _Scan:
    """讀一遍檔案得到的統計（只留開頭與結尾幾行）。"""

    encoding: str
    size: int = 0
    sha256: str = ""
    endings: Counter = field(default_factory=Counter)
    lines: int = 0
    blank: int = 0
    longest: int = 0
    widths: Counter = field(default_factory=Counter)
    delimiter: str = ","
    head: list[str] = field(default_factory=list)
    tail: deque = field(default_factory=deque)


class _Failed(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


def _scan(path: Path, encoding: str, head: int, tail: int) -> _Scan:
    """用一種編碼讀一遍：解不了碼時丟出 ``_Failed``（訊息和 ``decode_bytes`` 一樣）。"""
    result = _Scan(encoding, tail=deque(maxlen=tail))
    decoder = codecs.getincrementaldecoder(encoding)()
    digest = hashlib.sha256()
    sample: list[str] | None = []  # 推測分隔符號前先留著；推測完就是 None
    carry, prev_cr, newlines, done = "", False, 0, 0
    bom, cut = False, 0  # cut：還沒結束的那一行已經丟掉多少字（太長的行只留開頭）

    def take(line: str, extra: int = 0) -> None:
        nonlocal sample
        result.lines += 1
        length = len(line) + extra
        if length > LINE_CHARS:
            line = line[:LINE_CHARS] + "…"
        if len(result.head) < head:
            result.head.append(line)
        if tail:
            result.tail.append((result.lines, line))
        result.longest = max(result.longest, length)
        if not line.strip():
            result.blank += 1
        if sample is not None:
            sample.append(line)
            if len(sample) >= DELIMITER_SAMPLE:
                flush()
        else:
            width(line)

    def width(line: str) -> None:
        if line.strip():
            result.widths[len(next(csv.reader([line], delimiter=result.delimiter), []))] += 1

    def flush() -> None:
        nonlocal sample
        if sample is None:
            return
        result.delimiter = guess_delimiter(sample)
        lines, sample = sample, None
        for line in lines:
            width(line)

    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            if done == 0:
                bom = chunk.startswith(codecs.BOM_UTF8)
            digest.update(chunk)
            crlf = chunk.count(b"\r\n") + (1 if prev_cr and chunk.startswith(b"\n") else 0)
            result.endings["CRLF"] += crlf
            result.endings["LF"] += chunk.count(b"\n") - crlf
            result.endings["CR"] += chunk.count(b"\r") - crlf
            prev_cr = chunk.endswith(b"\r")
            try:
                text = decoder.decode(chunk)
            except UnicodeDecodeError as err:
                pos = min(max(err.start, 0), len(chunk) - 1)
                line_no = newlines + chunk.count(b"\n", 0, pos) + 1
                raise _Failed(
                    f"{encoding} 在第 {done + pos} 個位元組（第 {line_no} 行，0x{chunk[pos]:02X}）無法解碼"
                ) from None
            newlines += chunk.count(b"\n")
            done += len(chunk)
            buffer = carry + text
            hold = ""
            if buffer.endswith("\r"):  # 可能是跨兩塊的 \r\n，留到下一塊再切
                buffer, hold = buffer[:-1], "\r"
            parts = LINE_BREAK.split(buffer)
            carry = parts.pop()
            for k, line in enumerate(parts):
                take(line, cut if k == 0 else 0)
            if parts:
                cut = 0
            if len(carry) > LINE_CHARS:  # 還沒結束的一行太長：只留開頭，丟掉的字數另外記
                cut += len(carry) - LINE_CHARS
                carry = carry[:LINE_CHARS]
            carry += hold
    try:
        rest = carry + decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        raise _Failed(f"{encoding} 在檔案結尾（第 {newlines + 1} 行）有不完整的字元") from None
    parts = LINE_BREAK.split(rest)
    if parts[-1] == "" and (len(parts) > 1 or not cut):
        parts.pop()  # 檔案結尾的換行不算一行
    for k, line in enumerate(parts):
        take(line, cut if k == 0 else 0)
    flush()
    result.size = done
    result.sha256 = digest.hexdigest()
    if encoding == "utf-8-sig" and not bom:
        result.encoding = "utf-8"
    return result


def _scan_auto(path: Path, head: int, tail: int) -> _Scan:
    """依序試 utf-8-sig → cp950（和 ``decode_bytes`` 的 auto 一樣），都不行就丟出 ``DecodeError``。"""
    failures = []
    for encoding in AUTO_ORDER:
        try:
            return _scan(path, encoding, head, tail)
        except _Failed as err:
            failures.append(err.message)
    raise DecodeError("；".join(failures))  # 印出時前面是「無法判斷編碼：」


def profile_file(path: Path, *, head: int = 30, tail: int = 5, mask: bool = False) -> str:
    if head < 0 or tail < 0:
        raise ValueError("head、tail 不能小於 0")
    lines_out = [f"## {mask_name(path.name) if mask else path.name}", ""]
    try:
        scan = _scan_auto(path, head, tail)
    except DecodeError as err:
        return "\n".join([*lines_out, f"- 無法判斷編碼：{err}", ""])

    def show(line: str) -> str:
        return mask_text(line) if mask else line

    endings = ", ".join(f"{k} {v:,}" for k in ("CRLF", "LF", "CR") if (v := scan.endings[k]))
    lines_out += [
        f"- 大小：{scan.size:,} bytes，SHA-256 {scan.sha256[:16]}…",
        f"- 編碼：{scan.encoding}",
        f"- 行數：{scan.lines:,}（空白行 {scan.blank:,}）；換行：{endings}",
        f"- 推測分隔符號：{DELIMITER_NAMES.get(scan.delimiter, repr(scan.delimiter))}；"
        f"各行拆出的欄數（前 3 名）：{', '.join(f'{w} 欄 × {n:,} 行' for w, n in scan.widths.most_common(3))}",
        f"- 最長一行：{scan.longest:,} 字元",
        "",
        f"前 {len(scan.head)} 行（行號：內容{'，已遮罩' if mask else ''}）：",
        "```",
        *[f"{i:>5}: {show(line)}" for i, line in enumerate(scan.head, start=1)],
        "```",
    ]
    last = [(i, line) for i, line in scan.tail if i > head]
    if last:
        lines_out += [
            f"最後 {len(last)} 行：",
            "```",
            *[f"{i:>5}: {show(line)}" for i, line in last],
            "```",
        ]
    return "\n".join([*lines_out, ""])


def profile(paths: list[Path], *, head: int = 30, tail: int = 5, mask: bool = False) -> str:
    header = [
        "# Tablade 資料摘要",
        "",
        "給 AI 助理撰寫 recipe 用：每個檔案的編碼、行數、前後幾行原文與分隔符號推測。",
        "",
    ]
    return "\n".join(header + [profile_file(p, head=head, tail=tail, mask=mask) for p in paths])
