"""換行只認 CRLF：欄位裡單獨的 \\n、\\r 不切行（docs/line-breaks-plan.md）。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import polars as pl
import pytest

from tablade.engine.check import format_report
from tablade.engine.run import execute
from tablade.fixtures import (
    INNER_BREAK_COLUMNS,
    INNER_BREAKS_MEMBER,
    inner_breaks_bytes,
    inner_breaks_report,
    only_lf_lines,
    write_inner_breaks,
    write_inner_breaks_zip,
)
from tablade.i18n import set_lang
from tablade.ingest import sources, transcode
from tablade.ingest.sources import (
    InputError,
    line_break_hint,
    line_break_warning,
    prepare,
    scan_parts,
)
from tablade.ingest.transcode import CRLF, decode_range
from tablade.profile import profile_file
from tablade.recipe import validator
from tablade.recipe.schema import Recipe, parse_recipe
from tablade.recipe.spec import build_spec
from tablade.session import Session

RECORDS, MARKED = inner_breaks_report()
INNER = len(MARKED)


def read(tmp_path: Path, name: str, reader: str, encoding: str, breaks: str):
    members = "**/*.TXT" if reader == "zip_lines" else ""
    parts, files = scan_parts(
        [name], reader, encoding, tmp_path, members=members, line_breaks=breaks
    )
    return pl.concat([lf.collect() for _, lf in parts]), files[0]


SOURCES = [
    ("cp950.txt", "text_lines", "cp950"),
    ("utf8.txt", "text_lines", "utf-8"),
    ("auto.txt", "text_lines", "auto"),
    ("m.zip", "zip_lines", "cp950"),
]


@pytest.fixture
def data(tmp_path: Path) -> Path:
    write_inner_breaks(tmp_path / "cp950.txt")
    write_inner_breaks(tmp_path / "utf8.txt", encoding="utf-8")
    write_inner_breaks(tmp_path / "auto.txt")
    write_inner_breaks_zip(tmp_path / "m.zip")
    return tmp_path


@pytest.mark.parametrize(("name", "reader", "encoding"), SOURCES)
def test_any_splits_records_at_inner_breaks(data, name, reader, encoding):
    frame, f = read(data, name, reader, encoding, "any")
    assert frame.height == f.rows == len(RECORDS) + INNER  # 每個欄位裡的換行多切一行
    assert f.endings == (len(RECORDS), INNER // 2, INNER // 2)
    assert f.inner_breaks == 0 and line_break_hint(f) is not None


@pytest.mark.parametrize(("name", "reader", "encoding"), SOURCES)
def test_crlf_keeps_each_record_in_one_line(data, name, reader, encoding):
    frame, f = read(data, name, reader, encoding, CRLF)
    assert frame.height == f.rows == len(RECORDS)
    assert frame["line"].to_list() == RECORDS  # 欄位裡的 \n、\r 照原樣
    assert frame["__line_no"].to_list() == list(range(1, len(RECORDS) + 1))
    assert f.inner_breaks == INNER and list(f.inner_break_lines) == MARKED
    assert line_break_hint(f) is None
    assert f"有 {INNER} 個換行在欄位裡" in line_break_warning(f)


def test_utf8_without_stray_breaks_is_read_directly(tmp_path):
    clean = "".join(f"{r}\r\n" for r in RECORDS if "\n" not in r and "\r" not in r)
    (tmp_path / "c.txt").write_text(clean, encoding="utf-8", newline="")
    prepared = prepare(tmp_path / "c.txt", "utf-8", lines=True, breaks=CRLF)
    assert prepared.effective == tmp_path / "c.txt" and not prepared.parquet
    assert prepared.inner_breaks == 0


def test_every_chunk_cut_gives_the_same_result(monkeypatch):
    data = inner_breaks_bytes(rows=60)

    def run() -> tuple[str, tuple]:
        out = io.BytesIO()
        failure, stats = decode_range(io.BytesIO(data), out, "cp950", breaks=CRLF)
        assert failure is None
        return out.getvalue().decode(), stats

    whole = run()
    for size in (1, 2, 3, 7, 64, 333):  # 1：每個位元組一塊，跨兩塊的 \r\n 都試到
        monkeypatch.setattr(transcode, "CHUNK", size)
        assert run() == whole, size
    text, (_, _, newlines, endings, inner, lines) = whole
    assert newlines == 60 and endings == (60, 1, 1) and inner == 2 and lines == (24, 49)
    assert text.count(transcode.PLACEHOLDER) == 1 and "\r補登" in text


def test_parallel_segments_cut_only_after_crlf(tmp_path):
    (tmp_path / "r.txt").write_bytes(inner_breaks_bytes(rows=400))
    single, loose = sources.Replaced(crlf=True), sources.Replaced(crlf=True)
    one = sources._stage_lines(
        tmp_path / "r.txt", "cp950", tmp_path / "s", None, segment=97, replaced=single, breaks=CRLF
    )
    with sources.new_pool(2) as pool:
        many = sources._stage_lines(
            tmp_path / "r.txt",
            "cp950",
            tmp_path / "p",
            None,
            pool=pool,
            segment=97,
            replaced=loose,
            breaks=CRLF,
        )
    assert len(many[1]) > 3
    a = pl.scan_parquet(list(one[1])).collect()
    b = pl.scan_parquet(list(many[1])).collect()
    expected = [r.replace("\n", transcode.PLACEHOLDER) for r in inner_breaks_report(rows=400)[0]]
    assert a.equals(b) and a["line"].to_list() == expected
    assert single == loose and single.inner == 16 and single.inner_lines == list(range(25, 251, 25))


def test_zip_segments_cut_only_after_crlf(tmp_path):
    from tablade.ingest import zip_lines

    with zipfile.ZipFile(tmp_path / "m.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a/R.TXT", inner_breaks_bytes(rows=400))
    member = zip_lines.list_members(tmp_path / "m.zip")[0]
    tally = sources.Replaced(crlf=True)
    staged = sources._stage_member(
        member, "r", "cp950", tmp_path / "s", None, segment=97, replaced=tally, breaks=CRLF
    )
    lines = pl.scan_parquet(list(staged[1])).collect()["line"].to_list()
    assert len(lines) == 400 and tally.inner == 16


def test_lenient_line_numbers_count_only_crlf(tmp_path):
    records = [r.encode("cp950") for r in RECORDS[:30]]
    records[26] += b"\x8f\x46"  # 第 27 筆結尾有一個造字；第 25 筆的備註裡有換行
    (tmp_path / "e.txt").write_bytes(b"".join(r + b"\r\n" for r in records))
    _, files = scan_parts(
        ["e.txt"], "text_lines", "cp950", tmp_path, decode_errors="replace", line_breaks=CRLF
    )
    f = files[0]
    assert f.replaced == 1 and f.replaced_lines == (27,)  # 都算換行的話是第 28 行
    assert f.inner_breaks == 1 and f.inner_break_lines == (25,)


@pytest.mark.parametrize("encoding", ["cp950", "utf-8"])
def test_only_lf_file_is_rejected(tmp_path, encoding):
    (tmp_path / "lf.txt").write_bytes(only_lf_lines(encoding=encoding))
    with pytest.raises(InputError, match="不能選「只認 CRLF」"):
        scan_parts(["lf.txt"], "text_lines", encoding, tmp_path, line_breaks=CRLF)
    frame, _ = read(tmp_path, "lf.txt", "text_lines", encoding, "any")
    assert frame.height == 50


def test_only_lf_file_in_zip_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ZIP_SEGMENT", 512)
    with zipfile.ZipFile(tmp_path / "m.zip", "w") as z:
        z.writestr("a/LF.TXT", only_lf_lines(rows=200))
    with pytest.raises(InputError, match="不能選「只認 CRLF」"):
        scan_parts(["m.zip"], "zip_lines", "cp950", tmp_path, members="**/*.TXT", line_breaks=CRLF)


def test_placeholder_in_the_file_is_rejected(tmp_path):
    (tmp_path / "p.txt").write_text("a﷐b\r\nc\nd\r\ne\r\n", encoding="utf-8", newline="")
    with pytest.raises(InputError, match="U\\+FDD0"):
        scan_parts(["p.txt"], "text_lines", "utf-8", tmp_path, line_breaks=CRLF)


def test_small_files_are_not_rejected(tmp_path):
    (tmp_path / "s.txt").write_bytes(b"a\nb\nc\n")  # 幾行而已：讀成一行，警告裡看得到
    frame, f = read(tmp_path, "s.txt", "text_lines", "cp950", CRLF)
    assert frame["line"].to_list() == ["a\nb\nc\n"] and f.inner_breaks == 3


# ── 提示、警告、處理紀錄 ──────────────────────────────────────────


def test_hint_only_for_mostly_crlf_files(tmp_path):
    (tmp_path / "clean.txt").write_bytes(b"".join(f"{r}\r\n".encode("cp950") for r in RECORDS[:20]))
    (tmp_path / "lf.txt").write_bytes(only_lf_lines())
    write_inner_breaks(tmp_path / "few.txt", rows=200)
    assert line_break_hint(read(tmp_path, "clean.txt", "text_lines", "cp950", "any")[1]) is None
    assert line_break_hint(read(tmp_path, "lf.txt", "text_lines", "cp950", "any")[1]) is None
    hint = line_break_hint(read(tmp_path, "few.txt", "text_lines", "cp950", "any")[1])
    assert hint and "LF、CR" in hint and '"line_breaks": "crlf"' in hint


def test_warning_text_in_english(data):
    _, f = read(data, "cp950.txt", "text_lines", "cp950", CRLF)
    try:
        set_lang("en")
        text = line_break_warning(f)
    finally:
        set_lang("zh")
    assert (
        text
        == f"{INNER} line breaks inside fields kept in the same line (lines 25, 50, 75, 100, 125, 150, 175, 200)"
    )


def recipe(data_dir: Path, breaks: str = CRLF, reader: str = "text_lines") -> dict:
    source = {"files": ["cp950.txt"], "reader": reader, "encoding": "cp950", "line_breaks": breaks}
    if reader == "zip_lines":
        source.update(files=["m.zip"], members="**/*.TXT")
    return {
        "tablade_recipe": "1.2",
        "name": "breaks",
        "stages": [
            {
                "name": "a",
                "output_format": "parquet",
                "input": source,
                "steps": [
                    {
                        "id": "s1",
                        "type": "split_delimited",
                        "delimiter": "|",
                        "columns": list(INNER_BREAK_COLUMNS),
                    }
                ],
            }
        ],
        "output": {},
    }


def test_split_columns_after_crlf(data):
    frame, report = execute(parse_recipe(recipe(data)), data, mode="eager")
    assert frame.height == len(RECORDS) and report.input_warnings == 1
    notes = frame["備註"].to_list()
    assert notes[MARKED[0] - 1].endswith("\n補登") and notes[MARKED[1] - 1].endswith("\r補登")
    assert all(v is not None for v in notes)


@pytest.mark.parametrize("reader", ["text_lines", "zip_lines"])
def test_stream_matches_eager(data, reader):
    r = parse_recipe(recipe(data, reader=reader))
    _, eager = execute(r, data, output=data / "e.parquet", mode="eager")
    _, streamed = execute(r, data, output=data / "s.parquet", mode="stream")
    assert pl.read_parquet(data / "e.parquet").equals(pl.read_parquet(data / "s.parquet"))
    assert eager.rows == streamed.rows == len(RECORDS)


def test_manifest_and_report_text(data):
    _, report = execute(
        parse_recipe(recipe(data)), data, output=data / "o.parquet", log_dir=data / "log"
    )
    manifest = json.loads(Path(report.manifest).read_text(encoding="utf-8"))
    assert manifest["inputs"][0]["line_breaks"] == {"inner": INNER, "inner_lines": MARKED}
    assert f"警告：有 {INNER} 個換行在欄位裡" in format_report(report)
    _, plain = execute(
        parse_recipe(recipe(data, "any")), data, output=data / "p.parquet", log_dir=data / "log2"
    )
    assert (
        "line_breaks"
        not in json.loads(Path(plain.manifest).read_text(encoding="utf-8"))["inputs"][0]
    )
    assert "提示：這個檔用 CRLF 換行" in format_report(plain)


def test_profile_shows_the_hint(data):
    text = profile_file(data / "cp950.txt")
    assert '"line_breaks": "crlf"' in text


# ── Recipe、Session ─────────────────────────────────────────────


@pytest.mark.parametrize("reader", ["csv", "excel", "parquet"])
def test_recipe_rejects_crlf_for_other_readers(reader):
    data = {
        "tablade_recipe": "1.2",
        "name": "x",
        "stages": [
            {
                "name": "a",
                "output_format": "parquet",
                "input": {"files": ["a"], "reader": reader, "line_breaks": "crlf"},
                "steps": [],
            }
        ],
        "output": {},
    }
    issues = [i for i in validator.validate(data, build_spec(), {}) if i["severity"] == "error"]
    assert [i["path"] for i in issues] == ["stages[0].input.line_breaks"]
    with pytest.raises(ValueError, match="line_breaks 才能設成 crlf") as err:
        Recipe.model_validate(data)
    assert issues[0]["error"] in str(err.value)
    data["stages"][0]["input"]["reader"] = "text_lines"
    assert not [i for i in validator.validate(data, build_spec(), {}) if i["severity"] == "error"]
    Recipe.model_validate(data)


def test_old_recipes_still_load():
    data = recipe(Path("."), "any")
    data["tablade_recipe"] = "1.1"
    del data["stages"][0]["input"]["line_breaks"]
    assert parse_recipe(data).stages[0].input.line_breaks == "any"


def test_session(data):
    s = Session()
    s.new(data / "mine.recipe.json")
    s.add_files([data / "cp950.txt"])
    s.set_input({"encoding": "cp950"})
    s.load_data()
    state = s.state()["input"]
    assert state["rows"] == len(RECORDS) + INNER and state["line_breaks"] == "any"
    assert [h["file"] for h in state["line_break_hints"]] == ["cp950.txt"]
    assert "line_breaks" not in s.recipe_dict()["stages"][0]["input"]  # 預設值不寫
    s.set_input({"line_breaks": "crlf"})
    assert s.recipe_dict()["stages"][0]["input"]["line_breaks"] == "crlf"
    assert s.state()["input"]["stale"]  # 改了要重新載入
    s.load_data()
    state = s.state()["input"]
    assert state["rows"] == len(RECORDS) and not state["line_break_hints"]
    assert [w["file"] for w in state["file_warnings"]] == ["cp950.txt"]
    assert state["sources"][0]["inner_breaks"] == INNER
    s.set_input({"reader": "csv"})  # CSV 不寫進 Recipe
    assert "line_breaks" not in s.recipe_dict()["stages"][0]["input"]


def test_zip_member_name(data):
    _, f = read(data, "m.zip", "zip_lines", "cp950", CRLF)
    assert f.path.endswith(INNER_BREAKS_MEMBER)
