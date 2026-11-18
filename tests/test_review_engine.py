"""審查發現（docs/review-findings.md）在引擎、讀檔、匯出這幾塊的回歸測試。

每個測試的 docstring 寫是哪一個發現；資料都在測試裡產生（假資料）。
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import polars as pl
import pytest

from tablade import app
from tablade.engine import run as engine_run
from tablade.engine.check import check_recipe, check_recipe_file
from tablade.engine.run import RunError, apply_step, execute, fill_message
from tablade.export import outputs
from tablade.export.outputs import OutputError, log_dir, plan_outputs, resolve_outputs
from tablade.ingest import sources
from tablade.ingest.sources import check_entries, peek_columns
from tablade.recipe.schema import parse_recipe
from tablade.steps import get


def _recipe(stages, keep=True, manifest=False):
    return {
        "tablade_recipe": "1.0",
        "name": "review",
        "stages": stages,
        "output": {"keep_system_columns": keep, "manifest": manifest},
    }


def _stage(name, source, steps=(), **extra):
    return {
        "name": name,
        "input": source,
        "steps": list(steps),
        "output_format": "parquet",
        **extra,
    }


def _csv(*files):
    return {"files": list(files), "reader": "csv", "encoding": "auto"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── F-001：輸出檔和輸入檔只差大小寫或 Unicode 的組合方式 ──────────────────────


def _output_case(tmp_path: Path, disk_name: str, output_file: str, fmt: str = "csv"):
    (tmp_path / "output").mkdir()
    source = tmp_path / "output" / disk_name
    source.write_text("品名,金額\n筆,12\n紙,34\n", encoding="utf-8")
    data = _recipe(
        [
            _stage(
                "主要",
                _csv(f"output/{disk_name}"),
                [{"id": "s1", "type": "remove_top_rows", "n": 1}],
                output_file=output_file,
                output_format=fmt,
            )
        ],
        keep=False,
    )
    return source, data


def test_output_name_differing_only_in_case_is_still_the_input(tmp_path):
    """F-001：輸入檔是 output/Data.csv、輸出檔名填 data（CSV）時，算寫到輸入檔，不能匯出、輸入檔不變。"""
    source, data = _output_case(tmp_path, "Data.csv", "data")
    planned = plan_outputs(data, tmp_path, "review")[0]
    assert planned.error == (
        "「output/data.csv」是階段「主要」的輸入檔，不能寫到這裡（請換一個輸出檔名）"
    )
    with pytest.raises(OutputError):
        resolve_outputs(parse_recipe(data), tmp_path, "review")
    before = source.read_bytes()
    # 不經過 plan_outputs 直接交給引擎（例如命令列）：動到任何檔之前就擋下
    with pytest.raises(OutputError, match="輸入檔"):
        execute(parse_recipe(data), tmp_path, output=tmp_path / "output" / "data.csv")
    assert source.read_bytes() == before


def test_output_name_in_another_unicode_form_is_still_the_input(tmp_path):
    """F-001：磁碟上是 NFD 的 café.csv、輸出檔名填 NFC 的 café 時，也算寫到輸入檔。"""
    nfd, nfc = unicodedata.normalize("NFD", "café.csv"), unicodedata.normalize("NFC", "café")
    _, data = _output_case(tmp_path, nfd, nfc)
    assert "輸入檔" in plan_outputs(data, tmp_path, "review")[0].error


def test_manifest_location_cannot_be_an_input(tmp_path):
    """F-001：處理紀錄（log/主檔名.manifest.json）的位置也不能是輸入檔；處理紀錄關掉時不檢查。"""
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "結果.manifest.json").write_text("a\n1\n", encoding="utf-8")
    stage = _stage("主要", _csv("log/結果.manifest.json"), output_file="結果")
    on = plan_outputs(_recipe([stage], manifest=True), tmp_path, "review")[0]
    assert on.error.startswith("「log/結果.manifest.json」是階段「主要」的輸入檔")
    off = plan_outputs(_recipe([stage], manifest=False), tmp_path, "review")[0]
    assert off.error is None


def test_other_outputs_are_still_allowed(tmp_path):
    """F-001：和輸入檔不同名的輸出檔照常可以寫。"""
    _, data = _output_case(tmp_path, "Data.csv", "結果")
    assert plan_outputs(data, tmp_path, "review")[0].error is None


# ── F-002：同一個檔經兩個階段處理、附加後再被下一個階段讀 ─────────────────────


def _mark(step_id, letter):
    """把 row 換成 A 或 B：分得出是哪個階段處理過的。"""
    return {
        "id": step_id,
        "type": "replace_values",
        "columns": ["line"],
        "replacements": [{"find": "row", "replace": letter}],
    }


def _chain(tmp_path: Path, last_step, a_files=("data/f.csv",), b_files=("data/f.csv",)):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "f.csv").write_text("row1\nrow2\nrow3\n", encoding="utf-8")
    (tmp_path / "data" / "g.csv").write_text("row7\nrow8\n", encoding="utf-8")

    def lines(files):
        return {"files": list(files), "reader": "text_lines", "encoding": "auto"}

    return parse_recipe(
        _recipe(
            [
                _stage("A", lines(a_files), [_mark("a1", "A")]),
                _stage("B", lines(b_files), [_mark("b1", "B")]),
                _stage("C", {"reader": "stages", "stages": ["A", "B"]}),  # 附加，不合併
                _stage("D", {"reader": "stages", "stages": ["C"]}, [last_step]),
            ]
        )
    )


def _both_modes(tmp_path: Path, recipe) -> dict[str, pl.DataFrame]:
    out = {}
    for mode in ("eager", "stream"):
        target = tmp_path / f"{mode}.parquet"
        execute(recipe, tmp_path, output=target, mode=mode)
        out[mode] = pl.read_parquet(target)
    return out


def test_merge_after_two_stages_read_the_same_file(tmp_path):
    """F-002：A、B 讀同一個檔，C 附加 A、B，D 讀 C 再合併來源檔：串流不能丟掉 A 的資料。"""
    got = _both_modes(tmp_path, _chain(tmp_path, {"id": "d1", "type": "merge_files"}))
    assert got["stream"].equals(got["eager"])
    assert got["stream"]["line"].to_list() == ["A1", "A2", "A3", "B1", "B2", "B3"]


def test_per_file_step_after_two_stages_read_the_same_file(tmp_path):
    """F-002：同樣的四個階段，D 用分檔的步驟（移除前 1 列）：串流和整份讀進記憶體的結果一樣。
    D 看到的來源都是「@C／data/f.csv」，算同一個來源。"""
    step = {"id": "d1", "type": "remove_top_rows", "n": 1}
    got = _both_modes(tmp_path, _chain(tmp_path, step))
    assert got["stream"].equals(got["eager"])
    assert got["stream"]["line"].to_list() == ["A2", "A3", "B1", "B2", "B3"]


def test_same_file_names_that_are_not_next_to_each_other(tmp_path):
    """F-002：A 讀 f、g，B 讀 f：D 裡 f 的列接在一起（依第一次出現的位置），兩種模式一樣、不丟資料。"""
    for step in (
        {"id": "d1", "type": "merge_files"},
        {"id": "d1", "type": "remove_top_rows", "n": 1},
    ):
        recipe = _chain(tmp_path, step, a_files=("data/f.csv", "data/g.csv"))
        got = _both_modes(tmp_path, recipe)
        assert got["stream"].equals(got["eager"]), step["type"]
    merged = _both_modes(
        tmp_path,
        _chain(tmp_path, {"id": "d1", "type": "merge_files"}, ("data/f.csv", "data/g.csv")),
    )["stream"]
    assert merged["line"].to_list() == ["A1", "A2", "A3", "B1", "B2", "B3", "A7", "A8"]


def test_combining_parts_with_the_same_name_is_an_error():
    """F-002：part 的名稱重複時，合併來源檔報錯（不要默默丟資料）。"""
    step = get("merge_files").model_validate({"id": "m", "type": "merge_files"})
    lf = pl.LazyFrame({"__source_file": ["f"], "__line_no": [1], "line": ["x"]})
    with pytest.raises(RunError, match="來源的名稱重複：f"):
        engine_run.apply_parts(step, [("f", lf), ("f", lf)])


# ── F-003：欄名含大括號時的警告訊息 ───────────────────────────────────────


@pytest.mark.parametrize("column", ["金額{元}", "金額{0}", "{", "}"])
def test_warning_message_with_braces_in_the_column_name(tmp_path, column):
    """F-003：欄名有 {、} 時，有警告也照常執行（eager、stream），訊息裡的欄名原樣保留。"""
    (tmp_path / "a.csv").write_text(f'品名,"{column}"\n筆,12\n紙,N/A\n', encoding="utf-8")
    step = {"id": "s1", "type": "parse_number", "columns": [column], "kind": "integer"}
    recipe = parse_recipe(_recipe([_stage("主要", _csv("a.csv"), [step])]))
    for mode in ("eager", "stream"):
        _, report = execute(recipe, tmp_path, output=tmp_path / f"{mode}.parquet", mode=mode)
        messages = [w.message for s in report.steps for w in s.warnings]
        assert messages == [f"「{column}」的「N/A」不是整數，已改成空值"], mode


def test_fill_message_never_formats_user_text():
    """F-003：訊息的 {} 換成值；其他的大括號（使用者的欄名）原樣保留，不會丟出例外。"""
    assert fill_message("「金額{元}」的「{}」不是數字", "N/A") == "「金額{元}」的「N/A」不是數字"
    assert fill_message("「{0}」：{}", 3) == "「{0}」：3"
    assert fill_message("沒有位置 {x}", "v") == "沒有位置 {x}"


# ── F-006：多個輸出檔全部成功才改名 ──────────────────────────────────────────


def _two_outputs(tmp_path: Path):
    (tmp_path / "data").mkdir(exist_ok=True)
    data = _recipe(
        [
            _stage("對照", _csv("data/a.csv"), output_file="對照", output_format="csv"),
            _stage("結果", {"reader": "stages", "stages": ["對照"]}, output_file="結果"),
        ],
        keep=False,
        manifest=True,
    )
    return data


def _export(tmp_path: Path, data):
    recipe = parse_recipe(data)
    targets = resolve_outputs(recipe, tmp_path, "two")
    execute(recipe, tmp_path, outputs=targets, log_dir=log_dir(tmp_path))
    return targets


def _snapshot(tmp_path: Path) -> dict[str, bytes]:
    files = list((tmp_path / "output").iterdir()) + list((tmp_path / "log").iterdir())
    return {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in sorted(files)}


def test_failed_rename_keeps_every_output_and_manifest(tmp_path, monkeypatch):
    """F-006：第 2 個輸出檔改名失敗時，第 1 個輸出檔與每一份處理紀錄都維持上一次的內容。"""
    data = _two_outputs(tmp_path)
    (tmp_path / "data" / "a.csv").write_text("品名,數量\n筆,1\n", encoding="utf-8")
    targets = _export(tmp_path, data)
    first = _snapshot(tmp_path)
    for name, path in targets.items():  # 第 1 次匯出：處理紀錄記的 SHA-256 和輸出檔一致
        manifest = json.loads((tmp_path / "log" / f"{path.stem}.manifest.json").read_text("utf-8"))
        assert manifest["output"]["sha256"] == _sha(path), name

    (tmp_path / "data" / "a.csv").write_text("品名,數量\n筆,1\n紙,2\n", encoding="utf-8")
    real_replace = os.replace

    def second_fails(src, dst):
        # 模擬「結果.parquet」被別的程式開著（Windows 上是 PermissionError）
        if Path(dst).name == "結果.parquet" and Path(src).name.endswith(".tmp"):
            raise PermissionError(13, "Permission denied")
        return real_replace(src, dst)

    monkeypatch.setattr(engine_run.os, "replace", second_fails)
    with pytest.raises(RuntimeError, match=r"「結果\.parquet」寫不進去"):
        _export(tmp_path, data)
    monkeypatch.undo()
    assert _snapshot(tmp_path) == first  # 沒有留下暫存檔、備份檔，內容全部是第 1 次的


def test_locked_output_is_found_before_anything_is_renamed(tmp_path, monkeypatch):
    """F-006：Windows 上改名前先確認每個已經存在的檔都寫得進去：有一個不行，就一個檔都不動。"""
    monkeypatch.setattr(engine_run, "CHECK_LOCKS", True)  # 這台不是 Windows 也照 Windows 的做法檢查
    data = _two_outputs(tmp_path)
    (tmp_path / "data" / "a.csv").write_text("品名,數量\n筆,1\n", encoding="utf-8")
    _export(tmp_path, data)
    first = _snapshot(tmp_path)
    (tmp_path / "data" / "a.csv").write_text("品名,數量\n筆,1\n紙,2\n", encoding="utf-8")
    real_open, renamed = Path.open, []

    def locked(self, mode="r", *args, **kwargs):
        if self.name == "結果.parquet" and mode == "r+b":
            raise PermissionError(13, "Permission denied")
        return real_open(self, mode, *args, **kwargs)

    real_replace = os.replace

    def spy(src, dst):
        renamed.append(Path(dst).name)
        return real_replace(src, dst)

    monkeypatch.setattr(Path, "open", locked)
    monkeypatch.setattr(engine_run.os, "replace", spy)
    with pytest.raises(RuntimeError, match=r"「結果\.parquet」寫不進去"):
        _export(tmp_path, data)
    monkeypatch.undo()
    assert renamed == []
    assert _snapshot(tmp_path) == first


@pytest.mark.skipif(os.name == "nt", reason="Windows 不能用新的檔取代唯讀的檔")
def test_read_only_output_is_still_replaced_outside_windows(tmp_path, monkeypatch):
    """F-006：macOS、Linux 不先試著開啟（唯讀的舊輸出檔照樣可以換成新的，和以前一樣）。"""
    monkeypatch.setattr(engine_run, "CHECK_LOCKS", False)
    data = _two_outputs(tmp_path)
    (tmp_path / "data" / "a.csv").write_text("品名,數量\n筆,1\n", encoding="utf-8")
    targets = _export(tmp_path, data)
    old = targets["結果"].read_bytes()
    targets["結果"].chmod(0o444)
    (tmp_path / "data" / "a.csv").write_text("品名,數量\n筆,1\n紙,2\n", encoding="utf-8")
    _export(tmp_path, data)
    assert targets["結果"].read_bytes() != old


# ── F-007：檔名或資料夾名稱有 [、? ───────────────────────────────────────────


def test_file_names_with_brackets_are_found(tmp_path):
    """F-007：檔名或資料夾名稱有 [ ] 的實際檔（瀏覽檔案選到的）找得到；glob 照常可以用。"""
    (tmp_path / "data").mkdir()
    (tmp_path / "匯出 [2026]").mkdir()
    (tmp_path / "data" / "明細[1].csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "匯出 [2026]" / "明細.csv").write_text("a,b\n3,4\n", encoding="utf-8")
    (tmp_path / "data" / "明細2.csv").write_text("a,b\n5,6\n", encoding="utf-8")
    entries = ["data/明細[1].csv", "匯出 [2026]/明細.csv", "data/明細[0-9].csv", "data/*.csv"]
    assert [e["found"] for e in check_entries(entries, tmp_path)] == [1, 1, 1, 2]
    _, files = sources.scan_parts(entries[:2], "csv", "auto", tmp_path)
    assert [f.path for f in files] == entries[:2]


# ── F-010、F-022：欄位檢查只讀標題列、看每一個檔 ──────────────────────────────


def test_peek_columns_reads_only_the_head(tmp_path, monkeypatch):
    """F-010：peek_columns 不把整個 CSV 讀進記憶體（不呼叫 Path.read_bytes）。"""
    (tmp_path / "a.csv").write_text("日期,門市\n" + "2026-01-05,北區門市\n" * 10, encoding="utf-8")

    def whole_file(self):
        raise AssertionError(f"整個檔讀進記憶體：{self}")

    monkeypatch.setattr(Path, "read_bytes", whole_file)
    assert peek_columns("a.csv", "csv", "auto", tmp_path) == ["日期", "門市"]


def test_peek_columns_handles_lone_cr_and_cp950(tmp_path):
    """F-010：只讀開頭時，單獨 \\r 換行與 cp950 的標題列也讀得到（和引擎一樣）。"""
    (tmp_path / "cr.csv").write_bytes("日期,門市\r1,2\r".encode())
    (tmp_path / "big5.csv").write_bytes("日期,門市\r\n1,2\r\n".encode("cp950"))
    assert peek_columns("cr.csv", "csv", "auto", tmp_path) == ["日期", "門市"]
    assert peek_columns("big5.csv", "csv", "auto", tmp_path) == ["日期", "門市"]


def test_column_check_uses_every_file(tmp_path):
    """F-022：多個檔欄位不同時，欄位取聯集（和引擎對齊欄位一樣），只在後面的檔才有的欄不誤報。"""
    (tmp_path / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("品號,金額,備註\nP2,2,急件\nP3,3,\n", encoding="utf-8")
    assert peek_columns(["a.csv", "b.csv"], "csv", "auto", tmp_path) == ["品號", "金額", "備註"]
    step = {"id": "s1", "type": "fill_down", "columns": ["備註"]}
    data = _recipe([_stage("主要", _csv("a.csv", "b.csv"), [step])])
    assert check_recipe(data, tmp_path).issues == []
    missing = {"id": "s1", "type": "fill_down", "columns": ["沒有"]}
    issues = check_recipe(_recipe([_stage("主要", _csv("a.csv", "b.csv"), [missing])]), tmp_path)
    assert [i["error"] for i in issues.issues] == ["欄位「沒有」在這一步之前不存在"]


def test_too_many_files_skip_the_column_check(tmp_path, monkeypatch):
    """F-022：檔案很多時不看欄位（回傳 None，驗證器不檢查欄位引用），開啟 Recipe 不會等很久。"""
    for i in range(3):
        (tmp_path / f"{i}.csv").write_text("a\n1\n", encoding="utf-8")
    monkeypatch.setattr(sources, "PEEK_MAX_FILES", 2)
    assert peek_columns("*.csv", "csv", "auto", tmp_path) is None


# ── F-016：結合這一邊的鍵不存在 ───────────────────────────────────────────────


def test_join_with_a_missing_key_is_a_step_error(tmp_path):
    """F-016：warn_unmatched 開著時，這一邊的鍵不存在也是 RunError「欄位不存在」（eager、stream）。"""
    left = pl.DataFrame({"__source_file": ["a.csv"], "__line_no": [2], "品號": ["P1"]})
    right = pl.DataFrame({"__source_file": ["b.csv"], "__line_no": [2], "代號": ["P1"]})
    for warn in (True, False):
        step = get("join").model_validate(
            {
                "id": "j",
                "type": "join",
                "right": "對照",
                "on": [{"left": "不存在", "right": "代號"}],
                "warn_unmatched": warn,
            }
        )
        with pytest.raises(RunError, match="欄位不存在：不存在"):
            apply_step(step, left, tables={"對照": right})

    (tmp_path / "a.csv").write_text("品號\nP1\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("代號\nP1\n", encoding="utf-8")
    join = {
        "id": "j",
        "type": "join",
        "right": "對照",
        "on": [{"left": "不存在", "right": "代號"}],
    }
    recipe = parse_recipe(
        _recipe([_stage("對照", _csv("b.csv")), _stage("主要", _csv("a.csv"), [join])])
    )
    with pytest.raises(RunError, match="欄位不存在：不存在"):
        execute(recipe, tmp_path, output=tmp_path / "s.parquet", mode="stream")


# ── F-019：輸出格式不是文字 ──────────────────────────────────────────────────


def test_plan_outputs_with_a_list_as_the_output_format(tmp_path, monkeypatch):
    """F-019：output_format 是清單時，plan_outputs 不丟 TypeError（格式錯誤由驗證器報）。"""
    monkeypatch.setattr(outputs, "output_issues", lambda *args: [])  # 只測這裡，不測驗證器
    stage = _stage("主要", _csv(), output_file="x", output_format=["csv"])
    planned = plan_outputs(_recipe([stage]), tmp_path, "x")[0]
    assert (planned.file, planned.format) == ("x.parquet", "parquet")


# ── F-023：tablade validate 也檢查輸出檔的規劃 ────────────────────────────────


def test_validate_reports_the_output_plan(tmp_path):
    """F-023：最後一個階段沒填檔名、用 Recipe 名稱，和中間的階段撞名：validate 就回報（不用等 run）。"""
    (tmp_path / "a.csv").write_text("品號,金額\nP1,1\n", encoding="utf-8")
    path = tmp_path / "合計.recipe.json"
    data = _recipe(
        [
            _stage("對照", _csv("a.csv"), output_file="合計"),
            _stage("主要", {"reader": "stages", "stages": ["對照"]}),
        ]
    )
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for dry_run in (False, True):
        result = check_recipe_file(path, dry_run=dry_run)
        assert not result.ok
        assert [(i["path"], i["error"]) for i in result.issues] == [
            ("stages[1].output_file", "輸出檔名「合計.parquet」和階段「對照」重複")
        ]


def test_validate_reports_an_output_that_is_an_input(tmp_path):
    """F-023：寫出的位置是輸入檔，validate 就回報；問題沒有重複列出。"""
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "上次.csv").write_text("a\n1\n", encoding="utf-8")
    path = tmp_path / "next.recipe.json"
    data = _recipe(
        [_stage("主要", _csv("output/上次.csv"), output_file="上次", output_format="csv")]
    )
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = check_recipe_file(path)
    assert [i["path"] for i in result.issues] == ["stages[0].output_file"]
    assert "輸入檔" in result.issues[0]["error"]


# ── F-037：試跑之後關掉平行轉碼的工作程序 ─────────────────────────────────────


def test_dry_run_releases_the_workers(tmp_path, monkeypatch):
    """F-037：check_recipe 的試跑（錯誤報告、validate --run）用到平行轉碼時，跑完就關掉工作程序。"""
    monkeypatch.setattr(sources, "PARALLEL_MIN", 1 << 14)
    monkeypatch.setattr(sources, "SEGMENT", 1 << 13)
    monkeypatch.setattr(sources, "_workers", lambda: 2)
    monkeypatch.setattr(sources, "_pool_failed", False)
    (tmp_path / "cp950.csv").write_bytes(("門市,金額\n" + "北區門市,100\n" * 4_000).encode("cp950"))
    opened = []
    real_get_pool = sources._get_pool

    def spy():
        opened.append(True)
        return real_get_pool()

    monkeypatch.setattr(sources, "_get_pool", spy)
    data = _recipe([_stage("主要", _csv("cp950.csv"))])
    result = check_recipe(data, tmp_path, dry_run=True)
    assert result.ok and result.report is not None
    assert opened  # 真的開了工作程序
    assert sources._pool is None


# ── F-039：TABLADE_DEV_URL 只接受 localhost ──────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173@example.com/",
        "http://127.0.0.1:1@example.com/",
        "http://localhost:5173.example.com/",
        "http://localhost:5173\\@example.com/",
        "https://localhost:5173",
        "http://localhost",
        "http://example.com/",
    ],
)
def test_dev_url_rejects_other_hosts(monkeypatch, url):
    """F-039：開頭是 http://localhost:埠 但實際的主機是別的（帳號密碼的寫法）也不接受。"""
    monkeypatch.setenv(app.DEV_URL_ENV, url)
    with pytest.raises(ValueError, match="只接受 localhost"):
        app.dev_url()


@pytest.mark.parametrize(
    "url", ["http://localhost:5173", "http://127.0.0.1:5173/", "http://localhost:5173/?a=1#x"]
)
def test_dev_url_accepts_localhost(monkeypatch, url):
    """F-039：localhost 的網址（可以接路徑）照常接受。"""
    monkeypatch.setenv(app.DEV_URL_ENV, url)
    assert app.dev_url() == url


# ── F-040：平行轉碼的 I/O 錯誤不算「開不了工作程序」 ──────────────────────────


class _FakePool:
    """假的工作程序：submit 時丟出 ``on_submit``，或回傳結果是 ``on_result`` 例外的 future。"""

    def __init__(self, on_submit=None, on_result=None):
        self.on_submit, self.on_result = on_submit, on_result

    def submit(self, fn, job):
        if self.on_submit is not None:
            raise self.on_submit
        future = Future()
        future.set_exception(self.on_result)
        return future

    def shutdown(self, wait=False, cancel_futures=False):
        pass


@pytest.fixture
def cp950_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "_workers", lambda: 2)
    monkeypatch.setattr(sources, "_pool_failed", False)
    monkeypatch.setattr(sources, "_pool", None)
    source = tmp_path / "a.csv"
    source.write_bytes(("門市,金額\n" + "北區門市,100\n" * 2_000).encode("cp950"))
    return source


def test_io_error_during_parallel_transcode_is_not_a_pool_failure(cp950_file, monkeypatch):
    """F-040：工作程序裡讀寫檔失敗（例如暫存碟空間不足）照原樣丟出，之後的大檔照樣平行轉碼。"""
    full = OSError(28, "No space left on device")
    monkeypatch.setattr(sources, "_get_pool", lambda: _FakePool(on_result=full))
    out = cp950_file.parent / "out.txt"
    with pytest.raises(OSError, match="No space left"):
        sources._transcode(cp950_file, "cp950", out, None, parallel_min=0, segment=1024)
    assert sources._pool_failed is False
    assert not list(cp950_file.parent.glob("*.part*"))


@pytest.mark.parametrize(
    "pool",
    [
        _FakePool(on_submit=OSError("不能開新程序")),
        _FakePool(on_submit=RuntimeError("cannot schedule new futures")),
        _FakePool(on_result=BrokenProcessPool("工作程序意外結束")),
    ],
)
def test_pool_failures_fall_back_to_single_core(cp950_file, monkeypatch, pool):
    """F-040：真的開不了工作程序（或工作程序意外結束）才改用單核心，這次開著的期間不再試。"""
    monkeypatch.setattr(sources, "_get_pool", lambda: pool)
    out = cp950_file.parent / "out.txt"
    sources._transcode(cp950_file, "cp950", out, None, parallel_min=0, segment=1024)
    assert out.read_bytes() == cp950_file.read_bytes().decode("cp950").encode("utf-8")
    assert sources._pool_failed is True
