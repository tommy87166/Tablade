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
import zlib
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


XLSX_TIME = (2026, 1, 1, 0, 0, 0)
"""假資料 .xlsx 裡每個檔的時間（zip 的格式，最小單位 2 秒）。"""


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
        # 每個檔的時間固定（預設是現在的時間）：同一個 seed 產生的檔每次都一樣
        def put(name: str, data: str) -> None:
            z.writestr(zipfile.ZipInfo(name, date_time=XLSX_TIME), data, zipfile.ZIP_DEFLATED)

        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            f'ContentType="{sheet_type}.worksheet+xml"/>'
            for i in range(len(names))
        )
        put(
            "[Content_Types].xml",
            f'{head}<Types xmlns="{pkg}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{sheet_type}.sheet.main+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{sheet_type}.styles+xml"/>'
            f"{overrides}</Types>",
        )
        put(
            "_rels/.rels",
            f'{head}<Relationships xmlns="{pkg}/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        sheet_list = "".join(
            f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(names)
        )
        put(
            "xl/workbook.xml",
            f'{head}<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{sheet_list}</sheets></workbook>',
        )
        links = "".join(
            f'<Relationship Id="rId{i + 1}" Type="{rel}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(names))
        )
        put(
            "xl/_rels/workbook.xml.rels",
            f'{head}<Relationships xmlns="{pkg}/relationships">{links}'
            f'<Relationship Id="rId{len(names) + 1}" Type="{rel}/styles" Target="styles.xml"/>'
            "</Relationships>",
        )
        put(
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
            put(
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


CATEGORIES = {"A": "文具", "B": "生活用品", "C": "3C"}
"""品項對照表的類別（依品號第一個字母）；D 開頭的品號故意不放進對照表，示範「沒對到」的警告。"""


def item_table() -> list[dict[str, str]]:
    """品項對照表：品號、類別、建議售價（結合的右表）。"""
    return [
        {"品號": code, "類別": CATEGORIES[code[0]], "建議售價": str(price)}
        for code, _, price in ITEMS
        if code[0] in CATEGORIES
    ]


def write_item_table(path: str | Path, encoding: str = "utf-8-sig") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = item_table()
    lines = [",".join(rows[0])] + [",".join(r.values()) for r in rows]
    target.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return target


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


class _Cp950Name(zipfile.ZipInfo):
    """檔名用 cp950、不標 UTF-8（台灣的 Windows 壓縮程式常這樣）：測試 zip 裡中文檔名的判斷。"""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp950"), self.flag_bits & ~0x800


def _zip_entry(name: str, cp950_name: bool = False) -> zipfile.ZipInfo:
    info = (_Cp950Name if cp950_name else zipfile.ZipInfo)(name, date_time=XLSX_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


ZIP_MONTHS = ("01", "02", "03")
"""``write_zip_months`` 產生的月份；03 月的 zip 裡沒有 HEADER 的檔（測試「沒有符合的檔，略過」）。"""


def write_zip_months(directory: str | Path, seed: int = 0) -> list[Path]:
    """每個月一個 zip（``2026-01.zip``…），裡面有資料夾與兩種文字檔（examples/sales_zip.recipe.json 讀）：

    - ``202601/HEADER_001.TXT``：cp950 的分段報表（和 ``銷售報表.txt`` 同一種格式）
    - ``202601/header_002.txt``：同一種報表，UTF-8（檔名大小寫不同，測試不分大小寫）
    - ``202601/ITEM_001.TXT``：cp950 的凌亂匯出檔
    - ``202601/說明.TXT``：檔名是 cp950、沒標 UTF-8
    - ``readme.pdf``：不是文字檔（不會被 ``2026*/HEADER*.TXT`` 選到）

    03 月的 zip 只有 ITEM 的檔。每個檔的時間固定，同一個 seed 產生的 zip 每次都一樣。
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for k, month in enumerate(ZIP_MONTHS):
        folder = f"2026{month}"
        report, _ = sectioned_report(seed + k, sections=2, entries=15)
        items, _ = messy_export(20, seed + k)
        target = out / f"2026-{month}.zip"
        with zipfile.ZipFile(target, "w") as z:
            if month != ZIP_MONTHS[-1]:
                z.writestr(_zip_entry(f"{folder}/HEADER_001.TXT"), _crlf(report, "cp950"))
                z.writestr(_zip_entry(f"{folder}/header_002.txt"), _crlf(report[:40], "utf-8"))
            z.writestr(_zip_entry(f"{folder}/ITEM_001.TXT"), _crlf(items, "cp950"))
            z.writestr(
                _zip_entry(f"{folder}/說明.TXT", cp950_name=True), _crlf(["假資料"], "cp950")
            )
            z.writestr(_zip_entry("readme.pdf"), b"%PDF-1.4 fake")
        written.append(target)
    return written


DEFLATE64_MEMBER = "202601/HEADER_001.TXT"
"""``write_deflate64_zip`` 寫的那個檔在 zip 裡的路徑。"""


def deflate64_lines(seed: int = 0, entries: int = 400) -> list[str]:
    """``write_deflate64_zip`` 裡那個檔的各行（分段報表，和 ``銷售報表.txt`` 同一種格式）。"""
    return sectioned_report(seed, sections=3, entries=entries)[0]


def write_deflate64_zip(path: str | Path, seed: int = 0, entries: int = 400) -> Path:
    """一個 Deflate64 壓縮的 zip（Windows 檔案總管壓縮大檔時可能用這種），裡面是 cp950 的分段報表。

    標準函式庫寫不出 Deflate64：用 zlib 的「只用 Huffman、不做重複比對」（``Z_HUFFMAN_ONLY``）壓縮，
    這樣的資料同時也是合法的 Deflate64（Deflate64 只改了長度 258 的寫法與更遠的距離，不比對就用不到）。
    先寫成不壓縮的項目，再把本機標頭與中央目錄的壓縮方式改成 9、CRC 與解開後大小改成原文的。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = _crlf(deflate64_lines(seed, entries), "cp950")
    packer = zlib.compressobj(6, zlib.DEFLATED, -15, 9, zlib.Z_HUFFMAN_ONLY)
    packed = packer.compress(raw) + packer.flush()
    info = zipfile.ZipInfo(DEFLATE64_MEMBER, date_time=XLSX_TIME)
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(target, "w") as z:
        z.writestr(info, packed)
    data = bytearray(target.read_bytes())
    for signature, method_at, crc_at, size_at in (
        (b"PK\x03\x04", 8, 14, 22),
        (b"PK\x01\x02", 10, 16, 24),
    ):
        i = data.find(signature)
        data[i + method_at : i + method_at + 2] = (9).to_bytes(2, "little")
        data[i + crc_at : i + crc_at + 4] = zlib.crc32(raw).to_bytes(4, "little")
        data[i + size_at : i + size_at + 4] = len(raw).to_bytes(4, "little")
    target.write_bytes(bytes(data))
    return target


def _crlf(lines: list[str], encoding: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode(encoding)


# ── 造字與壞掉的位元組（寬鬆解碼，docs/eudc-lenient-plan.md）───────────────

EUDC_CODES = (b"\x8f\x46", b"\x8f\x7c", b"\xfa\x40")
"""假資料用的 Big5 造字區碼（cp950 解不開）。``8F 7C`` 的第 2 個位元組是 ``|``：Python 內建的
``errors="replace"`` 會把它漏出來，用分隔符號拆欄時欄位就錯位了。"""
_EUDC_MARK = "鑫"
"""佔位字：cp950 解得開、佔 2 格、其他假資料沒用到；寫檔時換成造字的 2 個位元組（寬度不變）。"""


def eudc_report(
    seed: int = 0, entries: int = 30, every: int = 4
) -> tuple[bytes, list[str], list[int]]:
    """有造字的分段報表（cp950、固定位置），回傳（檔案內容, 寬鬆解碼後的各行, 有造字的行號）。

    每 ``every`` 筆明細，品名的第一個字換成造字（輪流用 ``EUDC_CODES``）。2 個位元組換 2 個位元組，
    寬鬆解碼成兩個 ``�`` 也佔 2 格，「依固定位置拆欄」的欄位不會偏。
    """
    lines, _ = sectioned_report(seed, sections=2, entries=entries)
    names = [name for _, name, _ in ITEMS]
    marked: list[int] = []
    detail = 0
    for i, line in enumerate(lines):
        name = next((n for n in names if n in line), None)
        if name is None or "115/" not in line:  # 只改明細（有日期的那幾行）
            continue
        if detail % every == 0:
            lines[i] = line.replace(name, _EUDC_MARK + name[1:], 1)
            marked.append(i + 1)
        detail += 1
    parts = _crlf(lines, "cp950").split(_EUDC_MARK.encode("cp950"))
    if len(parts) != len(marked) + 1:  # 佔位字的位元組剛好跨在兩個字之間：換一個 seed
        raise ValueError(f"seed {seed} 產生的報表不能用來放造字")  # i18n-ignore：測試用
    raw = parts[0] + b"".join(EUDC_CODES[k % len(EUDC_CODES)] + p for k, p in enumerate(parts[1:]))
    decoded = [line.replace(_EUDC_MARK, "��") for line in lines]
    return raw, decoded, marked


def write_eudc_report(path: str | Path, seed: int = 0, entries: int = 30) -> Path:
    """``eudc_report`` 的檔案（cp950，CRLF）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(eudc_report(seed, entries)[0])
    return target


BROKEN_UTF8_LINE = 3
"""``broken_utf8`` 在第幾行放了一個壞掉的位元組。"""


def broken_utf8(n: int = 20, seed: int = 0) -> bytes:
    """凌亂匯出檔（UTF-8），第 ``BROKEN_UTF8_LINE`` 行的開頭多了一個不合 UTF-8 的位元組 ``FF``。"""
    lines, _ = messy_export(n, seed)
    rows = [line.encode("utf-8") for line in lines]
    rows[BROKEN_UTF8_LINE - 1] = b"\xff" + rows[BROKEN_UTF8_LINE - 1]
    return b"\r\n".join(rows) + b"\r\n"


EUDC_ZIP_MEMBERS = ("202601/HEADER_001.TXT", "202601/ITEM_001.TXT")
"""``write_eudc_zip`` 裡的檔：有造字的 cp950 報表、壞了一個位元組的 UTF-8 匯出檔。"""


def write_eudc_zip(path: str | Path, seed: int = 0, entries: int = 30) -> Path:
    """一個 zip，裡面是 ``EUDC_ZIP_MEMBERS``（測試 zip 裡的檔的寬鬆解碼）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    report, utf8 = eudc_report(seed, entries)[0], broken_utf8(seed=seed)
    with zipfile.ZipFile(target, "w") as z:
        for name, data in zip(EUDC_ZIP_MEMBERS, (report, utf8), strict=True):
            z.writestr(_zip_entry(name), data)
    return target


def write_all(directory: str | Path, seed: int = 0) -> list[Path]:
    """產生所有範例假資料到指定資料夾。

    examples/sales_detail.recipe.json 讀 明細_*.csv；examples/sales_report.recipe.json 讀 銷售報表.txt；
    examples/sales_excel.recipe.json 讀 明細.xlsx；examples/sales_combined.recipe.json 另外讀 品項對照.csv；
    examples/sales_zip.recipe.json 讀 2026-*.zip 裡的 HEADER 檔。
    """
    out = Path(directory)
    return [
        write_messy_export(out / "明細_01.csv", n=50, seed=seed),
        write_messy_export(out / "明細_02.csv", n=30, seed=seed + 1),
        write_simple_csv(out / "乾淨_01.csv", n=20, seed=seed + 3),
        write_simple_csv(out / "乾淨_utf8.csv", n=20, seed=seed + 2, encoding="utf-8-sig"),
        write_report(out / "銷售報表.txt", seed=seed, entries=20),
        write_excel_export(out / "明細.xlsx", n=20, seed=seed + 4),
        write_item_table(out / "品項對照.csv"),
        *write_zip_months(out, seed=seed),
    ]
