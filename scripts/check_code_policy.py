#!/usr/bin/env python3
"""檢查 src/ 是否違反程式規則（DESIGN.md §4、§11）。

- 禁止任何網路相關模組（不做遙測、不自動更新、不下載擴充）
- 禁止 eval / exec / compile / __import__ / importlib.import_module

用 AST 分析，只用標準函式庫。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

FORBIDDEN_MODULES = (
    "socket",
    "socketserver",
    "ssl",
    "select",
    "selectors",
    "asyncio",
    "http",
    "urllib",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "webbrowser",
    "bottle",
)
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_ATTR_CALLS = {("importlib", "import_module")}


def _forbidden(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in FORBIDDEN_MODULES)


def check_source(path: str, source: str) -> list[str]:
    problems = []
    tree = ast.parse(source, filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden(alias.name):
                    problems.append(f"{path}:{node.lineno}: 禁止 import 網路相關模組 {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _forbidden(node.module):
                problems.append(f"{path}:{node.lineno}: 禁止 import 網路相關模組 {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                problems.append(f"{path}:{node.lineno}: 禁止呼叫 {func.id}()")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and (func.value.id, func.attr) in FORBIDDEN_ATTR_CALLS
            ):
                problems.append(f"{path}:{node.lineno}: 禁止呼叫 {func.value.id}.{func.attr}()")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["src"], help="要檢查的目錄或檔案")
    args = parser.parse_args(argv)

    problems: list[str] = []
    for target in args.paths:
        base = Path(target)
        files = sorted(base.rglob("*.py")) if base.is_dir() else [base]
        for file in files:
            problems.extend(check_source(file.as_posix(), file.read_text(encoding="utf-8")))

    if problems:
        print("check_code_policy：違反程式規則。", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
