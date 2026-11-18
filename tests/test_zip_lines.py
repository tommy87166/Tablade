"""讀 zip 裡的文字檔（讀取方式 zip_lines，docs/zip-lines-plan.md）。假資料都是當場產生的（tablade.fixtures）。"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import polars as pl
import pytest

from tablade import i18n
from tablade.engine.run import execute
from tablade.fixtures import XLSX_TIME, report_lines, write_zip_months
from tablade.ingest import sources, zip_lines
from tablade.ingest.sources import InputError, scan_parts, scan_zips
from tablade.recipe import validator
from tablade.recipe.schema import RecipeError, load_json, parse_recipe
from tablade.recipe.spec import build_spec
from tablade.session import Session

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sales_zip.recipe.json"


def entry(name: str, data: bytes, zf: zipfile.ZipFile, method: int = zipfile.ZIP_DEFLATED) -> None:
    info = zipfile.ZipInfo(name, date_time=XLSX_TIME)
    info.compress_type = method
    zf.writestr(info, data)


def make_zip(path: Path, files: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            entry(name, data, zf, method)
    return path


def patch_method(path: Path, method: int) -> None:
    """把 zip 裡每個檔記的壓縮方式改成 method（標準函式庫寫不出 Deflate64 等，測試時直接改標頭）。"""
    data = bytearray(path.read_bytes())
    for signature, at in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        start = 0
        while (i := data.find(signature, start)) >= 0:
            data[i + at : i + at + 2] = method.to_bytes(2, "little")
            start = i + 4
    path.write_bytes(bytes(data))


def patch_flag(path: Path, bit: int) -> None:
    """把每個檔的旗標加上 bit（例如 0x1：有密碼）。"""
    data = bytearray(path.read_bytes())
    for signature, at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (i := data.find(signature, start)) >= 0:
            flags = int.from_bytes(data[i + at : i + at + 2], "little") | bit
            data[i + at : i + at + 2] = flags.to_bytes(2, "little")
            start = i + 4
    path.write_bytes(bytes(data))


def read(tmp_path: Path, files: list[str], members: str, encoding: str = "auto"):
    parts, found = scan_parts(files, "zip_lines", encoding, tmp_path, members=members)
    return sources.concat(parts).collect(), found


# ── zip 裡的路徑與比對 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "path", "matched"),
    [
        ("2026*/HEADER*.TXT", "202601/HEADER_001.TXT", True),
        ("2026*/HEADER*.TXT", "202601/header_002.txt", True),  # 不分大小寫
        ("2026*/HEADER*.TXT", "202601/sub/HEADER_001.TXT", False),  # * 不跨過 /
        ("HEADER*.TXT", "202601/HEADER_001.TXT", False),  # 比對完整路徑
        ("**/HEADER*.TXT", "202601/sub/HEADER_001.TXT", True),  # ** 任何層數
        ("**/HEADER*.TXT", "HEADER_001.TXT", True),  # ** 也可以是 0 層
        ("2026**/HEADER*.TXT", "202601/HEADER_001.TXT", True),
        ("202601/HEADER_00?.TXT", "202601/HEADER_001.TXT", True),
        ("202601/HEADER_00[12].TXT", "202601/HEADER_003.TXT", False),
        ("202601/HEADER_00[!3].TXT", "202601/HEADER_001.TXT", True),
        ("./202601\\HEADER_001.TXT", "202601/HEADER_001.TXT", True),  # \\ 與 ./ 整理過再比
        ("202601/說明.TXT", "202601/說明.txt", True),
        ("a+b(1).TXT", "a+b(1).txt", True),  # 正規表示式的特殊字元照字面比
    ],
)
def test_members_pattern(pattern, path, matched):
    assert bool(zip_lines.compile_members(pattern).fullmatch(path)) is matched


def test_member_paths_are_normalized(tmp_path):
    """\\ 換成 /、拿掉 ./ 與開頭的 /、資料夾項目不算；沒標 UTF-8 的檔名依序試 UTF-8 → cp950。"""
    from tablade.fixtures import _zip_entry

    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("dir/", date_time=XLSX_TIME), b"")
        zf.writestr(_zip_entry("./dir\\許可.TXT", cp950_name=True), b"x")  # 許的第二個位元組是 0x5C
        zf.writestr(_zip_entry("/說明.txt"), b"y")  # 標了 UTF-8
    names = [m.path for m in zip_lines.list_members(path)]
    assert names == ["dir/許可.TXT", "說明.txt"]


def test_member_names_without_utf8_flag_can_be_utf8(tmp_path):
    """Mac 的壓縮程式常是 UTF-8 但沒標：照樣是正確的中文。"""

    class Utf8NoFlag(zipfile.ZipInfo):
        def _encodeFilenameFlags(self):
            return self.filename.encode("utf-8"), self.flag_bits & ~0x800

    path = tmp_path / "mac.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(Utf8NoFlag("資料/明細.TXT", date_time=XLSX_TIME), b"x")
    assert [m.path for m in zip_lines.list_members(path)] == ["資料/明細.TXT"]


# ── 掃描 ────────────────────────────────────────────────────────────────


def test_scan_lists_matches_and_skips_empty_zips(tmp_path):
    write_zip_months(tmp_path / "data")
    scan = scan_zips(["data/2026-*.zip"], "2026*/HEADER*.TXT", tmp_path)
    assert [z.path for z in scan.zips] == [f"data/2026-0{m}.zip" for m in (1, 2, 3)]
    assert [m.path for _, m in scan.members] == [
        "202601/HEADER_001.TXT",
        "202601/header_002.txt",
        "202602/HEADER_001.TXT",
        "202602/header_002.txt",
    ]
    assert scan.empty == ["data/2026-03.zip"]
    assert scan.problems("2026*/HEADER*.TXT") == []
    assert scan.estimate > 0


def test_scan_problems_block_loading(tmp_path, monkeypatch):
    """有密碼、不支援的壓縮方式、全部沒有符合的檔、空間不夠：都列成問題（不能載入、匯出）。"""
    locked = make_zip(tmp_path / "locked.zip", {"a/HEADER.TXT": b"x\r\n"})
    patch_flag(locked, 0x1)
    odd = make_zip(tmp_path / "odd.zip", {"a/HEADER.TXT": b"x\r\n"}, zipfile.ZIP_STORED)
    patch_method(odd, 93)
    problems = scan_zips(["locked.zip", "odd.zip"], "a/*.TXT", tmp_path).problems("a/*.TXT")
    assert any("有密碼" in p for p in problems) and any("不支援的壓縮方式" in p for p in problems)
    assert scan_zips(["odd.zip"], "b/*", tmp_path).problems("b/*") == [
        "所有 zip 裡都沒有符合「b/*」的檔"
    ]
    monkeypatch.setattr(sources.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(1, 1, 1))
    assert any("暫存空間不夠" in p for p in scan_zips(["odd.zip"], "a/*", tmp_path).problems("a/*"))
    with pytest.raises(InputError, match="有密碼"):
        read(tmp_path, ["locked.zip"], "a/*.TXT")
    i18n.set_lang("en")
    assert any(
        "password-protected" in p
        for p in scan_zips(["locked.zip"], "a/*", tmp_path).problems("a/*")
    )


def test_deflate64_without_the_package_is_reported(tmp_path, monkeypatch):
    """Deflate64 的檔：沒裝套件時掃描就標出來（不會讀到一半才失敗）。"""
    monkeypatch.setattr(zip_lines, "deflate64_available", lambda: False)
    path = make_zip(tmp_path / "big.zip", {"a/HEADER.TXT": b"x\r\n"}, zipfile.ZIP_STORED)
    patch_method(path, zip_lines.DEFLATE64)
    member = zip_lines.matching(path, "a/*")[0]
    assert zip_lines.method_name(member.method) == "Deflate64"
    assert "Deflate64" in member.problem()


def test_not_a_zip_and_missing_members(tmp_path):
    (tmp_path / "a.zip").write_text("不是 zip", encoding="utf-8")
    with pytest.raises(InputError, match="不是 zip 檔"):
        scan_zips(["a.zip"], "*", tmp_path)
    with pytest.raises(InputError, match="zip 裡的檔"):
        scan_zips(["a.zip"], " ", tmp_path)


# ── 讀出來的內容 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("encoding", ["cp950", "utf-8"])
@pytest.mark.parametrize("newline", ["\r\n", "\n", "\r"])
@pytest.mark.parametrize("last_newline", [True, False])
def test_zip_reads_the_same_as_the_extracted_file(
    tmp_path, monkeypatch, encoding, newline, last_newline
):
    """zip 裡的檔和解開後用逐行讀取讀：行、行號、行數完全一樣（空行、最後一行沒換行、各種換行）。"""
    monkeypatch.setattr(sources, "ZIP_SEGMENT", 256)  # 切成很多段
    lines = report_lines(seed=3, sections=2, entries=20) * 3
    data = (newline.join(lines) + (newline if last_newline else "")).encode(encoding)
    make_zip(tmp_path / "m.zip", {"202601/HEADER_001.TXT": data})
    (tmp_path / "extracted.txt").write_bytes(data)
    got, found = read(tmp_path, ["m.zip"], "2026*/HEADER*.TXT")
    lf, files = sources.scan("extracted.txt", "text_lines", "auto", tmp_path)
    want = lf.collect()
    assert got["line"].to_list() == want["line"].to_list()
    assert got["__line_no"].to_list() == want["__line_no"].to_list()
    assert found[0].rows == files[0].rows and found[0].encoding == files[0].encoding
    assert got["__source_file"].unique().to_list() == ["m.zip/202601/HEADER_001.TXT"]


def test_zip_strips_the_bom_and_records_hashes(tmp_path):
    data = b"\xef\xbb\xbf" + "第一行\r\n第二行\r\n".encode()
    path = make_zip(tmp_path / "m.zip", {"a.txt": data})
    got, found = read(tmp_path, ["m.zip"], "*.txt")
    assert got["line"].to_list() == ["第一行", "第二行"]
    assert found[0].encoding == "utf-8-sig"
    assert found[0].sha256 == hashlib.sha256(data).hexdigest() and found[0].bytes == len(data)
    assert found[0].archive == "m.zip"
    assert found[0].archive_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_auto_reads_utf8_once_and_cp950_twice(tmp_path, monkeypatch):
    """自動判斷：UTF-8 的檔只解壓一遍；cp950 的先當 UTF-8 讀到第一個不合的位元組就停，再用 cp950 重讀。"""
    opened = []
    original = zip_lines.open_member

    def spy(member):
        opened.append(member.path)
        return original(member)

    monkeypatch.setattr(zip_lines, "open_member", spy)
    late = ("abc\r\n" * 5000 + "結尾\r\n").encode("cp950")  # 很後面才出現中文
    make_zip(
        tmp_path / "m.zip",
        {
            "u.txt": "中文\r\n".encode() * 100,
            "b.txt": "中文\r\n".encode("cp950") * 100,
            "late.txt": late,
        },
    )
    got, found = read(tmp_path, ["m.zip"], "*.txt")
    assert [f.encoding for f in found] == ["cp950", "cp950", "utf-8"]  # b、late、u（依路徑排序）
    assert opened.count("u.txt") == 1 and opened.count("b.txt") == 2
    assert got.filter(pl.col("__source_file") == "m.zip/late.txt")["line"].to_list()[-1] == "結尾"
    archive = str((tmp_path / "m.zip").resolve())
    folders = [p.effective for k, p in sources._cache.items() if k[0] == "zip" and k[1] == archive]
    assert len(folders) == 3 and not [t for f in folders for t in f.glob("*.txt")]  # 文字段都清掉了


def test_zip_in_parallel_matches_single_core(tmp_path, monkeypatch):
    """用工作程序同時轉（主程序依序解壓、切段）和單核心依序轉：結果一樣；暫存的文字段不超過
    「工作程序數 × 2」段加上等著合寫成一個 Parquet 的段。"""
    lines = report_lines(seed=5, sections=3, entries=200)
    path = make_zip(tmp_path / "m.zip", {"a.txt": ("\r\n".join(lines) + "\r\n").encode("cp950")})
    member = zip_lines.matching(path, "a.txt")[0]
    seen = []
    original = sources._stream_segments

    def spy(read, total, name, encoding, folder, *args):
        for segment in original(read, total, name, encoding, folder, *args):
            seen.append(len(list(folder.glob("*.txt"))))
            yield segment

    monkeypatch.setattr(sources, "_stream_segments", spy)
    single = sources._stage_member(
        member, "m.zip/a.txt", "cp950", tmp_path / "s", None, segment=1024
    )
    pool = sources.new_pool(2)
    try:
        seen.clear()
        parallel = sources._stage_member(
            member, "m.zip/a.txt", "cp950", tmp_path / "p", None, pool=pool, segment=1024
        )
    finally:
        pool.shutdown(wait=True)
    as_lines = lambda files: pl.scan_parquet(list(files)).collect()["line"]  # noqa: E731
    assert len(parallel[1]) > 3 and as_lines(parallel[1]).equals(as_lines(single[1]))
    assert parallel[0] == single[0] and parallel[2] == single[2] == len(lines)
    # 在路上的最多 2 × 2 段，加上等著合寫成一個 Parquet 的（最多 ZIP_GROUP 段）
    assert max(seen) <= 4 + sources.ZIP_GROUP


def test_decode_error_names_the_member_and_line(tmp_path):
    good = "門市：北區\r\n".encode("cp950") * 50
    make_zip(tmp_path / "m.zip", {"a/b.txt": good + b"\xff\xff\r\n"})
    with pytest.raises(sources.DecodeError) as err:
        read(tmp_path, ["m.zip"], "a/*", encoding="cp950")
    assert "m.zip/a/b.txt" in str(err.value) and "第 51 行" in str(err.value)


def test_damaged_zip_is_reported(tmp_path):
    """CRC 和 zip 目錄記的不一樣：zip 壞了（不是 Python 的原文）。"""
    path = make_zip(tmp_path / "m.zip", {"a.txt": "中文\r\n".encode() * 100})
    data = bytearray(path.read_bytes())
    at = data.find(b"PK\x01\x02") + 16  # 中央目錄的 CRC
    data[at : at + 4] = b"\x00\x00\x00\x00"
    path.write_bytes(bytes(data))
    with pytest.raises(InputError, match="zip 可能壞了"):
        read(tmp_path, ["m.zip"], "*.txt")


# ── 執行、處理紀錄、Session、驗證 ─────────────────────────────────────────


@pytest.fixture
def example(tmp_path: Path) -> Path:
    write_zip_months(tmp_path / "data")
    shutil.copy(EXAMPLE, tmp_path / EXAMPLE.name)
    return tmp_path


@pytest.mark.parametrize("mode", ["eager", "stream"])
def test_example_runs_and_records_the_zips(example, mode):
    """範例 Recipe：讀每個月的 zip；處理紀錄記 zip 本身的 SHA-256 與略過的 zip；串流＝整份。"""
    recipe = parse_recipe(load_json(example / EXAMPLE.name))
    out = example / f"{mode}.parquet"
    execute(recipe, example, output=out, mode=mode)
    frame = pl.read_parquet(out)
    assert frame.height > 0 and frame["門市"].null_count() == 0
    assert frame["__source_file"].unique().sort().to_list() == [
        "data/2026-01.zip/202601/HEADER_001.TXT",
        "data/2026-01.zip/202601/header_002.txt",
        "data/2026-02.zip/202602/HEADER_001.TXT",
        "data/2026-02.zip/202602/header_002.txt",
    ]
    manifest = json.loads((example / "log" / f"{mode}.manifest.json").read_text(encoding="utf-8"))
    first = manifest["inputs"][0]
    zip_path = example / "data" / "2026-01.zip"
    assert first["archive"] == {
        "file": "data/2026-01.zip",
        "bytes": zip_path.stat().st_size,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    }
    assert manifest["archives_without_matches"] == [{"file": "data/2026-03.zip"}]


def test_stream_equals_eager_for_zip(example):
    recipe = parse_recipe(load_json(example / EXAMPLE.name))
    execute(recipe, example, output=example / "e.parquet", mode="eager")
    execute(recipe, example, output=example / "s.parquet", mode="stream")
    assert pl.read_parquet(example / "e.parquet").equals(pl.read_parquet(example / "s.parquet"))


def test_session_scans_before_loading(example):
    """Session：選 zip 的讀取方式、填 members，畫面狀態馬上有掃描清單；存回 Recipe 時寫 members。"""
    s = Session()
    s.load_recipe(example / EXAMPLE.name)
    scan = s.state()["input"]["zip_scan"]
    assert [z["path"] for z in scan["zips"]] == [f"data/2026-0{m}.zip" for m in (1, 2, 3)]
    assert scan["files"] == 4 and scan["zips"][2]["members"] == [] and scan["problems"] == []
    assert scan["zips"][0]["members"][0]["method"] == "Deflate"
    assert s.state()["actions"]["load"]["ok"]
    s.load_data()
    assert s.state()["input"]["sources"][0]["archive"] == "data/2026-01.zip"
    s.set_input({"members": "沒有/*.TXT"})
    state = s.state()
    assert not state["actions"]["load"]["ok"] and "沒有符合" in state["actions"]["load"]["reason"]
    assert s.recipe_dict()["stages"][0]["input"]["members"] == "沒有/*.TXT"
    s.set_input({"reader": "text_lines"})
    assert "members" not in s.recipe_dict()["stages"][0]["input"]  # 不是 zip 時不寫


def test_members_are_validated():
    data = load_json(EXAMPLE)
    del data["stages"][0]["input"]["members"]
    with pytest.raises(RecipeError, match="members"):
        parse_recipe(data)
    assert any(
        i["path"] == "stages[0].input.members" for i in validator.validate(data, build_spec(), {})
    )
    data = load_json(EXAMPLE)
    data["stages"][0]["input"]["reader"] = "text_lines"
    with pytest.raises(RecipeError, match="members"):
        parse_recipe(data)
    assert any(
        i["path"] == "stages[0].input.members" for i in validator.validate(data, build_spec(), {})
    )


def test_deflate64_is_read_when_the_package_is_installed(tmp_path):
    """Deflate64 的檔：從目錄記的位置讀出壓縮過的原始位元組交給 inflate64，讀出來和原本一樣、核對 CRC。"""
    inflate64 = pytest.importorskip("inflate64")
    import zlib

    raw = ("\r\n".join(report_lines(seed=7, sections=2, entries=200)) + "\r\n").encode("cp950")
    deflater = inflate64.Deflater()
    packed = deflater.deflate(raw) + deflater.flush()
    path = make_zip(tmp_path / "d64.zip", {"a/HEADER.TXT": packed}, zipfile.ZIP_STORED)
    data = bytearray(path.read_bytes())
    for signature, method_at, crc_at in ((b"PK\x03\x04", 8, 14), (b"PK\x01\x02", 10, 16)):
        i = data.find(signature)
        data[i + method_at : i + method_at + 2] = zip_lines.DEFLATE64.to_bytes(2, "little")
        data[i + crc_at : i + crc_at + 4] = zlib.crc32(raw).to_bytes(4, "little")
        data[i + crc_at + 8 : i + crc_at + 12] = len(raw).to_bytes(4, "little")  # 解開後大小
    path.write_bytes(bytes(data))
    member = zip_lines.matching(path, "a/*")[0]
    assert member.method == zip_lines.DEFLATE64 and member.problem() is None
    with zip_lines.open_member(member) as stream:
        assert stream.read() == raw
    got, found = read(tmp_path, ["d64.zip"], "a/*")
    assert found[0].encoding == "cp950" and got.height == raw.count(b"\n")
