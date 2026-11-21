"""檢查與下載 GitHub 上最新發布的版本（DESIGN.md §11）。

這是 Tablade **唯一**對外連線的地方，對象只有 GitHub，不送出任何使用者資料：

- 啟動時：對 api.github.com 發一個 GET，只讀最新 release 的版號。
- 使用者按「更新並重新啟動」時：下載這個平台的 Release 檔（zip）與它的 minisign 簽章（.minisig）。
  網址寫死成 ``github.com/<repo>/releases/download/v<版號>/<固定檔名>``，畫面只能指定版號。
  驗章、解壓、換檔在 ``selfupdate.py``，重新啟動在 ``desktop.py``。

設定環境變數 ``TABLADE_NO_UPDATE_CHECK=1`` 就完全不連線（例如網路受管控的電腦）。
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from . import __version__
from .i18n import t

REPOSITORY = "tommy87166/Tablade"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TIMEOUT = 3.0
DOWNLOAD_TIMEOUT = 30.0
"""下載時每一次讀取最多等幾秒（不是整個下載的時間）。"""
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_SIGNATURE_BYTES = 4096
CHUNK = 1024 * 1024
VERSION_RE = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")


class UpdateError(Exception):
    """下載或安裝更新失敗：訊息直接顯示在畫面上。"""


def parse_version(text: str) -> tuple[int, ...]:
    """版號 → 數字（v0.2.0 → (0, 2, 0)）；讀不出數字時是空的 tuple。"""
    return tuple(int(n) for n in re.findall(r"\d+", text.split("-")[0])[:3])


def _headers() -> dict[str, str]:
    return {"User-Agent": f"Tablade/{__version__}"}


def _certificate_error(err: BaseException) -> bool:
    """HTTPS 的憑證驗不過（urllib 包成 URLError，原因放在 reason）。"""
    reason = getattr(err, "reason", err)
    return isinstance(reason, ssl.SSLCertVerificationError) or isinstance(
        err, ssl.SSLCertVerificationError
    )


def check_latest(timeout: float = TIMEOUT) -> dict[str, Any]:
    """回傳目前版本、最新版本、是否有新版、下載頁網址；連不上時 ``error`` 說明原因。

    ``error_code`` 是原因的代碼（disabled、no_release、http、offline、tls、bad_version；HTTP 錯誤另有
    ``http_status``）：前端依代碼自己產生文字，切換語言時才會跟著換（``error`` 是查的當下的語言）。
    有新版時 ``install`` 說明能不能在程式裡直接更新（``selfupdate.install_status``）。
    """
    result: dict[str, Any] = {
        "current": __version__,
        "latest": None,
        "newer": False,
        "url": RELEASES_URL,
        "error": None,
        "error_code": None,
        "http_status": None,
        "install": None,
    }
    if os.environ.get("TABLADE_NO_UPDATE_CHECK"):
        result["error"] = t("已關閉檢查更新", "Update check is turned off")
        result["error_code"] = "disabled"
        return result
    request = urllib.request.Request(
        LATEST_API, headers={"Accept": "application/vnd.github+json", **_headers()}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as err:
        result["error_code"] = "no_release" if err.code == 404 else "http"
        result["http_status"] = err.code
        result["error"] = (
            t("GitHub 上還沒有發布版本", "No releases on GitHub yet")
            if err.code == 404
            else t(
                f"無法檢查更新（HTTP {err.code}）", f"Couldn't check for updates (HTTP {err.code})"
            )
        )
        return result
    except (OSError, ValueError) as err:  # 連不上、逾時、憑證驗不過、回應不是 JSON
        if _certificate_error(err):
            result["error_code"] = "tls"
            result["error"] = t(
                "無法驗證 GitHub 的憑證，略過檢查更新",
                "Couldn't verify GitHub's certificate; skipped the update check",
            )
        else:
            result["error_code"] = "offline"
            result["error"] = t(
                "無法連線到 GitHub，略過檢查更新",
                "Couldn't connect to GitHub; skipped the update check",
            )
        return result
    latest = str(data.get("tag_name") or "").removeprefix("v") if isinstance(data, dict) else ""
    if not parse_version(latest):
        result["error"] = t("無法判讀最新版號", "Couldn't read the latest version number")
        result["error_code"] = "bad_version"
        return result
    result["latest"] = latest
    result["newer"] = parse_version(latest) > parse_version(__version__)
    result["url"] = str(data.get("html_url") or RELEASES_URL)
    if result["newer"]:
        from .selfupdate import install_status

        result["install"] = install_status()
    return result


def asset_url(version: str, name: str) -> str:
    """Release 檔的下載網址：只接受 x.y.z 的版號與固定的檔名（不能指定別的網址）。"""
    if not VERSION_RE.fullmatch(version) or not re.fullmatch(r"[a-z0-9.-]+", name):
        raise UpdateError(t(f"不正確的版號 {version!r}", f"Invalid version {version!r}"))
    return f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"


def _download(
    url: str, limit: int, progress: Callable[[int, int | None], None] | None = None
) -> bytes:
    request = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            total = response.headers.get("Content-Length")
            size = int(total) if total and total.isdigit() else None
            if size is not None and size > limit:
                raise UpdateError(t("下載的檔案太大", "The download is too large"))
            parts: list[bytes] = []
            done = 0
            while chunk := response.read(CHUNK):
                done += len(chunk)
                if done > limit:
                    raise UpdateError(t("下載的檔案太大", "The download is too large"))
                parts.append(chunk)
                if progress:
                    progress(done, size)
            return b"".join(parts)
    except urllib.error.HTTPError as err:
        raise UpdateError(
            t(f"下載失敗（HTTP {err.code}）：{url}", f"Download failed (HTTP {err.code}): {url}")
        ) from err
    except OSError as err:
        if _certificate_error(err):
            raise UpdateError(
                t("無法驗證 GitHub 的憑證，沒有下載", "Couldn't verify GitHub's certificate")
            ) from err
        raise UpdateError(
            t(f"無法下載更新：{err}", f"Couldn't download the update: {err}")
        ) from err


def download(
    version: str, asset: str, progress: Callable[[int, int | None], None] | None = None
) -> tuple[bytes, str]:
    """下載某一版的 Release 檔與它的簽章（.minisig），回傳（檔案內容, 簽章全文）。不驗章。"""
    if os.environ.get("TABLADE_NO_UPDATE_CHECK"):
        raise UpdateError(t("已關閉檢查更新", "Update check is turned off"))
    if parse_version(version) <= parse_version(__version__):
        raise UpdateError(
            t(f"v{version} 不比目前的版本新", f"v{version} is not newer than this version")
        )
    signature = _download(asset_url(version, asset + ".minisig"), MAX_SIGNATURE_BYTES)
    data = _download(asset_url(version, asset), MAX_PACKAGE_BYTES, progress)
    try:
        return data, signature.decode("utf-8")
    except UnicodeDecodeError as err:
        raise UpdateError(t("簽章檔格式不對", "The signature file is malformed")) from err
