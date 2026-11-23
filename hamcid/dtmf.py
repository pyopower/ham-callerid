"""DTMF PTT-ID (fase 1): generador y decodificador para audio de 8 kHz.

El decodificador trabaja por bloques de 10 ms (80 muestras), como haría dentro de un
nodo ASL o de un puente, y entrega las secuencias ``D<ID>`` completas junto con los
bloques que conviene silenciar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

RATE = 8000
BLOCK = 80  # 10 ms

LOW = (697, 770, 852, 941)
HIGH = (1209, 1336, 1477, 1633)
KEYS = ("123A", "456B", "789C", "*0#D")

PREFIX = "D"
TERMINATOR = "#"


def _coef(f: float, n: int = BLOCK * 2) -> float:
    k = round(n * f / RATE)
    return 2 * math.cos(2 * math.pi * k / n)


_COEFS = {f: _coef(f) for f in LOW + HIGH}


def tone(key: str, ms: int = 60, amp: float = 0.35) -> list[float]:
    """Muestras de un dígito DTMF."""
    r = next(i for i, row in enumerate(KEYS) if key in row)
    c = KEYS[r].index(key)
    fl, fh = LOW[r], HIGH[c]
    n = RATE * ms // 1000
    return [amp * (math.sin(2 * math.pi * fl * t / RATE) + math.sin(2 * math.pi * fh * t / RATE)) / 2
            for t in range(n)]


def encode(ident: str, tone_ms: int = 60, gap_ms: int = 50, terminator: bool = True) -> list[float]:
    """Audio de la secuencia PTT-ID ``D<ident>[#]``."""
    seq = PREFIX + ident + (TERMINATOR if terminator else "")
    out: list[float] = []
    for k in seq:
        out += tone(k, tone_ms)
        out += [0.0] * (RATE * gap_ms // 1000)
    return out


def _power(samples: list[float], f: float) -> float:
    c = _COEFS[f]
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + c * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - c * s1 * s2


def detect_block(samples: list[float]) -> str | None:
    """Dígito presente en un bloque de 20 ms (o None)."""
    energy = sum(x * x for x in samples)
    if energy < 1e-4 * len(samples):
        return None
    pl = [_power(samples, f) for f in LOW]
    ph = [_power(samples, f) for f in HIGH]
    il = max(range(4), key=pl.__getitem__)
    ih = max(range(4), key=ph.__getitem__)
    lo, hi = pl[il], ph[ih]
    # cada tono debe dominar a los demás de su grupo (8 dB) ...
    if any(p * 6.3 > lo for i, p in enumerate(pl) if i != il):
        return None
    if any(p * 6.3 > hi for i, p in enumerate(ph) if i != ih):
        return None
    # ... twist dentro de ±8 dB ...
    if not (1 / 6.3 < hi / lo < 6.3):
        return None
    # ... y los dos tonos deben ser la mayor parte de la energía del bloque (no voz)
    n = len(samples)
    if (lo + hi) * 2 / (n * n) < 0.6 * energy / n:
        return None
    return KEYS[il][ih]


@dataclass
class Result:
    ident: str
    start_block: int
    end_block: int


@dataclass
class Decoder:
    """Decodificador en flujo. ``feed`` recibe bloques de 10 ms (80 muestras)."""

    min_blocks: int = 4          # dígito estable >= 40 ms
    end_silence_blocks: int = 30  # 300 ms sin tonos cierran la secuencia
    lookup: callable | None = None  # ID -> indicativo; si devuelve None se descarta
    results: list[Result] = field(default_factory=list)
    mute: set[int] = field(default_factory=set)  # bloques con tono (para silenciar)

    _n: int = 0
    _prev: list[float] = field(default_factory=list)
    _cand: str | None = None
    _run: int = 0
    _gap: int = 0
    _last: str | None = None
    _seq: str = ""
    _start: int = -1

    def feed(self, block: list[float]) -> Result | None:
        win = self._prev + block  # ventana deslizante de 20 ms
        self._prev = block
        d = detect_block(win) if len(win) == 2 * BLOCK else None
        i = self._n
        self._n += 1
        if d is not None:
            self.mute.add(i)
            self.mute.add(i - 1)
        if d == self._cand and d is not None:
            self._run += 1
        else:
            self._cand, self._run = d, 1 if d else 0
        out = None
        if d is not None and self._run == self.min_blocks and d != self._last:
            out = self._digit(d, i)
            self._last = d
            self._gap = 0
        elif d is None:
            self._gap += 1
            if self._gap >= 2:
                self._last = None  # permite repetir el mismo dígito tras una pausa
            if self._seq and self._gap >= self.end_silence_blocks:
                out = self._finish(i)
        return out

    def _digit(self, d: str, i: int) -> Result | None:
        if not self._seq:
            if d == PREFIX:
                self._seq, self._start = d, i
            return None
        if d == TERMINATOR:
            return self._finish(i)
        if d.isdigit():
            self._seq += d
            return None
        self._seq = ""  # otra letra: no es un PTT-ID
        return None

    def _finish(self, i: int) -> Result | None:
        ident = self._seq[len(PREFIX):]
        self._seq = ""
        if not ident:
            return None
        if self.lookup is not None and self.lookup(ident) is None:
            return None
        r = Result(ident, self._start, i)
        self.results.append(r)
        return r

    def flush(self) -> Result | None:
        """Fin de la transmisión (COS cae): cierra una secuencia sin terminador."""
        return self._finish(self._n) if self._seq else None


def decode(samples: list[float], **kw) -> list[Result]:
    dec = Decoder(**kw)
    for i in range(0, len(samples) - BLOCK + 1, BLOCK):
        dec.feed(samples[i:i + BLOCK])
    dec.flush()
    return dec.results
