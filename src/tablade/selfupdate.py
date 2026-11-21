"""自動更新的本機部分：驗章、解壓、換檔、清掉舊版（DESIGN.md §11）。

連網（下載）在 ``update.py``，重新啟動在 ``desktop.py``，這裡不連網、不呼叫外部程式。

換檔不需要另外的更新程式：兩個平台都允許把**執行中**的程式改名，所以

1. 新版解壓到程式旁邊的暫存資料夾（同一顆磁碟，改名才是瞬間完成）
2. 舊的改名讓開（``tablade.exe`` → ``tablade.exe.old``；``Tablade.app`` → ``.Tablade.app.old``）
3. 新的改名成原本的名字，失敗就把舊的換回來
4. 視窗關掉後啟動新版；新版啟動時刪掉舊版（``cleanup_previous``，刪不掉就下次再刪）

只有發布的 App 能更新：Windows 的 onefile ``tablade.exe``、macOS 的 ``Tablade.app``。
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import stat
import sys
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from .i18n import t
from .update import UpdateError, download

PUBLIC_KEY = "RWR1Fyq+kJosoLO+zM9r1lQOMd1eo9ykuUxoByMFOfoJuUGRHKbc3BV8"
"""發版用的 minisign 公鑰（金鑰 ID A02C9A90BE2A1775，minisign.pub 的第二行）。私鑰只在 GitHub Actions 的 secret
（TABLADE_UPDATE_SIGNING_KEY），CI 簽章後先用這把公鑰驗一次才發布（scripts/sign_release.py）。"""

STAGING_PREFIX = ".tablade-update-"
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
WINDOWS_EXE = "tablade.exe"
MAC_APP = "Tablade.app"
MAC_EXE = "tablade"


def platform_asset() -> str | None:
    """這個平台的 Release 檔名（和 scripts/build.py 的 package() 一致）；沒有發布的平台是 None。"""
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "tablade-windows-x64-onefile.zip"
    if sys.platform == "darwin" and machine == "arm64":
        return "tablade-macos-arm64-app.zip"
    return None


def trusted_comment(version: str, asset: str) -> str:
    """簽章裡受保護的註解：版號與檔名都簽進去，舊版的檔與簽章拿來冒充新版會被擋下。"""
    return f"tablade {version} {asset}"


def install_target(executable: str | None = None, frozen: bool | None = None) -> Path:
    """要換掉的東西（Windows：tablade.exe；macOS：Tablade.app）；不能自動更新時丟 UpdateError。

    UpdateError 的 ``code``：source、platform、onedir、not_app、translocated、no_write
    （前端 UpdateNotice 依代碼寫原因與該怎麼做）。
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        raise UpdateError(
            t(
                "從原始碼執行，不會自動更新。用 git pull 取得新版",
                "Running from source, so Tablade doesn't update itself. Use git pull to get the new version",
            ),
            "source",
        )
    if platform_asset() is None:
        raise UpdateError(
            t(
                "這個平台沒有發布的版本，不能自動更新",
                "There is no release for this platform, so Tablade can't update itself",
            ),
            "platform",
        )
    exe = Path(executable or sys.executable).resolve()
    if sys.platform == "win32":
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle and Path(bundle).resolve().parent == exe.parent:
            raise UpdateError(
                t(
                    "資料夾版（onedir）不能自動更新",
                    "The folder (onedir) build can't update itself",
                ),
                "onedir",
            )
        target = exe
    else:
        target = exe.parents[2]
        if target.suffix != ".app" or exe.parent != target / "Contents" / "MacOS":
            raise UpdateError(
                t(
                    "不是從 Tablade.app 開啟的，不能自動更新",
                    "Tablade wasn't opened from Tablade.app, so it can't update itself",
                ),
                "not_app",
            )
        if "/AppTranslocation/" in str(target):
            raise UpdateError(
                t(
                    "macOS 從暫時的位置開啟了 Tablade，不能自動更新。把 Tablade.app 移到「應用程式」再開一次，就能自動更新",
                    "macOS opened Tablade from a temporary location, so it can't update itself. "
                    "Move Tablade.app to Applications and open it again to update automatically",
                ),
                "translocated",
            )
    if not os.access(target.parent, os.W_OK):
        raise UpdateError(
            t(
                f"沒有權限寫入 {target.parent}，不能自動更新",
                f"No permission to write to {target.parent}, so Tablade can't update itself",
            ),
            "no_write",
            str(target.parent),
        )
    return target


def install_status() -> dict[str, Any]:
    """能不能在程式裡直接更新：``{"ok": True, …}`` 或 ``{"ok": False, "code": "…", "reason": "…"}``。

    ``code`` 是 install_target 的代碼，或沒有公鑰時的 ``no_key``；``folder`` 是沒有權限寫入的資料夾。
    ``reason`` 是查的當下的語言（前端有代碼時改用自己的文字）。
    """
    if not PUBLIC_KEY:
        return {
            "ok": False,
            "code": "no_key",
            "folder": None,
            "reason": t("這個版本不能自動更新", "This version can't update itself"),
        }
    try:
        install_target()
    except UpdateError as err:
        return {"ok": False, "code": err.code, "folder": err.folder, "reason": str(err)}
    return {"ok": True, "code": None, "folder": None, "reason": None}


def verify(
    data: bytes, signature: str, version: str, asset: str, public_key: str | None = None
) -> None:
    """用 Rust 模組驗 minisign 簽章，並核對簽章裡寫的版號與檔名；不符合時丟 UpdateError。"""
    key = PUBLIC_KEY if public_key is None else public_key
    if not key:
        raise UpdateError(t("這個版本不能自動更新", "This version can't update itself"))
    try:
        import tablade_native
    except ImportError as err:
        raise UpdateError(
            t("這個版本沒有驗章的模組", "This build has no signature module")
        ) from err
    try:
        comment = tablade_native.verify_minisign(key, signature, data)
    except ValueError as err:
        raise UpdateError(
            t(f"簽章不符，沒有安裝：{err}", f"The signature doesn't match; not installed: {err}")
        ) from err
    if comment != trusted_comment(version, asset):
        raise UpdateError(
            t(
                f"簽章寫的是「{comment}」，不是 v{version} 的 {asset}，沒有安裝",
                f'The signature is for "{comment}", not {asset} v{version}; not installed',
            )
        )


def _member_path(root: str, name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != root
    ):
        raise UpdateError(
            t(f"更新檔裡有不該有的路徑：{name}", f"Unexpected path in the update: {name}")
        )
    return path


def _apple_double(name: str) -> bool:
    """macOS 的 ditto 把延伸屬性存成 ``._檔名``（或放在 ``__MACOSX/``）：不是 App 的檔，解出來
    反而會讓 App 的簽章對不上，略過。"""
    parts = PurePosixPath(name).parts
    return bool(parts) and (parts[0] == "__MACOSX" or parts[-1].startswith("._"))


def extract(data: bytes, dest: Path, root: str) -> Path:
    """把 zip 解到 dest（只接受 root 底下的項目），還原符號連結與執行權限，回傳 dest / root。"""
    total = 0
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for info in zf.infolist():
            if _apple_double(info.filename):
                continue
            path = _member_path(root, info.filename)
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise UpdateError(t("更新檔解開後太大", "The update is too large when unpacked"))
            out = dest.joinpath(*path.parts)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                link = zf.read(info).decode("utf-8")
                resolved = PurePosixPath(os.path.normpath(str(path.parent / link)))
                if PurePosixPath(link).is_absolute() or resolved.parts[:1] != (root,):
                    raise UpdateError(
                        t(
                            f"更新檔裡的連結指到外面：{info.filename}",
                            f"A link points outside: {info.filename}",
                        )
                    )
                out.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(link, out)
            elif info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if os.name != "nt" and stat.S_IMODE(mode):
                    out.chmod(stat.S_IMODE(mode) & 0o755)
    top = dest / root
    if not top.exists():
        raise UpdateError(t(f"更新檔裡沒有 {root}", f"{root} is missing from the update"))
    return top


def _check_windows(exe: Path) -> None:
    with exe.open("rb") as f:
        if f.read(2) != b"MZ":
            raise UpdateError(
                t("更新檔不是 Windows 的執行檔", "The update is not a Windows executable")
            )


def _check_mac(app: Path, version: str) -> None:
    exe = app / "Contents" / "MacOS" / MAC_EXE
    if not exe.is_file() or not os.access(exe, os.X_OK):
        raise UpdateError(t("更新檔裡的 App 不完整", "The app in the update is incomplete"))
    with (app / "Contents" / "Info.plist").open("rb") as f:
        found = plistlib.load(f).get("CFBundleShortVersionString")
    if found != version:
        raise UpdateError(
            t(
                f"更新檔的版號是 {found}，不是 {version}",
                f"The update is version {found}, not {version}",
            )
        )


def old_copy(target: Path) -> Path:
    """換下來的舊版放哪裡（macOS 用點開頭的名字，Finder 不會顯示）。"""
    if target.suffix == ".app":
        return target.with_name(f".{target.name}.old")
    return target.with_name(f"{target.name}.old")


def _remove(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError:
        return False
    return True


def swap(target: Path, new: Path) -> Path:
    """舊的改名讓開、新的改名成原本的名字；失敗時換回來。回傳舊版現在的位置。"""
    old = old_copy(target)
    # Windows：上次換下來的還在執行（例如還沒關掉的另一個視窗）時刪不掉，換一個名字
    if (old.exists() or old.is_symlink()) and not _remove(old):
        old = next(
            p
            for n in range(2, 100)
            if not (p := target.with_name(f"{target.name}.{n}.old")).exists()
        )
    target.rename(old)
    try:
        new.rename(target)
    except OSError:
        old.rename(target)
        raise
    return old


def install(
    data: bytes,
    version: str,
    asset: str,
    target: Path,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """解壓、檢查、換檔（data 已經驗過章）。回傳要啟動的路徑（新的 tablade.exe 或 Tablade.app）。"""
    staging = target.parent / f"{STAGING_PREFIX}{version}"
    _remove(staging)
    staging.mkdir()
    try:
        if progress:
            progress("extract")
        if target.suffix == ".app":
            new = extract(data, staging, MAC_APP)
            _check_mac(new, version)
        else:
            new = extract(data, staging, WINDOWS_EXE)
            _check_windows(new)
        if progress:
            progress("install")
        swap(target, new)
    finally:
        _remove(staging)
    return target


def cleanup_previous(target: Path | None = None) -> list[Path]:
    """刪掉上次更新換下來的舊版與沒清完的暫存資料夾（啟動時呼叫；刪不掉的下次再刪）。"""
    if target is None:
        try:
            target = install_target()
        except UpdateError:
            return []
    removed = []
    candidates = [old_copy(target), *target.parent.glob(f"{target.name}.*.old")]
    candidates += [p for p in target.parent.glob(f"{STAGING_PREFIX}*") if p.is_dir()]
    for path in candidates:
        if (path.exists() or path.is_symlink()) and _remove(path):
            removed.append(path)
    return removed


def update_to(
    version: str,
    progress: Callable[[dict[str, Any]], None] | None = None,
    target: Path | None = None,
    public_key: str | None = None,
) -> Path:
    """下載、驗章、換檔，回傳要啟動的新版路徑（呼叫的人關掉視窗後用 desktop.relaunch 啟動）。"""
    asset = platform_asset()
    if asset is None:
        raise UpdateError(t("這個平台沒有發布的版本", "No release for this platform"))
    target = target or install_target()

    def report(phase: str, done: int = 0, total: int | None = None) -> None:
        if progress:
            progress({"phase": phase, "done": done, "total": total, "version": version})

    report("download")
    data, signature = download(version, asset, lambda done, total: report("download", done, total))
    report("verify")
    verify(data, signature, version, asset, public_key)
    path = install(data, version, asset, target, report)
    report("restart")
    return path
