"""不開視窗的部分：HTML 組裝與 JS bridge 的回傳格式。"""

import polars as pl

from tablade import app
from tablade.api import Api, _preview


def test_load_html_inlines_assets():
    html = app.load_html()
    assert 'href="style.css"' not in html
    assert 'src="app.js"' not in html
    assert "pywebviewready" in html
    assert "--accent" in html
    assert "Content-Security-Policy" in html


def test_csp_blocks_all_network_access():
    html = app.load_html()
    csp = html.split('content="', 2)[2].split('"', 1)[0]
    assert csp.startswith("default-src 'none'")
    for directive in ("connect-src", "frame-src", "http:", "https:", "*"):
        assert directive not in csp


def test_frontend_never_uses_innerhtml():
    js = (app.frontend_dir() / "app.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    assert "innerHTML" not in code
    assert "insertAdjacentHTML" not in code


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
    assert public == {"ready", "selftest", "smoke_done", "open_csv", "export_parquet"}
