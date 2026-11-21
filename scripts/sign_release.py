#!/usr/bin/env python3
"""發版時替 Release 的 App 簽章（CI 的 release 作業，.github/workflows/build.yml）。

    TABLADE_UPDATE_SIGNING_KEY=… uv run python scripts/sign_release.py v1.3.0 release/

- 用 minisign 替每個平台的 zip 簽章，產生 ``<檔名>.minisig``；受保護的註解（trusted comment）寫
  ``tablade <版號> <檔名>``（selfupdate.trusted_comment），App 下載後會核對。
- 私鑰從環境變數 ``TABLADE_UPDATE_SIGNING_KEY``（GitHub Actions 的 secret，minisign -G -W 產生的
  沒有密碼的私鑰檔全文）讀進暫存檔，簽完就刪。
- 簽完立刻用程式裡的公鑰（selfupdate.PUBLIC_KEY）驗一次：對不上就停止發版，不會發出 App 驗不過的檔。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tablade import selfupdate  # noqa: E402

ASSETS = ("tablade-windows-x64-onefile.zip", "tablade-macos-arm64-app.zip")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    version = argv[0].removeprefix("v")
    folder = Path(argv[1])
    secret = os.environ.get("TABLADE_UPDATE_SIGNING_KEY", "")
    if not secret.strip():
        print("沒有設定 TABLADE_UPDATE_SIGNING_KEY（GitHub Actions 的 secret）", file=sys.stderr)
        return 1
    if not selfupdate.PUBLIC_KEY:
        print("src/tablade/selfupdate.py 的 PUBLIC_KEY 是空的", file=sys.stderr)
        return 1
    missing = [name for name in ASSETS if not (folder / name).is_file()]
    if missing:
        print(f"找不到要簽章的檔：{', '.join(missing)}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "update.key"
        key.write_text(secret if secret.endswith("\n") else secret + "\n", encoding="utf-8")
        key.chmod(0o600)
        pub = Path(tmp) / "update.pub"
        pub.write_text(
            f"untrusted comment: Tablade update key\n{selfupdate.PUBLIC_KEY}\n", encoding="utf-8"
        )
        for name in ASSETS:
            path = folder / name
            comment = selfupdate.trusted_comment(version, name)
            sig = path.with_name(name + ".minisig")
            subprocess.run(
                ["minisign", "-S", "-s", str(key), "-m", str(path), "-x", str(sig), "-t", comment],
                check=True,
                stdin=subprocess.DEVNULL,
            )
            # 用程式裡的公鑰驗一次，並確認受保護的註解是 App 會核對的那一行
            subprocess.run(
                ["minisign", "-V", "-p", str(pub), "-m", str(path), "-x", str(sig)], check=True
            )
            lines = sig.read_text(encoding="utf-8").splitlines()
            if len(lines) < 3 or lines[2] != f"trusted comment: {comment}":
                print(f"{sig.name} 的受保護註解不對：{lines[2:3]}", file=sys.stderr)
                return 1
            print(f"== 簽章 {sig.name}（{comment}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
