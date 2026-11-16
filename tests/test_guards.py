"""資料防護腳本的測試。

注意：這個檔案本身也會被 check_no_data 掃描，所以「看起來像真實個資」的字串一律在執行時組出來，
不寫成字面值。
"""

import subprocess
import sys

import pytest

from tablade.fixtures import write_all


def make_tw_id(module, prefix: str = "A1") -> str:
    """組出一個通過檢查碼的假號碼（只在記憶體裡）。"""
    body = prefix + "2345678"
    for check in "0123456789":
        if module.is_valid_tw_id(body + check):
            return body + check
    raise AssertionError("找不到檢查碼")


# ── check_no_data ──────────────────────────────────────────


def write(root, rel, content=b"hello\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return rel


@pytest.mark.parametrize(
    "rel",
    ["data.csv", "sub/report.TXT", "x.xlsx", "out.parquet", "a.manifest.json", "real.recipe.json"],
)
def test_blocks_data_extensions(check_no_data, tmp_path, rel):
    write(tmp_path, rel)
    problems = check_no_data.check_file(tmp_path, rel, [])
    assert any("資料檔副檔名" in p.reason for p in problems)


@pytest.mark.parametrize(
    "rel", ["examples/gl.recipe.json", "docs/notes.txt", "requirements-dev.txt", "src/a.py"]
)
def test_allows_allowlisted_paths(check_no_data, tmp_path, rel):
    write(tmp_path, rel)
    assert check_no_data.check_file(tmp_path, rel, []) == []


def test_blocks_large_files(check_no_data, tmp_path):
    write(tmp_path, "big.bin", b"\x00" * (check_no_data.MAX_BYTES + 1))
    problems = check_no_data.check_file(tmp_path, "big.bin", [])
    assert any("超過 1 MiB" in p.reason for p in problems)


def test_tw_id_checksum(check_no_data):
    valid = make_tw_id(check_no_data)
    assert check_no_data.is_valid_tw_id(valid)
    wrong = valid[:-1] + str((int(valid[-1]) + 1) % 10)
    assert not check_no_data.is_valid_tw_id(wrong)
    assert check_no_data.is_valid_tw_id(make_tw_id(check_no_data, "Z8"))


def test_detects_tw_id_and_masks_it(check_no_data, tmp_path):
    valid = make_tw_id(check_no_data)
    write(tmp_path, "notes.md", f"第一行\n某人 {valid} 的資料\n")
    problems = check_no_data.check_file(tmp_path, "notes.md", [])
    assert [(p.line, p.reason.split()[0]) for p in problems] == [(2, "疑似身分證字號／居留證號")]
    assert valid not in str(problems[0])


def test_ignores_id_like_strings_that_fail_checksum(check_no_data, tmp_path):
    valid = make_tw_id(check_no_data)
    wrong = valid[:-1] + str((int(valid[-1]) + 1) % 10)
    write(tmp_path, "notes.md", f"{wrong} X{valid} {valid}9\n")
    assert check_no_data.check_file(tmp_path, "notes.md", []) == []


def test_detects_ubn_near_keyword(check_no_data, tmp_path):
    number = "".join(str(i) for i in range(1, 9))
    write(tmp_path, "a.md", f"統編：{number}\n{number}（統一編號）\n單號 {number}\n")
    problems = check_no_data.check_file(tmp_path, "a.md", [])
    assert sorted(p.line for p in problems) == [1, 2]
    assert all(number not in str(p) for p in problems)


def test_detects_ubn_in_cp950_file(check_no_data, tmp_path):
    number = "".join(str(i) for i in range(1, 9))
    write(tmp_path, "a.md", f"統一編號 {number}\n".encode("cp950"))
    assert len(check_no_data.check_file(tmp_path, "a.md", [])) == 1


def test_denylist_matches_content_and_path(check_no_data, tmp_path):
    term = "範例" + "專案名"
    (tmp_path / check_no_data.DENYLIST_NAME).write_text(f"# 註解\n\n{term}\n", encoding="utf-8")
    denylist = check_no_data.load_denylist(tmp_path)
    assert denylist == [term]

    write(tmp_path, "a.md", f"x\n這是{term}的檔案\n")
    write(tmp_path, f"docs/{term}.md")
    assert [p.line for p in check_no_data.check_file(tmp_path, "a.md", denylist)] == [2]
    assert check_no_data.check_file(tmp_path, f"docs/{term}.md", denylist)


def test_ascii_denylist_terms_ignore_hashes_but_catch_word_forms(check_no_data, tmp_path):
    term = "zq" + "xv"  # 組出來的假字詞，避免本檔自己命中
    denylist = [term]
    write(tmp_path, "lock.json", f'"integrity": "sha512-Ab3{term.upper()}9+/{term}Q=="\n')
    assert check_no_data.check_file(tmp_path, "lock.json", denylist) == []

    write(tmp_path, "a.md", f"x\n{term.upper()}'s tool\n{term}ing\nsee ({term})\n")
    assert [p.line for p in check_no_data.check_file(tmp_path, "a.md", denylist)] == [2, 3, 4]


def test_denylist_file_itself_is_blocked(check_no_data, tmp_path):
    write(tmp_path, check_no_data.DENYLIST_NAME, "x\n")
    problems = check_no_data.check_file(tmp_path, check_no_data.DENYLIST_NAME, [])
    assert "不得 commit" in problems[0].reason


def test_generated_fixtures_are_blocked(check_no_data, tmp_path):
    paths = write_all(tmp_path / "fixtures")
    for path in paths:
        rel = path.relative_to(tmp_path).as_posix()
        assert check_no_data.check_file(tmp_path, rel, []), rel


def test_repo_itself_is_clean():
    """整個 repo（版控中的檔案）必須通過檢查。"""
    result = subprocess.run(
        [sys.executable, "scripts/check_no_data.py", "--all"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# ── check_code_policy ──────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import socket",
        "import urllib.request",
        "from urllib import request",
        "from http.client import HTTPConnection",
        "import requests as r",
        "import httpx",
        "eval('1')",
        "exec('x = 1')",
        "__import__('os')",
        "import importlib\nimportlib.import_module('x')",
    ],
)
def test_code_policy_rejects(check_code_policy, source):
    assert check_code_policy.check_source("x.py", source)


@pytest.mark.parametrize(
    "source",
    ["import polars as pl", "import re\nre.compile('x')", "from . import api", "import threading"],
)
def test_code_policy_allows(check_code_policy, source):
    assert check_code_policy.check_source("x.py", source) == []


def test_src_follows_code_policy(check_code_policy):
    assert check_code_policy.main(["src"]) == 0
