"""``text_lines`` 模式：每一個實體行原封不動讀進來（DESIGN.md §5.1）。

不略過空行、不猜格式，所以 ``__line_no`` 一定對得上原始檔的行號。
只認 ``\\r\\n``、``\\n``、``\\r`` 為換行（``str.splitlines`` 還會把 ``\\x0c`` 等字元當換行，行號會錯開）。
"""

from __future__ import annotations

import re

import polars as pl

LINE_BREAK = re.compile(r"\r\n|\n|\r")


def split_lines(text: str) -> list[str]:
    if not text:
        return []
    lines = LINE_BREAK.split(text)
    if lines[-1] == "":
        lines.pop()  # 檔案結尾的換行不算一行
    return lines


def lines_frame(text: str, source_name: str) -> pl.DataFrame:
    lines = split_lines(text)
    return pl.DataFrame(
        {
            "__source_file": [source_name] * len(lines),
            "__line_no": list(range(1, len(lines) + 1)),
            "line": lines,
        },
        schema={"__source_file": pl.String, "__line_no": pl.Int64, "line": pl.String},
    )
