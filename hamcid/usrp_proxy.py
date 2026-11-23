"""hcid-usrp-proxy: Ham CallerID entre AllStarLink (chan_usrp) y DVSwitch Analog_Bridge.

    ASL ⇄ [proxy] ⇄ Analog_Bridge ⇄ MMDVM_Bridge ⇄ red DMR (ADN...)

ASL → DMR
    El audio de ASL se retrasa ``--delay`` ms. Mientras tanto se busca la ráfaga Ham CallerID;
    si aparece, se silencia, se busca el ID DMR del indicativo y se manda a Analog_Bridge un
    TLV ``SET_INFO`` por su puerto TLV para que la llamada salga con ese ID. Al acabar la
    transmisión se restaura el ID de la pasarela (Analog_Bridge lo deja «pegado»).

DMR → ASL
    Analog_Bridge (``exportMetadata = true``) anuncia cada llamada con un TLV ``SET_INFO``
    (ID, TG, indicativo). El proxy inserta la ráfaga con ese indicativo al principio y al final
    del audio hacia ASL; la voz va detrás (se retrasa lo que dura la ráfaga).

Solo usa la biblioteca estándar. Ver docs/PUENTE-DVSWITCH.md.
"""
from __future__ import annotations

import argparse
import array
import os
import collections
import logging
import socket
import struct
import threading
import time

from hamcid import burst

FRAME = 160
FRAME_S = 0.02
T_VOICE, T_TEXT = 0, 2
TLV_SET_INFO = 0x08

log = logging.getLogger("hcid-usrp-proxy")


# --------------------------------------------------------------------------- USRP / TLV

def usrp_parse(d: bytes):
    if len(d) < 32 or d[:4] != b"USRP":
        return None
    seq, mem, key, tg, typ, mpx, res = struct.unpack(">iiiiiii", d[4:32])
    if typ > 255:                       # algunos emisores ponen el tipo en el byte alto
        typ >>= 24
    return seq, key, typ, d[32:]


def usrp_build(seq: int, key: int, typ: int, payload: bytes) -> bytes:
    return b"USRP" + struct.pack(">iiiiiii", seq, 0, key, 0, typ, 0, 0) + payload


def pcm_to_floats(b: bytes):
    n = len(b) // 2
    return [v / 32768.0 for v in struct.unpack("<%dh" % n, b[:n * 2])]


def floats_to_pcm(x) -> bytes:
    return struct.pack("<%dh" % len(x), *(max(-32768, min(32767, int(v * 32767))) for v in x))


def set_info(dmrid: int, call: str, tg: int, ts: int, cc: int = 1, repeater: int = 0) -> bytes:
    body = (struct.pack(">I", dmrid)[1:] + struct.pack(">I", repeater) + struct.pack(">I", tg)[1:]
            + bytes([ts, cc]) + call.encode("ascii", "ignore") + b"\0")
    return bytes([TLV_SET_INFO, len(body)]) + body


def parse_set_info(p: bytes):
    """TLV SET_INFO → (dmrid, tg, indicativo) o None."""
    if len(p) < 15 or p[0] != TLV_SET_INFO:
        return None
    dmrid = int.from_bytes(p[2:5], "big")
    tg = int.from_bytes(p[9:12], "big")
    call = p[14:2 + p[1]].split(b"\0", 1)[0].decode("ascii", "ignore").strip()
    return dmrid, tg, call


# --------------------------------------------------------------------------- base de IDs

class IdLookup:
    """Indicativo → ID DMR con un índice compacto en memoria (~5 MB para 310 000 IDs).

    Se construye al arrancar (y cuando cambia el fichero): un bloque de bytes con líneas
    ``INDICATIVO ID`` ordenadas y un array de posiciones para búsqueda binaria. Leer el
    fichero en cada llamada tardaba ~350 ms en una máquina pequeña: demasiado para decidir
    el ID antes de que salga la primera trama.
    """

    def __init__(self, path: str):
        self.path = path
        self.mtime = None
        self.blob = b""
        self.offs = array.array("I")
        self.rcache: dict[int, str | None] = {}
        self._load()

    def _load(self):
        try:
            mt = os.path.getmtime(self.path)
        except OSError:
            return
        if mt == self.mtime:
            return
        rows = []
        with open(self.path, "rb") as f:
            for line in f:
                parts = line.replace(b",", b" ").split()
                if len(parts) >= 2 and len(parts[0]) == 7 and parts[0].isdigit():
                    rows.append(parts[1].upper() + b" " + parts[0])
        rows.sort()
        offs = array.array("I")
        pos = 0
        for r in rows:
            offs.append(pos)
            pos += len(r) + 1
        self.blob = b"\n".join(rows) + b"\n"
        self.offs = offs
        self.mtime = mt
        log.info("base de IDs: %d indicativos (%d KB)", len(rows), len(self.blob) // 1024)

    def _row(self, i: int) -> bytes:
        a = self.offs[i]
        return self.blob[a:self.blob.index(b"\n", a)]

    def id_for(self, call: str) -> int | None:
        base = call.upper().split("/")[0].split("-")[0].encode("ascii", "ignore")
        if not base:
            return None
        key = base + b" "
        lo, hi = 0, len(self.offs)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._row(mid) < key:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(self.offs):
            r = self._row(lo)
            if r.startswith(key):
                return int(r[len(key):])
        return None

    def call_for(self, dmrid: int) -> str | None:
        """Solo de reserva (Analog_Bridge ya manda el indicativo): recorre el índice."""
        if dmrid not in self.rcache:
            needle = b" %d\n" % dmrid
            i = self.blob.find(needle)
            if i < 0:
                self.rcache[dmrid] = None
            else:
                start = self.blob.rfind(b"\n", 0, i) + 1
                self.rcache[dmrid] = self.blob[start:i].decode("ascii", "ignore")
        return self.rcache[dmrid]

    def refresh(self):
        self._load()


# --------------------------------------------------------------------------- proxy

class Proxy:
    def __init__(self, a):
        self.a = a
        self.ids = IdLookup(a.ids)
        self.s_asl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s_asl.bind(_addr(a.listen_asl))
        self.s_ab = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s_ab.bind(_addr(a.listen_ab))
        self.s_tlv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.asl = _addr(a.asl)
        self.ab = _addr(a.ab)
        self.ab_tlv = _addr(a.ab_tlv)
        self.lock = threading.Lock()
        # ASL → AB
        self.up_q: collections.deque = collections.deque()   # (hora_salida, seq, key, typ, payload, n_trama)
        self.dec = burst.StreamDecoder()
        self.mute: set[int] = set()
        self.frame_n = 0
        self.up_keyed = False
        self.key_frame = 0
        self.up_fwd_keyed = False
        self.id_set = False
        self.restore_at = 0.0
        # AB → ASL
        self.down_q: collections.deque = collections.deque()  # (seq, key, typ, payload)
        self.down_keyed = False
        self.down_call: str | None = None
        self.down_start_pending = False
        self.seq = 1

    # ---- ASL → AB -----------------------------------------------------------
    def from_asl(self, d: bytes):
        u = usrp_parse(d)
        if u is None:
            return
        seq, key, typ, payload = u
        now = time.monotonic()
        if typ != T_VOICE:
            with self.lock:
                self.up_q.append((now + self.a.delay / 1000, seq, key, typ, payload, -1))
            return
        n = self.frame_n
        self.frame_n += 1
        x = pcm_to_floats(payload)
        if len(x) == FRAME:
            hits = self.dec.feed(x)
            if key == 0 and self.up_keyed:
                hits += self.dec.flush()
            for call, first, last in hits:
                self.mute.update(range(first, last + 1))
                # solo la ráfaga del principio decide el ID (el de una llamada ya empezada no cambia)
                if first - self.key_frame < self.a.delay // 20:
                    self.on_upstream_call(call)
        if key and not self.up_keyed:
            self.up_keyed = True
            self.key_frame = n
            log.debug("ASL→DMR: inicio de transmisión (n=%d)", n)
        elif not key and self.up_keyed:
            self.up_keyed = False
            if self.id_set:     # restaurar el ID de la pasarela cuando haya salido el final
                self.restore_at = now + self.a.delay / 1000 + 0.3
        with self.lock:
            self.up_q.append((now + self.a.delay / 1000, seq, key, typ, payload, n))

    def on_upstream_call(self, call: str):
        if self.id_set:
            return
        dmrid = self.ids.id_for(call)
        log.info("ASL→DMR: ráfaga %s → ID %s (n=%d)", call, dmrid, self.frame_n)
        if dmrid:
            self.s_tlv.sendto(set_info(dmrid, call, self.a.tg, self.a.ts), self.ab_tlv)
            self.id_set = True

    def pump_up(self):
        now = time.monotonic()
        with self.lock:
            while self.up_q and self.up_q[0][0] <= now:
                _, seq, key, typ, payload, n = self.up_q.popleft()
                if n in self.mute:
                    self.mute.discard(n)
                    payload = b"\0" * len(payload)
                if typ == T_VOICE and key and not self.up_fwd_keyed:
                    log.debug("ASL→DMR: primera trama reenviada (n=%d)", n)
                self.up_fwd_keyed = bool(key) if typ == T_VOICE else self.up_fwd_keyed
                self.s_ab.sendto(usrp_build(seq, key, typ, payload), self.ab)
        if self.restore_at and now >= self.restore_at and not self.up_keyed:
            self.restore_at = 0.0
            self.id_set = False
            self.s_tlv.sendto(set_info(self.a.gateway_id, "", self.a.tg, self.a.ts), self.ab_tlv)
            log.info("ASL→DMR: ID de pasarela %d restaurado", self.a.gateway_id)

    # ---- AB → ASL -----------------------------------------------------------
    def from_ab(self, d: bytes):
        u = usrp_parse(d)
        if u is None:
            return
        seq, key, typ, payload = u
        with self.lock:
            if typ == T_TEXT:
                info = parse_set_info(payload)
                if info:
                    dmrid, tg, call = info
                    call = burst_call(call or self.ids.call_for(dmrid) or "")
                    if call and dmrid != self.a.gateway_id:
                        self.down_call = call
                        self.down_start_pending = True
                        log.info("DMR→ASL: llamada de %s (%d) en TG %d", call, dmrid, tg)
                self.down_q.append((seq, key, typ, payload))
                return
            if typ != T_VOICE:
                self.down_q.append((seq, key, typ, payload))
                return
            if key and self.down_start_pending and self.down_call:
                self.down_start_pending = False
                self._queue_burst(self.down_call)
            if key:
                self.down_keyed = True
            elif self.down_keyed:
                self.down_keyed = False
                if self.down_call:
                    self._queue_burst(self.down_call)
                    self.down_call = None
            self.down_q.append((seq, key, typ, payload))

    def _queue_burst(self, call: str):
        b = burst.modulate(call, amp=self.a.level)
        b += [0.0] * (-len(b) % FRAME)
        for i in range(0, len(b), FRAME):
            self.down_q.append((0, 1, T_VOICE, floats_to_pcm(b[i:i + FRAME])))

    def pump_down(self):
        with self.lock:
            if not self.down_q:
                return
            seq, key, typ, payload = self.down_q.popleft()
            # los paquetes sin audio no consumen turno
            while typ != T_VOICE:
                self.s_asl.sendto(usrp_build(seq, key, typ, payload), self.asl)
                if not self.down_q:
                    return
                seq, key, typ, payload = self.down_q.popleft()
            self.seq = (self.seq + 1) & 0x7fffffff
            self.s_asl.sendto(usrp_build(self.seq, key, typ, payload), self.asl)

    # ---- bucle -------------------------------------------------------------
    def run(self):
        for s, fn in ((self.s_asl, self.from_asl), (self.s_ab, self.from_ab)):
            threading.Thread(target=_rx_loop, args=(s, fn), daemon=True).start()
        log.info("ASL %s ⇄ proxy (%s | %s) ⇄ AB %s, TLV %s, pasarela %d, retardo %d ms",
                 self.a.asl, self.a.listen_asl, self.a.listen_ab, self.a.ab, self.a.ab_tlv,
                 self.a.gateway_id, self.a.delay)
        nxt = time.monotonic()
        while True:
            self.pump_up()
            self.pump_down()
            nxt += FRAME_S
            time.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 0.5:
                nxt = time.monotonic()


def burst_call(s: str) -> str:
    return "".join(c for c in s.upper() if c.isalnum() or c in "/-")[:burst.MAX_LEN]


def _rx_loop(s, fn):
    while True:
        d, _ = s.recvfrom(4096)
        try:
            fn(d)
        except Exception:
            log.exception("paquete descartado")


def _addr(s: str):
    h, p = s.rsplit(":", 1)
    return h, int(p)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--listen-asl", required=True, help="donde ASL manda su USRP (ip:puerto)")
    p.add_argument("--asl", required=True, help="adonde mandar el USRP hacia ASL (ip:puerto)")
    p.add_argument("--listen-ab", required=True, help="donde Analog_Bridge manda su USRP (ip:puerto)")
    p.add_argument("--ab", required=True, help="puerto USRP de Analog_Bridge (ip:puerto)")
    p.add_argument("--ab-tlv", required=True, help="puerto TLV de Analog_Bridge ([AMBE_AUDIO] rxPort)")
    p.add_argument("--gateway-id", type=int, required=True, help="gatewayDmrId de Analog_Bridge")
    p.add_argument("--tg", type=int, required=True)
    p.add_argument("--ts", type=int, default=2)
    p.add_argument("--ids", default="/var/lib/dvswitch/subscriber_ids.csv")
    p.add_argument("--delay", type=int, default=300, help="retardo ASL→DMR en ms (≥ 300)")
    p.add_argument("--level", type=float, default=0.25, help="amplitud de la ráfaga hacia ASL (0-1)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    Proxy(a).run()


if __name__ == "__main__":
    main()
