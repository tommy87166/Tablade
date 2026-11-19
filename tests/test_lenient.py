"""寬鬆解碼：解不開的字（造字、壞掉的位元組）換成 �，照樣讀完並警告（docs/eudc-lenient-plan.md）。"""

import io
import json
from pathlib import Path

import polars as pl
import pytest

from tablade.engine.check import format_report
from tablade.engine.run import execute
from tablade.fixtures import (
    BROKEN_UTF8_LINE,
    EUDC_ZIP_MEMBERS,
    broken_utf8,
    eudc_report,
    sectioned_report,
    write_eudc_report,
    write_eudc_zip,
)
from tablade.i18n import set_lang
from tablade.ingest import sources, transcode
from tablade.ingest.encoding import DecodeError
from tablade.ingest.sources import decode_warning, scan_parts
from tablade.ingest.transcode import LENIENT, decode_range
from tablade.recipe import validator
from tablade.recipe.schema import Recipe, parse_recipe
from tablade.recipe.spec import build_spec
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]
REPORT_RECIPE = ROOT / "examples" / "sales_report.recipe.json"
R = "�"


def lenient(data: bytes, encoding: str = "cp950") -> tuple[str, tuple]:
    out = io.BytesIO()
    failure, stats = decode_range(io.BytesIO(data), out, encoding, errors=LENIENT)
    assert failure is None
    return out.getvalue().decode("utf-8"), stats


# ── 換字的處理器 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"A\x8f\x46B", f"A{R}{R}B"),
        (b"A\x8f\x7cB", f"A{R}{R}B"),  # 內建的 replace 會漏出 |
        (b"A\x8f\xa1B", f"A{R}{R}B"),  # 內建的 replace 會吃掉 B
        (b"A\x80B", f"A{R}B"),  # 不是 lead byte：只換自己
        (b"A\xff\x41B", f"A{R}AB"),
        (b"A\x8f\nB", f"A{R}\nB"),  # lead byte 接著換行：換行不會被吃掉
        (b"A\x8f", f"A{R}"),  # 檔尾只有半個字
    ],
)
def test_whole_character_is_replaced(raw, expected):
    text, (count, lines, _) = lenient(raw)
    assert text == expected
    assert count == 1 and lines == (0,)
    assert raw.decode("cp950", LENIENT) == expected


def test_two_byte_characters_count_once():
    text, (count, lines, newlines) = lenient(b"\x8f\x46\x8f\x46\n\xa4\xa4\xfa\x40\n")
    assert text == f"{R * 4}\n中{R * 2}\n"
    assert count == 3 and lines == (0, 1) and newlines == 2


def test_every_cut_gives_the_same_result(monkeypatch):
    data = b"x\n\x8f\x46\n\x8f\x7c|\x80\n\xa4\xa4end\x8f"
    whole = lenient(data)
    monkeypatch.setattr(transcode, "CHUNK", 1)  # 每個位元組一塊：每一個切點都試到
    assert lenient(data) == whole
    for cut in range(1, len(data)):
        monkeypatch.setattr(transcode, "CHUNK", cut)
        assert lenient(data) == whole, cut
    assert whole[1] == (4, (1, 2, 3), 3)  # 同一行只記一次


def test_utf8_is_replaced_like_the_builtin():
    raw = b"\xef\xbb\xbfa\nb\xffc\n\xe4\xb8"
    text, (count, lines, _) = lenient(raw, "utf-8-sig")
    assert text == f"a\nb{R}c\n{R}"
    assert count == 2 and lines == (1, 2)
    assert raw[3:].decode("utf-8", "replace") == text


def test_strict_is_unchanged():
    out = io.BytesIO()
    failure, stats = decode_range(io.BytesIO(b"ab\n\x8f\x46"), out, "cp950")
    assert failure == (3, 0x8F) and stats[0] == 0


def test_delimited_columns_do_not_shift():
    raw = "門市|品名|金額\n北區|".encode("cp950") + b"\x8f\x7c" + "筆|1,280\n".encode("cp950")
    text, _ = lenient(raw)
    assert [line.count("|") for line in text.splitlines()] == [2, 2]
    assert raw.decode("cp950", "replace").count("|") == 5  # 內建的 replace 多了一個 |


# ── 讀檔 ─────────────────────────────────────────────────────


def test_eudc_report_is_read_with_replacements(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    _, decoded, marked = eudc_report()
    for encoding in ("auto", "cp950", "big5hkscs"):
        sources._cache.clear()
        parts, files = scan_parts(
            ["r.txt"], "text_lines", encoding, tmp_path, decode_errors="replace"
        )
        f = files[0]
        lines = parts[0][1].select("line").collect().to_series().to_list()
        if encoding != "big5hkscs":  # big5hkscs 把造字區拿去放別的字，解得開
            assert lines == decoded
            assert f.replaced == len(marked) and list(f.replaced_lines) == marked[:10]
            assert f.encoding == "cp950" and f.auto_picked == (encoding == "auto")
        else:
            assert f.replaced == 0


def test_strict_reports_error_with_hint(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    line = eudc_report()[2][0]
    with pytest.raises(
        DecodeError,
        match=rf"cp950 在第 \d+ 個位元組（第 {line} 行，0x8F）.*換成 �」"
        r'（Recipe：input 的 "decode_errors": "replace"）$',
    ):
        scan_parts(["r.txt"], "text_lines", "cp950", tmp_path)
    with pytest.raises(DecodeError, match="無法判斷編碼"):
        scan_parts(["r.txt"], "text_lines", "auto", tmp_path)


def test_csv_ignores_lenient(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    with pytest.raises(DecodeError) as err:
        scan_parts(["r.txt"], "csv", "cp950", tmp_path, decode_errors="replace")
    assert "換成 �" not in str(err.value)


def test_clean_files_are_unchanged_with_lenient(tmp_path):
    (tmp_path / "u.txt").write_bytes("中文\nabc\n".encode())
    (tmp_path / "b.txt").write_bytes("中文\nabc\n".encode("cp950"))
    for name, encoding in (("u.txt", "utf-8"), ("b.txt", "cp950")):
        strict = scan_parts([name], "text_lines", "auto", tmp_path)
        loose = scan_parts([name], "text_lines", "auto", tmp_path, decode_errors="replace")
        assert strict[1][0].encoding == loose[1][0].encoding == encoding
        assert loose[1][0].replaced == 0 and decode_warning(loose[1][0]) is None
        assert strict[0][0][1].collect().equals(loose[0][0][1].collect())
    assert scan_parts(["u.txt"], "text_lines", "auto", tmp_path, decode_errors="replace")[0]
    # 合法的 UTF-8 照樣直接讀原檔（不轉碼）
    assert (
        sources.prepare(tmp_path / "u.txt", lines=True, errors=LENIENT).effective
        == tmp_path / "u.txt"
    )


def test_auto_picks_utf8_for_a_broken_utf8_file(tmp_path):
    (tmp_path / "u.txt").write_bytes(broken_utf8())
    parts, files = scan_parts(["u.txt"], "text_lines", "auto", tmp_path, decode_errors="replace")
    f = files[0]
    assert (f.encoding, f.replaced, f.replaced_lines, f.auto_picked) == (
        "utf-8",
        1,
        (BROKEN_UTF8_LINE,),
        True,
    )
    lines = parts[0][1].select("line").collect().to_series().to_list()
    assert lines[BROKEN_UTF8_LINE - 1].startswith(R) and "門市" in "".join(lines)
    with pytest.raises(DecodeError):  # 指定 UTF-8、嚴格：照樣報錯
        scan_parts(["u.txt"], "text_lines", "utf-8", tmp_path)
    sources._cache.clear()
    explicit = scan_parts(["u.txt"], "text_lines", "utf-8", tmp_path, decode_errors="replace")[1][0]
    assert (explicit.encoding, explicit.replaced, explicit.auto_picked) == ("utf-8", 1, False)


def test_parallel_segments_match_single(tmp_path):
    raw, decoded, marked = eudc_report(entries=200)
    (tmp_path / "r.txt").write_bytes(raw)
    single, loose = sources.Replaced(), sources.Replaced()
    one = sources._stage_lines(
        tmp_path / "r.txt",
        "cp950",
        tmp_path / "s",
        None,
        segment=512,
        errors=LENIENT,
        replaced=single,
    )
    with sources.new_pool(2) as pool:
        many = sources._stage_lines(
            tmp_path / "r.txt",
            "cp950",
            tmp_path / "p",
            None,
            pool=pool,
            segment=512,
            errors=LENIENT,
            replaced=loose,
        )
    assert len(many[1]) > 3
    a = pl.scan_parquet(list(one[1])).collect()
    b = pl.scan_parquet(list(many[1])).collect()
    assert a.equals(b) and a["line"].to_list() == decoded
    assert single == loose and single.count == len(marked) and single.lines == marked[:10]


def test_zip_members(tmp_path):
    write_eudc_zip(tmp_path / "m.zip")
    _, files = scan_parts(
        ["m.zip"], "zip_lines", "auto", tmp_path, members="**/*.TXT", decode_errors="replace"
    )
    by = {f.path.split("/", 1)[1]: f for f in files}
    report, utf8 = (by[name] for name in EUDC_ZIP_MEMBERS)
    marked = eudc_report()[2]
    assert (report.encoding, report.replaced, report.auto_picked) == ("cp950", len(marked), True)
    assert list(report.replaced_lines) == marked[:10]
    assert (utf8.encoding, utf8.replaced, utf8.replaced_lines) == ("utf-8", 1, (BROKEN_UTF8_LINE,))
    with pytest.raises(DecodeError, match="換成 �"):
        scan_parts(["m.zip"], "zip_lines", "cp950", tmp_path, members="**/HEADER*.TXT")


def test_zip_parallel_segments_match_single(tmp_path):
    from tablade.ingest import zip_lines

    raw, decoded, marked = eudc_report(entries=200)
    import zipfile

    with zipfile.ZipFile(tmp_path / "m.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a/R.TXT", raw)
    member = zip_lines.list_members(tmp_path / "m.zip")[0]
    single, loose = sources.Replaced(), sources.Replaced()
    one = sources._stage_member(
        member, "r", "cp950", tmp_path / "s", None, segment=512, errors=LENIENT, replaced=single
    )
    with sources.new_pool(2) as pool:
        many = sources._stage_member(
            member,
            "r",
            "cp950",
            tmp_path / "p",
            None,
            pool=pool,
            segment=512,
            errors=LENIENT,
            replaced=loose,
        )
    a = pl.scan_parquet(list(one[1])).collect()
    b = pl.scan_parquet(list(many[1])).collect()
    assert a.equals(b) and a["line"].to_list() == decoded
    assert single == loose and single.count == len(marked) and single.lines == marked[:10]


# ── 警告的文字 ───────────────────────────────────────────────


def test_warning_text():
    f = sources.SourceFile("a.txt", 0, "", "cp950", 10_000, replaced=3, replaced_lines=(12, 80, 81))
    assert decode_warning(f) == "有 3 個字解不開，換成 �（第 12、80、81 行）"
    many = sources.SourceFile(
        "a.txt",
        0,
        "",
        "cp950",
        20,
        replaced=12,
        replaced_lines=tuple(range(1, 11)),
        auto_picked=True,
    )
    assert decode_warning(many).endswith("10… 行），可能選錯編碼（自動選了 cp950）")
    assert decode_warning(sources.SourceFile("a.txt", 0, "", "cp950", 10)) is None
    # 「可能選錯編碼」：換掉的字數超過行數的一半才寫（編碼對、只是有造字的報表不會出現）
    few = sources.SourceFile("a.txt", 0, "", "cp950", 100, replaced=12, replaced_lines=(1, 2, 3))
    assert decode_warning(few) == "有 12 個字解不開，換成 �（第 1、2、3 行）"
    raw, _, marked = eudc_report()
    assert len(marked) / raw.count(b"\n") > 0.1  # 造字很密的假報表也不會被當成選錯
    set_lang("en")
    try:
        assert decode_warning(f) == "3 undecodable characters replaced with � (lines 12, 80, 81)"
        assert decode_warning(many).endswith("; the encoding may be wrong (auto-detected cp950)")
    finally:
        set_lang("zh")


# ── Recipe、執行、處理紀錄 ───────────────────────────────────


def report_recipe(tmp_path: Path, decode_errors: str | None = "replace") -> Recipe:
    data = json.loads(REPORT_RECIPE.read_text(encoding="utf-8"))
    data["tablade_recipe"] = "1.1"
    data["stages"][0]["input"]["files"] = ["r.txt"]
    if decode_errors:
        data["stages"][0]["input"]["decode_errors"] = decode_errors
    return parse_recipe(data)


def test_fixed_width_columns_stay_aligned(tmp_path):
    """造字換成兩個 �（2 格）：依固定位置拆出來的金額和沒有造字的報表一樣。"""
    write_eudc_report(tmp_path / "r.txt")
    frame, report = execute(report_recipe(tmp_path), tmp_path, mode="eager")
    expected = sectioned_report(0, sections=2, entries=30)[1]
    amount = lambda text: -int(text[1:-1]) if text.startswith("(") else int(text)  # noqa: E731
    assert frame["金額"].to_list() == [amount(e["金額"].replace(",", "")) for e in expected]
    assert frame["品號"].to_list() == [e["品號"] for e in expected]
    names = frame["品名"].to_list()
    assert sum(n.startswith(R * 2) for n in names) == len(eudc_report()[2])
    assert report.input_warnings == 1 and report.warning_count == 0


def test_stream_matches_eager(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    r = report_recipe(tmp_path)
    _, eager = execute(r, tmp_path, output=tmp_path / "e.parquet", mode="eager")
    _, streamed = execute(r, tmp_path, output=tmp_path / "s.parquet", mode="stream")
    assert pl.read_parquet(tmp_path / "e.parquet").equals(pl.read_parquet(tmp_path / "s.parquet"))
    assert streamed.inputs[0].replaced == eager.inputs[0].replaced == len(eudc_report()[2])


def test_manifest_and_report_text(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    _, report = execute(
        report_recipe(tmp_path), tmp_path, output=tmp_path / "o.parquet", log_dir=tmp_path / "log"
    )
    manifest = json.loads(Path(report.manifest).read_text(encoding="utf-8"))
    marked = eudc_report()[2]
    assert manifest["inputs"][0]["decode"] == {
        "replaced": len(marked),
        "replaced_lines": marked[:10],
    }
    assert "警告：有 15 個字解不開" in format_report(report)
    (tmp_path / "c.txt").write_bytes(b"abc\n")
    data = json.loads(REPORT_RECIPE.read_text(encoding="utf-8"))
    data["stages"][0]["input"]["files"] = ["c.txt"]
    _, clean = execute(
        parse_recipe(data), tmp_path, output=tmp_path / "c.parquet", log_dir=tmp_path / "log"
    )
    assert "decode" not in json.loads(Path(clean.manifest).read_text(encoding="utf-8"))["inputs"][0]


@pytest.mark.parametrize("reader", ["csv", "excel", "parquet"])
def test_recipe_rejects_lenient_for_other_readers(reader):
    data = {
        "tablade_recipe": "1.1",
        "name": "x",
        "stages": [
            {
                "name": "a",
                "output_format": "parquet",
                "input": {"files": ["a"], "reader": reader, "decode_errors": "replace"},
                "steps": [],
            }
        ],
        "output": {},
    }
    issues = [i for i in validator.validate(data, build_spec(), {}) if i["severity"] == "error"]
    assert [i["path"] for i in issues] == ["stages[0].input.decode_errors"]
    with pytest.raises(ValueError, match="decode_errors 才能設成 replace") as err:
        Recipe.model_validate(data)
    assert issues[0]["error"] in str(err.value)  # 和 pydantic 的訊息一致
    data["stages"][0]["input"]["reader"] = "text_lines"
    assert not [i for i in validator.validate(data, build_spec(), {}) if i["severity"] == "error"]
    Recipe.model_validate(data)


def test_stages_reader_rejects_lenient():
    data = {
        "tablade_recipe": "1.1",
        "name": "x",
        "stages": [
            {"name": "a", "output_format": "parquet", "input": {"files": ["a"]}, "steps": []},
            {
                "name": "b",
                "output_format": "parquet",
                "input": {"reader": "stages", "stages": ["a"], "decode_errors": "replace"},
                "steps": [],
            },
        ],
        "output": {},
    }
    issues = [i for i in validator.validate(data, build_spec(), {}) if i["severity"] == "error"]
    assert [i["path"] for i in issues] == ["stages[1].input.decode_errors"]


# ── Session ──────────────────────────────────────────────────


def test_session(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    s = Session()
    s.new(tmp_path / "mine.recipe.json")
    s.add_files([tmp_path / "r.txt"])
    with pytest.raises(Exception, match="換成 �"):
        s.load_data()
    assert "decode_errors" not in s.recipe_dict()["stages"][0]["input"]
    s.set_input({"decode_errors": "replace"})
    assert s.recipe_dict()["stages"][0]["input"]["decode_errors"] == "replace"
    s.load_data()
    state = s.state()["input"]
    assert state["decode_errors"] == "replace"
    assert [w["file"] for w in state["decode_warnings"]] == ["r.txt"]
    assert state["decode_warnings"][0]["text"].startswith("有 15 個字解不開")
    assert state["sources"][0]["replaced"] == 15
    s.set_input({"decode_errors": "error"})  # 改了就要重新載入
    assert s.state()["input"]["stale"] and not s.state()["input"]["decode_warnings"]
    s.set_input({"decode_errors": "replace", "reader": "csv"})  # CSV 不寫進 Recipe
    assert "decode_errors" not in s.recipe_dict()["stages"][0]["input"]


# ── 審查發現（docs/review-findings.md F-077～F-081）────────────


def test_eudc_report_is_not_flagged_as_wrong_encoding(tmp_path):
    write_eudc_report(tmp_path / "r.txt")
    f = scan_parts(["r.txt"], "text_lines", "cp950", tmp_path, decode_errors="replace")[1][0]
    assert "可能選錯編碼" not in decode_warning(f)
    (tmp_path / "u.txt").write_bytes(eudc_report()[0])
    wrong = scan_parts(["u.txt"], "text_lines", "utf-8", tmp_path, decode_errors="replace")[1][0]
    assert "可能選錯編碼" in decode_warning(wrong)


TIE = b"ID,NAME,AMOUNT\r\n" + b"001,LEE \x8f\x46,100\r\n" * 3
"""只有英數字和造字：當 UTF-8 解與當 cp950 解，解不開的字一樣多（F-077）。"""


def test_tie_picks_cp950_unless_bom(tmp_path):
    (tmp_path / "a.txt").write_bytes(TIE)
    (tmp_path / "b.txt").write_bytes(b"\xef\xbb\xbfID,NAME\r\n001,LEE \xff,100\r\n")
    parts, files = scan_parts(["a.txt"], "text_lines", "auto", tmp_path, decode_errors="replace")
    assert files[0].encoding == "cp950"
    assert parts[0][1].collect()["line"][1] == f"001,LEE {R}{R},100"
    parts, files = scan_parts(["b.txt"], "text_lines", "auto", tmp_path, decode_errors="replace")
    assert files[0].encoding == "utf-8-sig"
    assert parts[0][1].collect()["line"].to_list() == ["ID,NAME", f"001,LEE {R},100"]


def test_tie_in_zip(tmp_path):
    import zipfile

    with zipfile.ZipFile(tmp_path / "m.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a/TIE.TXT", TIE)
        z.writestr("a/BOM.TXT", b"\xef\xbb\xbfID,NAME\r\n" + b"x" * 100 + b"\r\n001,LEE \xff\r\n")
    parts, files = scan_parts(
        ["m.zip"], "zip_lines", "auto", tmp_path, members="a/*.TXT", decode_errors="replace"
    )
    by = {f.path.rsplit("/", 1)[1]: (f, lf) for (_, lf), f in zip(parts, files, strict=True)}
    tie, tie_lf = by["TIE.TXT"]
    assert tie.encoding == "cp950" and tie_lf.collect()["line"][1] == f"001,LEE {R}{R},100"
    assert by["BOM.TXT"][0].encoding == "utf-8-sig"


def test_each_line_is_listed_once(tmp_path):
    rows = [f"第{i}行,保溫瓶".encode("cp950") for i in range(1, 8)]
    rows[1] = b"\x8f\x46" * 12 + rows[1]
    rows[4] += b"\xfa\x40"
    rows[6] += b"\xfa\x40"
    (tmp_path / "a.txt").write_bytes(b"\r\n".join(rows) + b"\r\n")
    f = scan_parts(["a.txt"], "text_lines", "cp950", tmp_path, decode_errors="replace")[1][0]
    assert f.replaced == 14 and f.replaced_lines == (2, 5, 7)
    assert "（第 2、5、7 行）" in decode_warning(f)
    _, (count, lines, _) = lenient(b"\x8f\x46\x8f\x46\n\x8f\x46")
    assert count == 3 and lines == (0, 1)


def lone_cr_file() -> bytes:
    """第 2 行中間有一個單獨的 \\r（逐行讀取把它當成換行），第 5 行（讀進來是第 6 行）有造字。"""
    rows = [f"第{i}行,保溫瓶".encode("cp950") for i in range(1, 7)]
    rows[1] = "第2行,保溫".encode("cp950") + b"\r" + "瓶".encode("cp950")
    rows[4] += b"\x8f\x46"
    return b"\r\n".join(rows) + b"\r\n"


def test_line_numbers_count_lone_cr(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_bytes(lone_cr_file())
    parts, files = scan_parts(["a.txt"], "text_lines", "cp950", tmp_path, decode_errors="replace")
    frame = parts[0][1].collect()
    assert frame.filter(pl.col("line").str.contains(R))["__line_no"].to_list() == [6]
    assert files[0].replaced_lines == (6,)
    with pytest.raises(DecodeError, match="第 6 行"):  # 嚴格模式的錯誤訊息也一樣
        scan_parts(["a.txt"], "text_lines", "cp950", tmp_path)
    # \r 剛好在塊的結尾（下一塊才知道後面是不是 \n）
    data = lone_cr_file()
    for cut in range(1, len(data)):
        monkeypatch.setattr(transcode, "CHUNK", cut)
        assert lenient(data)[1][1] == (5,), cut


def test_zip_line_numbers_count_lone_cr(tmp_path):
    import zipfile

    with zipfile.ZipFile(tmp_path / "m.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a/R.TXT", lone_cr_file())
    _, files = scan_parts(
        ["m.zip"], "zip_lines", "cp950", tmp_path, members="a/*.TXT", decode_errors="replace"
    )
    assert files[0].replaced_lines == (6,)
    with pytest.raises(DecodeError, match="第 6 行"):
        scan_parts(["m.zip"], "zip_lines", "cp950", tmp_path, members="a/*.TXT")


def test_utf8_error_line_counts_lone_cr(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"a\rb\r\nc\xff\n")
    with pytest.raises(DecodeError, match=r"utf-8 在第 6 個位元組（第 3 行"):
        scan_parts(["a.txt"], "text_lines", "utf-8", tmp_path)
