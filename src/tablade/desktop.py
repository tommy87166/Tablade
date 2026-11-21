"""用系統的程式做兩件事（src/ 裡只有這裡可以呼叫外部程式，scripts/check_code_policy.py 會檢查）：

- 在檔案總管（Windows）／Finder（macOS）裡打開本機的資料夾（自我診斷畫面的「開啟暫存資料夾」）
- 自動更新換好檔之後，啟動新版的 Tablade（只啟動 Tablade 自己，selfupdate.py）

不連網路。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def relaunch(target: Path) -> None:
    """啟動換好的新版（Windows：tablade.exe；macOS：Tablade.app），不等它結束。

    onefile 執行時會把解壓的位置寫在環境變數裡；``PYINSTALLER_RESET_ENVIRONMENT`` 讓新版當成
    全新啟動，不去用舊版的暫存資料夾。
    """
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        subprocess.Popen([str(target)], env=env, close_fds=True, creationflags=flags)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-n", str(target)], env=env, check=False)
    else:
        subprocess.Popen([str(target)], env=env, close_fds=True, start_new_session=True)
