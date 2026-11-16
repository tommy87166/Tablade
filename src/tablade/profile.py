"""``tablade profile``：把資料檔整理成給 AI 助理看的精簡摘要（DESIGN.md §17.1）。

大檔或 Big5 檔不適合整個上傳給 AI；摘要只有編碼、行數、前後幾行原文與分隔符號的推測。
``mask=True`` 時把數字、英文字母、其他文字分別換成 ``9`` / ``a`` / ``〇``，只保留版面。
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from pathlib import Path

from .ingest.encoding import DecodeError, decode_bytes
from .ingest.text_lines import split_lines
from .steps.base import guess_delimiter

DELIMITER_NAMES = {",": "逗號", "\t": "Tab", ";": "分號", "|": "直線"}


def mask_text(text: str) -> str:
    out = []
    for ch in text:
        if ch.isdigit():
            out.append("9")
        elif ch.isascii() and ch.isalpha():
            out.append("a")
        elif ch.isalpha():
            out.append("〇")
        else:
            out.append(ch)
    return "".join(out)


def profile_file(path: Path, *, head: int = 30, tail: int = 5, mask: bool = False) -> str:
    raw = path.read_bytes()
    lines_out = [f"## {path.name}", ""]
    try:
        decoded = decode_bytes(raw)
    except DecodeError as err:
        return "\n".join([*lines_out, f"- 無法判斷編碼：{err}", ""])
    lines = split_lines(decoded.text)
    endings = Counter()
    endings["CRLF"] = raw.count(b"\r\n")
    endings["LF"] = raw.count(b"\n") - endings["CRLF"]
    endings["CR"] = raw.count(b"\r") - endings["CRLF"]
    blank = sum(1 for line in lines if not line.strip())
    delimiter = guess_delimiter(lines[:500])
    widths = Counter(
        len(next(csv.reader([line], delimiter=delimiter), [])) for line in lines if line.strip()
    )

    def show(line: str) -> str:
        return mask_text(line) if mask else line

    lines_out += [
        f"- 大小：{len(raw):,} bytes，SHA-256 {hashlib.sha256(raw).hexdigest()[:16]}…",
        f"- 編碼：{decoded.encoding}",
        f"- 行數：{len(lines):,}（空白行 {blank:,}）；換行：{', '.join(f'{k} {v:,}' for k, v in endings.items() if v)}",
        f"- 推測分隔符號：{DELIMITER_NAMES.get(delimiter, repr(delimiter))}；"
        f"各行拆出的欄數（前 3 名）：{', '.join(f'{w} 欄 × {n:,} 行' for w, n in widths.most_common(3))}",
        f"- 最長一行：{max((len(line) for line in lines), default=0):,} 字元",
        "",
        f"前 {min(head, len(lines))} 行（行號：內容{'，已遮罩' if mask else ''}）：",
        "```",
        *[f"{i:>5}: {show(line)}" for i, line in enumerate(lines[:head], start=1)],
        "```",
    ]
    if len(lines) > head:
        start = max(head, len(lines) - tail)
        lines_out += [
            f"最後 {len(lines) - start} 行：",
            "```",
            *[f"{i:>5}: {show(line)}" for i, line in enumerate(lines[start:], start=start + 1)],
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
