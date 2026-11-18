"""pywebview 視窗進入點。

前端（frontend/，React + Vite）build 成單一 HTML 檔，這裡以字串載入（``create_window(html=...)``），
所以 pywebview 不會啟動本機 HTTP server，也不開任何 port（DESIGN.md §3、§16）。

開發時可以設定 ``TABLADE_DEV_URL=http://localhost:5173`` 改載入 Vite dev server（熱更新），
只接受 localhost 網址。
"""

from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path

from .api import Api
from .ingest.sources import clear_staging, sweep_stale_staging

SMOKE_TIMEOUT_SECONDS = 60
DEV_URL_ENV = "TABLADE_DEV_URL"
DEV_URL = re.compile(r"http://(?:localhost|127\.0\.0\.1):\d{1,5}(?:[/?#][^\\]*)?")
"""開發用的網址：主機一定是 localhost（只是比對字串，不連線；src/ 不能 import urllib）。"""
MAX_HTML_BYTES = 2 * 1024 * 1024
"""WebView2 的 NavigateToString 上限是 2 MB，前端產物必須小於這個大小。"""


def resource_dir() -> Path:
    """打包後是 PyInstaller 的解壓目錄，開發時是 repo 根目錄。"""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else Path(__file__).resolve().parents[2]


def frontend_html_path() -> Path:
    return resource_dir() / "frontend" / "dist" / "index.html"


def load_html() -> str:
    path = frontend_html_path()
    if not path.is_file():
        raise FileNotFoundError(f"找不到前端產物 {path}：請先執行 npm --prefix frontend run build")
    return path.read_text(encoding="utf-8")


def dev_url() -> str | None:
    """``TABLADE_DEV_URL``：只接受 ``http://localhost:埠`` 或 ``http://127.0.0.1:埠``（後面可以接路徑）。

    埠號後面只能是結尾或 ``/``、``?``、``#``：``http://localhost:5173@example.com/`` 開頭雖然一樣，
    實際的主機是 example.com（``localhost:5173`` 是帳號密碼的部分），不接受。
    """
    url = os.environ.get(DEV_URL_ENV)
    if not url:
        return None
    if not DEV_URL.fullmatch(url):
        raise ValueError(f"{DEV_URL_ENV} 只接受 localhost 網址，收到 {url!r}")
    return url


def run(smoke: bool = False) -> int:
    """開啟視窗，關閉後回傳結束代碼。

    ``smoke=True`` 時，前端載入後自動跑一次自我檢測、回報結果並關閉視窗；
    逾時沒有回報就強制關閉，結束代碼為 1。用來在 CI 與目標電腦上確認視窗與 JS bridge 正常。
    """
    import webview

    url = dev_url()
    content = {"url": url} if url else {"html": load_html()}

    api = Api(smoke=smoke)
    window = webview.create_window(
        "Tablade",
        js_api=api,
        width=1100,
        height=720,
        min_size=(800, 500),
        **content,
    )
    api._attach(window)
    # 暫存的轉碼副本：視窗一關就刪（macOS 關視窗時不一定會跑到 Python 的結束程序）；
    # 上次當掉或被強制結束時留下的，現在先清掉
    window.events.closed += clear_staging
    sweep_stale_staging()

    if smoke:

        def watchdog() -> None:
            if not api._done.wait(SMOKE_TIMEOUT_SECONDS):
                window.destroy()

        threading.Thread(target=watchdog, daemon=True).start()

    webview.start(private_mode=True)
    clear_staging()
    return api._exit_code
