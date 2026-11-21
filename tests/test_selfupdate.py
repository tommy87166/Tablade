"""自動更新的本機部分（selfupdate.py）：驗章、解壓、換檔、清掉舊版。不連網、不啟動程式。"""

from __future__ import annotations

import io
import os
import plistlib
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from tablade import selfupdate, update
from tablade.update import UpdateError

sys.path.insert(0, str(Path(__file__).parent))
from minisign_signer import SigningKey

native = pytest.importorskip("tablade_native", reason="沒有 Rust 模組（uv sync --group native）")

VERSION = "99.0.0"
ASSET = "tablade-macos-arm64-app.zip"
posix_only = pytest.mark.skipif(os.name == "nt", reason="符號連結與執行權限只在 macOS／Linux 測")


def make_zip(entries: list[tuple[str, bytes | None, int]]) -> bytes:
    """(名稱, 內容, mode)：內容是 None 時是資料夾；mode 有 S_IFLNK 時內容是連結的目標。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            zf.writestr(info, data or b"")
    return buf.getvalue()


def app_zip(version: str = VERSION) -> bytes:
    plist = plistlib.dumps({"CFBundleShortVersionString": version})
    return make_zip(
        [
            ("Tablade.app/", None, stat.S_IFDIR | 0o755),
            ("Tablade.app/Contents/", None, stat.S_IFDIR | 0o755),
            ("Tablade.app/Contents/Info.plist", plist, stat.S_IFREG | 0o644),
            ("Tablade.app/Contents/MacOS/tablade", b"#!new", stat.S_IFREG | 0o755),
            ("Tablade.app/Contents/Frameworks/lib.dylib", b"lib", stat.S_IFREG | 0o644),
            (
                "Tablade.app/Contents/Resources/lib.dylib",
                b"../Frameworks/lib.dylib",
                stat.S_IFLNK | 0o755,
            ),
        ]
    )


def exe_zip(content: bytes = b"MZnew") -> bytes:
    return make_zip([("tablade.exe", content, stat.S_IFREG | 0o755)])


def fake_app(folder: Path, marker: bytes = b"#!old") -> Path:
    app = folder / "Tablade.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "tablade").write_bytes(marker)
    return app


# ── 驗章 ─────────────────────────────────────────────────────


def test_verify_accepts_the_right_file_and_version():
    key, data = SigningKey(), b"package"
    sig = key.sign(data, selfupdate.trusted_comment(VERSION, ASSET))
    selfupdate.verify(data, sig, VERSION, ASSET, key.public_key)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"data": b"tampered"}, "簽章不符"),
        (
            {"comment": selfupdate.trusted_comment("98.0.0", ASSET)},
            "不是 v99.0.0",
        ),  # 舊版的檔冒充新版
        (
            {"comment": selfupdate.trusted_comment(VERSION, "tablade-windows-x64-onefile.zip")},
            "不是",
        ),
        ({"other_key": True}, "簽章不符"),
    ],
)
def test_verify_rejects(change, message):
    key, data = SigningKey(), b"package"
    sig = key.sign(data, change.get("comment", selfupdate.trusted_comment(VERSION, ASSET)))
    public = SigningKey().public_key if change.get("other_key") else key.public_key
    with pytest.raises(UpdateError, match=message):
        selfupdate.verify(change.get("data", data), sig, VERSION, ASSET, public)


def test_verify_without_a_public_key_refuses(monkeypatch):
    monkeypatch.setattr(selfupdate, "PUBLIC_KEY", "")
    with pytest.raises(UpdateError, match="不能自動更新"):
        selfupdate.verify(b"x", "sig", VERSION, ASSET)


# ── 解壓 ─────────────────────────────────────────────────────


@posix_only
def test_extract_restores_links_and_modes(tmp_path):
    app = selfupdate.extract(app_zip(), tmp_path, "Tablade.app")
    exe = app / "Contents" / "MacOS" / "tablade"
    assert os.access(exe, os.X_OK)
    link = app / "Contents" / "Resources" / "lib.dylib"
    assert link.is_symlink() and os.readlink(link) == "../Frameworks/lib.dylib"
    assert link.read_bytes() == b"lib"


def test_extract_skips_apple_double_files(tmp_path):
    """ditto 把延伸屬性存成 ._ 開頭的檔：解出來會讓 App 的簽章對不上。"""
    data = make_zip(
        [
            ("Tablade.app/", None, stat.S_IFDIR | 0o755),
            ("Tablade.app/._Contents", b"xattr", stat.S_IFREG | 0o644),
            ("Tablade.app/Contents/._Info.plist", b"xattr", stat.S_IFREG | 0o644),
            ("Tablade.app/Contents/Info.plist", b"plist", stat.S_IFREG | 0o644),
            ("__MACOSX/Tablade.app/._x", b"xattr", stat.S_IFREG | 0o644),
        ]
    )
    app = selfupdate.extract(data, tmp_path, "Tablade.app")
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == [
        "Tablade.app",
        str(Path("Tablade.app/Contents")),
        str(Path("Tablade.app/Contents/Info.plist")),
    ]
    assert (app / "Contents" / "Info.plist").read_bytes() == b"plist"


@pytest.mark.parametrize(
    "name", ["../evil", "/abs/path", "Other.app/x", "Tablade.app/../../x", "Tablade.app\\..\\x"]
)
def test_extract_rejects_paths_outside_the_root(tmp_path, name):
    data = make_zip([(name, b"x", stat.S_IFREG | 0o644)])
    with pytest.raises(UpdateError, match="不該有的路徑"):
        selfupdate.extract(data, tmp_path, "Tablade.app")


@posix_only
@pytest.mark.parametrize("link", ["/etc/passwd", "../../../outside"])
def test_extract_rejects_links_pointing_outside(tmp_path, link):
    data = make_zip([("Tablade.app/Contents/x", link.encode(), stat.S_IFLNK | 0o755)])
    with pytest.raises(UpdateError, match="指到外面"):
        selfupdate.extract(data, tmp_path, "Tablade.app")


def test_extract_needs_the_root(tmp_path):
    data = make_zip([("tablade.exe", b"MZ", stat.S_IFREG | 0o755)])
    with pytest.raises(UpdateError, match=r"沒有 Tablade\.app"):
        selfupdate.extract(make_zip([]), tmp_path, "Tablade.app")
    assert selfupdate.extract(data, tmp_path, "tablade.exe").read_bytes() == b"MZ"


# ── 換檔 ─────────────────────────────────────────────────────


@posix_only
def test_install_mac_app_swaps_and_keeps_the_old_copy(tmp_path):
    app = fake_app(tmp_path)
    launched = selfupdate.install(app_zip(), VERSION, ASSET, app)
    assert launched == app
    assert (app / "Contents" / "MacOS" / "tablade").read_bytes() == b"#!new"
    old = tmp_path / ".Tablade.app.old"
    assert (old / "Contents" / "MacOS" / "tablade").read_bytes() == b"#!old"
    assert not list(tmp_path.glob(".tablade-update-*"))  # 暫存資料夾清掉了
    assert selfupdate.cleanup_previous(app) == [old] and not old.exists()


def test_install_windows_exe_swaps(tmp_path):
    exe = tmp_path / "tablade.exe"
    exe.write_bytes(b"MZold")
    assert selfupdate.install(exe_zip(), VERSION, "x.zip", exe) == exe
    assert exe.read_bytes() == b"MZnew"
    assert (tmp_path / "tablade.exe.old").read_bytes() == b"MZold"


def test_install_rejects_a_wrong_package_and_leaves_the_app_alone(tmp_path):
    exe = tmp_path / "tablade.exe"
    exe.write_bytes(b"MZold")
    with pytest.raises(UpdateError, match="不是 Windows 的執行檔"):
        selfupdate.install(exe_zip(b"#!/bin/sh"), VERSION, "x.zip", exe)
    assert exe.read_bytes() == b"MZold" and not (tmp_path / "tablade.exe.old").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tablade.exe"]


@posix_only
def test_install_mac_checks_the_version_inside(tmp_path):
    app = fake_app(tmp_path)
    with pytest.raises(UpdateError, match=r"版號是 98\.0\.0"):
        selfupdate.install(app_zip("98.0.0"), VERSION, ASSET, app)
    assert (app / "Contents" / "MacOS" / "tablade").read_bytes() == b"#!old"


def test_swap_puts_the_old_one_back_when_the_new_one_cannot_move(tmp_path, monkeypatch):
    exe = tmp_path / "tablade.exe"
    exe.write_bytes(b"MZold")
    new = tmp_path / "new.exe"
    new.write_bytes(b"MZnew")
    real = Path.rename

    def rename(self, target):
        if self == new:
            raise OSError("disk full")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="disk full"):
        selfupdate.swap(exe, new)
    assert exe.read_bytes() == b"MZold"


def test_swap_uses_another_name_when_the_old_copy_is_locked(tmp_path, monkeypatch):
    exe = tmp_path / "tablade.exe"
    exe.write_bytes(b"MZ2")
    (tmp_path / "tablade.exe.old").write_bytes(b"MZ1")
    new = tmp_path / "new.exe"
    new.write_bytes(b"MZ3")
    monkeypatch.setattr(selfupdate, "_remove", lambda path: False)  # 上一個舊版還在執行，刪不掉
    old = selfupdate.swap(exe, new)
    assert old.name == "tablade.exe.2.old" and old.read_bytes() == b"MZ2"
    assert exe.read_bytes() == b"MZ3"


# ── 能不能更新 ───────────────────────────────────────────────


def test_source_checkout_cannot_update():
    with pytest.raises(UpdateError, match="原始碼"):
        selfupdate.install_target(frozen=False)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 的 App 路徑")
def test_mac_app_location_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(selfupdate, "platform_asset", lambda: ASSET)
    app = fake_app(tmp_path)
    exe = str(app / "Contents" / "MacOS" / "tablade")
    assert selfupdate.install_target(exe, frozen=True) == app.resolve()
    with pytest.raises(UpdateError, match=r"Tablade\.app"):
        selfupdate.install_target(str(tmp_path / "tablade"), frozen=True)
    moved = tmp_path / "AppTranslocation" / "X"
    moved.mkdir(parents=True)
    translocated = fake_app(moved)
    with pytest.raises(UpdateError, match="應用程式"):
        selfupdate.install_target(str(translocated / "Contents" / "MacOS" / "tablade"), frozen=True)


def test_install_status_without_key(monkeypatch):
    monkeypatch.setattr(selfupdate, "PUBLIC_KEY", "")
    assert selfupdate.install_status() == {"ok": False, "reason": "這個版本不能自動更新"}


# ── 整個流程（下載換成假的） ───────────────────────────────────


def test_update_to_downloads_verifies_and_installs(tmp_path, monkeypatch):
    key = SigningKey()
    exe = tmp_path / "tablade.exe"
    exe.write_bytes(b"MZold")
    asset = "tablade-windows-x64-onefile.zip"
    package = exe_zip()
    monkeypatch.setattr(selfupdate, "platform_asset", lambda: asset)

    def fake_download(version, name, progress):
        assert (version, name) == (VERSION, asset)
        progress(len(package), len(package))
        return package, key.sign(package, selfupdate.trusted_comment(version, name))

    monkeypatch.setattr(selfupdate, "download", fake_download)
    phases = []
    path = selfupdate.update_to(VERSION, lambda p: phases.append(p["phase"]), exe, key.public_key)
    assert path == exe and exe.read_bytes() == b"MZnew"
    assert phases == ["download", "download", "verify", "extract", "install", "restart"]


def test_update_to_stops_on_a_bad_signature(tmp_path, monkeypatch):
    key = SigningKey()
    exe = tmp_path / "tablade.exe"
    exe.write_bytes(b"MZold")
    monkeypatch.setattr(selfupdate, "platform_asset", lambda: "x.zip")
    monkeypatch.setattr(
        selfupdate,
        "download",
        lambda v, n, p: (exe_zip(), key.sign(b"other", "tablade 99.0.0 x.zip")),
    )
    with pytest.raises(UpdateError, match="簽章不符"):
        selfupdate.update_to(VERSION, None, exe, key.public_key)
    assert exe.read_bytes() == b"MZold"


def test_release_asset_names_match_the_build_script():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build

    asset = selfupdate.platform_asset()
    if asset is not None:
        label = "app" if sys.platform == "darwin" else "onefile"
        assert asset == f"tablade-{build.platform_tag()}-{label}.zip"
    assert update.asset_url(VERSION, ASSET).endswith(f"/releases/download/v{VERSION}/{ASSET}")
