"""在檔案總管（Windows）／Finder（macOS）裡打開本機的資料夾（自我診斷畫面的「開啟暫存資料夾」）。

只開本機資料夾，不連網路。
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
