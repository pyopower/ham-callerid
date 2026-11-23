"""Ráfaga Ham CallerID v0 (fase 2): AFSK Bell 202 con el indicativo en texto.

Trama (ver docs/SPEC.md §5):

    reposo (mark) 20 ms · preámbulo 0x55 0x55 0x55 · sync 0x2D 0xD4 ·
    cabecera (versión<<4 | tipo) · longitud · carga · CRC-16/CCITT-FALSE · reposo 2 bits

Cada byte va en formato asíncrono: bit de inicio 0, 8 bits LSB primero, bit de parada 1.
Mark (1) = 1200 Hz, space (0) = 2200 Hz, 1200 baudios, fase continua.
Con un indicativo de 5 letras dura ~145 ms.
"""
from __future__ import annotations

import math

RATE = 8000
BAUD = 1200
MARK, SPACE = 1200.0, 2200.0
SPB = RATE / BAUD  # 6.667 muestras por bit

PREAMBLE = b"\x55\x55\x55"
SYNC = b"\x2d\xd4"
VERSION = 0
TYPE_CALLSIGN = 1
TYPE_TEXT = 2
MAX_LEN = 32
LEAD_BITS = 24  # 20 ms de 1200 Hz puro: lo usan los detectores en flujo para saber cuándo mirar
ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def frame(payload: str, kind: int = TYPE_CALLSIGN) -> bytes:
    p = payload.strip().upper().encode("ascii")
    if not 0 < len(p) <= MAX_LEN:
        raise ValueError("payload length")
    if kind == TYPE_CALLSIGN and not set(p.decode()) <= ALLOWED:
        raise ValueError("callsign characters")
    body = bytes([(VERSION << 4) | kind, len(p)]) + p
    c = crc16(body)
    return PREAMBLE + SYNC + body + bytes([c >> 8, c & 0xFF])


def _bits(data: bytes) -> list[int]:
    out: list[int] = []
    for b in data:
        out.append(0)
        out += [(b >> i) & 1 for i in range(8)]
        out.append(1)
    return out


def modulate(callsign: str, kind: int = TYPE_CALLSIGN, amp: float = 0.5) -> list[float]:
    bits = [1] * LEAD_BITS + _bits(frame(callsign, kind)) + [1, 1]
    out, phase, t = [], 0.0, 0.0
    total = len(bits) * SPB
    n = 0
    while n < total:
        b = bits[min(int(n / SPB), len(bits) - 1)]
        phase += 2 * math.pi * (MARK if b else SPACE) / RATE
        out.append(amp * math.sin(phase))
        n += 1
    return out


# ------------------------------------------------------------------ demodulación

def _discriminator(x: list[float]) -> list[float]:
    """>0 = mark, <0 = space. Correlación no coherente sobre una ventana de 1 bit."""
    n = len(x)
    w = int(round(SPB))
    cm = [complex(math.cos(2 * math.pi * MARK * i / RATE), -math.sin(2 * math.pi * MARK * i / RATE)) for i in range(n)]
    cs = [complex(math.cos(2 * math.pi * SPACE * i / RATE), -math.sin(2 * math.pi * SPACE * i / RATE)) for i in range(n)]
    pm = [x[i] * cm[i] for i in range(n)]
    ps = [x[i] * cs[i] for i in range(n)]
    d = [0.0] * n
    am = as_ = 0j
    for i in range(n):
        am += pm[i]; as_ += ps[i]
        if i >= w:
            am -= pm[i - w]; as_ -= ps[i - w]
        d[i] = abs(am) - abs(as_)
    # el valor en i describe la ventana que termina en i: centrarlo
    half = w // 2
    return d[half:] + [0.0] * half


def _uart_bytes(d: list[float], start: float, nbytes: int) -> bytes | None:
    """Lee nbytes asíncronos empezando en el bit de inicio que comienza en la muestra start."""
    out = bytearray()
    pos = start
    for _ in range(nbytes):
        def bit(k: int) -> int:
            i = int(pos + (k + 0.5) * SPB)
            return 1 if i < len(d) and d[i] > 0 else 0
        if bit(0) != 0 or bit(9) != 1:
            return None
        out.append(sum(bit(1 + i) << i for i in range(8)))
        pos += 10 * SPB
        # resincroniza con el flanco del siguiente bit de inicio (tolerancia de reloj)
        lo, hi = int(pos - SPB / 2), int(pos + SPB / 2)
        for j in range(max(lo, 1), min(hi, len(d))):
            if d[j - 1] > 0 >= d[j]:
                pos = float(j)
                break
    return bytes(out)


def demodulate(x: list[float]) -> list[tuple[int, str, int]]:
    """Devuelve [(tipo, texto, muestra_inicio)] de las ráfagas válidas (CRC correcto)."""
    if len(x) < 100:
        return []
    d = _discriminator(x)
    found: list[tuple[int, str, int]] = []
    i = 1
    n = len(d)
    while i < n:
        # flanco mark -> space: posible bit de inicio
        if not (d[i - 1] > 0 >= d[i]):
            i += 1
            continue
        hdr = _uart_bytes(d, float(i), 2)
        if hdr is None or hdr != SYNC:
            i += 1
            continue
        pos = i + 20 * SPB
        lenb = _uart_bytes(d, pos, 2)
        if lenb is None or (lenb[0] >> 4) != VERSION or not 0 < lenb[1] <= MAX_LEN:
            i += 1
            continue
        rest = _uart_bytes(d, pos, 2 + lenb[1] + 2)
        if rest is None:
            i += 1
            continue
        body, c = rest[:-2], (rest[-2] << 8) | rest[-1]
        if crc16(body) == c:
            try:
                found.append((body[0] & 0x0F, body[2:].decode("ascii"), i))
            except UnicodeDecodeError:
                pass
            i = int(pos + len(rest) * 10 * SPB)
            continue
        i += 1
    return found


# ------------------------------------------------------------------ flujo (para nodos y puentes)

def _goertzel(x, f):
    c = 2 * math.cos(2 * math.pi * f / RATE)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + c * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - c * s1 * s2


def mark_purity(x):
    """Fracción de la energía de ``x`` que es un tono de 1200 Hz (≈1 en el reposo inicial)."""
    n = len(x)
    e = sum(v * v for v in x)
    if n == 0 or e < 1e-6 * n:
        return 0.0
    return _goertzel(x, MARK) * 2 / (n * e)


class StreamDecoder:
    """Decodificador en flujo por tramas (normalmente 160 muestras = 20 ms).

    Busca el reposo inicial (20 ms de 1200 Hz puro) con ventanas de 80 muestras cada 40;
    cuando lo ve, espera a tener la ráfaga entera y la demodula. ``feed`` devuelve
    ``[(texto, primera_trama, última_trama)]`` para poder silenciar esas tramas.
    """

    WIN, HOP = 80, 40

    def __init__(self, purity=0.8, keep_frames=40):
        self.purity = purity
        self.keep = keep_frames
        self.frames = []          # [(n, muestras)]
        self.n = 0
        self.tail = []            # últimas muestras de la trama anterior (ventanas que cruzan tramas)
        self.pending = []         # [(trama_inicio, tramas_a_esperar, intentos)]
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
                    self.pending.append((n - 1, 9))  # 20 ms de reposo + ~145 ms de trama
                    break
        out = []
        keep = []
        for start, wait in self.pending:
            if n - start < wait:
                keep.append((start, wait))
                continue
            hits = self._scan(start, n)
            if hits:
                out += hits
                self.done_until = max(h[2] for h in hits)
            elif wait < 22:
                keep.append((start, wait + 1))   # aún no ha llegado entera: probar en la trama siguiente
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
            nbytes = len(PREAMBLE) + len(SYNC) + 2 + len(t) + 2
            end = start + int((nbytes * 10 + 2) * SPB)
            # desde la trama anterior al reposo hasta la trama del final
            a = k0 + max(0, (start - int((LEAD_BITS + 30) * SPB)) // 160)
            out.append((t, a, k0 + end // 160))
        return out
