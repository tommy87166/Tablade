#!/usr/bin/env python3
"""擋下可能含真實資料的檔案（DESIGN.md §11）。

pre-commit 會把要 commit 的檔名當參數傳進來；CI 用 ``--all`` 掃描所有版控中的檔案。
只使用標準函式庫，方便在任何環境執行。

規則：
1. 資料檔副檔名（白名單路徑除外）
2. 超過 1 MiB 的檔案
3. 疑似身分證字號／居留證號（通過檢查碼驗證才算）
4. 疑似統一編號（8 碼數字出現在「統編」等關鍵字附近）
5. 本機私有封鎖清單 ``.tablade-denylist`` 裡的字詞（該檔不進版控）

輸出時會遮蔽命中的值，避免在公開的 CI log 裡二次外洩。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_BYTES = 1024 * 1024
DENYLIST_NAME = ".tablade-denylist"

DATA_SUFFIXES = {
    ".csv", ".tsv", ".txt",
    ".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".numbers",
    ".parquet", ".feather", ".arrow", ".ipc",
    ".db", ".sqlite", ".sqlite3", ".duckdb", ".accdb", ".mdb",
    ".sav", ".dta", ".sas7bdat",
    ".pdf", ".doc", ".docx",
    ".zip", ".7z", ".rar",
}  # fmt: skip
DATA_NAME_SUFFIXES = (".manifest.json", ".recipe.json")

# 身分證字號（第 2 碼 1/2）與新式居留證號（第 2 碼 8/9），檢查碼規則相同
TW_ID_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z])([1289]\d{8})(?![0-9])")
TW_ID_LETTER_CODES = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [10, 11, 12, 13, 14, 15, 16, 17, 34, 18, 19, 20, 21,
         22, 35, 23, 24, 25, 26, 27, 28, 29, 32, 30, 31, 33],
        strict=True,
    )
)  # fmt: skip
UBN_KEYWORDS = r"(?:統一編號|統編|UBN)"
UBN_RES = (
    re.compile(UBN_KEYWORDS + r"\D{0,10}?(?<!\d)(\d{8})(?!\d)"),
    re.compile(r"(?<!\d)(\d{8})(?!\d)\D{0,5}?" + UBN_KEYWORDS),
)


@dataclass(frozen=True)
class Problem:
    path: str
    line: int | None
    reason: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.reason}"


def is_valid_tw_id(text: str) -> bool:
    """檢查 1 個英文字母 + 9 碼數字是否通過身分證／居留證檢查碼。"""
    if len(text) != 10 or text[0] not in TW_ID_LETTER_CODES or not text[1:].isdigit():
        return False
    code = TW_ID_LETTER_CODES[text[0]]
    digits = [int(c) for c in text[1:]]
    total = code // 10 + (code % 10) * 9
    total += sum(d * w for d, w in zip(digits[:8], range(8, 0, -1), strict=True))
    total += digits[8]
    return total % 10 == 0


def mask(value: str) -> str:
    if len(value) <= 3:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 3) + value[-1]


def is_allowlisted(path: PurePosixPath) -> bool:
    parts = path.parts
    name = path.name
    if parts and parts[0] == "examples" and name.endswith(".recipe.json"):
        return True
    if parts and parts[0] == "docs" and path.suffix == ".txt":
        return True
    return name.startswith("requirements") and name.endswith(".txt")


def is_data_file(path: PurePosixPath) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in DATA_SUFFIXES or name.endswith(DATA_NAME_SUFFIXES)


def decode_text(raw: bytes) -> str | None:
    """回傳解碼後的文字；看起來像二進位檔時回傳 None。"""
    if b"\x00" in raw[:8192]:
        return None
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def load_denylist(root: Path) -> list[str]:
    path = root / DENYLIST_NAME
    if not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append(term)
    return terms


def scan_text(rel: str, text: str, denylist: list[str]) -> list[Problem]:
    problems: list[Problem] = []
    lowered_terms = [(t, t.casefold()) for t in denylist]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in TW_ID_RE.finditer(line):
            value = m.group(1) + m.group(2)
            if is_valid_tw_id(value):
                problems.append(Problem(rel, lineno, f"疑似身分證字號／居留證號 {mask(value)}"))
        for pattern in UBN_RES:
            for m in pattern.finditer(line):
                problems.append(Problem(rel, lineno, f"疑似統一編號 {mask(m.group(1))}"))
        folded = line.casefold()
        for term, folded_term in lowered_terms:
            if folded_term in folded:
                problems.append(Problem(rel, lineno, f"含有封鎖清單字詞 {mask(term)}"))
    return problems


def check_file(root: Path, rel: str, denylist: list[str]) -> list[Problem]:
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.name == DENYLIST_NAME:
        return [Problem(rel, None, "私有封鎖清單本身不得 commit")]

    problems: list[Problem] = []
    if is_data_file(pure) and not is_allowlisted(pure):
        problems.append(
            Problem(rel, None, "資料檔副檔名，不得 commit（測試資料請用 gen_fixtures.py 產生）")
        )

    folded_path = rel.casefold()
    for term in denylist:
        if term.casefold() in folded_path:
            problems.append(Problem(rel, None, f"檔名含有封鎖清單字詞 {mask(term)}"))

    full = root / rel
    if not full.is_file():
        return problems  # 已刪除的檔案
    size = full.stat().st_size
    if size > MAX_BYTES:
        problems.append(Problem(rel, None, f"檔案大小 {size:,} bytes 超過 1 MiB"))
        return problems

    text = decode_text(full.read_bytes())
    if text is not None:
        problems.extend(scan_text(rel, text, denylist))
    return problems


def repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        )
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd()


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", help="要檢查的檔案（相對於 repo 根目錄）")
    parser.add_argument("--all", action="store_true", help="檢查所有版控中的檔案")
    args = parser.parse_args(argv)

    root = repo_root()
    files = tracked_files(root) if args.all else args.files
    denylist = load_denylist(root)

    problems: list[Problem] = []
    for rel in files:
        path = Path(rel)
        if path.is_absolute():
            path = path.relative_to(root)
        problems.extend(check_file(root, path.as_posix(), denylist))

    if problems:
        print("check_no_data：發現可能含真實資料的檔案，已擋下。", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
