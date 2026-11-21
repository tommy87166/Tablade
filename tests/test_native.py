"""Rust 模組 tablade_native（native/，解 zip 裡的 Deflate64）。沒有編（uv sync --group native）時略過。"""

from __future__ import annotations

import tomllib
import zlib
from pathlib import Path

import pytest

native = pytest.importorskip("tablade_native", reason="沒有 Rust 模組（uv sync --group native）")
ROOT = Path(__file__).resolve().parents[1]


def huffman_only(raw: bytes) -> bytes:
    """只用 Huffman、不做重複比對的 Deflate：同時也是合法的 Deflate64（fixtures.write_deflate64_zip 也這樣做）。"""
    packer = zlib.compressobj(6, zlib.DEFLATED, -15, 9, zlib.Z_HUFFMAN_ONLY)
    return packer.compress(raw) + packer.flush()


def test_versions_match_the_lock_files():
    lock = tomllib.loads((ROOT / "native" / "Cargo.lock").read_text(encoding="utf-8"))
    locked = {p["name"]: p["version"] for p in lock["package"]}
    assert locked["deflate64"] == native.DEFLATE64_VERSION
    assert locked["minisign-verify"] == native.MINISIGN_VERIFY_VERSION
    cargo = tomllib.loads((ROOT / "native" / "Cargo.toml").read_text(encoding="utf-8"))
    assert native.__version__ == cargo["package"]["version"]


@pytest.mark.parametrize("piece", [1, 7, 997, 1 << 20])
def test_inflater_gives_the_same_result_for_any_pieces(piece):
    raw = ("門市：北區門市\r\n115/01/05 A001 原子筆 3 75\r\n" * 3000).encode("cp950")
    packed = huffman_only(raw)
    inflater = native.Inflater()
    out = b"".join(inflater.inflate(packed[i : i + piece]) for i in range(0, len(packed), piece))
    assert out == raw and inflater.finished


def test_inflater_rejects_broken_data():
    with pytest.raises(ValueError, match="invalid deflate64 data"):
        native.Inflater().inflate(b"\xff\xff\xff\xff")
