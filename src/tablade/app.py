"""pywebview 視窗進入點。

HTML 以字串載入（``create_window(html=...)``），CSS 與 JS 內嵌進去，
所以 pywebview 不會啟動本機 HTTP server，也不開任何 port（DESIGN.md §3）。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from .api import Api

SMOKE_TIMEOUT_SECONDS = 60
_INLINE = (
    ('<link rel="stylesheet" href="style.css">', "style.css", "style"),
    ('<script src="app.js"></script>', "app.js", "script"),
)


def frontend_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "frontend"
    return Path(__file__).resolve().parents[2] / "frontend"


def load_html() -> str:
    base = frontend_dir()
    html = (base / "index.html").read_text(encoding="utf-8")
    for tag, filename, element in _INLINE:
        if tag not in html:
            raise RuntimeError(f"index.html 找不到 {tag}")
        content = (base / filename).read_text(encoding="utf-8")
        html = html.replace(tag, f"<{element}>\n{content}\n</{element}>")
    return html


def run(smoke: bool = False) -> int:
    """開啟視窗，關閉後回傳結束代碼。

    ``smoke=True`` 時，前端載入後自動跑一次自我檢測、回報結果並關閉視窗；
    逾時沒有回報就強制關閉，結束代碼為 1。用來在 CI 與目標電腦上確認視窗與 JS bridge 正常。
    """
    import webview

    api = Api(smoke=smoke)
    window = webview.create_window(
        "Tablade",
        html=load_html(),
        js_api=api,
        width=1100,
        height=720,
        min_size=(800, 500),
    )
    api._attach(window)

    if smoke:

        def watchdog() -> None:
            if not api._done.wait(SMOKE_TIMEOUT_SECONDS):
                window.destroy()

        threading.Thread(target=watchdog, daemon=True).start()

    webview.start(private_mode=True)
    return api._exit_code
