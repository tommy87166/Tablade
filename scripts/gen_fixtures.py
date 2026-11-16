#!/usr/bin/env python3
"""產生範例假資料到指定資料夾（預設 tmp/fixtures，已在 .gitignore）。

    uv run python scripts/gen_fixtures.py [輸出資料夾] [--seed N]

產生的檔案不得 commit；需要新的資料形狀時，擴充 src/tablade/fixtures.py。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tablade.fixtures import write_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生範例假資料")
    parser.add_argument("out", nargs="?", type=Path, default=Path("tmp/fixtures"))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    for path in write_all(args.out, seed=args.seed):
        print(f"{path}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
