"""Ráfaga Ham CallerID v1: AFSK 1200 síncrona con Golay(24,12) y entrelazado.

    reposo mark 24 bits (20 ms) · preámbulo 0101… 16 bits · sincronismo 0x1ACFFC1D (32 bits) ·
    bloque codificado: k palabras Golay(24,12) entrelazadas (k ≥ 8)

Bloque de datos antes de codificar (12·k bits, relleno con ceros):

    versión (3 bits) | tipo (3) | longitud (6) | carga (8·longitud) | CRC-16/CCITT-FALSE (16)

El CRC cubre los 12 bits de cabecera (como 2 bytes, alineados a la derecha) y la carga.
Cada palabra Golay corrige hasta 3 bits; el entrelazado (se transmiten las columnas: bit 0
de todas las palabras, luego el bit 1...) reparte una pérdida de 20 ms (24 bits) en ≤3 bits
por palabra cuando k ≥ 8. El receptor no necesita saber k: prueba de 8 a 24 y se queda con
la que da CRC correcto. Mark (1) = 1200 Hz, space (0) = 2200 Hz, 1200 baudios, fase continua.
"""
from __future__ import annotations

import math

RATE = 8000
BAUD = 1200
MARK, SPACE = 1200.0, 2200.0
SPB = RATE / BAUD
VERSION = 1
TYPE_CALLSIGN = 1
TYPE_TEXT = 2
MAX_LEN = 32
LEAD_BITS = 24
PREAMBLE_BITS = [i & 1 for i in range(16)]            # 0101…
SYNC_WORD = 0x1ACFFC1D
SYNC_BITS = [(SYNC_WORD >> (31 - i)) & 1 for i in range(32)]
MIN_WORDS = 8
MAX_WORDS = 24
ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-")

# --------------------------------------------------------------------------- Golay (24,12)

_GOLAY_POLY = 0xC75      # g(x) = x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1  (Golay 23,12)


def golay_encode(data12: int) -> int:
    """Golay (24,12) sistemático: 12 bits de datos arriba, 11 de paridad (23,12) y 1 de paridad global."""
    r = data12 << 11
    for i in range(22, 10, -1):
        if r & (1 << i):
            r ^= _GOLAY_POLY << (i - 11)
    cw23 = (data12 << 11) | r
    return (cw23 << 1) | (bin(cw23).count("1") & 1)


def _syndrome(word24: int) -> int:
    return (word24 & 0xFFF) ^ (golay_encode(word24 >> 12) & 0xFFF)


def _build_table():
    table = {0: 0}
    for a in range(24):
        table.setdefault(_syndrome(1 << a), 1 << a)
    for a in range(24):
        for b in range(a + 1, 24):
            e = (1 << a) | (1 << b)
            table.setdefault(_syndrome(e), e)
    for a in range(24):
        for b in range(a + 1, 24):
            for c in range(b + 1, 24):
                e = (1 << a) | (1 << b) | (1 << c)
                table.setdefault(_syndrome(e), e)
    return table


_GOLAY_TABLE = _build_table()


def golay_decode(word24: int):
    """→ (datos de 12 bits, nº de bits corregidos) o None si no se puede corregir."""
    e = _GOLAY_TABLE.get(_syndrome(word24))
    if e is None:
        return None
    return (word24 ^ e) >> 12, bin(e).count("1")


# --------------------------------------------------------------------------- trama

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _payload_bits(text: str, kind: int) -> list[int]:
    p = text.strip().upper().encode("ascii")
    if not 0 < len(p) <= MAX_LEN:
        raise ValueError("payload length")
    if kind == TYPE_CALLSIGN and not set(p.decode()) <= ALLOWED:
        raise ValueError("callsign characters")
    hdr = (VERSION << 9) | (kind << 6) | len(p)
    crc = crc16(hdr.to_bytes(2, "big") + p)
    bits = [(hdr >> (11 - i)) & 1 for i in range(12)]
    for b in p + crc.to_bytes(2, "big"):
        bits += [(b >> (7 - i)) & 1 for i in range(8)]
    return bits


def words_for(n_bits: int) -> int:
    return max(MIN_WORDS, -(-n_bits // 12))


def encode_block(text: str, kind: int = TYPE_CALLSIGN) -> list[int]:
    """Bits del bloque codificado (Golay + entrelazado)."""
    bits = _payload_bits(text, kind)
    k = words_for(len(bits))
    bits += [0] * (12 * k - len(bits))
    words = []
    for w in range(k):
        v = 0
        for b in bits[12 * w:12 * w + 12]:
            v = (v << 1) | b
        words.append(golay_encode(v))
    return [(words[w] >> (23 - c)) & 1 for c in range(24) for w in range(k)]


def frame_bits(text: str, kind: int = TYPE_CALLSIGN) -> list[int]:
    return [1] * LEAD_BITS + PREAMBLE_BITS + SYNC_BITS + encode_block(text, kind) + [1, 1]


def modulate(text: str, kind: int = TYPE_CALLSIGN, amp: float = 0.5) -> list[float]:
    bits = frame_bits(text, kind)
    out, phase = [], 0.0
    total = int(math.ceil(len(bits) * SPB))
    for n in range(total):
        b = bits[min(int(n / SPB), len(bits) - 1)]
        phase += 2 * math.pi * (MARK if b else SPACE) / RATE
        out.append(amp * math.sin(phase))
    return out


def duration_ms(text: str) -> float:
    return len(frame_bits(text)) * 1000 / BAUD


# --------------------------------------------------------------------------- demodulación

def _discriminator(x):
    n = len(x)
    w = int(round(SPB))
    cm = [math.cos(2 * math.pi * MARK * i / RATE) for i in range(n)]
    sm = [math.sin(2 * math.pi * MARK * i / RATE) for i in range(n)]
    cs = [math.cos(2 * math.pi * SPACE * i / RATE) for i in range(n)]
    ss = [math.sin(2 * math.pi * SPACE * i / RATE) for i in range(n)]
    d = [0.0] * n
    a = b = c = e = 0.0
    half = w // 2
    for i in range(n):
        v = x[i]
        a += v * cm[i]; b += v * sm[i]; c += v * cs[i]; e += v * ss[i]
        if i >= w:
            u = x[i - w]
            a -= u * cm[i - w]; b -= u * sm[i - w]; c -= u * cs[i - w]; e -= u * ss[i - w]
        j = i - half
        if j >= 0:
            d[j] = math.hypot(a, b) - math.hypot(c, e)
    return d


_REF = PREAMBLE_BITS[-8:] + SYNC_BITS          # lo que se busca: cola del preámbulo + sincronismo


def _find_sync(d, start, end, max_err=3):
    """Posiciones (muestra donde empieza el 1.er bit de _REF) con ≤max_err errores."""
    found = []
    nref = len(_REF)
    span = int(nref * SPB) + 2
    o = start
    while o < end and o + span < len(d):
        err = 0
        for k in range(nref):
            bit = 1 if d[int(o + (k + 0.5) * SPB)] > 0 else 0
            if bit != _REF[k]:
                err += 1
                if err > max_err:
                    break
        if err <= max_err:
            # afinar: la mejor fase dentro de ±3 muestras (menos errores, y si empata, más margen)
            best = None
            for f in range(o, o + 7):
                if f + span >= len(d):
                    break
                e2 = 0; marg = 0.0
                for k in range(nref):
                    v = d[int(f + (k + 0.5) * SPB)]
                    if (v > 0) != (_REF[k] == 1):
                        e2 += 1
                    marg += abs(v)
                key = (e2, -marg)
                if best is None or key < best[0]:
                    best = (key, f)
            found.append(best[1])
            o = best[1] + span
            continue
        o += 1
    return found


def _read_bits(d, pos, n):
    """Lee n bits desde pos siguiendo el reloj con los cruces por cero."""
    bits = []
    prev = None
    for k in range(n):
        c = pos + 0.5 * SPB
        i = int(c)
        if i >= len(d):
            break
        v = 1 if d[i] > 0 else 0
        if prev is not None and v != prev:
            # el cruce debería caer en «pos» (inicio del bit): corregir la fase un poco
            lo, hi = int(pos - SPB / 2), int(pos + SPB / 2)
            for j in range(max(lo, 1), min(hi, len(d))):
                if (d[j - 1] > 0) != (d[j] > 0):
                    pos += 0.25 * (j - pos)
                    break
        bits.append(v)
        prev = v
        pos += SPB
    return bits


def _decode_block(bits):
    """Prueba k = 8..24 palabras; devuelve (tipo, texto, k, corregidos) o None."""
    for k in range(MIN_WORDS, MAX_WORDS + 1):
        if len(bits) < 24 * k:
            break
        words = [0] * k
        for c in range(24):
            for w in range(k):
                words[w] = (words[w] << 1) | bits[c * k + w]
        data = []
        fixed = 0
        ok = True
        for w in words:
            r = golay_decode(w)
            if r is None:
                ok = False
                break
            data.append(r[0]); fixed += r[1]
        if not ok:
            continue
        hdr = data[0]
        ver, kind, n = hdr >> 9, (hdr >> 6) & 7, hdr & 0x3F
        if ver != VERSION or not 0 < n <= MAX_LEN or words_for(12 + 8 * n + 16) != k:
            continue
        flat = []
        for v in data[1:]:
            flat += [(v >> (11 - i)) & 1 for i in range(12)]
        by = bytes(int("".join(map(str, flat[8 * i:8 * i + 8])), 2) for i in range(n + 2))
        if crc16(hdr.to_bytes(2, "big") + by[:n]) != int.from_bytes(by[n:n + 2], "big"):
            continue
        try:
            return kind, by[:n].decode("ascii"), k, fixed
        except UnicodeDecodeError:
            return None
    return None


def demodulate(x, stats=None):
    """[(tipo, texto, muestra_inicio)] de las ráfagas válidas."""
    if len(x) < 200:
        return []
    d = _discriminator(x)
    out = []
    for pos in _find_sync(d, 0, len(d)):
        start = pos + len(_REF) * SPB
        bits = _read_bits(d, start, 24 * MAX_WORDS)
        r = _decode_block(bits)
        if r:
            kind, text, k, fixed = r
            if stats is not None:
                stats.append(fixed)
            begin = int(pos - (LEAD_BITS + len(PREAMBLE_BITS) - 8) * SPB)
            out.append((kind, text, max(0, begin)))
    return out


# --------------------------------------------------------------------------- flujo

def _goertzel(x, f):
    c = 2 * math.cos(2 * math.pi * f / RATE)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + c * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - c * s1 * s2


def mark_purity(x):
    n = len(x)
    e = sum(v * v for v in x)
    if n == 0 or e < 1e-6 * n:
        return 0.0
    return _goertzel(x, MARK) * 2 / (n * e)


def frame_samples(text: str) -> int:
    return int(math.ceil(len(frame_bits(text)) * SPB))


class StreamDecoder:
    """Igual que en v0: busca el reposo de 1200 Hz y, al verlo, demodula cuando ha llegado la ráfaga."""

    WIN, HOP = 80, 40
    FIRST_WAIT = 13      # tramas de 20 ms hasta tener una ráfaga corta entera (~240 ms)
    LAST_WAIT = 30       # la más larga (32 caracteres, ~560 ms)

    def __init__(self, purity=0.5, keep_frames=50):
        self.purity = purity
        self.keep = keep_frames
        self.frames = []
        self.n = 0
        self.tail = []
        self.pending = []
        self.done_until = -1

    def feed(self, frame):
        n = self.n
        self.n += 1
        self.frames.append((n, frame))
        if len(self.frames) > self.keep:
            self.frames.pop(0)
        x = self.tail + list(frame)
        self.tail = list(frame[-self.WIN:])
        if n > self.done_until and not self.pending:
            for i in range(0, len(x) - self.WIN + 1, self.HOP):
                if mark_purity(x[i:i + self.WIN]) > self.purity:
                    self.pending.append((n - 1, self.FIRST_WAIT))
                    break
        out, keep = [], []
        for start, wait in self.pending:
            if n - start < wait:
                keep.append((start, wait))
                continue
            hits = self._scan(start, n)
            if hits:
                out += hits
                self.done_until = max(h[2] for h in hits)
            elif wait < self.LAST_WAIT:
                keep.append((start, wait + 1))
        self.pending = keep
        return out

    def flush(self):
        out = []
        for start, _ in self.pending:
            out += self._scan(start, self.n - 1)
        self.pending = []
        return out

    def _scan(self, first, last):
        sel = [(k, f) for k, f in self.frames if first <= k <= last]
        if not sel:
            return []
        k0 = sel[0][0]
        x = [v for _, f in sel for v in f]
        out = []
        for kind, t, start in demodulate(x):
            if kind != TYPE_CALLSIGN:
                continue
            end = start + frame_samples(t)
            out.append((t, k0 + start // 160, k0 + end // 160))
        return out
