"""hcid-usrp-proxy: Ham CallerID entre AllStarLink (chan_usrp) y DVSwitch Analog_Bridge.

    ASL ⇄ [proxy] ⇄ Analog_Bridge ⇄ MMDVM_Bridge ⇄ red DMR (ADN...)

ASL → DMR
    El audio de ASL se retrasa ``--delay`` ms. Mientras tanto se busca la ráfaga Ham CallerID;
    si aparece, se silencia, se busca el ID DMR del indicativo y se manda a Analog_Bridge un
    Talker Alias con el origen marcado (``C31AG/ASL``; ``ASL <nodo>`` si no hubo ráfaga): el
    dato es *declarado*, no verificado. Con ``--hbp-listen``/``--hbp-master`` el proxy hace de
    relevo HBP transparente entre MMDVM_Bridge y el master y mete el TA como paquetes ``DMRA``
    en la sesión del peer: dentro de la voz (LC embebido, como una radio; vale para cualquier
    red) y, con ``--dmra``, también como paquetes DMRA (ADN v2 en modo ``both`` respeta el TA
    de la fuente).
    Opcional (``--real-id``): poner el ID DMR real del indicativo con un TLV ``SET_INFO``;
    pensado para cuando la ráfaga vaya firmada.

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

from hamcid import burst, dmr_ta

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


def dmra_packets(rf_src: bytes, text: str) -> list[bytes]:
    """Paquetes HBP DMRA (Talker Alias UTF-8, formato ETSI 2) como los de MMDVMHost."""
    raw = text.encode("utf-8")[:27]
    buf = bytearray(28)
    buf[0] = (2 << 6) | (len(raw) << 1)
    buf[1:1 + len(raw)] = raw
    blocks = [bytes(buf[i * 7:(i + 1) * 7]) for i in range(4)]
    last = max([i for i, b in enumerate(blocks) if any(b)] or [0])
    return [b"DMRA" + rf_src[:3] + bytes([i]) + blocks[i] for i in range(last + 1)]


class HbpRelay:
    """Relevo HBP transparente MMDVM_Bridge ⇄ master que puede añadir paquetes DMRA.

    El master ve al proxy como el peer (misma sesión, mismo puerto), así que los DMRA que se
    inyectan cuentan como del peer. ``ta_for_stream()`` da el texto para cada llamada nueva.
    """

    def __init__(self, listen: str, master: str, ta_for_stream, dmra: bool = False):
        self.local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.local.bind(_addr(listen))
        self.up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.master_name = master
        self.master = None
        self.bridge = None
        self.ta_for_stream = ta_for_stream
        self.streams = collections.OrderedDict()
        self.injector = dmr_ta.TaInjector()
        self.dmra = dmra
        self.pending: tuple[bytes, bytes, float] | None = None   # (stream, rf_src, desde) esperando indicativo

    def _resolve(self):
        h, p = _addr(self.master_name)
        self.master = (socket.gethostbyname(h), p)

    def run(self):
        self._resolve()
        threading.Thread(target=_rx_loop, args=(self.local, self.from_bridge), daemon=True).start()
        threading.Thread(target=_rx_loop, args=(self.up, self.from_master), daemon=True).start()
        log.info("HBP: MMDVM_Bridge ⇄ proxy ⇄ master %s %s", self.master_name, self.master)

    def from_bridge(self, d: bytes, addr=None):
        new_text = None
        if d[:4] == b"DMRD" and len(d) >= 20:
            sid = d[16:20]
            if sid not in self.streams:
                self.streams[sid] = True
                while len(self.streams) > 16:
                    self.streams.popitem(last=False)
                new_text = self.ta_for_stream()
                if new_text is not None:
                    self.injector.start(sid, new_text or None, d)
                    if not new_text:          # de ASL pero aún sin indicativo: se decide en marcha
                        self.pending = (sid, d[5:8], time.monotonic())
                        new_text = None
            d = self.injector.process(d)          # alias también dentro de la voz (LC embebido)
        self.up.sendto(d, self.master)
        if new_text:
            self._announce(d[16:20], d[5:8], new_text)

    def set_alias(self, text: str, why: str):
        """Alias para la llamada que espera indicativo (si la hay)."""
        if not self.pending:
            return
        sid, rf, _ = self.pending
        self.pending = None
        if self.injector.set_text(sid, text):
            self._announce(sid, rf, text, why)

    def tick(self, fallback):
        if self.pending and time.monotonic() - self.pending[2] > 1.5:
            text = fallback()
            self.set_alias(text, "sin ráfaga") if text else setattr(self, "pending", None)

    def _announce(self, sid: bytes, rf: bytes, text: str, why: str = ""):
        if self.dmra:
            # opcional: DMRA como MMDVMHost. ¡Ojo! HBlink3/4 no lo entienden, contestan NAK y el
            # peer se reconecta (la llamada se corta). El alias embebido en la voz basta.
            for pk in dmra_packets(rf, text):
                self.up.sendto(pk, self.master)
        log.info("HBP: Talker Alias «%s» en la llamada %s%s", text, sid.hex(), f" ({why})" if why else "")

    def from_master(self, d: bytes, addr=None):
        if self.bridge:
            self.local.sendto(d, self.bridge)


class AmiWatcher:
    """Pregunta a app_rpt (AMI) qué enlace del nodo está transmitiendo y quién es.

    ``RPT_ALINKS`` marca con ``K`` los enlaces que transmiten. Un nodo EchoLink (3 + 6 dígitos)
    se traduce a su indicativo (validado por EchoLink) con ``echolink dbget nodename``.
    ``source()`` devuelve ("el", "EA3XXX"), ("iax", "C31AG"), ("node", "49352") o None.
    """

    def __init__(self, addr: str, user: str, secret: str, node: str, skip: set[str]):
        self.addr = _addr(addr)
        self.user, self.secret, self.node, self.skip = user, secret, node, skip
        self.sock = None
        self.buf = b""
        self.lock = threading.Lock()
        self.el_cache: dict[str, str | None] = {}

    def _connect(self):
        s = socket.create_connection(self.addr, timeout=3)
        s.settimeout(3)
        self.sock, self.buf = s, b""
        self._read_until(b"\r\n")                    # saludo
        r = self._action(f"Action: Login\r\nUsername: {self.user}\r\nSecret: {self.secret}\r\nEvents: off\r\n")
        if "Success" not in r:
            raise OSError("AMI: login rechazado")

    def _read_until(self, sep: bytes) -> bytes:
        while sep not in self.buf:
            d = self.sock.recv(4096)
            if not d:
                raise OSError("AMI cerrado")
            self.buf += d
        out, self.buf = self.buf.split(sep, 1)
        return out

    def _action(self, text: str) -> str:
        aid = "hcid%d" % int(time.monotonic() * 1000)
        self.sock.sendall((text + f"ActionID: {aid}\r\n\r\n").encode())
        while True:
            block = self._read_until(b"\r\n\r\n").decode("utf-8", "ignore")
            if aid in block or block.startswith("Response: Error"):
                # algunas respuestas (Command) traen el texto en un bloque «Follows» que acaba en --END COMMAND--
                if "Follows" in block and "--END COMMAND--" not in block:
                    block += self._read_until(b"--END COMMAND--").decode("utf-8", "ignore")
                return block

    def _query(self, text: str) -> str:
        with self.lock:
            for _ in range(2):
                try:
                    if self.sock is None:
                        self._connect()
                    return self._action(text)
                except OSError:
                    try:
                        self.sock and self.sock.close()
                    except OSError:
                        pass
                    self.sock = None
            return ""

    def keyed_links(self) -> list[str]:
        r = self._query(f"Action: RptStatus\r\nCOMMAND: XStat\r\nNODE: {self.node}\r\n")
        for line in r.splitlines():
            if "RPT_ALINKS=" in line:
                items = line.split("RPT_ALINKS=", 1)[1].split(",")[1:]
                return [i[:-2] for i in items if len(i) > 2 and i.endswith("K")]
        return []

    def echolink_call(self, num: str) -> str | None:
        if num not in self.el_cache:
            r = self._query(f"Action: Command\r\nCommand: echolink dbget nodename {num}\r\n")
            call = None
            for line in r.splitlines():
                parts = line.replace("Output:", "").strip().split("|")
                if len(parts) >= 2 and parts[0].strip() == num:
                    call = parts[1].strip().upper()
            self.el_cache[num] = call
        return self.el_cache[num]

    def source(self):
        for link in self.keyed_links():
            if link in self.skip:
                continue
            if link.isdigit() and len(link) == 7 and link.startswith("3"):
                call = self.echolink_call(link[1:])
                return ("el", call.split("-")[0] if call else "EL " + link[1:])
            if link.isdigit():
                return ("node", link)
            name = "".join(c for c in link.upper() if c.isalnum() or c in "/-")
            if name.startswith("0") and len(name) > 1:
                name = name[1:]                       # clientes iaxRpt: «0» + indicativo
            if name:
                return ("iax", name)
        return None


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
        self.up_call: str | None = None       # indicativo de la ráfaga de esta transmisión
        self.last_asl_audio = 0.0
        self.last_seq = None
        self.last_asl_n = -1000
        self.hbp = HbpRelay(a.hbp_listen, a.hbp_master, self.ta_for_stream, a.dmra) if a.hbp_listen else None
        self.ami = None
        if a.ami:
            host, user, secret_file = a.ami.split(",")
            secret = open(secret_file).read().strip()
            self.ami = AmiWatcher(host, user, secret, a.node, {a.bridge_node} if a.bridge_node else set())
        self.ami_src = None              # último origen visto por AMI durante la transmisión
        self.ami_next = 0.0
        # pistas locales «nodo alias» (p. ej. una pasarela que sabe quién habla por su nodo)
        self.hints: dict[str, tuple[str, float]] = {}
        self.s_hint = None
        if a.hint_listen:
            self.s_hint = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.s_hint.bind(_addr(a.hint_listen))
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
        if n - self.last_asl_n > 25 or now - self.last_asl_audio > 1.0:   # hueco: transmisión nueva
            self.up_call = None
            self.ami_src = None
            self.key_frame = n
        self.last_asl_n = n
        self.last_asl_audio = now
        x = pcm_to_floats(payload)
        if len(x) == FRAME:
            # paquetes perdidos (salto en la secuencia USRP): el decodificador recibe silencio en
            # su lugar para no perder el compás (la corrección de errores se ocupa del resto)
            hits = []
            gap = (seq - self.last_seq - 1) if self.last_seq is not None else 0
            if 0 < gap <= 5:
                for _ in range(gap):
                    hits += self.dec.feed([0.0] * FRAME)
            self.last_seq = seq
            hits += self.dec.feed(x)
            if key == 0 and self.up_keyed:
                hits += self.dec.flush()
            for call, first, last in hits:
                self.mute.update(range(first, last + 1))
                # solo la ráfaga del principio decide el ID (el de una llamada ya empezada no cambia)
                if first - self.key_frame < 75:   # solo una ráfaga de los primeros ~1,5 s
                    self.on_upstream_call(call)
        if key and not self.up_keyed:
            self.up_keyed = True
            log.debug("ASL→DMR: inicio de transmisión (n=%d)", n)
        elif not key and self.up_keyed:
            self.up_keyed = False
            if self.id_set:     # restaurar el ID de la pasarela cuando haya salido el final
                self.restore_at = now + self.a.delay / 1000 + 0.3
        with self.lock:
            self.up_q.append((now + self.a.delay / 1000, seq, key, typ, payload, n))

    def on_upstream_call(self, call: str):
        if self.up_call is None:
            self.up_call = call
            log.info("ASL→DMR: ráfaga %s (n=%d)", call, self.frame_n)
            if self.hbp:
                self.hbp.set_alias(f"{call}/{self.a.origin}", "ráfaga")
        if self.id_set or not self.a.real_id:
            return
        base = call.upper().split("/")[0].split("-")[0]
        if self.a.allow and base not in self.a.allow:
            log.info("ASL→DMR: ráfaga %s (fuera de --allow: ID de pasarela)", call)
            return
        dmrid = self.ids.id_for(call)
        log.info("ASL→DMR: ráfaga %s → ID %s (n=%d)", call, dmrid, self.frame_n)
        if dmrid:
            self.s_tlv.sendto(set_info(dmrid, call, self.a.tg, self.a.ts), self.ab_tlv)
            self.id_set = True

    def pump_up(self):
        now = time.monotonic()
        self.poll_ami()
        if self.hbp:
            self.hbp.tick(self.fallback_ta)
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

    def poll_ami(self):
        """Mientras entra audio de ASL, mira quién transmite (en un hilo, para no frenar el audio)."""
        now = time.monotonic()
        if not self.ami or now < self.ami_next or now - self.last_asl_audio > 1.0:
            return
        self.ami_next = now + 0.5

        def run():
            try:
                src = self.ami.source()
            except Exception:
                log.exception("AMI")
                return
            if src and src != self.ami_src:
                log.info("ASL: transmite %s %s", *src)
            if src:
                self.ami_src = src
        threading.Thread(target=run, daemon=True).start()

    def from_hint(self, d: bytes, addr=None):
        """Pista «<nodo> <alias>» (o «<nodo> -» para borrarla), en texto, por UDP local.
        El alias solo es texto para el Talker Alias: nunca cambia el DMR ID de la pasarela."""
        parts = d.decode("utf-8", "replace").strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            return
        node, alias = parts
        alias = "".join(c for c in alias if 32 <= ord(c) < 127)[:dmr_ta.MAX_LEN].strip()
        with self.lock:
            if alias in ("", "-"):
                self.hints.pop(node, None)
            else:
                if self.hints.get(node, ("",))[0] != alias:
                    log.info("Pista: nodo %s = «%s»", node, alias)
                self.hints[node] = (alias, time.monotonic())

    def hint_for(self, node: str) -> str | None:
        with self.lock:
            h = self.hints.get(node)
        if h and time.monotonic() - h[1] < self.a.hint_ttl:
            return h[0]
        return None

    def fallback_ta(self) -> str:
        """Alias cuando no hubo ráfaga: lo que diga AMI, o el texto fijo."""
        src = self.ami_src
        if src:
            kind, who = src
            if kind == "el":
                return f"{who}/EL" if not who.startswith("EL ") else who
            if kind == "iax":
                return f"{who}/{self.a.origin}"
            if kind == "node":
                hint = self.hint_for(who)
                if hint:
                    return hint
                return f"{self.a.origin} {who}"
        return self.a.unknown_ta

    def ta_for_stream(self) -> str | None:
        """Alias de una llamada que empieza hacia DMR: texto, "" (de ASL, indicativo aún
        desconocido) o None (no viene de ASL)."""
        if time.monotonic() - self.last_asl_audio > 2.0:
            return None
        if self.up_call:
            return f"{self.up_call}/{self.a.origin}"
        src = self.ami_src                    # un nodo con pista ya conocido: alias desde la 1ª trama
        if src and src[0] == "node":
            return self.hint_for(src[1]) or ""
        return ""

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
        if self.hbp:
            self.hbp.run()
        socks = [(self.s_asl, self.from_asl), (self.s_ab, self.from_ab)]
        if self.s_hint:
            socks.append((self.s_hint, self.from_hint))
        for s, fn in socks:
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
        d, addr = s.recvfrom(4096)
        try:
            if getattr(fn, "__self__", None) is not None and isinstance(fn.__self__, HbpRelay) and fn.__name__ == "from_bridge":
                fn.__self__.bridge = addr
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
    p.add_argument("--hbp-listen", default="", help="relevo HBP: donde escucha a MMDVM_Bridge (ip:puerto)")
    p.add_argument("--hbp-master", default="", help="relevo HBP: master real (host:puerto)")
    p.add_argument("--dmra", action="store_true", help="mandar también el alias en paquetes HBP DMRA "
                   "(ADN/BM sí; HBlink3/4 NO: contestan NAK y cortan la llamada)")
    p.add_argument("--origin", default="ASL", help="sufijo de origen en el Talker Alias (C31AG/ASL)")
    p.add_argument("--hint-listen", default="", help="UDP local para pistas «<nodo> <alias>» de otras "
                   "pasarelas (p. ej. 127.0.0.1:31200); solo cambian el Talker Alias, nunca el DMR ID")
    p.add_argument("--hint-ttl", type=float, default=75.0, help="segundos que vale una pista sin renovarse")
    p.add_argument("--unknown-ta", default="", help="Talker Alias si no hubo ráfaga (p. ej. 'ASL 60596')")
    p.add_argument("--ami", default="", help="AMI de Asterisk para saber quién transmite: "
                   "'host:puerto,usuario,fichero_con_la_clave'")
    p.add_argument("--node", default="", help="nodo ASL donde mirar los enlaces (p. ej. 60596)")
    p.add_argument("--bridge-node", default="", help="nodo USRP del propio puente, a ignorar (p. ej. 1932)")
    p.add_argument("--real-id", action="store_true", help="poner el ID DMR real del indicativo (solo "
                   "para ráfagas verificadas; hoy sin firma: desaconsejado)")
    p.add_argument("--allow", default="", help="con --real-id: indicativos que pueden salir con su ID real")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    a.allow = {c.strip().upper() for c in a.allow.split(",") if c.strip()}
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    Proxy(a).run()


if __name__ == "__main__":
    main()
