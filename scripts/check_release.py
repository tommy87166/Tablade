#!/usr/bin/env python3
"""發版前檢查：推上去的 tag 要和程式的版號一致（CI 在推 v* tag 時執行）。

    uv run python scripts/check_release.py v0.2.0

App 啟動時拿 GitHub 最新 release 的 tag 和自己的 ``__version__`` 比較（``src/tablade/update.py``）。
tag 和版號不一致時（例如推了 v0.2.0 卻忘了改 ``src/tablade/__init__.py``），新版的 App 會以為自己
是舊版，永遠顯示「有新版本」，所以不一致就讓發版失敗。
"""

from __future__ import annotations

import re
import sys

from tablade import __version__

TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def check(tag: str, version: str = __version__) -> str | None:
    """沒問題回傳 None，否則回傳錯誤說明。"""
    if not TAG.fullmatch(tag):
        return f"tag「{tag}」的格式要是 v主版號.次版號.修訂號，例如 v0.2.0"
    if tag[1:] != version:
        return (
            f"tag 是 {tag}，但 src/tablade/__init__.py 的 __version__ 是 {version}："
            f"請先把 __version__ 改成 {tag[1:]}、commit，再打 tag"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法：check_release.py v0.2.0", file=sys.stderr)
        return 2
    error = check(args[0])
    if error:
        print(error, file=sys.stderr)
        return 1
    print(f"tag {args[0]} 與程式版號一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
