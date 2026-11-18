"""處理紀錄（manifest）：輸出旁邊的 xxx.manifest.json。"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tablade.cli import main
from tablade.engine.run import execute
from tablade.export.manifest import manifest_path, recipe_content, recipe_sha256
from tablade.fixtures import write_all
from tablade.recipe.schema import load_json, parse_recipe
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_detail.recipe.json"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLE, tmp_path / EXAMPLE.name)
    write_all(tmp_path / "data")
    return tmp_path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recipe(workdir: Path, **changes):
    data = load_json(workdir / EXAMPLE.name)
    data.update(changes)
    return parse_recipe(data)


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_manifest_records_inputs_steps_and_output(workdir, mode):
    out = workdir / "out" / "明細.parquet"
    _, report = execute(recipe(workdir), workdir, output=out, mode=mode)
    path = manifest_path(out)
    assert report.manifest == path.as_posix() and path.name == "明細.manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    assert m["tablade_manifest"] == "1.0" and m["mode"] == mode
    assert m["tool"]["name"] == "tablade" and m["tool"]["polars"].startswith("2.")
    # 輸入檔：SHA-256 和實際的檔案一樣
    files = {i["file"]: i for i in m["inputs"]}
    assert set(files) == {"data/明細_01.csv", "data/明細_02.csv"}
    for name, item in files.items():
        assert item["sha256"] == sha256(workdir / name) and item["encoding"] == "cp950"
        assert item["bytes"] == (workdir / name).stat().st_size
    # 輸出檔
    assert m["output"]["file"] == "明細.parquet" and m["output"]["rows"] == 70
    assert m["output"]["sha256"] == sha256(out) and m["output"]["bytes"] == out.stat().st_size
    assert "金額" in m["output"]["columns"]
    # 每一步：整份讀進記憶體時有列數，串流執行時沒有
    assert [s["id"] for s in m["steps"]] == [f"s{i}" for i in range(1, 9)]
    rows = [s["rows"] for s in m["steps"]]
    assert rows[-1] == 70 if mode == "eager" else rows == [None] * 8
    # Recipe：完整內容與雜湊
    assert m["recipe"]["content"]["stages"][0]["steps"][0]["type"] == "remove_top_rows"
    assert m["recipe"]["sha256"] == recipe_sha256(m["recipe"]["content"])


def test_recipe_hash_is_stable_and_changes_with_parameters(workdir):
    a = recipe_sha256(recipe_content(recipe(workdir)))
    assert a == recipe_sha256(recipe_content(recipe(workdir)))
    data = load_json(workdir / EXAMPLE.name)
    data["stages"][0]["steps"][0]["n"] = 3
    assert recipe_sha256(recipe_content(parse_recipe(data))) != a


def test_manifest_lists_warnings(workdir):
    data = load_json(workdir / EXAMPLE.name)
    data["stages"][0]["steps"].append(
        {"id": "n", "type": "parse_number", "columns": ["品號"]}
    )  # 品號轉不了
    out = workdir / "w.parquet"
    _, report = execute(parse_recipe(data), workdir, output=out, mode="eager")
    m = json.loads(manifest_path(out).read_text(encoding="utf-8"))
    assert m["warnings"]["count"] == report.warning_count == 70
    first = m["warnings"]["items"][0]
    assert first["step"] == "n" and first["source_file"].startswith("data/明細_0")
    assert "不是數字" in first["message"] and first["line_no"] > 0


def test_manifest_can_be_turned_off(workdir):
    out = workdir / "off.parquet"
    off = recipe(workdir, output={"format": "parquet", "manifest": False})
    _, report = execute(off, workdir, output=out, mode="eager")
    assert out.exists() and report.manifest is None and not manifest_path(out).exists()
    # 只試跑（不寫出檔案）時也不寫
    _, dry = execute(recipe(workdir), workdir)
    assert dry.manifest is None


def test_session_export_and_cli_report_the_manifest(workdir, capsys):
    s = Session()
    s.load_recipe(workdir / EXAMPLE.name)
    s.load_data()
    result = s.export()  # 寫到 Recipe 所在資料夾底下的 output/，處理紀錄在並排的 log/
    assert result["path"] == (workdir / "output" / "sales_detail.parquet").as_posix()
    assert result["manifest"] == (workdir / "log" / "sales_detail.manifest.json").as_posix()
    s.set_output({"manifest": False})
    assert s.recipe_dict()["output"]["manifest"] is False
    (workdir / "log" / "sales_detail.manifest.json").unlink()
    assert s.export()["manifest"] is None
    assert not (workdir / "log" / "sales_detail.manifest.json").exists()

    assert main(["run", str(workdir / EXAMPLE.name)]) == 0
    printed = capsys.readouterr().out
    assert f"輸出：{(workdir / 'output' / 'sales_detail.parquet').as_posix()}" in printed
