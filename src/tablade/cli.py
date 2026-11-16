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

    run = sub.add_parser("run", help="執行 recipe，輸出 Parquet")
    run.add_argument("recipe", type=Path)
    run.add_argument("--json", action="store_true", help="以 JSON 輸出結果")

    validate = sub.add_parser("validate", help="驗證 recipe（結束代碼 0 表示通過）")
    validate.add_argument("recipe", type=Path)
    validate.add_argument("--run", action="store_true", help="另外用真實資料試跑（不寫出檔案）")
    validate.add_argument("--json", action="store_true", help="以 JSON 輸出結果")

    schema = sub.add_parser("schema", help="輸出步驟規格與 recipe JSON Schema")
    schema.add_argument("--out", type=Path, help="寫到檔案（預設印出）")

    sub.add_parser("version", help="顯示版本")
    return parser


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _check(args: argparse.Namespace, *, dry_run: bool) -> int:
    from .engine.check import check_recipe_file, format_issues, format_report
    from .engine.run import RunError, execute

    result = check_recipe_file(args.recipe, dry_run=dry_run)
    writing = args.command == "run" and result.ok and result.recipe is not None
    if writing:
        try:
            _, result.report = execute(result.recipe, args.recipe.parent)
        except RunError as err:
            result.issues.append({"path": "steps", "error": str(err), "severity": "error"})
    if args.json:
        _print_json(
            {
                "ok": result.ok,
                "issues": result.issues,
                "report": result.report.to_dict() if result.report else None,
            }
        )
    else:
        if result.issues:
            errors = sum(1 for i in result.issues if i["severity"] == "error")
            print(f"recipe 有 {errors} 個錯誤：" if errors else "recipe 通過驗證，有提醒：")
            print(format_issues(result.issues))
        elif not result.report:
            print("recipe 通過驗證")
        if result.report:
            print(format_report(result.report))
    return 0 if result.ok else 1


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

    if command in ("run", "validate"):
        return _check(args, dry_run=command == "validate" and args.run)

    if command == "schema":
        from .recipe.spec import build_spec

        text = json.dumps(build_spec(), ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
            print(f"已寫入 {args.out}")
        else:
            print(text)
        return 0

    if command == "version":
        from .selftest import versions

        print(" ".join(f"{k}={v}" for k, v in versions().items()))
        return 0

    raise AssertionError(command)


if __name__ == "__main__":
    sys.exit(main())
