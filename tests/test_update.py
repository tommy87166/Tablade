"""檢查與下載 GitHub 上的最新版：測試時不連網，用假的回應。"""

import io
import json
import ssl
import urllib.error

import pytest

from tablade import __version__, update


@pytest.fixture(autouse=True)
def update_check_on(monkeypatch):
    """F-076：開發用的電腦可能在系統裡設了 TABLADE_NO_UPDATE_CHECK；測試一律從「有檢查」開始。"""
    monkeypatch.delenv("TABLADE_NO_UPDATE_CHECK", raising=False)


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
        "error_code": None,
        "http_status": None,
        "install": result["install"],
    }
    assert set(result["install"]) == {"ok", "code", "folder", "reason"}


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
        (
            urllib.error.URLError(ssl.SSLCertVerificationError(1, "unable to get local issuer")),
            "憑證",
        ),
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


def test_certificate_errors_have_their_own_code(monkeypatch):
    """打包的 App 找不到系統的憑證時，畫面要說是憑證的問題，不是「連不上」。"""
    answer(monkeypatch, error=urllib.error.URLError(ssl.SSLCertVerificationError(1, "x")))
    assert update.check_latest()["error_code"] == "tls"


# ── 下載 ─────────────────────────────────────────────────────


class FakeDownload(FakeResponse):
    def __init__(self, data, length=True):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))} if length else {}


def serve(monkeypatch, files):
    seen = []

    def urlopen(request, timeout):
        seen.append(request.full_url)
        assert timeout == update.DOWNLOAD_TIMEOUT
        assert set(request.headers) == {"User-agent"}  # 只送 User-Agent
        if request.full_url not in files:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return FakeDownload(files[request.full_url])

    monkeypatch.setattr(update.urllib.request, "urlopen", urlopen)
    return seen


def test_download_uses_the_fixed_release_urls(monkeypatch):
    base = f"https://github.com/{update.REPOSITORY}/releases/download/v99.0.0/"
    seen = serve(monkeypatch, {base + "a.zip": b"zip", base + "a.zip.minisig": b"sig"})
    done = []
    assert update.download("99.0.0", "a.zip", lambda d, t: done.append((d, t))) == (b"zip", "sig")
    assert seen == [base + "a.zip.minisig", base + "a.zip"]
    assert done == [(3, 3)]


@pytest.mark.parametrize("version", ["0.0.1", __version__, "1.2", "99.0.0/../../x", "99.0.0?x"])
def test_download_refuses_old_or_odd_versions(monkeypatch, version):
    serve(monkeypatch, {})
    with pytest.raises(update.UpdateError):
        update.download(version, "a.zip")


def test_download_refuses_odd_asset_names():
    with pytest.raises(update.UpdateError):
        update.asset_url("99.0.0", "../x.zip")
    with pytest.raises(update.UpdateError):
        update.asset_url("99.0.0", "https://example.org/x.zip")


def test_download_stops_when_too_large(monkeypatch):
    base = f"https://github.com/{update.REPOSITORY}/releases/download/v99.0.0/"
    serve(monkeypatch, {base + "a.zip": b"z" * 10, base + "a.zip.minisig": b"s" * 5000})
    with pytest.raises(update.UpdateError, match="太大"):
        update.download("99.0.0", "a.zip")


def test_download_reports_http_errors(monkeypatch):
    serve(monkeypatch, {})
    with pytest.raises(update.UpdateError, match="HTTP 404"):
        update.download("99.0.0", "a.zip")


def test_download_respects_the_off_switch(monkeypatch):
    monkeypatch.setenv("TABLADE_NO_UPDATE_CHECK", "1")
    serve(monkeypatch, {})
    with pytest.raises(update.UpdateError, match="已關閉"):
        update.download("99.0.0", "a.zip")
