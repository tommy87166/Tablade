"""啟動時檢查 GitHub 上的最新版號：測試時不連網，用假的回應。"""

import io
import json
import urllib.error

import pytest

from tablade import __version__, update


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(monkeypatch, payload=None, error=None):
    def urlopen(request, timeout):
        assert request.full_url == update.LATEST_API and timeout == update.TIMEOUT
        assert request.headers["User-agent"] == f"Tablade/{__version__}"  # 不送出使用者的資料
        if error:
            raise error
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)


def test_parse_version():
    assert update.parse_version("v0.2.0") == (0, 2, 0)
    assert update.parse_version("1.10.3-beta") == (1, 10, 3)
    assert update.parse_version("abc") == ()


def test_newer_release(monkeypatch):
    url = "https://github.com/x/y/releases/tag/v99.0.0"
    answer(monkeypatch, {"tag_name": "v99.0.0", "html_url": url})
    result = update.check_latest()
    assert result == {
        "current": __version__,
        "latest": "99.0.0",
        "newer": True,
        "url": url,
        "error": None,
    }


def test_same_version(monkeypatch):
    answer(monkeypatch, {"tag_name": f"v{__version__}"})
    result = update.check_latest()
    assert result["newer"] is False and result["url"] == update.RELEASES_URL


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (urllib.error.HTTPError(update.LATEST_API, 404, "Not Found", {}, None), "還沒有發布版本"),
        (urllib.error.HTTPError(update.LATEST_API, 403, "Forbidden", {}, None), "HTTP 403"),
        (urllib.error.URLError("offline"), "無法連線"),
        (TimeoutError(), "無法連線"),
    ],
)
def test_failures_are_reported_not_raised(monkeypatch, error, message):
    answer(monkeypatch, error=error)
    result = update.check_latest()
    assert message in result["error"] and result["latest"] is None and not result["newer"]


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("TABLADE_NO_UPDATE_CHECK", "1")
    answer(monkeypatch, error=AssertionError("不應該連線"))
    assert update.check_latest()["error"] == "已關閉檢查更新"
