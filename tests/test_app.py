"""不開視窗的部分：前端產物、前端原始碼規則、JS bridge 的回傳格式。"""

import os
import re
from pathlib import Path

import pytest

from tablade import app
from tablade.api import Api
from tablade.recipe.schema import RECIPE_VERSION

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
API_METHODS = {
    "ready",
    "selftest",
    "staging_info",
    "open_staging",
    "smoke_done",
    "check_update",
    "smoke_example",
    "state",
    "set_language",
    "rows",
    "column_info",
    "find_row",
    "new_recipe",
    "open_recipe",
    "save_recipe",
    "save_recipe_as",
    "add_files",
    "add_file_entry",
    "remove_file",
    "load_data",
    "set_input",
    "set_output",
    "set_stage_output",
    "set_meta",
    "add_stage",
    "rename_stage",
    "move_stage",
    "delete_stage",
    "select_stage",
    "full_run",
    "add_step",
    "update_step",
    "refresh_values",
    "move_step",
    "toggle_step",
    "delete_step",
    "select_step",
    "undo",
    "redo",
    "export_parquet",
    "error_report",
    "download_skill",
}


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


def test_preview_cells_have_no_hover_tooltip():
    """資料格與行號格沒有滑鼠停留的提示：完整的內容在「檢視」分頁（docs/cell-view-plan.md）。"""
    source = (FRONTEND / "src" / "components" / "PreviewGrid.tsx").read_text(encoding="utf-8")
    rows = source[source.index("{items.map((item) => {") :]
    rows = rows[: rows.index("</ContextMenuTrigger>")]
    assert "data-col={name}" in rows
    assert "title=" not in rows


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
    res = Api().update_step(5, {})
    assert res == {"ok": False, "error": "SessionError: 沒有第 6 步"}


def test_ready_reports_versions_and_catalog():
    res = Api(smoke=True).ready()
    assert res["ok"] and res["smoke"] is True
    assert res["polars"].startswith("2.") and res["recipe_version"] == RECIPE_VERSION
    assert next(c["type"] for c in res["catalog"]) == "remove_top_rows"


def test_smoke_example_runs_the_whole_flow_through_the_bridge():
    assert Api().smoke_example()["ok"] is False  # 只有 --smoke 模式可以用
    api = Api(smoke=True)
    res = api.smoke_example()
    assert res["ok"] and res["issues"] == []
    steps = res["state"]["steps"]
    assert [s["status"] for s in steps] == ["ok"] * 8 and steps[-1]["rows"] == 70
    page = api.rows(7, 0, 3)
    assert page["ok"] and page["total"] == 70 and len(page["rows"]) == 3
    res = api.select_step(-1)
    assert res["state"]["preview"]["rows"] == 94
    res = api.undo()  # select 不算修改
    assert res["state"]["can_undo"] is False
    report = api.error_report()
    assert "沒有發現問題" in report["text"]
    api._smoke_dir and __import__("shutil").rmtree(api._smoke_dir, ignore_errors=True)


def test_mutations_return_new_state():
    api = Api()
    res = api.add_step("remove_top_rows", {})
    assert res["ok"] and res["index"] == 0 and len(res["state"]["steps"]) == 1
    res = api.update_step(0, {"n": 3})
    assert res["state"]["steps"][0]["params"]["n"] == 3
    assert api.undo()["state"]["steps"][0]["params"]["n"] == 1
    assert api.delete_step(0)["state"]["steps"] == []


def test_bridge_does_not_expose_private_state():
    public = {name for name in dir(Api()) if not name.startswith("_")}
    assert public == API_METHODS


def test_staging_folder_info_and_open(monkeypatch):
    """自我診斷畫面的「暫存資料夾」：位置、目前大小；開啟時交給檔案總管／Finder（測試裡不真的開）。"""
    from tablade import api as api_module
    from tablade.ingest import sources

    opened = []
    monkeypatch.setattr(api_module, "open_folder", opened.append)
    folder = sources.staging_dir()
    sample = folder / "size-check.bin"
    sample.write_bytes(b"12345")
    try:
        info = Api().staging_info()
        assert info["ok"] and info["path"] == str(folder) and info["bytes"] >= 5
        res = Api().open_staging()
        assert res["ok"] and opened == [folder]
    finally:
        sample.unlink()
