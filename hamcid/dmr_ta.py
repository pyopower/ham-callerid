"""Talker Alias DMR dentro de la voz (LC embebido, FLCO 4-7), en Python puro.

Las ráfagas de voz B-E de cada supertrama llevan 32 bits de LC embebido. Una radio con
Talker Alias alterna supertramas con el LC de grupo y supertramas con el alias (cabecera
FLCO 4 y bloques 5-7). ``TaInjector`` hace lo mismo con las tramas DMRD de un peer HBP:
deja pasar una supertrama con el LC original y sustituye las siguientes por el alias,
en bucle, para que una radio que entre a mitad de llamada reciba las dos cosas.

Basado en ``talker_alias.py`` (parche TA de junio de 2026, validado contra el firmware
MD-380), sin ``bitarray`` ni ``dmr_utils3`` (las pruebas lo comparan bit a bit con aquel).
"""
from __future__ import annotations

FID = 0x00
MAX_LEN = 27

# entrelazado del LC embebido (ETSI TS 102 361-1, 9.1.6): posición k de la salida ← bit _IDX[k]
_IDX = [(b * 4 + j) + 16 * r for b in range(4) for j in range(4) for r in range(8)]


def _bits(data: bytes) -> list[int]:
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def _hamming_16114(d: list[int]) -> list[int]:
    return [
        d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8],
        d[1] ^ d[2] ^ d[3] ^ d[4] ^ d[6] ^ d[8] ^ d[9],
        d[2] ^ d[3] ^ d[4] ^ d[5] ^ d[7] ^ d[9] ^ d[10],
        d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7] ^ d[10],
        d[0] ^ d[2] ^ d[5] ^ d[6] ^ d[8] ^ d[9] ^ d[10],
    ]


def _matrix(lc: bytes) -> list[int]:
    """LC de 9 bytes → matriz BPTC de 128 bits (checksum de 5 bits + Hamming + paridad)."""
    b = _bits(lc)                                    # 72 bits
    cs = sum(lc) % 31
    csum = [(cs >> (4 - i)) & 1 for i in range(5)]
    for pos, bit in zip((32, 43, 54, 65, 76), csum):
        b.insert(pos, bit)                           # 77
    for row in range(0, 112, 16):
        for k, hb in enumerate(_hamming_16114(b[row:row + 11])):
            b.insert(row + 11 + k, hb)               # 112
    for col in range(16):
        b.insert(112 + col, b[col] ^ b[col + 16] ^ b[col + 32] ^ b[col + 48]
                 ^ b[col + 64] ^ b[col + 80] ^ b[col + 96])   # 128
    return b


def encode_emblc(lc: bytes) -> dict[int, int]:
    """LC de 9 bytes → {1..4: fragmento de 32 bits (entero)} para las ráfagas B..E."""
    m = _matrix(lc)
    out = [m[i] for i in _IDX]
    frags = {}
    for v in range(4):
        val = 0
        for bit in out[v * 32:(v + 1) * 32]:
            val = (val << 1) | bit
        frags[v + 1] = val
    return frags


def build_ta_lcs(text: str, fmt: int = 1) -> list[bytes]:
    """LCs del alias: cabecera (FLCO 4) + los bloques necesarios (FLCO 5-7)."""
    raw = text.encode("latin-1" if fmt == 1 else "utf-8", errors="replace")[:MAX_LEN]
    n = len(raw)
    p = raw + b"\x00" * (MAX_LEN - n)
    lcs = [bytes([0x04, FID, ((fmt & 3) << 6) | ((n & 0x1F) << 1)]) + p[0:6]]
    for i, flco in enumerate((0x05, 0x06, 0x07)):
        if n > 6 + 7 * i:
            lcs.append(bytes([flco, FID]) + p[6 + 7 * i:13 + 7 * i])
    return lcs


def ta_superframes(text: str, fmt: int = 1) -> list[dict[int, int]]:
    return [encode_emblc(lc) for lc in build_ta_lcs(text, fmt)]


# --------------------------------------------------------------------------- DMRD

def _get_emb(payload: bytes) -> int:
    v = int.from_bytes(payload, "big")
    return (v >> (264 - 148)) & 0xFFFFFFFF


def _set_emb(payload: bytes, frag: int) -> bytes:
    v = int.from_bytes(payload, "big")
    shift = 264 - 148
    v = (v & ~(0xFFFFFFFF << shift)) | (frag << shift)
    return v.to_bytes(33, "big")


class TaInjector:
    """Inserta el alias en las tramas DMRD de voz de las llamadas que se le indiquen."""

    def __init__(self, fmt: int = 1):
        self.fmt = fmt
        self.streams: dict[bytes, dict] = {}

    def start(self, stream_id: bytes, text: str | None, dmrd: bytes | None = None):
        """Programa la llamada: LC de grupo (rehecho con el TG y el origen de la trama) y el alias,
        alternando por supertramas. Se sustituyen TODAS las supertramas, para no mezclar con un
        alias que ya trajera la fuente (MMDVM_Bridge genera el suyo propio). Sin texto todavía
        (``None``) solo va el LC de grupo hasta que se llame a ``set_text``."""
        group = None
        if dmrd is not None and len(dmrd) >= 16 and not (dmrd[15] & 0x40):   # llamada de grupo
            lc = bytes([0x00, FID, 0x00]) + dmrd[8:11] + dmrd[5:8]
            group = encode_emblc(lc)
        if group is None and not text:
            return
        self.streams[stream_id] = {"group": group, "sched": [group], "sf": -1, "cur": None}
        if text:
            self.set_text(stream_id, text)
        while len(self.streams) > 16:
            self.streams.pop(next(iter(self.streams)))

    def set_text(self, stream_id: bytes, text: str) -> bool:
        """Pone (o cambia) el alias de una llamada en curso; vale desde la supertrama siguiente."""
        st = self.streams.get(stream_id)
        if st is None:
            return False
        st["sched"] = [st["group"]] + ta_superframes(text, self.fmt)
        return True

    def process(self, d: bytes) -> bytes:
        """Paquete DMRD completo → paquete (con el alias en las ráfagas B-E si toca)."""
        if len(d) < 53 or d[:4] != b"DMRD":
            return d
        st = self.streams.get(d[16:20])
        if st is None:
            return d
        bits = d[15]
        frame_type = (bits >> 4) & 0x03
        vseq = bits & 0x0F
        if frame_type == 2 and vseq == 2:            # fin de llamada
            self.streams.pop(d[16:20], None)
            return d
        if frame_type != 0 or not 1 <= vseq <= 4:    # solo ráfagas de voz B..E
            return d
        if vseq == 1:
            st["sf"] += 1
            st["cur"] = st["sched"][st["sf"] % len(st["sched"])]
        cur = st["cur"]
        if cur is None:
            return d
        return d[:20] + _set_emb(d[20:53], cur[vseq]) + d[53:]
