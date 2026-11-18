#!/usr/bin/env python3
"""打包、檢測、壓縮成可發布的 zip（本機與 CI 共用）。

    uv run python scripts/build.py                 # 發布的產物（見下面）
    uv run python scripts/build.py onedir          # 指定要打包哪一種（onefile、onedir）
    uv run python scripts/build.py --gui-smoke     # 另外開視窗跑 smoke test

發布的產物（不指定 mode 時）：
- Windows：只有 onefile（單一 tablade.exe）。onefile 每次啟動都解壓到暫存資料夾；
  遇到防毒軟體或 AppLocker 擋暫存資料夾裡的程式時，可以用 ``build.py onedir`` 另外打包。
- macOS：只有 Tablade.app。.app 就是 onedir 包成 App（PyInstaller 不支援 onefile 的 .app）。

需要 Node.js 24 以上（先 build 前端成單一 HTML，再交給 PyInstaller），以及 Rust 模組
（解 zip 裡的 Deflate64，native/）：先執行 ``uv sync --group native``（要裝 Rust）。

輸出：
    frontend/dist/index.html         前端產物（必須小於 2 MB）
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
FRONTEND = ROOT / "frontend"
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


def release_modes() -> list[str]:
    """不指定 mode 時打包的種類：Windows 只發 onefile，macOS 只發 Tablade.app（onedir）。"""
    if IS_WIN:
        return ["onefile"]
    if IS_MAC:
        return ["onedir"]
    return list(MODES)


def executable(mode: str) -> Path:
    exe = "tablade.exe" if IS_WIN else "tablade"
    if mode == "onefile":
        return DIST / "onefile" / exe
    if IS_MAC:
        return DIST / "onedir" / "Tablade.app" / "Contents" / "MacOS" / exe
    return DIST / "onedir" / "tablade" / exe


def check_native() -> None:
    """發布的 App 一定要有 Rust 模組（解 zip 裡的 Deflate64）。"""
    try:
        import tablade_native
    except ImportError:
        raise SystemExit(
            "找不到 Rust 模組 tablade_native：先執行 uv sync --group native（要裝 Rust，見 native/README.md）"
        ) from None
    print(
        f"== Rust 模組 tablade_native {tablade_native.__version__}"
        f"（deflate64 {tablade_native.DEFLATE64_VERSION}）",
        flush=True,
    )


def build_frontend() -> None:
    from tablade.app import MAX_HTML_BYTES

    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("找不到 npm：請先安裝 Node.js 24 以上")
    print("== 前端", flush=True)
    if os.environ.get("CI") or not (FRONTEND / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)
    size = (FRONTEND / "dist" / "index.html").stat().st_size
    if size >= MAX_HTML_BYTES:
        raise SystemExit(f"前端產物 {size:,} bytes，超過 WebView2 的 2 MB 上限")
    print(f"== 前端產物 {size / 1024:.0f} KB")


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


def expected_skill_files() -> set[str]:
    """AI skill 一定要有的檔：說明、驗證器、步驟目錄、schema，以及 examples/ 底下的每一個範例 recipe。"""
    examples = {f"references/examples/{p.name}" for p in (ROOT / "examples").glob("*.recipe.json")}
    return {
        "SKILL.md",
        "scripts/validate_recipe.py",
        "references/steps.md",
        "references/recipe.schema.json",
    } | examples


def skill_check(mode: str) -> bool:
    """打包後的 App 也要能產生 AI skill，內容和原始碼產生的一樣（原始碼與每個範例都有打包進去）。"""
    import zipfile

    exe = executable(mode)
    out = DIST / f"skill-check-{mode}.zip"
    out.unlink(missing_ok=True)
    print(f"== 產生 AI skill {exe.relative_to(ROOT)}", flush=True)
    proc = subprocess.run([str(exe), "skill", "--out", str(out)], timeout=120)
    if proc.returncode != 0 or not out.is_file():
        return False
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    out.unlink()
    missing = sorted(expected_skill_files() - names)
    if missing:
        print(f"   AI skill 少了：{'、'.join(missing)}（tablade.spec 的 datas）")
    return not missing


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
        "modes",
        nargs="*",
        metavar="mode",
        help="onefile、onedir（預設：Windows 只打包 onefile，macOS 只打包 Tablade.app）",
    )
    parser.add_argument("--gui-smoke", action="store_true", help="另外開視窗跑 smoke test")
    parser.add_argument("--skip-build", action="store_true", help="只檢測與壓縮現有的產物")
    parser.add_argument("--skip-frontend", action="store_true", help="沿用現有的前端產物")
    args = parser.parse_args(argv)
    args.modes = args.modes or release_modes()
    if unknown := set(args.modes) - set(MODES):
        parser.error(f"不認得的 mode：{', '.join(sorted(unknown))}")

    if not args.skip_build:
        check_native()
    if not (args.skip_build or args.skip_frontend):
        build_frontend()

    failed = []
    for mode in args.modes:
        if not args.skip_build:
            build(mode)
        if not selftest(mode) or not skill_check(mode):
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
