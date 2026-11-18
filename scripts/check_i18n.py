"""檢查前端沒有漏翻的中文：中文只能出現在 ``tr('中文', 'English')`` 的第一個參數（docs/i18n-plan.md）。

掃 ``frontend/src`` 的 .ts／.tsx，不含 ``dev/``、``mock.ts``（開發用假資料）、``i18n.ts``、``components/ui/``。
註解裡的中文不管；同一行有 ``i18n-ignore`` 的不檢查（例如比對 Python 產生的文字的正規表示式）。

Python：``PY_MODULES`` 列的檔案（畫面上看得到的文字），中文只能出現在 ``t("中文", "English")`` 的第一個參數
（``tablade/i18n.py``）；docstring 與註解不算，同一行有 ``i18n-ignore`` 的不檢查（例如程式產生的欄名是資料）。

用法：``uv run python scripts/check_i18n.py [檔案 …]``（不給檔案就掃全部；.py 檔用 Python 的規則）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
SKIP_DIRS = {"dev", "ui"}
SKIP_FILES = {"mock.ts", "i18n.ts", "vite-env.d.ts"}
CJK = re.compile(r"[　-〿㐀-鿿豈-﫿＀-￯]")
TR_BEFORE = re.compile(r"\btr\(\s*$")
PY_SRC = ROOT / "src" / "tablade"
PY_MODULES = [
    # 第 2 批：畫面狀態與流程（docs/i18n-plan.md）
    "session.py",
    "api.py",
    "engine/run.py",
    "engine/check.py",
    "engine/progress.py",
    "export/outputs.py",
    "dtypes.py",
    "ingest/sources.py",
    "ingest/excel.py",
    "ingest/encoding.py",
    "ingest/transcode.py",
    "selftest.py",
    "update.py",
]
"""要檢查的 Python 檔（相對於 src/tablade）；之後幾批會再加上 steps/、recipe/。"""


def files() -> list[Path]:
    out = []
    for path in sorted(SRC.rglob("*.ts*")):
        rel = path.relative_to(SRC)
        if path.suffix not in (".ts", ".tsx") or path.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        out.append(path)
    return out


class _Scanner:
    """走過程式碼：註解跳過；字串依「是不是 tr() 的第一個參數」決定裡面的中文算不算數；
    樣板字串裡的 ${…} 當作程式碼再走一次（裡面的 tr() 另外判斷），在 tr() 第一個參數裡的就全部算翻過。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.bad: set[int] = set()

    def mark(self, start: int, end: int) -> None:
        for m in CJK.finditer(self.text, start, end):
            self.bad.add(self.text.count("\n", 0, m.start()) + 1)

    def code(self, i: int, allowed: bool, nested: bool) -> int:
        """從 i 開始的程式碼；nested 時走到對應的 } 為止（回傳 } 的下一個位置）。"""
        text, n, depth, plain = self.text, len(self.text), 0, i
        while i < n:
            if text.startswith("//", i) or text.startswith("/*", i):
                if not allowed:
                    self.mark(plain, i)
                close = "\n" if text[i + 1] == "/" else "*/"
                end = text.find(close, i + 2)
                i = plain = n if end < 0 else end + len(close)
                continue
            c = text[i]
            if c in "'\"`":
                if not allowed:
                    self.mark(plain, i)  # 字串前面的程式碼（例如 JSX 的文字）
                arg = allowed or bool(TR_BEFORE.search(text[max(0, i - 40) : i]))
                i = plain = self.string(i, arg)
                continue
            if nested and c == "{":
                depth += 1
            elif nested and c == "}":
                if depth == 0:
                    if not allowed:
                        self.mark(plain, i)
                    return i + 1
                depth -= 1
            i += 1
        if not allowed:
            self.mark(plain, n)
        return n

    def string(self, i: int, allowed: bool) -> int:
        """從引號開始的字串；回傳結束引號的下一個位置。"""
        text, n, quote = self.text, len(self.text), self.text[i]
        i += 1
        plain = i
        while i < n:
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == quote or (quote != "`" and c == "\n"):
                if not allowed:
                    self.mark(plain, i)
                return i + 1 if c == quote else i
            if quote == "`" and text.startswith("${", i):
                if not allowed:
                    self.mark(plain, i)
                i = plain = self.code(i + 2, allowed, nested=True)
                continue
            i += 1
        if not allowed:
            self.mark(plain, n)
        return n


def python_files() -> list[Path]:
    return [PY_SRC / name for name in PY_MODULES]


def python_violations(text: str) -> list[tuple[int, str]]:
    """Python：沒有包在 t() 第一個參數裡的中文字串（docstring 不算）：[(行號, 那一行)]。"""
    lines = text.split("\n")
    tree = ast.parse(text)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def docstring(node: ast.AST) -> bool:
        return isinstance(parents.get(node), ast.Expr)

    def inside_t(node: ast.AST) -> bool:
        child, parent = node, parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.Call) and parent.args and parent.args[0] is child:
                func = parent.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "t":
                    return True
            child, parent = parent, parents.get(parent)
        return False

    bad: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not CJK.search(node.value) or docstring(node):
            continue
        first = node.lineno
        last = getattr(node, "end_lineno", first) or first
        if any("i18n-ignore" in lines[i - 1] for i in range(first, last + 1)):
            continue
        if not inside_t(node):
            bad.add(first)
    return [(no, lines[no - 1].strip()) for no in sorted(bad)]


def violations(text: str) -> list[tuple[int, str]]:
    """沒有包在 tr() 第一個參數裡的中文：[(行號, 那一行)]。"""
    lines = text.split("\n")
    # 有 i18n-ignore 的行換成空白（保留位置）
    blanked = "\n".join(" " * len(line) if "i18n-ignore" in line else line for line in lines)
    scanner = _Scanner(blanked)
    scanner.code(0, allowed=False, nested=False)
    return [(no, lines[no - 1].strip()) for no in sorted(scanner.bad)]


def main(argv: list[str]) -> int:
    targets = [Path(a).resolve() for a in argv] if argv else files() + python_files()
    found = 0
    for path in targets:
        check = python_violations if path.suffix == ".py" else violations
        for no, line in check(path.read_text(encoding="utf-8")):
            found += 1
            print(f"{path.relative_to(ROOT)}:{no}: {line}")
    if found:
        print(f"\n{found} 行有沒包在 tr()／t() 裡的中文", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
