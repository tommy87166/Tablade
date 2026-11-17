"""假資料產生器。

repo 內不得有任何真實資料，測試、自我檢測、範例一律用這裡產生的假資料（DESIGN.md §11）。
情境是虛構的門市銷售資料；輸出是決定性的：同樣的 seed 會產生一模一樣的檔案。
"""

from __future__ import annotations

import csv
import io
import random
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

STORES = ("北區門市", "中區門市", "南區門市")
ITEMS = (
    ("A001", "原子筆", 25),
    ("A002", "筆記本", 60),
    ("B010", "保溫瓶", 450),
    ("B020", "折疊傘", 320),
    ("C100", "無線耳機", 1_290),
    ("C200", "滑鼠", 390),
    ("D300", "後背包", 1_680),
)
CHANNELS = ("門市", "網路", "電話")
PAYMENTS = ("現金", "信用卡", "行動支付")
SIMPLE_HEADER = ("日期", "門市", "品號", "品名", "備註", "金額")


def _roc_date(rng: random.Random, year: int = 115, month: int = 1) -> str:
    return f"{year:03d}/{month:02d}/{rng.randint(1, 28):02d}"


def _amount(value: int) -> str:
    """千分位金額，負數（退貨）用括號表示。"""
    return f"({-value:,})" if value < 0 else f"{value:,}"


def simple_rows(n: int = 50, seed: int = 0) -> list[dict[str, str]]:
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        code, name, price = rng.choice(ITEMS)
        qty = rng.randint(1, 12)
        if rng.random() < 0.1:
            qty = -qty  # 退貨
        rows.append(
            {
                "日期": _roc_date(rng),
                "門市": rng.choice(STORES),
                "品號": code,
                "品名": name,
                "備註": f"{rng.choice(CHANNELS)}|{rng.choice(PAYMENTS)}|單號 {i:04d}",
                "金額": _amount(price * qty),
            }
        )
    return rows


def write_simple_csv(path: str | Path, n: int = 50, seed: int = 0, encoding: str = "cp950") -> Path:
    """寫一份乾淨的 CSV（含標題列），預設 cp950、CRLF，模擬 Windows 匯出的檔案。"""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=SIMPLE_HEADER, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(simple_rows(n, seed))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(buf.getvalue().encode(encoding))
    return target


EXPORT_COLUMNS = ("日期", "門市", "品號", "品名", "通路", "付款方式", "單號", "金額")


def _csv_line(values: list[str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(values)
    return buf.getvalue()


def messy_export(n: int = 30, seed: int = 0) -> tuple[list[str], list[dict[str, str]]]:
    """凌亂的匯出檔，回傳（檔案的各行, 用範例 recipe 清洗後預期的資料列）。

    兩行標題雜訊 → 標題列 → 依門市分組（只有每組第一列寫門市）→ 組間空行 → 結尾「共 N 筆」。
    預期結果：補齊門市、備註拆成通路／付款方式／單號、排除退貨（金額有括號）。
    """
    rng = random.Random(seed)
    rows = simple_rows(n, seed)
    lines = [
        "門市銷售明細（假資料）",
        f"匯出時間：115/02/01 09:{rng.randint(10, 59)}",
        _csv_line(list(SIMPLE_HEADER)),
    ]
    expected: list[dict[str, str]] = []
    for store in STORES:
        group = [r for r in rows if r["門市"] == store]
        if not group:
            continue
        for j, row in enumerate(group):
            values = [row[c] for c in SIMPLE_HEADER]
            if j > 0:
                values[SIMPLE_HEADER.index("門市")] = ""
            lines.append(_csv_line(values))
            if not row["金額"].startswith("("):
                channel, payment, order = (part.strip() for part in row["備註"].split("|"))
                expected.append(
                    {
                        "日期": row["日期"],
                        "門市": store,
                        "品號": row["品號"],
                        "品名": row["品名"],
                        "通路": channel,
                        "付款方式": payment,
                        "單號": order,
                        "金額": row["金額"],
                    }
                )
        lines.append("")
    lines.append(f"共 {n} 筆")
    return lines, expected


def write_messy_export(
    path: str | Path, n: int = 30, seed: int = 0, encoding: str = "cp950"
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines, _ = messy_export(n, seed)
    target.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return target


# ── 分段報表（列印格式）──────────────────────────────────────────

REPORT_TITLE = "範例零售股份有限公司（假資料）"
REPORT_COLUMNS = ("日期", "品號", "品名", "數量", "單價", "金額")
REPORT_LAYOUT = ((10, False), (5, False), (12, False), (6, True), (8, True), (11, True))
"""每一欄的寬度（格數，中文字 2 格）與是否靠右；欄與欄之間空一格。"""
REPORT_STARTS = (1, 12, 18, 31, 38, 47)
"""每一欄的開始位置（第幾格，從 1 起），範例 Recipe 的 split_fixed_width 用這組。"""


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    """依顯示寬度補空白（舊系統印出的 Big5 報表一個中文字佔兩格）。"""
    space = " " * max(width - _width(text), 0)
    return space + text if right else text + space


def _report_row(values: list[str]) -> str:
    cells = (_pad(v, w, right) for v, (w, right) in zip(values, REPORT_LAYOUT, strict=True))
    return " ".join(cells).rstrip()


def sectioned_report(
    seed: int = 0, sections: int = 3, entries: int = 6, page_lines: int = 40
) -> tuple[list[str], list[dict[str, str]]]:
    """分段報表，回傳（檔案的各行, 攤平後預期的明細）。

    頁首（每頁重複）→ 每個門市一段：段落標題「門市：…」、欄位標題與分隔線、明細、小計 → 段落間空行
    → 總計 → 報表結束。一頁超過 ``page_lines`` 行就換頁：重複頁首與欄位標題，但不重複段落標題
    （跨頁的明細要靠向下填滿補上門市）。欄位用空白對齊，中文字算兩格寬。
    """
    rng = random.Random(seed)
    lines: list[str] = []
    expected: list[dict[str, str]] = []
    page, page_start = 0, 0

    def new_page() -> None:
        nonlocal page, page_start
        page += 1
        if lines:
            lines.append("")
        page_start = len(lines)
        lines.append(REPORT_TITLE)
        lines.append(_pad("門市銷售報表", 52) + f"頁次：{page}")
        lines.append("期間：115/01/01 ~ 115/01/31")
        lines.append("")

    def column_header() -> None:
        lines.append(_report_row(list(REPORT_COLUMNS)))
        lines.append(" ".join("-" * w for w, _ in REPORT_LAYOUT))

    new_page()
    grand_qty = grand_amount = 0
    for store in STORES[:sections]:
        lines.append(f"門市：{store}")
        column_header()
        qty_total = amount_total = 0
        for _ in range(entries):
            if len(lines) - page_start >= page_lines:
                new_page()
                column_header()
            code, name, price = rng.choice(ITEMS)
            qty = rng.randint(1, 12)
            if rng.random() < 0.15:
                qty = -qty  # 退貨
            day = _roc_date(rng)
            amount = _amount(price * qty)
            lines.append(_report_row([day, code, name, str(qty), f"{price:,}", amount]))
            expected.append(
                {
                    "門市": store,
                    "日期": day,
                    "品號": code,
                    "品名": name,
                    "數量": str(qty),
                    "單價": f"{price:,}",
                    "金額": amount,
                }
            )
            qty_total += qty
            amount_total += price * qty
        lines.append(_report_row(["", "", "小計", str(qty_total), "", _amount(amount_total)]))
        lines.append("")
        grand_qty += qty_total
        grand_amount += amount_total
    lines.append(_report_row(["", "", "總計", str(grand_qty), "", _amount(grand_amount)]))
    lines.append("*** 報表結束 ***")
    return lines, expected


def report_lines(seed: int = 0, sections: int = 3, entries: int = 6) -> list[str]:
    """分段報表的各行（見 ``sectioned_report``）。"""
    return sectioned_report(seed, sections, entries)[0]


def write_report(
    path: str | Path,
    seed: int = 0,
    encoding: str = "cp950",
    sections: int = 3,
    entries: int = 6,
    page_lines: int = 40,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines, _ = sectioned_report(seed, sections, entries, page_lines)
    target.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return target


# ── Excel（只用標準函式庫寫最小的 .xlsx，不需要額外的套件）────────────────

Cell = str | int | float | date | None


def _column_name(index: int) -> str:
    letters, n = "", index + 1
    while n:
        n, rest = divmod(n - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _xlsx_cell(ref: str, value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, date):  # Excel 的日期是 1899-12-30 起算的天數，加上日期格式
        serial = (value - date(1899, 12, 30)).days
        return f'<c r="{ref}" s="1"><v>{serial}</v></c>'
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def write_xlsx(path: str | Path, sheets: dict[str, list[list[Cell] | None]]) -> Path:
    """寫一個最小的 .xlsx：每個工作表是一串列（None 代表整列空白），格子可以是文字、數字、日期。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    names = list(sheets)
    ns = "http://schemas.openxmlformats.org"
    main = f"{ns}/spreadsheetml/2006/main"
    rel = f"{ns}/officeDocument/2006/relationships"
    pkg = f"{ns}/package/2006"
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    sheet_type = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            f'ContentType="{sheet_type}.worksheet+xml"/>'
            for i in range(len(names))
        )
        z.writestr(
            "[Content_Types].xml",
            f'{head}<Types xmlns="{pkg}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{sheet_type}.sheet.main+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{sheet_type}.styles+xml"/>'
            f"{overrides}</Types>",
        )
        z.writestr(
            "_rels/.rels",
            f'{head}<Relationships xmlns="{pkg}/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        sheet_list = "".join(
            f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(names)
        )
        z.writestr(
            "xl/workbook.xml",
            f'{head}<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{sheet_list}</sheets></workbook>',
        )
        links = "".join(
            f'<Relationship Id="rId{i + 1}" Type="{rel}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(names))
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f'{head}<Relationships xmlns="{pkg}/relationships">{links}'
            f'<Relationship Id="rId{len(names) + 1}" Type="{rel}/styles" Target="styles.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/styles.xml",
            f'{head}<styleSheet xmlns="{main}"><fonts count="1"><font/></fonts>'
            '<fills count="1"><fill/></fills><borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs>'
            "</styleSheet>",
        )
        for i, name in enumerate(names):
            rows = "".join(
                f'<row r="{r + 1}">'
                + "".join(_xlsx_cell(f"{_column_name(c)}{r + 1}", v) for c, v in enumerate(row))
                + "</row>"
                for r, row in enumerate(sheets[name])
                if row
            )
            z.writestr(
                f"xl/worksheets/sheet{i + 1}.xml",
                f'{head}<worksheet xmlns="{main}"><sheetData>{rows}</sheetData></worksheet>',
            )
    return target


def excel_export(
    n: int = 20, seed: int = 0
) -> tuple[list[list[Cell] | None], list[dict[str, str]]]:
    """Excel 版的明細：上面有標題與空白列，日期是 Excel 的日期格子、金額與數量是數字格子。

    回傳（工作表的各列, 讀成文字後預期的明細）。
    """
    rng = random.Random(seed)
    rows: list[list[Cell] | None] = [
        ["門市銷售明細（假資料）"],
        [f"匯出時間：2026/02/01 09:{rng.randint(10, 59)}"],
        None,
        ["日期", "門市", "品號", "品名", "數量", "金額"],
    ]
    expected = []
    for _ in range(n):
        code, name, price = rng.choice(ITEMS)
        day = date(2026, 1, rng.randint(1, 28))
        qty = rng.randint(1, 12)
        store = rng.choice(STORES)
        rows.append([day, store, code, name, qty, price * qty])
        expected.append(
            {
                "日期": day.isoformat(),
                "門市": store,
                "品號": code,
                "品名": name,
                "數量": str(qty),
                "金額": str(price * qty),
            }
        )
    return rows, expected


def write_excel_export(path: str | Path, n: int = 20, seed: int = 0) -> Path:
    """明細在第一個工作表「明細」，另外有一個「說明」工作表。"""
    rows, _ = excel_export(n, seed)
    return write_xlsx(path, {"明細": rows, "說明": [["這是假資料，只用來測試讀 Excel"]]})


def write_many(
    directory: str | Path, count: int, rows: int = 20, seed: int = 0, encoding: str = "utf-8"
) -> list[Path]:
    """很多個同格式的凌亂匯出檔（明細_0001.csv …），測試多檔案用。"""
    out = Path(directory)
    width = max(4, len(str(count)))
    return [
        write_messy_export(
            out / f"明細_{i + 1:0{width}d}.csv", n=rows, seed=seed + i, encoding=encoding
        )
        for i in range(count)
    ]


def write_big_messy_export(
    path: str | Path, size: int, seed: int = 0, encoding: str = "utf-8"
) -> Path:
    """約 ``size`` bytes 的凌亂匯出檔（同一段明細重複寫入，分塊寫出，不佔記憶體），實測大檔用。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines, _ = messy_export(5_000, seed)
    head, body, tail = lines[:3], lines[3:-1], lines[-1:]
    block = ("\r\n".join(body) + "\r\n").encode(encoding)
    with target.open("wb") as f:
        f.write(("\r\n".join(head) + "\r\n").encode(encoding))
        written = 0
        while written < size:
            f.write(block)
            written += len(block)
        f.write(("\r\n".join(tail) + "\r\n").encode(encoding))
    return target


def write_all(directory: str | Path, seed: int = 0) -> list[Path]:
    """產生所有範例假資料到指定資料夾。

    examples/sales_detail.recipe.json 讀 明細_*.csv；examples/sales_report.recipe.json 讀 銷售報表.txt。
    """
    out = Path(directory)
    return [
        write_messy_export(out / "明細_01.csv", n=50, seed=seed),
        write_messy_export(out / "明細_02.csv", n=30, seed=seed + 1),
        write_simple_csv(out / "乾淨_01.csv", n=20, seed=seed + 3),
        write_simple_csv(out / "乾淨_utf8.csv", n=20, seed=seed + 2, encoding="utf-8-sig"),
        write_report(out / "銷售報表.txt", seed=seed, entries=20),
        write_excel_export(out / "明細.xlsx", n=20, seed=seed + 4),
    ]
