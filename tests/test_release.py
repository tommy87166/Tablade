"""發版：tag 與版號一致的檢查、各平台發布哪些產物。"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tablade import __version__

ROOT = Path(__file__).resolve().parents[1]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v0.2.0", None),
        ("v0.2.1", "tag 是 v0.2.1，但 src/tablade/__init__.py 的 __version__ 是 0.2.0"),
        ("0.2.0", "格式要是 v主版號.次版號.修訂號"),
        ("v0.2", "格式要是"),
        ("v0.2.0-beta", "格式要是"),
    ],
)
def test_release_tag_must_match_version(tag, message):
    error = script("check_release").check(tag, "0.2.0")
    if message is None:
        assert error is None
    else:
        assert message in error


def test_release_check_cli_uses_the_real_version():
    ok = subprocess.run(
        [sys.executable, "scripts/check_release.py", f"v{__version__}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    bad = subprocess.run(
        [sys.executable, "scripts/check_release.py", "v99.0.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1 and "請先把 __version__ 改成 99.0.0" in bad.stderr


@pytest.mark.parametrize(
    ("win", "mac", "modes"),
    [(True, False, ["onefile"]), (False, True, ["onedir"]), (False, False, ["onefile", "onedir"])],
)
def test_release_modes_per_platform(monkeypatch, win, mac, modes):
    """Windows 只發 onefile；macOS 只發 Tablade.app（onedir 包成 App）。"""
    build = script("build")
    monkeypatch.setattr(build, "IS_WIN", win)
    monkeypatch.setattr(build, "IS_MAC", mac)
    assert build.release_modes() == modes
