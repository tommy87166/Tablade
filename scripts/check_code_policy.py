#!/usr/bin/env python3
"""檢查 src/ 是否違反程式規則（DESIGN.md §4、§11）。

- 禁止任何網路相關模組（不做遙測、不自動更新、不下載擴充）。
  唯一例外：``src/tablade/update.py`` 可以用 ``urllib.request`` 向 GitHub 查最新版號（不下載）
- 禁止 eval / exec / compile / __import__ / importlib.import_module：呼叫、取別名
  （``e = eval``、``from importlib import import_module``）、從模組取（``builtins.eval``、
  ``getattr(builtins, "eval")``）都算
- 呼叫外部程式（subprocess、os.system、os.popen、os.exec*、os.spawn*、os.startfile）只有
  ``src/tablade/desktop.py`` 可以（用系統的程式開啟資料夾）
- Polars 的 ``read_*`` / ``scan_*`` 不能讀網址（字串裡有 ``://``）或雲端（``storage_options``）

例外清單比對的是完整的相對路徑（相對於 repo 根目錄），別的資料夾裡同名的檔不算。
用 AST 分析，只用標準函式庫。這是把關的工具、不是沙箱：刻意繞過（例如拼字串再 getattr）擋不住。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
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
ALLOWED_NETWORK = {"src/tablade/update.py": {"urllib.request", "urllib.error"}}
"""只有這些檔案（相對於 repo 根目錄）可以 import 這幾個網路模組。"""

FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "import_module"}
"""不能呼叫、也不能取別名的名稱。"""
BUILTIN_MODULES = {"builtins", "importlib"}
"""從這些模組取 FORBIDDEN_NAMES 也不行（``builtins.eval``，含 ``import builtins as b`` 的別名）。
其他物件的同名方法可以：``re.compile`` 是正規表示式，Polars 的 ``.list.eval`` 是運算式。"""

PROCESS_MODULES = {"subprocess", "pty"}
PROCESS_OS_CALLS = {"system", "popen", "startfile", "posix_spawn", "posix_spawnp"}
PROCESS_OS_PREFIXES = ("exec", "spawn")
ALLOWED_PROCESS = {"src/tablade/desktop.py"}
"""只有這些檔案可以呼叫外部程式。"""

POLARS_NAMES = {"pl", "polars"}


def relative(path: str) -> str:
    """比對例外清單用的路徑：相對於 repo 根目錄、用 / 分隔（``./src/x.py`` 與絕對路徑都換成 ``src/x.py``）。"""
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return p.as_posix()
    return PurePosixPath(p.as_posix()).as_posix()  # 「./」會被拿掉


def _forbidden(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in FORBIDDEN_MODULES)


def _allowed(path: str, module: str) -> bool:
    return module in ALLOWED_NETWORK.get(relative(path), set())


def _process_module(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in PROCESS_MODULES)


def _process_os(name: str) -> bool:
    return name in PROCESS_OS_CALLS or name.startswith(PROCESS_OS_PREFIXES)


def check_source(path: str, source: str) -> list[str]:
    problems = []
    may_run = relative(path) in ALLOWED_PROCESS
    tree = ast.parse(source, filename=path)
    owners = {"__builtins__"} | {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name in BUILTIN_MODULES
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden(alias.name) and not _allowed(path, alias.name):
                    problems.append(f"{path}:{node.lineno}: 禁止 import 網路相關模組 {alias.name}")
                elif _process_module(alias.name) and not may_run:
                    problems.append(
                        f"{path}:{node.lineno}: 只有 desktop.py 可以呼叫外部程式（{alias.name}）"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _forbidden(node.module) and not _allowed(path, node.module):
                problems.append(f"{path}:{node.lineno}: 禁止 import 網路相關模組 {node.module}")
            elif _process_module(node.module) and not may_run:
                problems.append(
                    f"{path}:{node.lineno}: 只有 desktop.py 可以呼叫外部程式（{node.module}）"
                )
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES and node.module in ("builtins", "importlib"):
                    problems.append(f"{path}:{node.lineno}: 禁止使用 {node.module}.{alias.name}")
                elif node.module == "os" and _process_os(alias.name) and not may_run:
                    problems.append(
                        f"{path}:{node.lineno}: 只有 desktop.py 可以呼叫外部程式（os.{alias.name}）"
                    )
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"{path}:{node.lineno}: 禁止使用 {node.id}")
        elif isinstance(node, ast.Attribute):
            owner = node.value.id if isinstance(node.value, ast.Name) else None
            if node.attr == "import_module" or (node.attr in FORBIDDEN_NAMES and owner in owners):
                problems.append(f"{path}:{node.lineno}: 禁止使用 {owner or '…'}.{node.attr}")
            elif owner == "os" and _process_os(node.attr) and not may_run:
                problems.append(
                    f"{path}:{node.lineno}: 只有 desktop.py 可以呼叫外部程式（os.{node.attr}）"
                )
        if isinstance(node, ast.Call):
            problems += _call_problems(path, node)
    return problems


def _call_problems(path: str, node: ast.Call) -> list[str]:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
        name = node.args[1]
        if isinstance(name, ast.Constant) and name.value in FORBIDDEN_NAMES:
            return [f"{path}:{node.lineno}: 禁止使用 getattr(…, {name.value!r})"]
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in POLARS_NAMES
        and func.attr.startswith(("read_", "scan_"))
    ):
        values = [*node.args, *(k.value for k in node.keywords)]
        urls = [
            v
            for v in values
            if isinstance(v, ast.Constant) and isinstance(v.value, str) and "://" in v.value
        ]
        if urls or any(k.arg == "storage_options" for k in node.keywords):
            return [f"{path}:{node.lineno}: Polars 不能讀網址或雲端（{func.value.id}.{func.attr}）"]
    return []


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
