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

    run = sub.add_parser("run", help="執行 Recipe，輸出 Parquet")
    run.add_argument("recipe", type=Path)
    run.add_argument(
        "-o", "--output", type=Path, help="輸出的 Parquet 檔（預設在 Recipe 旁邊、同名的 .parquet）"
    )
    run.add_argument("--json", action="store_true", help="以 JSON 輸出結果")

    validate = sub.add_parser("validate", help="驗證 Recipe（結束代碼 0 表示通過）")
    validate.add_argument("recipe", type=Path)
    validate.add_argument("--run", action="store_true", help="另外用真實資料試跑（不寫出檔案）")
    validate.add_argument("--json", action="store_true", help="以 JSON 輸出結果")

    schema = sub.add_parser("schema", help="輸出步驟規格與 Recipe JSON Schema")
    schema.add_argument("--out", type=Path, help="寫到檔案（預設印出）")

    skill = sub.add_parser(
        "skill", help="產生 AI skill（給 Microsoft 365 Copilot 等 AI 助理撰寫 Recipe）"
    )
    skill.add_argument("--out", type=Path, help="zip 檔位置（預設為目前資料夾）")
    skill.add_argument(
        "--probe", action="store_true", help="改產生 probe skill：檢查 AI 環境能不能執行 script"
    )

    prof = sub.add_parser("profile", help="把資料檔整理成給 AI 助理看的摘要（Markdown）")
    prof.add_argument("files", type=Path, nargs="+")
    prof.add_argument("--lines", type=int, default=30, help="列出前幾行（預設 30）")
    prof.add_argument("--mask", action="store_true", help="把數字、英文字母、文字遮罩成 9 / a / 〇")
    prof.add_argument("--out", type=Path, help="寫到檔案（預設印出）")

    sub.add_parser("version", help="顯示版本")
    return parser


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def default_output(recipe: Path) -> Path:
    """沒指定 --output 時：Recipe 旁邊、同名的 .parquet（明細.recipe.json → 明細.parquet）。"""
    name = recipe.name.removesuffix(".json").removesuffix(".recipe") or "result"
    return recipe.with_name(f"{name}.parquet")


def _check(args: argparse.Namespace, *, dry_run: bool) -> int:
    from .engine.check import check_recipe_file, format_issues, format_report
    from .engine.run import RunError, execute

    result = check_recipe_file(args.recipe, dry_run=dry_run)
    writing = args.command == "run" and result.ok and result.recipe is not None
    if writing:
        try:
            output = args.output or default_output(args.recipe)
            _, result.report = execute(result.recipe, args.recipe.parent, output=output)
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
            print(f"Recipe 有 {errors} 個錯誤：" if errors else "Recipe 通過驗證，有提醒：")
            print(format_issues(result.issues))
        elif not result.report:
            print("Recipe 通過驗證")
        if result.report:
            print(format_report(result.report))
    return 0 if result.ok else 1


def _tolerant_output() -> None:
    """主控台編碼印不出的字元（例如 cp950 沒有 ≥）改成替代字元，不要讓整個指令失敗。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _tolerant_output()
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

    if command == "skill":
        from .skill import skill_filename, write_zip

        target = args.out or Path(skill_filename(args.probe))
        write_zip(target, probe=args.probe)
        print(f"已產生 {target}")
        return 0

    if command == "profile":
        from .profile import profile

        text = profile(args.files, head=args.lines, mask=args.mask)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
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
