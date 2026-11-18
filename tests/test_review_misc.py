"""審查發現（docs/review-findings.md）裡零散的幾項：檢查更新的原因代碼、漏翻檢查的「・」。"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from tablade import update

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_i18n  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _urlopen(result):
    def fake(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return _Response(json.dumps(result).encode())

    return fake


@pytest.mark.parametrize(
    ("result", "code", "status"),
    [
        (urllib.error.HTTPError("u", 404, "x", {}, None), "no_release", 404),
        (urllib.error.HTTPError("u", 500, "x", {}, None), "http", 500),
        (urllib.error.URLError("offline"), "offline", None),
        ({"tag_name": "最新"}, "bad_version", None),
    ],
)
def test_update_check_gives_a_reason_code(monkeypatch, result, code, status):
    """F-058：沒查到最新版時有原因代碼（前端依代碼產生文字，切換語言才會跟著換）。"""
    monkeypatch.delenv("TABLADE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update.urllib.request, "urlopen", _urlopen(result))
    info = update.check_latest()
    assert (info["error_code"], info["http_status"]) == (code, status)
    assert info["error"]


def test_update_check_turned_off_has_a_code(monkeypatch):
    monkeypatch.setenv("TABLADE_NO_UPDATE_CHECK", "1")
    info = update.check_latest()
    assert info["error_code"] == "disabled" and info["latest"] is None


def test_update_check_found_a_version(monkeypatch):
    monkeypatch.delenv("TABLADE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update.urllib.request, "urlopen", _urlopen({"tag_name": "v99.0.0"}))
    info = update.check_latest()
    assert info["newer"] and info["error"] is None and info["error_code"] is None


def test_checker_flags_the_middle_dot_outside_tr():
    """F-055：英文要用 " · "，中文的間隔號「・」也只能出現在 tr() 的第一個參數。"""
    source = "\n".join(["const a = tr('・', ' · ')", "const b = `${x}・${y}`"])
    assert [no for no, _ in check_i18n.violations(source)] == [2]


def test_selftest_reports_io_errors_instead_of_switching_to_one_core(monkeypatch):
    """F-040（自我檢測）：平行轉碼遇到讀寫檔的錯誤時照原樣回報，不當成「開不了工作程序」。"""
    from tablade import selftest
    from tablade.ingest import sources

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(sources, "_transcode_parallel", broken)
    monkeypatch.setattr(sources, "_pool_failed", False)
    result = selftest.run_selftest()
    check = next(c for c in result["checks"] if c["name"] == "多核心轉碼")
    assert not check["ok"] and "disk full" in check["detail"]
    assert sources._pool_failed is False


def _cli_recipe(work: Path, name: str, files: list[str], steps=(), encoding: str = "auto") -> str:
    path = work / f"{name}.recipe.json"
    stage = {
        "name": "主要",
        "input": {"files": files, "reader": "csv", "encoding": encoding},
        "steps": list(steps),
        "output_format": "parquet",
    }
    data = {"tablade_recipe": "1.0", "name": name, "stages": [stage], "output": {"manifest": False}}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _cli(capsys, *args: str) -> tuple[int, list[dict]]:
    from tablade.cli import main

    code = main(list(args))
    out = capsys.readouterr().out
    return code, json.loads(out)["issues"]


def test_run_reports_missing_files_and_step_errors_like_validate(tmp_path, capsys):
    """F-020：`tablade run` 找不到輸入檔時回報問題（不是 traceback）；步驟錯誤的 path 和 validate --run 一樣。"""
    (tmp_path / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")
    missing = _cli_recipe(tmp_path, "missing", ["沒有這個檔.csv"])
    code, issues = _cli(capsys, "run", "--json", missing)
    assert code == 1 and any("沒有這個檔.csv" in i["error"] for i in issues)

    broken = _cli_recipe(
        tmp_path, "broken", ["a.csv"], [{"id": "s1", "type": "fill_down", "columns": ["__sheet"]}]
    )
    _, by_validate = _cli(capsys, "validate", "--run", "--json", broken)
    code, by_run = _cli(capsys, "run", "--json", broken)
    assert code == 1
    assert [i["path"] for i in by_run if i["severity"] == "error"] == [
        i["path"] for i in by_validate if i["severity"] == "error"
    ]


def test_validate_reports_a_missing_recipe_and_a_bad_encoding(tmp_path, capsys):
    """F-053：Recipe 不存在或是資料夾時回報問題；編碼寫錯時沒有 Python 的 LookupError 原文。"""
    code, issues = _cli(capsys, "validate", "--json", str(tmp_path / "沒有.recipe.json"))
    assert code == 1 and "找不到這個檔" in issues[0]["error"]
    code, issues = _cli(capsys, "validate", "--json", str(tmp_path))
    assert code == 1 and "資料夾" in issues[0]["error"]
    (tmp_path / "a.csv").write_text("品號\nP1\n", encoding="utf-8")
    bad = _cli_recipe(tmp_path, "enc", ["a.csv"], encoding="nope")
    code, issues = _cli(capsys, "validate", "--json", bad)
    assert code == 1 and issues and not any("unknown encoding" in i["error"] for i in issues)
