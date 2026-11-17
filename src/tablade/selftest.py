"""自我檢測：用假資料跑一次 CSV → Parquet，再完整執行範例 recipe，確認打包後的環境可以正常運作。"""

from __future__ import annotations

import json
import platform
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import polars as pl

from . import __version__
from .export.parquet import write_parquet
from .fixtures import write_simple_csv
from .ingest.csv_reader import read_csv

ROWS = 200


SELFTEST_WORKERS = 2
"""自我檢測「多核心轉碼」實際開幾個工作程序（不管核心數多少）。"""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    purpose: str = ""
    """這一項確認的是什麼（顯示在 UI）。"""
    ran: bool = True
    """False：前面的檢查失敗，這一項沒有執行。"""


def versions() -> dict[str, str]:
    import fastexcel
    import pydantic

    return {
        "tablade": __version__,
        "python": platform.python_version(),
        "polars": pl.__version__,
        "fastexcel": getattr(fastexcel, "__version__", "?"),
        "pydantic": pydantic.VERSION,
        "platform": platform.platform(),
        "frozen": str(getattr(sys, "frozen", False)),
    }


def run_selftest() -> dict[str, Any]:
    checks: list[Check] = []

    def check(name: str, purpose: str, fn: Callable[[], str]) -> bool:
        try:
            checks.append(Check(name, True, fn(), purpose))
            return True
        except Exception as exc:
            checks.append(Check(name, False, f"{type(exc).__name__}: {exc}", purpose))
            return False

    with tempfile.TemporaryDirectory(prefix="tablade-selftest-", ignore_cleanup_errors=True) as tmp:
        src = Path(tmp) / "selftest.csv"
        out = Path(tmp) / "selftest.parquet"
        state: dict[str, Any] = {}

        def make_fixture() -> str:
            write_simple_csv(src, n=ROWS, encoding="cp950")
            return f"{src.stat().st_size:,} bytes（cp950）"

        def read() -> str:
            result = read_csv(src)
            assert result.encoding == "cp950", f"偵測到的編碼是 {result.encoding}，預期 cp950"
            assert result.frame.height == ROWS, f"讀到 {result.frame.height} 列，預期 {ROWS}"
            state["frame"] = result.frame
            return f"{result.frame.height} 列 × {result.frame.width} 欄，編碼 {result.encoding}"

        def write() -> str:
            write_parquet(state["frame"], out)
            return f"{out.stat().st_size:,} bytes"

        def read_back() -> str:
            back = pl.read_parquet(out)
            assert back.equals(state["frame"]), "讀回的資料與原始資料不一致"
            return "內容一致"

        def lazy() -> str:
            summary = pl.scan_parquet(out).group_by("門市").len().collect()
            assert summary["len"].sum() == ROWS
            return f"group_by 得到 {summary.height} 組"

        def excel_engine() -> str:
            # 寫一個假的 .xlsx 再讀回來：打包後的 App 讀得了 Excel，列號、日期、數字都對
            import fastexcel

            from .fixtures import excel_export, write_excel_export
            from .ingest.excel import read_sheet

            path = write_excel_export(Path(tmp) / "明細.xlsx", n=20, seed=0)
            frame = read_sheet(path, "明細", path.name)
            first = excel_export(20, 0)[1][0]
            row = frame.row(4, named=True)  # 第 5 列：標題、匯出時間、空白列、標題列之後
            assert row["__line_no"] == 5, "Excel 的列號對不上"
            assert row["A"] == first["日期"] and row["F"] == first["金額"], (
                "Excel 的日期或數字讀錯了"
            )
            version = getattr(fastexcel, "__version__", "?")
            return f"讀回 {frame.height} 列，列號、日期、數字正確（fastexcel {version}）"

        def parallel_transcode() -> str:
            # 不管這台電腦有幾個核心，都實際開 2 個工作程序轉碼一次（切成很小的段，強迫走平行），
            # 確認打包後的 App 開得了工作程序；核心太少時，平常的大檔才改用單核心
            import os
            from concurrent.futures.process import BrokenProcessPool

            from .fixtures import write_big_messy_export
            from .ingest import sources

            src = write_big_messy_export(Path(tmp) / "平行.csv", 300_000, encoding="cp950")
            single, parallel = Path(tmp) / "single.txt", Path(tmp) / "parallel.txt"
            sources._transcode_single(src, "cp950", single, None)
            usual = sources._workers()
            normally = (
                f"大檔平常用 {usual} 個工作程序"
                if usual
                else f"這台電腦只有 {os.cpu_count()} 個核心，大檔平常用單核心轉碼"
            )
            pool = sources.new_pool(SELFTEST_WORKERS)
            try:
                digest = sources._transcode_parallel(
                    src, "cp950", parallel, None, 16_384, pool=pool
                )
            except (OSError, RuntimeError, BrokenProcessPool):
                sources._pool_failed = True  # 這次執行之後的大檔都用單核心，不再重試
                return "這台電腦不能開工作程序，大檔改用單核心轉碼（結果正確）"
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
            assert digest is not None, "測試檔沒有切成多段"
            assert parallel.read_bytes() == single.read_bytes(), "平行轉碼的結果和單核心不同"
            return f"開 {SELFTEST_WORKERS} 個工作程序同時轉碼，結果與單核心相同；{normally}"

        def full_recipe() -> str:
            # 跟按「處理並匯出資料」同一條路：轉碼成暫存檔、各檔分開處理、8 個步驟、串流匯出；
            # 最後再臨時加上「合併來源檔」（02 排前面），連合併也一起檢查（範例 recipe 本身不動）
            from .app import resource_dir  # 打包後也找得到範例 recipe（延後 import，避免循環）
            from .engine.run import execute
            from .fixtures import messy_export, write_messy_export
            from .recipe.schema import load_json, parse_recipe

            work = Path(tmp) / "recipe"
            write_messy_export(work / "data" / "明細_01.csv", n=50, seed=0, encoding="cp950")
            write_messy_export(work / "data" / "明細_02.csv", n=30, seed=1, encoding="cp950")
            data = load_json(resource_dir() / "examples" / "sales_detail.recipe.json")
            data["steps"].append(
                {"id": "selftest_merge", "type": "merge_files", "order": ["data/明細_02.csv"]}
            )
            result = work / "result.parquet"
            _, report = execute(parse_recipe(data), work, output=result, mode="stream")
            out = pl.read_parquet(result)
            expected = messy_export(30, 1)[1] + messy_export(50, 0)[1]
            got = out.drop("__source_file", "__line_no").to_dicts()
            assert got == expected, f"結果有 {len(got)} 列，和預期的 {len(expected)} 列不同"
            assert set(out["__source_file"]) == {"合併"}, "合併後的來源檔名稱不對"
            assert out["__line_no"].to_list() == list(range(1, out.height + 1)), (
                "合併後的行號沒有從 1 重編"
            )
            # 處理紀錄：輸出檔的 SHA-256 要和實際的檔案一樣
            from .export.manifest import file_sha256

            assert report.manifest, "沒有寫出處理紀錄（manifest）"
            manifest = json.loads(Path(report.manifest).read_text(encoding="utf-8"))
            assert manifest["output"]["sha256"] == file_sha256(result), "處理紀錄的 SHA-256 不對"
            assert len(manifest["inputs"]) == 2, "處理紀錄的輸入檔數量不對"
            return f"2 個 cp950 檔 → 合併成 {report.rows} 列，與預期一致；處理紀錄正確"

        steps = [
            ("產生假資料 CSV", "可以在暫存資料夾寫檔", make_fixture),
            ("讀取 CSV（自動偵測編碼）", "讀檔與 Big5（cp950）編碼偵測正常", read),
            ("寫出 Parquet", "可以輸出 Parquet 檔", write),
            ("讀回 Parquet 並比對", "寫出的內容完全正確", read_back),
            ("Lazy 查詢", "Polars 的查詢引擎正常", lazy),
            ("讀 Excel", "可以讀 Excel 工作表（列號、日期、數字）", excel_engine),
            ("多核心轉碼", "大檔可以用多個程序同時轉碼，結果和單核心相同", parallel_transcode),
            (
                "完整執行範例 Recipe",
                "轉碼、各檔分開處理、8 個步驟、合併來源檔、串流匯出整條流程正常",
                full_recipe,
            ),
        ]
        for i, (name, purpose, fn) in enumerate(steps):
            if not check(name, purpose, fn):
                checks += [
                    Check(n, False, "前面的檢查失敗，沒有執行", p, ran=False)
                    for n, p, _ in steps[i + 1 :]
                ]
                break

    try:
        info = versions()
    except Exception as exc:
        info = {"error": f"{type(exc).__name__}: {exc}"}
    ok = all(c.ok for c in checks)
    return {"ok": ok, "checks": [asdict(c) for c in checks], "versions": info}


def format_report(result: dict[str, Any]) -> str:
    lines = ["Tablade 自我檢測：" + ("通過" if result["ok"] else "失敗")]
    for c in result["checks"]:
        mark = "OK" if c["ok"] else ("NG" if c.get("ran", True) else "--")
        lines.append(f"  [{mark}] {c['name']}：{c['detail']}")
    lines.append("  " + "，".join(f"{k} {v}" for k, v in result["versions"].items()))
    return "\n".join(lines)
