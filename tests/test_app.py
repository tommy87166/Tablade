"""不開視窗的部分：前端產物、前端原始碼規則、JS bridge 的回傳格式。"""

import os
import re
from pathlib import Path

import polars as pl
import pytest

from tablade import app
from tablade.api import Api, _preview

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
API_METHODS = {"ready", "selftest", "smoke_done", "open_csv", "export_parquet"}


@pytest.fixture(scope="module")
def built_html() -> str:
    if not app.frontend_html_path().is_file():
        message = "前端尚未 build：npm --prefix frontend ci && npm --prefix frontend run build"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return app.load_html()


def test_built_html_is_a_single_self_contained_file(built_html):
    assert len(built_html.encode("utf-8")) < app.MAX_HTML_BYTES
    assert not re.search(r"<script[^>]+\bsrc=", built_html)
    assert not re.search(r"<link[^>]+\bhref=", built_html)
    assert "pywebviewready" in built_html


def test_csp_blocks_all_network_access(built_html):
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', built_html)
    assert match, "找不到 CSP"
    csp = match.group(1)
    assert csp.startswith("default-src 'none'")
    for directive in ("connect-src", "frame-src", "http:", "https:", "*"):
        assert directive not in csp


FORBIDDEN_IN_FRONTEND = (
    # 資料不得當成 HTML 寫入（這個頁面可以呼叫 Python）
    "dangerouslySetInnerHTML",
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    # 不得有任何網路連線（CSP 也會擋，這裡是第二道）
    "fetch(",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "sendBeacon",
    # 不得動態執行程式
    "eval(",
    "new Function",
)


def test_frontend_sources_follow_policy():
    problems = []
    for path in sorted((FRONTEND / "src").rglob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        problems += [
            f"{path.relative_to(FRONTEND)}: {w}" for w in FORBIDDEN_IN_FRONTEND if w in text
        ]
    assert problems == []


def test_bridge_types_match_python_api():
    source = (FRONTEND / "src" / "bridge.ts").read_text(encoding="utf-8")
    block = source.split("export interface TabladeApi {", 1)[1].split("\n}\n", 1)[0]
    assert set(re.findall(r"^\s*(\w+)\(", block, re.M)) == API_METHODS


def test_dev_url_only_accepts_localhost(monkeypatch):
    monkeypatch.delenv(app.DEV_URL_ENV, raising=False)
    assert app.dev_url() is None
    monkeypatch.setenv(app.DEV_URL_ENV, "http://localhost:5173")
    assert app.dev_url() == "http://localhost:5173"
    monkeypatch.setenv(app.DEV_URL_ENV, "https://example.com")
    with pytest.raises(ValueError):
        app.dev_url()


def test_bridge_wraps_errors():
    api = Api()
    res = api.export_parquet()
    assert res == {"ok": False, "error": "RuntimeError: 請先開啟 CSV"}


def test_ready_reports_versions():
    res = Api(smoke=True).ready()
    assert res["ok"] and res["smoke"] is True
    assert res["polars"].startswith("2.")


def test_preview_shape():
    frame = pl.DataFrame({"a": [str(i) for i in range(150)], "b": [None] * 150})
    p = _preview(frame)
    assert p["rows"] == 150
    assert p["columns"] == ["a", "b"]
    assert len(p["preview"]) == 100
    assert p["preview"][0] == ["0", None]


def test_bridge_does_not_expose_private_state():
    public = {name for name in dir(Api()) if not name.startswith("_")}
    assert public == API_METHODS
