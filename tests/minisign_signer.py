"""測試用的 minisign 簽章工具（只給測試與 CI 的拋棄式金鑰用，正式發版用真正的 minisign）。

Ed25519 照 RFC 8032 的參考寫法（純 Python、很慢、不是常數時間），只簽 64 bytes 的雜湊，
所以夠快。產生的公鑰與簽章格式和 minisign 一樣（預先用 BLAKE2b-512 雜湊的 "ED" 格式），
用 Rust 模組的 ``verify_minisign`` 驗得過，等於也交叉驗證了兩邊的格式。
"""

from __future__ import annotations

import base64
import hashlib
import os

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _recover_x(y: int, sign: int) -> int:
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if x % 2 != sign:
        x = P - x
    return x


_BY = 4 * pow(5, P - 2, P) % P
B = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _encode(p) -> bytes:
    zinv = pow(p[2], P - 2, P)
    x, y = p[0] * zinv % P, p[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    public = _encode(_mul(a, B))
    r = _sha512_int(prefix + message) % L
    big_r = _encode(_mul(r, B))
    k = _sha512_int(big_r + public + message) % L
    return big_r + ((r + k * a) % L).to_bytes(32, "little")


class SigningKey:
    """一把拋棄式的 minisign 金鑰。"""

    def __init__(self, seed: bytes | None = None, key_id: bytes | None = None) -> None:
        self.seed = seed or os.urandom(32)
        self.key_id = key_id or os.urandom(8)
        self.public = _encode(_mul(_expand(self.seed)[0], B))

    @property
    def public_key(self) -> str:
        """minisign.pub 的第二行（base64）。"""
        return base64.b64encode(b"Ed" + self.key_id + self.public).decode()

    def sign(self, data: bytes, trusted_comment: str) -> str:
        """.minisig 檔的全文。"""
        digest = hashlib.blake2b(data, digest_size=64).digest()
        signature = _sign(self.seed, digest)
        comment = trusted_comment.encode()
        global_signature = _sign(self.seed, signature + comment)
        return (
            "untrusted comment: signature from a test key\n"
            + base64.b64encode(b"ED" + self.key_id + signature).decode()
            + "\n"
            + f"trusted comment: {trusted_comment}\n"
            + base64.b64encode(global_signature).decode()
            + "\n"
        )
