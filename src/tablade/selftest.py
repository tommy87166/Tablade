"""自我檢測：用假資料跑一次 CSV → Parquet，確認打包後的環境可以正常運作（M0）。"""

from __future__ import annotations

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


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


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

    def check(name: str, fn: Callable[[], str]) -> bool:
        try:
            checks.append(Check(name, True, fn()))
            return True
        except Exception as exc:
            checks.append(Check(name, False, f"{type(exc).__name__}: {exc}"))
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
            import fastexcel

            return f"fastexcel {getattr(fastexcel, '__version__', '?')}"

        steps = [
            ("產生假資料 CSV", make_fixture),
            ("讀取 CSV（自動偵測編碼）", read),
            ("寫出 Parquet", write),
            ("讀回 Parquet 並比對", read_back),
            ("Lazy 查詢", lazy),
            ("Excel 引擎", excel_engine),
        ]
        for name, fn in steps:
            if not check(name, fn):
                break

    try:
        info = versions()
    except Exception as exc:
        info = {"error": f"{type(exc).__name__}: {exc}"}
    ok = len(checks) == len(steps) and all(c.ok for c in checks)
    return {"ok": ok, "checks": [asdict(c) for c in checks], "versions": info}


def format_report(result: dict[str, Any]) -> str:
    lines = ["Tablade 自我檢測：" + ("通過" if result["ok"] else "失敗")]
    for c in result["checks"]:
        lines.append(f"  [{'OK' if c['ok'] else 'NG'}] {c['name']}：{c['detail']}")
    lines.append("  " + "，".join(f"{k} {v}" for k, v in result["versions"].items()))
    return "\n".join(lines)
