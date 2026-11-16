import polars as pl
import pytest

from tablade.fixtures import SIMPLE_HEADER, simple_rows, write_simple_csv
from tablade.ingest.csv_reader import read_csv
from tablade.ingest.encoding import DecodeError, decode_bytes, to_utf8

TEXT = "日期,品名\r\n115/01/05,折疊傘\r\n"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (TEXT.encode("utf-8"), "utf-8"),
        (b"\xef\xbb\xbf" + TEXT.encode("utf-8"), "utf-8-sig"),
        (TEXT.encode("cp950"), "cp950"),
        (b"plain ascii\n", "utf-8"),
    ],
)
def test_auto_detects_encoding(raw, expected):
    decoded = decode_bytes(raw)
    assert decoded.encoding == expected
    assert decoded.text.replace("\r\n", "\n") == (
        "plain ascii\n" if raw == b"plain ascii\n" else TEXT.replace("\r\n", "\n")
    )
    assert not decoded.text.startswith("\ufeff")


def test_cp950_superset_characters():
    # 「碁」「銹」是 cp950 有、標準 Big5 沒有的字
    raw = "碁銹".encode("cp950")
    assert decode_bytes(raw).text == "碁銹"


def test_explicit_encoding():
    raw = "香港".encode("big5hkscs")
    assert decode_bytes(raw, "big5hkscs").text == "香港"


def test_undecodable_reports_byte_position_and_line():
    raw = b"ok\nline2 \xff\xff\n"
    with pytest.raises(DecodeError) as excinfo:
        decode_bytes(raw)
    message = str(excinfo.value)
    assert "utf-8-sig 在第 9 個位元組（第 2 行，0xFF）" in message
    assert "cp950" in message


def test_explicit_encoding_failure():
    with pytest.raises(DecodeError, match="utf-8 在第 0 個位元組"):
        decode_bytes("測試".encode("cp950"), "utf-8")


def test_unsupported_encoding():
    with pytest.raises(DecodeError, match="不支援的編碼"):
        decode_bytes(b"x", "latin-1")


def test_to_utf8_strips_bom_and_keeps_plain_utf8_bytes():
    raw = TEXT.encode("utf-8")
    assert to_utf8(raw) == (raw, "utf-8")
    assert to_utf8(b"\xef\xbb\xbf" + raw) == (raw, "utf-8-sig")
    assert to_utf8(TEXT.encode("cp950")) == (raw, "cp950")


@pytest.mark.parametrize("encoding", ["cp950", "utf-8-sig", "utf-8"])
def test_read_csv_keeps_all_columns_as_text(tmp_path, encoding):
    path = write_simple_csv(tmp_path / "x.csv", n=25, seed=3, encoding=encoding)
    result = read_csv(path)

    assert result.encoding == encoding
    assert result.bytes_read == path.stat().st_size
    assert result.frame.columns == list(SIMPLE_HEADER)
    assert all(dtype == pl.String for dtype in result.frame.dtypes)
    assert result.frame.to_dicts() == simple_rows(25, seed=3)


# ── text_lines 與多檔（M1）──────────────────────────────────────

from tablade.ingest.sources import (  # noqa: E402
    InputError,
    check_entries,
    display_name,
    load,
    peek_columns,
    resolve,
)
from tablade.ingest.text_lines import lines_frame, split_lines  # noqa: E402


def test_split_lines_only_breaks_on_real_newlines():
    assert split_lines("a\r\nb\nc\rd") == ["a", "b", "c", "d"]
    assert split_lines("a\x0cb c\n") == ["a\x0cb c"]
    assert split_lines("a\n\n") == ["a", ""]
    assert split_lines("") == []


def test_lines_frame_keeps_blank_lines_and_numbers():
    df = lines_frame("x\n\ny\n", "f.txt")
    assert df.to_dicts() == [
        {"__source_file": "f.txt", "__line_no": 1, "line": "x"},
        {"__source_file": "f.txt", "__line_no": 2, "line": ""},
        {"__source_file": "f.txt", "__line_no": 3, "line": "y"},
    ]


def test_load_multiple_files_in_order_with_detected_encodings(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b.txt").write_bytes("第二\n".encode("cp950"))
    (tmp_path / "d" / "a.txt").write_bytes("第一\n二\n".encode())
    df, files = load("d/*.txt", "text_lines", "auto", tmp_path)
    assert df["line"].to_list() == ["第一", "二", "第二"]
    assert [(f.path, f.encoding, f.rows) for f in files] == [
        ("d/a.txt", "utf-8", 2),
        ("d/b.txt", "cp950", 1),
    ]
    assert len(files[0].sha256) == 64


def test_load_csv_with_different_columns_and_line_numbers(tmp_path):
    (tmp_path / "1.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    (tmp_path / "2.csv").write_text("a,c\n5,6\n", encoding="utf-8")
    df, _ = load("*.csv", "csv", "auto", tmp_path)
    assert df.columns == ["__source_file", "__line_no", "a", "b", "c"]
    assert df["__line_no"].to_list() == [2, 3, 2]
    assert df["c"].to_list() == [None, None, "6"]


def test_resolve_file_list_in_order(tmp_path):
    for name in ("b.csv", "a1.csv", "a2.csv"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-外部.csv"
    outside.write_text("x\n", encoding="utf-8")
    # 依清單順序；同一項裡依檔名；重複的檔只算一次
    got = resolve(["b.csv", "a*.csv", "a1.csv", str(outside)], tmp_path)
    assert [p.name for p in got] == ["b.csv", "a1.csv", "a2.csv", outside.name]
    assert check_entries(["a*.csv", "nope.csv"], tmp_path) == [
        {"path": "a*.csv", "found": 2},
        {"path": "nope.csv", "found": 0},
    ]
    with pytest.raises(InputError, match=r"找不到檔案「nope\.csv」"):
        resolve(["b.csv", "nope.csv"], tmp_path)
    with pytest.raises(InputError, match="還沒有加入資料檔"):
        resolve([], tmp_path)
    # Recipe 資料夾外的檔：顯示整理過的絕對路徑，不會有 ..
    assert display_name(tmp_path / ".." / outside.name, tmp_path) == outside.resolve().as_posix()
    outside.unlink()


def test_resolve_and_peek(tmp_path):
    with pytest.raises(InputError, match="找不到符合"):
        resolve("nothing_*.csv", tmp_path)
    (tmp_path / "x.csv").write_bytes("\ufeff日期,門市\n1,2\n".encode("utf-8"))
    assert peek_columns("x.csv", "csv", "auto", tmp_path) == ["日期", "門市"]
    assert peek_columns("x.csv", "text_lines", "auto", tmp_path) == ["line"]
