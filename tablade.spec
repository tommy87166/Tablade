# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 設定（DESIGN.md §10）。

    uv run pyinstaller tablade.spec                          # onefile（預設）
    TABLADE_BUILD=onedir uv run pyinstaller tablade.spec     # 資料夾版；macOS 另外產生 Tablade.app

- Windows：兩種都是視窗程式（不跳出主控台）。命令列子命令的結果請用結束代碼或 ``--out`` 取得。
- macOS：PyInstaller 不支援 onefile 的 .app，所以 onefile 是命令列執行檔，視窗版用 onedir 的 Tablade.app。
"""

import os
import sys

MODE = os.environ.get("TABLADE_BUILD", "onefile")
if MODE not in ("onefile", "onedir"):
    raise SystemExit(f"TABLADE_BUILD 必須是 onefile 或 onedir，收到 {MODE!r}")
IS_MAC = sys.platform == "darwin"
ICON = "assets/icon.icns" if IS_MAC else "assets/icon.ico"

a = Analysis(
    ["src/tablade/__main__.py"],
    pathex=["src"],
    datas=[("frontend", "frontend"), ("assets/icon.svg", "assets")],
    hiddenimports=[],
    excludes=["tkinter", "unittest", "pydoc", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if MODE == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="tablade",
        icon=ICON,
        console=IS_MAC,
        upx=False,
        runtime_tmpdir=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="tablade",
        icon=ICON,
        console=False,
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="tablade", upx=False)
    if IS_MAC:
        app = BUNDLE(
            coll,
            name="Tablade.app",
            icon=ICON,
            info_plist={
                "CFBundleName": "Tablade",
                "CFBundleDisplayName": "Tablade",
                "NSHighResolutionCapable": True,
            },
        )
