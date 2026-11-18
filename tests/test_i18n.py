"""畫面的中英文切換（docs/i18n-plan.md）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tablade.api import Api
from tablade.session import Session, SessionError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_i18n  # noqa: E402


def test_frontend_has_no_untranslated_chinese():
    """前端寫死的中文都包在 tr('中文', 'English') 的第一個參數裡（scripts/check_i18n.py）。"""
    found = [
        f"{path.relative_to(ROOT)}:{no}: {line}"
        for path in check_i18n.files()
        for no, line in check_i18n.violations(path.read_text(encoding="utf-8"))
    ]
    assert found == []


def test_checker_finds_chinese_outside_tr():
    source = "\n".join(
        [
            "const a = tr('中文', 'Chinese')",
            "const b = tr(`共 ${n} 列`, `${n} rows`)",
            "// 註解裡的中文不管",
            "<span>還沒儲存</span>",
            '<Button aria-label="更多" />',
            "const c = `${x}${tr('。', '.')}`",
            "const d = `${tr('第', 'No.')} 列`",
            "const e = /^階段「/ // i18n-ignore",
        ]
    )
    assert [no for no, _ in check_i18n.violations(source)] == [4, 5, 7]


def test_language_is_kept_in_the_session(tmp_path):
    """語言放在 Python：狀態裡有 lang；新增、開啟 Recipe（回到空白狀態）時保留；不支援的語言報錯。"""
    s = Session()
    assert s.state()["lang"] == "zh"
    s.set_language("en")
    assert s.state()["lang"] == "en"
    s.new(tmp_path / "新的.recipe.json")
    assert s.state()["lang"] == "en"
    with pytest.raises(SessionError, match="不支援的語言"):
        s.set_language("fr")


def test_api_set_language_returns_the_new_state():
    api = Api()
    res = api.set_language("en")
    assert res["ok"] and res["state"]["lang"] == "en"
    assert api.set_language("xx")["ok"] is False
