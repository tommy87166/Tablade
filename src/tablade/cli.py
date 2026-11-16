"""命令列介面。沒有子命令時開啟視窗。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ingest.encoding import SUPPORTED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tablade", description="本機執行的資料清洗工具")
    sub = parser.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help="開啟視窗（預設）")
    gui.add_argument(
        "--smoke", action="store_true", help="開啟視窗、自動跑自我檢測後關閉（結束代碼 0 表示通過）"
    )

    selftest = sub.add_parser(
        "selftest", help="用假資料跑一次 CSV → Parquet（結束代碼 0 表示通過）"
    )
    selftest.add_argument("--out", type=Path, help="把結果另外寫成 JSON 檔")

    convert = sub.add_parser("convert", help="把一個 CSV 轉成 Parquet")
    convert.add_argument("input", type=Path)
    convert.add_argument("output", type=Path)
    convert.add_argument("--encoding", default="auto", choices=SUPPORTED)

    sub.add_parser("version", help="顯示版本")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "gui"

    if command == "gui":
        from .app import run

        return run(smoke=getattr(args, "smoke", False))

    if command == "selftest":
        from .selftest import format_report, run_selftest

        result = run_selftest()
        print(format_report(result))
        if args.out:
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["ok"] else 1

    if command == "convert":
        from .export.parquet import write_parquet
        from .ingest.csv_reader import read_csv

        result = read_csv(args.input, encoding=args.encoding)
        write_parquet(result.frame, args.output)
        print(
            f"{args.input.name}（{result.encoding}）→ {args.output.name}："
            f"{result.frame.height:,} 列 × {result.frame.width} 欄"
        )
        return 0

    if command == "version":
        from .selftest import versions

        print(" ".join(f"{k}={v}" for k, v in versions().items()))
        return 0

    raise AssertionError(command)


if __name__ == "__main__":
    sys.exit(main())
