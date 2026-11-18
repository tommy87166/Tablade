"""zip 裡的文字檔（讀取方式 ``zip_lines``，docs/zip-lines-plan.md）：讀 zip 的目錄、比對要讀的檔、依序讀出內容。

- **只讀目錄就知道有哪些檔**（``zipfile.ZipFile.infolist()`` 讀的是 zip 尾端的目錄），幾 GB 的 zip 也很快。
- **zip 裡的路徑**：``\\`` 換成 ``/``、拿掉開頭的 ``./``，資料夾項目不算。檔名有 UTF-8 標記的照用；
  沒有的，用 zip 原本的位元組依序試 UTF-8 → cp950，都不行才用 cp437（zip 的預設）。
- **要讀的檔**（``members``）：比對整理過的完整路徑、不分大小寫；``*`` 不跨過 ``/``，``**`` 是任何層數的資料夾。
- **每個檔各自記壓縮方式**：不壓縮、Deflate、bzip2、LZMA 用標準函式庫的 ``zipfile``；Deflate64 用 ``inflate64``
  套件（沒有裝時那種檔標成不支援）。有加密旗標的檔一律不支援（程式不碰密碼）。

只讀 zip、不寫檔：解出來的內容交給 ``sources`` 一段一段轉成暫存 Parquet。暫存一律用雜湊命名，
不用 zip 裡的路徑當檔名（zip 裡的 ``../`` 不會寫到別的地方）。
"""

from __future__ import annotations

import contextlib
import io
import re
import struct
import unicodedata
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..i18n import t

STORED, DEFLATED, DEFLATE64, BZIP2, LZMA = 0, 8, 9, 12, 14
METHOD_NAMES = {
    STORED: "Stored",
    DEFLATED: "Deflate",
    DEFLATE64: "Deflate64",
    BZIP2: "bzip2",
    LZMA: "LZMA",
}
"""壓縮方式的數字（zip 目錄裡每個檔各自記的）與名稱。"""
ZIPFILE_METHODS = {STORED, DEFLATED, BZIP2, LZMA}
"""標準函式庫的 ``zipfile`` 解得開的壓縮方式。"""
UTF8_FLAG = 0x800
"""zip 的檔名旗標：檔名是 UTF-8。"""
CHUNK = 1 << 20


class ZipError(ValueError):
    """zip 讀不了（不是 zip、壞了、不支援的壓縮方式…），訊息已經是畫面上的文字。"""


def deflate64_available() -> bool:
    """裝了解 Deflate64 的套件（``inflate64``）沒有。"""
    try:
        import inflate64  # noqa: F401
    except ImportError:
        return False
    return True


def method_name(method: int) -> str:
    return METHOD_NAMES.get(method, t(f"不明（{method}）", f"unknown ({method})"))


@dataclass(frozen=True)
class Member:
    """zip 裡的一個檔。"""

    archive: Path
    """zip 檔。"""
    path: str
    """整理過的 zip 裡的路徑（``/`` 分隔，NFC）。"""
    info: zipfile.ZipInfo
    size: int
    """解開後多大（bytes，zip 目錄記的）。"""
    compressed: int
    crc: int
    method: int
    encrypted: bool

    def problem(self) -> str | None:
        """這個檔讀不了的原因（可以讀時是 None）。"""
        if self.encrypted:
            return t(
                "有密碼（請先解壓縮，程式不處理密碼）",
                "password-protected (extract it first; Tablade doesn't handle passwords)",
            )
        if self.method == DEFLATE64 and not deflate64_available():
            return t(
                "Deflate64 壓縮：需要 Deflate64 的套件（或用 7-Zip 重新壓成一般的 zip）",
                "Deflate64-compressed: needs the Deflate64 package "
                "(or recompress it as a regular zip with 7-Zip)",
            )
        if self.method not in ZIPFILE_METHODS | {DEFLATE64}:
            return t(
                f"不支援的壓縮方式：{method_name(self.method)}（請先解壓縮）",
                f"unsupported compression: {method_name(self.method)} (extract it first)",
            )
        return None


def _raw_name(info: zipfile.ZipInfo) -> str:
    """zip 裡記的檔名（還沒把 Windows 的 ``\\`` 換掉的那個），依編碼規則轉成文字。"""
    original = getattr(info, "orig_filename", info.filename)
    if info.flag_bits & UTF8_FLAG:
        return original
    raw = original.encode(
        "cp437"
    )  # zipfile 沒有 UTF-8 標記時用 cp437 解：位元組一個對一個，還得回來
    for encoding in ("utf-8", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return original


def member_path(info: zipfile.ZipInfo) -> str | None:
    """整理過的 zip 裡的路徑；資料夾項目是 None。

    先轉成文字再把 ``\\`` 換成 ``/``：cp950 的中文字第二個位元組可能是 0x5C（``\\``），轉成文字後才不會切壞。
    """
    name = unicodedata.normalize("NFC", _raw_name(info)).replace("\\", "/")
    if name.endswith("/"):
        return None
    while name.startswith("./"):
        name = name[2:]
    name = name.lstrip("/")
    return name or None


def compile_members(pattern: str) -> re.Pattern[str]:
    """``members`` 轉成正規表示式：完整路徑、不分大小寫；``*`` 不跨 ``/``，``**`` 是任何層數的資料夾（含 0 層）。"""
    text = unicodedata.normalize("NFC", pattern.strip()).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if text.startswith("**/", i):
            out.append("(?:[^/]*/)*")
            i += 3
        elif text.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            end = text.find("]", i + 2)
            if end < 0:
                out.append(re.escape(ch))
                i += 1
                continue
            body = text[i + 1 : end]
            negate = body[:1] in ("!", "^")
            body = body[1:] if negate else body
            out.append(f"[{'^' if negate else ''}{body.replace('\\', '\\\\')}]")
            i = end + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


def list_members(archive: Path) -> list[Member]:
    """zip 裡所有的檔（資料夾項目不算），依整理過的路徑排序。只讀 zip 的目錄。"""
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, UnicodeDecodeError):  # 標了 UTF-8 的檔名卻不是 UTF-8 也算壞了
        raise ZipError(
            t(
                f"「{archive.name}」不是 zip 檔（或已經壞了）",
                f'"{archive.name}" is not a zip file (or is damaged)',
            )
        ) from None
    except OSError as err:
        raise ZipError(
            t(f"讀不了「{archive.name}」：{err}", f'Cannot read "{archive.name}": {err}')
        ) from None
    members = []
    for info in infos:
        path = member_path(info)
        if path is None:
            continue
        members.append(
            Member(
                archive,
                path,
                info,
                info.file_size,
                info.compress_size,
                info.CRC,
                info.compress_type,
                bool(info.flag_bits & 0x1),
            )
        )
    return sorted(members, key=lambda m: m.path)


def matching(archive: Path, members: str) -> list[Member]:
    """zip 裡符合 ``members`` 的檔（依路徑排序）。"""
    pattern = compile_members(members)
    return [m for m in list_members(archive) if pattern.fullmatch(m.path)]


def _damaged(member: Member, err: object) -> ZipError:
    return ZipError(
        t(
            f"「{member.archive.name}」裡的「{member.path}」讀不了（zip 可能壞了）：{err}",
            f'Cannot read "{member.path}" in "{member.archive.name}" (the zip may be damaged): {err}',
        )
    )


class _Deflate64Reader(io.RawIOBase):
    """Deflate64 的檔：從目錄記的位置讀出壓縮過的原始位元組，交給 ``inflate64`` 一邊解一邊讀；讀完核對 CRC。"""

    def __init__(self, member: Member) -> None:
        import inflate64

        self._member = member
        self._file = member.archive.open("rb")
        self._file.seek(member.info.header_offset)
        header = self._file.read(30)
        if len(header) < 30 or header[:4] != b"PK\x03\x04":
            raise _damaged(member, t("找不到檔案的標頭", "local file header not found"))
        name_length, extra_length = struct.unpack("<HH", header[26:30])
        self._file.seek(member.info.header_offset + 30 + name_length + extra_length)
        self._left = member.compressed
        self._inflater = inflate64.Inflater()
        self._buffer = bytearray()
        self._crc = 0
        self._size = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        while (size < 0 or len(self._buffer) < size) and self._left > 0:
            chunk = self._file.read(min(CHUNK, self._left))
            if not chunk:
                raise _damaged(self._member, t("檔案提早結束", "unexpected end of file"))
            self._left -= len(chunk)
            self._buffer += self._inflater.inflate(chunk)
        take = len(self._buffer) if size < 0 else min(size, len(self._buffer))
        data = bytes(self._buffer[:take])
        del self._buffer[:take]
        self._crc = zlib.crc32(data, self._crc)
        self._size += len(data)
        if (
            not data
            and self._left == 0
            and (self._crc != self._member.crc or self._size != self._member.size)
        ):
            raise _damaged(self._member, t("CRC 不符", "CRC mismatch"))
        return data

    def close(self) -> None:
        self._file.close()
        super().close()


@contextlib.contextmanager
def open_member(member: Member) -> Iterator[BinaryIO]:
    """依序讀出 zip 裡一個檔的內容（解壓後的位元組）；讀到結尾時核對 CRC，不符丟出 ``ZipError``。"""
    problem = member.problem()
    if problem is not None:
        raise ZipError(
            t(
                f"「{member.archive.name}」裡的「{member.path}」：{problem}",
                f'"{member.path}" in "{member.archive.name}": {problem}',
            )
        )
    if member.method == DEFLATE64:
        reader = _Deflate64Reader(member)
        try:
            yield reader  # type: ignore[misc]
        finally:
            reader.close()
        return
    try:
        zf = zipfile.ZipFile(member.archive)
    except (zipfile.BadZipFile, OSError) as err:
        raise _damaged(member, err) from None
    with zf, zf.open(member.info) as stream:
        yield _Checked(stream, member)  # type: ignore[misc]


class _Checked:
    """把 zipfile 讀檔時的錯誤（CRC 不符、壓縮資料壞了）換成看得懂的 ``ZipError``。"""

    def __init__(self, stream: BinaryIO, member: Member) -> None:
        self._stream, self._member = stream, member

    def read(self, size: int = -1) -> bytes:
        try:
            return self._stream.read(size)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as err:
            raise _damaged(self._member, err) from None


def estimate_staging(members: list[Member], workers: int, segment: int, group: int = 1) -> int:
    """讀這些檔大約要多少暫存空間（bytes）：寫好的 Parquet 用「解開後大小 ÷ 5」估（實測約 1/10，保守一點），
    加上轉碼途中的文字段：最多「工作程序數 × 2」段在路上，再加上等著合寫成一個 Parquet 的 ``group`` 段；
    不會超過最大的那個檔（一次只轉一個檔）。"""
    if not members:
        return 0
    in_flight = min((2 * max(1, workers) + group) * segment, max(m.size for m in members))
    return sum(m.size for m in members) // 5 + in_flight
