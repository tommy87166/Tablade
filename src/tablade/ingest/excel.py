"""讀 Excel 工作表（DESIGN.md §5）：每一列都是資料、欄名是 A、B、C…、行號是 Excel 的列號。

- 不把第一列當標題：拿到的 Excel 上面常有標題、說明列，跟文字檔一樣用「移除前 N 列」
  「把第一列作為欄位標題」處理。
- 每一格都讀成文字（和 csv 一樣不猜型別）：數字是儲存格裡的值，不套用顯示格式（顯示成 1,234.50 的格子讀成 1234.5、12% 讀成 0.12），
  沒有時間的日期寫成 2026-01-05，有時間的是 2026-01-05 13:45:00。
- 空白的列也保留（含最上面的），所以 ``__line_no`` 就是 Excel 的列號。
- Excel 檔不能串流讀：整張工作表會讀進記憶體（Excel 本身最多約 104 萬列）。
- 工作表寫 ``*`` 代表全部工作表（Excel 的工作表名稱不能有 ``*``）：每個工作表各自是一個來源，
  多一個系統欄位 ``__sheet`` 記工作表名稱。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl

from ..i18n import t

EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xlsb", ".xls", ".ods")
SOURCE_FILE, LINE_NO, SHEET = "__source_file", "__line_no", "__sheet"
ALL_SHEETS = "*"
"""工作表寫這個代表全部工作表。"""
CHUNK = 1 << 20


class ExcelError(ValueError):
    """Excel 檔讀不了（不是 Excel、沒有這個工作表…）。"""


def is_excel(path: Path) -> bool:
    return path.suffix.lower() in EXCEL_SUFFIXES


def column_letter(index: int) -> str:
    """0 → A、25 → Z、26 → AA（Excel 的欄名）。"""
    letters, n = "", index + 1
    while n:
        n, rest = divmod(n - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _reader(path: Path, name: str):
    import fastexcel

    try:
        return fastexcel.read_excel(path)
    except fastexcel.FastExcelError as err:
        raise ExcelError(
            t(
                f"「{name}」不是讀得了的 Excel 檔：{err}",
                f'"{name}" is not a readable Excel file: {err}',
            )
        ) from None


def sheet_names(path: Path) -> list[str]:
    return list(_reader(path, path.name).sheet_names)


def _sheet(path: Path, sheet: str, name: str, n_rows: int | None = None):
    reader = _reader(path, name)
    names = list(reader.sheet_names)
    if sheet and sheet not in names:
        raise ExcelError(
            t(
                f"「{name}」沒有工作表「{sheet}」（有：{'、'.join(names)}）",
                f'"{name}" has no sheet "{sheet}" (sheets: {", ".join(names)})',
            )
        )
    if not names:
        raise ExcelError(t(f"「{name}」沒有任何工作表", f'"{name}" has no sheets'))
    # header_row=None：每一列都是資料；skip_rows=0：連最上面的空白列也保留，列號才對得上
    return reader.load_sheet(
        sheet or 0, header_row=None, dtypes="string", skip_rows=0, n_rows=n_rows
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def read_sheet(path: Path, sheet: str, name: str, with_sheet: bool = False) -> pl.DataFrame:
    """整張工作表：__source_file、（__sheet）、__line_no（Excel 列號）、A、B、C…（全部是文字）。

    ``with_sheet`` 時多一個 ``__sheet`` 系統欄位（讀全部工作表時用）。
    """
    frame = _sheet(path, sheet, name).to_polars()
    columns = [column_letter(i) for i in range(frame.width)]
    frame = frame.rename(dict(zip(frame.columns, columns, strict=True)))
    # 沒有時間的日期不要帶「 00:00:00」
    frame = frame.with_columns(
        pl.col(c).str.replace(r"^(\d{4}-\d{2}-\d{2}) 00:00:00$", "$1") for c in columns
    )
    sheet_column = [pl.lit(sheet, dtype=pl.String).alias(SHEET)] if with_sheet else []
    return frame.select(
        pl.lit(name, dtype=pl.String).alias(SOURCE_FILE),
        *sheet_column,
        pl.int_range(1, frame.height + 1, dtype=pl.Int64).alias(LINE_NO),
        *columns,
    )


def peek_columns(path: Path, sheet: str) -> list[str]:
    """工作表有幾欄（A、B、C…），不讀整張表；全部工作表時取最寬的那一張。"""
    sheets = sheet_names(path) if sheet == ALL_SHEETS else [sheet]
    width = max((_sheet(path, s, path.name, n_rows=1).width for s in sheets), default=0)
    return [column_letter(i) for i in range(width)]
