"""假資料產生器。

repo 內不得有任何真實資料，測試、自我檢測、範例一律用這裡產生的假資料（DESIGN.md §11）。
情境是虛構的門市銷售資料；輸出是決定性的：同樣的 seed 會產生一模一樣的檔案。
"""

from __future__ import annotations

import csv
import io
import random
from pathlib import Path

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


def report_lines(seed: int = 0, sections: int = 3, entries: int = 6) -> list[str]:
    """分段報表：標題雜訊、每個門市一段（段落標題、欄位標題、明細、小計）、段落間空行。"""
    rng = random.Random(seed)
    lines = [
        "範例零售股份有限公司（假資料）",
        "門市銷售報表",
        "期間：115/01/01 ~ 115/01/31                         頁次：1",
        "",
    ]
    for store in STORES[:sections]:
        qty_total = amount_total = 0
        lines.append(f"門市：{store}")
        lines.append(f"{'日期':<10}{'品號':<6}{'品名':<10}{'數量':>6}{'單價':>8}{'金額':>12}")
        for _ in range(entries):
            code, name, price = rng.choice(ITEMS)
            qty = rng.randint(1, 12)
            if rng.random() < 0.15:
                qty = -qty
            qty_total += qty
            amount_total += price * qty
            lines.append(
                f"{_roc_date(rng):<10}{code:<6}{name:<10}{qty:>6}{price:>8,}{_amount(price * qty):>12}"
            )
        lines.append(f"{'':<10}{'':<6}{'小計':<10}{qty_total:>6}{'':>8}{_amount(amount_total):>12}")
        lines.append("")
    lines.append("*** 報表結束 ***")
    return lines


def write_report(
    path: str | Path, seed: int = 0, encoding: str = "cp950", sections: int = 3, entries: int = 6
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\r\n".join(report_lines(seed, sections, entries)) + "\r\n"
    target.write_bytes(text.encode(encoding))
    return target


def write_all(directory: str | Path, seed: int = 0) -> list[Path]:
    """產生所有範例假資料到指定資料夾（examples/sales_detail.recipe.json 讀的是 明細_*.csv）。"""
    out = Path(directory)
    return [
        write_messy_export(out / "明細_01.csv", n=50, seed=seed),
        write_messy_export(out / "明細_02.csv", n=30, seed=seed + 1),
        write_simple_csv(out / "乾淨_01.csv", n=20, seed=seed + 3),
        write_simple_csv(out / "乾淨_utf8.csv", n=20, seed=seed + 2, encoding="utf-8-sig"),
        write_report(out / "銷售報表.txt", seed=seed),
    ]
