"""啟動時檢查 GitHub 上最新發布的版本（DESIGN.md §11）。

這是 Tablade **唯一**的對外連線：對 api.github.com 發一個 GET，只讀最新 release 的版號。
不送出任何使用者資料、不下載、不自動更新。設定環境變數 ``TABLADE_NO_UPDATE_CHECK=1``
就完全不連線（例如網路受管控的電腦）。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from . import __version__

REPOSITORY = "tommy87166/Tablade"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TIMEOUT = 3.0


def parse_version(text: str) -> tuple[int, ...]:
    """版號 → 數字（v0.2.0 → (0, 2, 0)）；讀不出數字時是空的 tuple。"""
    return tuple(int(n) for n in re.findall(r"\d+", text.split("-")[0])[:3])


def check_latest(timeout: float = TIMEOUT) -> dict[str, Any]:
    """回傳目前版本、最新版本、是否有新版、下載頁網址；連不上時 ``error`` 說明原因。"""
    result: dict[str, Any] = {
        "current": __version__,
        "latest": None,
        "newer": False,
        "url": RELEASES_URL,
        "error": None,
    }
    if os.environ.get("TABLADE_NO_UPDATE_CHECK"):
        result["error"] = "已關閉檢查更新"
        return result
    request = urllib.request.Request(
        LATEST_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"Tablade/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as err:
        result["error"] = (
            "GitHub 上還沒有發布版本" if err.code == 404 else f"無法檢查更新（HTTP {err.code}）"
        )
        return result
    except (OSError, ValueError):  # 連不上、逾時、回應不是 JSON
        result["error"] = "無法連線到 GitHub，略過檢查更新"
        return result
    latest = str(data.get("tag_name") or "").removeprefix("v") if isinstance(data, dict) else ""
    if not parse_version(latest):
        result["error"] = "無法判讀最新版號"
        return result
    result["latest"] = latest
    result["newer"] = parse_version(latest) > parse_version(__version__)
    result["url"] = str(data.get("html_url") or RELEASES_URL)
    return result
