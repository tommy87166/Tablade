#!/usr/bin/env python3
"""打包、檢測、壓縮成可發布的 zip（本機與 CI 共用）。

    uv run python scripts/build.py                 # onefile + onedir
    uv run python scripts/build.py onedir          # 只打包一種
    uv run python scripts/build.py --gui-smoke     # 另外開視窗跑 smoke test

輸出：
    dist/onefile/、dist/onedir/      PyInstaller 產物
    dist/release/*.zip               可發布的壓縮檔

每個產物都會跑一次 ``selftest``，失敗就結束代碼 1。
``--gui-smoke`` 的失敗只會警告（CI 環境不一定開得了視窗）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
RELEASE = DIST / "release"
MODES = ("onefile", "onedir")
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def platform_tag() -> str:
    system = "windows" if IS_WIN else "macos" if IS_MAC else "linux"
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def executable(mode: str) -> Path:
    exe = "tablade.exe" if IS_WIN else "tablade"
    if mode == "onefile":
        return DIST / "onefile" / exe
    if IS_MAC:
        return DIST / "onedir" / "Tablade.app" / "Contents" / "MacOS" / exe
    return DIST / "onedir" / "tablade" / exe


def build(mode: str) -> None:
    print(f"== 打包 {mode}", flush=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST / mode),
            "--workpath",
            str(ROOT / "build" / mode),
            str(ROOT / "tablade.spec"),
        ],
        cwd=ROOT,
        env={**os.environ, "TABLADE_BUILD": mode},
        check=True,
    )


def selftest(mode: str) -> bool:
    exe = executable(mode)
    out = DIST / f"selftest-{mode}.json"
    out.unlink(missing_ok=True)
    print(f"== 自我檢測 {exe.relative_to(ROOT)}", flush=True)
    proc = subprocess.run([str(exe), "selftest", "--out", str(out)], timeout=300)
    if out.is_file():
        result = json.loads(out.read_text(encoding="utf-8"))
        for check in result["checks"]:
            print(f"   [{'OK' if check['ok'] else 'NG'}] {check['name']}：{check['detail']}")
    else:
        print("   沒有產生結果檔")
    return proc.returncode == 0


def gui_smoke(mode: str) -> bool:
    exe = executable(mode)
    print(f"== 視窗 smoke test {exe.relative_to(ROOT)}", flush=True)
    try:
        proc = subprocess.run([str(exe), "gui", "--smoke"], timeout=180)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def package(mode: str) -> Path:
    RELEASE.mkdir(parents=True, exist_ok=True)
    label = "app" if (IS_MAC and mode == "onedir") else mode
    target = RELEASE / f"tablade-{platform_tag()}-{label}.zip"
    target.unlink(missing_ok=True)
    if mode == "onefile":
        source = executable(mode)
    elif IS_MAC:
        source = DIST / "onedir" / "Tablade.app"
    else:
        source = DIST / "onedir" / "tablade"

    if IS_MAC:
        # ditto 會保留 .app 裡的符號連結與執行權限
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(source), str(target)], check=True)
    else:
        base = shutil.make_archive(
            str(target.with_suffix("")), "zip", root_dir=source.parent, base_dir=source.name
        )
        assert Path(base) == target
    print(f"== 壓縮 {target.relative_to(ROOT)}（{target.stat().st_size / 1024 / 1024:.1f} MB）")
    return target


def warn(message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::{message}")
    else:
        print(f"警告：{message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包 Tablade")
    parser.add_argument(
        "modes", nargs="*", metavar="mode", help="onefile、onedir（預設兩種都打包）"
    )
    parser.add_argument("--gui-smoke", action="store_true", help="另外開視窗跑 smoke test")
    parser.add_argument("--skip-build", action="store_true", help="只檢測與壓縮現有的產物")
    args = parser.parse_args(argv)
    args.modes = args.modes or list(MODES)
    if unknown := set(args.modes) - set(MODES):
        parser.error(f"不認得的 mode：{', '.join(sorted(unknown))}")

    failed = []
    for mode in args.modes:
        if not args.skip_build:
            build(mode)
        if not selftest(mode):
            failed.append(mode)
            continue
        if args.gui_smoke and not gui_smoke(mode):
            warn(f"{mode} 的視窗 smoke test 沒有通過")
        package(mode)

    if failed:
        print(f"自我檢測失敗：{', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
