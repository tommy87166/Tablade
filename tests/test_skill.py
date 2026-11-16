"""AI skill（DESIGN.md §17）：內容從 registry 產生、符合 Agent Builder 限制、驗證器可單獨執行。"""

import ast
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from tablade.cli import main
from tablade.fixtures import write_messy_export
from tablade.profile import mask_text, profile
from tablade.recipe.schema import RECIPE_VERSION
from tablade.skill import (
    build_zip,
    check_limits,
    skill_filename,
    skill_files,
    steps_markdown,
    validator_script,
)
from tablade.steps import all_steps

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"


def test_skill_contains_every_registered_step():
    files = skill_files()
    catalog = files["references/steps.md"]
    for cls in all_steps():
        assert f"## `{cls.type_name()}` — {cls.label}" in catalog
        assert f"| `{cls.type_name()}` |" in files["SKILL.md"]
    assert "TextCondition" in catalog and "NumberCondition" in catalog


def test_skill_frontmatter_and_version():
    text = skill_files()["SKILL.md"]
    assert text.startswith("---\nname: tablade-recipe\ndescription: ")
    assert f"recipe 版本 {RECIPE_VERSION}" in text.split("---")[1]
    assert f'"tablade_recipe": "{RECIPE_VERSION}"' in text
    assert skill_filename() == f"tablade-recipe-skill-v{RECIPE_VERSION}.zip"


def test_skill_fits_agent_builder_limits():
    files = skill_files()
    assert check_limits(files) == []
    data = build_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "SKILL.md" in zf.namelist()  # SKILL.md 在 zip 最上層
        assert zf.read("SKILL.md").decode("utf-8") == files["SKILL.md"]
    assert check_limits({**files, "a/b/c/d.md": "x"}) == ["a/b/c/d.md 的目錄深度超過 3"]
    assert check_limits({**files, "x.exe": ""}) == ["x.exe 的副檔名不在允許清單"]


def test_embedded_validator_runs_alone_on_python_39_syntax(tmp_path):
    script = validator_script()
    ast.parse(script, feature_version=(3, 9))  # Copilot 沙箱的 Python 版本未公開，往下相容
    path = tmp_path / "validate_recipe.py"
    path.write_text(script, encoding="utf-8")
    good = tmp_path / "good.recipe.json"
    good.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    bad_data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    bad_data["steps"][5]["columns"] = ["門"]
    bad = tmp_path / "bad.recipe.json"
    bad.write_text(json.dumps(bad_data, ensure_ascii=False), encoding="utf-8")

    def run(*args):
        return subprocess.run(
            [sys.executable, "-I", str(path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
        )

    assert run(str(good)).returncode == 0
    result = run(str(bad), "--json")
    assert result.returncode == 1
    issue = json.loads(result.stdout)["issues"][0]
    assert issue["path"] == "steps[5].columns[0]" and "是不是「門市」？" in issue["hint"]


def test_steps_markdown_examples_are_the_registry_examples():
    text = steps_markdown()
    for cls in all_steps():
        for example in cls.examples:
            assert f"### 範例：{example.title}" in text


def test_probe_skill(tmp_path):
    assert main(["skill", "--probe", "--out", str(tmp_path / "p.zip")]) == 0
    with zipfile.ZipFile(tmp_path / "p.zip") as zf:
        assert sorted(zf.namelist()) == ["SKILL.md", "scripts/probe.py"]
        zf.extractall(tmp_path / "p")
    data = tmp_path / "x.csv"
    data.write_bytes("日期,門市\n1,2\n".encode("cp950"))
    out = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "p" / "scripts" / "probe.py"), str(data)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 0
    assert "編碼: cp950" in out.stdout and "== 結束 ==" in out.stdout
    assert "日期" not in out.stdout  # 不讀檔案內容


def test_skill_cli(tmp_path):
    assert main(["skill", "--out", str(tmp_path / "s.zip")]) == 0
    assert zipfile.is_zipfile(tmp_path / "s.zip")


def test_profile_summarizes_and_masks(tmp_path, capsys):
    path = write_messy_export(tmp_path / "明細.csv", n=12)
    text = profile([path], head=5)
    assert "- 編碼：cp950" in text
    assert "推測分隔符號：逗號" in text and "6 欄" in text
    assert "    1: 門市銷售明細（假資料）" in text
    assert "最後" in text and "共 12 筆" in text
    masked = profile([path], head=3, mask=True)
    assert "門市" not in masked and "〇〇〇〇〇〇（〇〇〇）" in masked
    assert mask_text("115/01/05 北區門市 A001 1,280") == "999/99/99 〇〇〇〇 a999 9,999"
    assert main(["profile", str(path), "--lines", "3"]) == 0
    assert "Tablade 資料摘要" in capsys.readouterr().out
