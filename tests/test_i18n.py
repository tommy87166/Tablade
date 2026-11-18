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


def test_python_ui_text_is_translated():
    """Python 產生、畫面上看得到的中文都包在 t("中文", "English") 的第一個參數裡（PY_MODULES、steps/）。"""
    found = []
    for path in check_i18n.python_files():
        text = path.read_text(encoding="utf-8")
        only = check_i18n._functions_only(path)
        found += [
            f"{path.relative_to(ROOT)}:{no}: {line}"
            for no, line in check_i18n.python_violations(text, only)
        ]
    assert found == []


CJK_TEXT = __import__("re").compile(r"[\u3400-\u9fff]")
CJK_RUN = __import__("re").compile(r"[\u3400-\u9fff]+")


def _quotes_only(english: str, chinese: str) -> bool:
    """英文裡的中文字都是照抄中文原文的一部分（例如比對資料用的正規表示式裡的「年」「月」）。"""
    return all(run in chinese for run in CJK_RUN.findall(english))


def _untranslated(schema: object, path: str = "") -> list[str]:
    """schema 裡有中文標題、說明、選項文字，卻沒有對應英文（json_schema_extra 的 en）的地方。"""
    out: list[str] = []
    if isinstance(schema, dict):
        text = schema.get("en") or {}
        for key in ("title", "description"):
            value = schema.get(key)
            if isinstance(value, str) and CJK_TEXT.search(value):
                english = text.get(key)
                if not english or CJK_TEXT.search(english):
                    out.append(f"{path}.{key}：{value}")
        for key in ("ui_labels", "ui_option_hints"):  # 選項文字、選項下面的說明
            labels = schema.get(key)
            if isinstance(labels, dict) and any(CJK_TEXT.search(str(v)) for v in labels.values()):
                english = text.get(key) or {}
                if set(english) != set(labels) or any(
                    not _quotes_only(str(v), str(labels[k])) for k, v in english.items()
                ):
                    out.append(f"{path}.{key}")
        for key, value in schema.items():
            if key != "en":
                out += _untranslated(value, f"{path}.{key}")
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            out += _untranslated(value, f"{path}[{i}]")
    return out


def test_every_step_has_english_names_and_form_text():
    """每種步驟都有英文名稱、說明；參數表單的每個欄位標題、說明、選項文字都有英文。"""
    from tablade.steps import all_steps

    missing = []
    for cls in all_steps():
        name = cls.type_name()
        if not cls.label_en or CJK_TEXT.search(cls.label_en):
            missing.append(f"{name}.label_en")
        if not cls.doc_en or CJK_TEXT.search(cls.doc_en):
            missing.append(f"{name}.doc_en")
        if cls.scope_note and (not cls.scope_note_en or CJK_TEXT.search(cls.scope_note_en)):
            missing.append(f"{name}.scope_note_en")
        missing += [f"{name}{where}" for where in _untranslated(cls.model_json_schema())]
    assert missing == []


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
    with pytest.raises(SessionError, match="Unsupported language"):
        s.set_language("fr")


def test_api_set_language_returns_the_new_state():
    api = Api()
    res = api.set_language("en")
    assert res["ok"] and res["state"]["lang"] == "en"
    assert api.set_language("xx")["ok"] is False


def test_progress_window_texts_follow_the_language():
    """進度視窗的 phase 用代碼辨認（prepare、trial），畫面上寫的字跟著語言；細節的句子也是。"""
    from tablade import i18n
    from tablade.engine.run import PREPARE, TRIAL, StageTracker

    seen: list[dict] = []
    StageTracker(["a"], seen.append, [PREPARE, TRIAL])
    assert [(p["key"], p["name"]) for p in seen[-1]["phases"]] == [
        ("prepare", "準備檔案"),
        ("trial", "試跑"),
    ]
    i18n.set_lang("en")
    seen.clear()
    tracker = StageTracker(["a", "b"], seen.append, [PREPARE, TRIAL])
    assert [p["name"] for p in seen[-1]["phases"]] == ["Preparing files", "Trial run"]
    tracker.begin(PREPARE)
    tracker.files(1, 3, "data/x.csv")
    assert seen[-2]["phases"][0]["detail"] == "1 of 3 files done, reading data/x.csv"
    tracker.end(PREPARE, "done")
    tracker.skip_phase(TRIAL, "skipped")  # 準備完才輪到之後略過的 phase：用代碼找得到
    assert [p["status"] for p in seen[-1]["phases"]] == ["done", "done"]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_step_error_has_no_prefix_in_either_language(tmp_path, language):
    """步驟執行失敗時，畫面上的 run_error 只寫原因（不含「步驟 x（類型）失敗：」／"Step x (type) failed: "）。"""
    import shutil

    from tablade.fixtures import write_all

    write_all(tmp_path / "data")
    shutil.copy(ROOT / "examples" / "sales_detail.recipe.json", tmp_path / "r.recipe.json")
    s = Session()
    s.set_language(language)
    s.load_recipe(tmp_path / "r.recipe.json")
    s.load_data()
    n = len(s.steps)
    s.add_step("remove_columns", {"column": "單號"}, index=n)
    s.update_step(n, {"columns": ["不存在的欄"]})
    error = s.state()["steps"][n]["run_error"]
    assert error and "不存在的欄" in error
    assert (
        not error.startswith(("步驟", "Step")) and "失敗：" not in error and "failed:" not in error
    )
