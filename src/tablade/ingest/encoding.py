"""編碼偵測與轉換（DESIGN.md §5.2）。

Polars 的 ``read_csv`` 只接受 UTF-8，所以轉碼在 Python 層完成，之後一律以 UTF-8 交給 Polars。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

AUTO_ORDER = ("utf-8-sig", "cp950")
SUPPORTED = ("auto", "utf-8", "utf-8-sig", "cp950", "big5hkscs")


class DecodeError(ValueError):
    """無法用指定（或自動偵測）的編碼解碼。"""


@dataclass(frozen=True)
class Decoded:
    text: str
    encoding: str
    """實際使用的編碼。UTF-8 有 BOM 時為 ``utf-8-sig``，沒有時為 ``utf-8``。"""


def _describe_failure(raw: bytes, encoding: str, err: UnicodeDecodeError) -> str:
    line_no = raw.count(b"\n", 0, err.start) + 1
    return (
        f"{encoding} 在第 {err.start} 個位元組（第 {line_no} 行，0x{raw[err.start]:02X}）無法解碼"
    )


def _decode(raw: bytes, encoding: str) -> Decoded:
    text = raw.decode(encoding)
    if encoding in ("utf-8", "utf-8-sig"):
        encoding = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
        text = text.removeprefix("\ufeff")
    return Decoded(text, encoding)


def decode_bytes(raw: bytes, encoding: str = "auto") -> Decoded:
    """把原始位元組解碼成文字。

    ``encoding="auto"`` 依序嘗試 ``utf-8-sig`` → ``cp950``，都失敗就丟出 :class:`DecodeError`，
    訊息中列出每種編碼第一個無法解碼的位元組位置與行號。
    """
    if encoding not in SUPPORTED:
        raise DecodeError(f"不支援的編碼：{encoding}（可用：{', '.join(SUPPORTED)}）")

    if encoding != "auto":
        try:
            return _decode(raw, encoding)
        except UnicodeDecodeError as err:
            raise DecodeError(_describe_failure(raw, encoding, err)) from None

    failures = []
    for candidate in AUTO_ORDER:
        try:
            return _decode(raw, candidate)
        except UnicodeDecodeError as err:
            failures.append(_describe_failure(raw, candidate, err))
    raise DecodeError("無法判斷編碼：" + "；".join(failures))


def to_utf8(raw: bytes, encoding: str = "auto") -> tuple[bytes, str]:
    """轉成不含 BOM 的 UTF-8 位元組，回傳 ``(utf8_bytes, 偵測到的編碼)``。"""
    decoded = decode_bytes(raw, encoding)
    if decoded.encoding == "utf-8":
        return raw, decoded.encoding
    return decoded.text.encode("utf-8"), decoded.encoding
