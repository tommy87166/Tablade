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
