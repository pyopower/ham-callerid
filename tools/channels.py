"""Banco de pruebas de canales: ¿cuántas ráfagas sobreviven a cada camino real?

Genera N ráfagas (indicativos al azar) entre voz simulada, las pasa por un canal
(µ-law, GSM 06.10 de EchoLink, Opus, AMR-NB, FM con filtros y ruido, pérdida de paquetes)
y cuenta las que se descodifican. Uso: python3 tools/channels.py [N]
"""
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hamcid import burst  # noqa: E402
from tests.test_dtmf import fake_voice  # noqa: E402

RATE = 8000
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
MOD = os.environ.get("HCID_MOD", "burst")      # módulo a probar (para comparar formatos)
mod = __import__("hamcid." + MOD, fromlist=["x"])


def calls(n, rnd):
    pre = ["C31", "EA3", "EA5", "HP3", "XE1", "VA3", "K1", "W2", "DL1", "IK2", "F4", "G0"]
    out = []
    for _ in range(n):
        c = rnd.choice(pre) + "".join(rnd.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(rnd.randint(2, 3)))
        if rnd.random() < 0.2:
            c += "/P"
        out.append(c)
    return out


def build(rnd):
    cs = calls(N, rnd)
    x = []
    for i, c in enumerate(cs):
        x += fake_voice(rnd.randint(300, 900), seed=rnd.randint(0, 10**6))
        x += mod.modulate(c, amp=0.3)
    x += fake_voice(500, seed=1)
    return x, cs


def wav_write(path, x, rate=RATE):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, int(v * 32767)))) for v in x))


def wav_read(path):
    with wave.open(path, "rb") as w:
        n = w.getnframes(); d = w.readframes(n)
        return [v / 32768 for v in struct.unpack("<%dh" % (len(d) // 2), d)]


def via(cmds, x):
    with tempfile.TemporaryDirectory() as t:
        a, b = os.path.join(t, "a.wav"), os.path.join(t, "b.wav")
        wav_write(a, x)
        for c in cmds:
            subprocess.run(c.format(a=a, b=b, t=t), shell=True, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return wav_read(b)


def biquad(x, b, a):
    y = []; x1 = x2 = y1 = y2 = 0.0
    for v in x:
        o = (b[0] * v + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2) / a[0]
        x2, x1, y2, y1 = x1, v, y1, o
        y.append(o)
    return y


def bandpass_fm(x, rnd, snr_db=None, emph=None):
    # paso alto 300 Hz + paso bajo 3000 Hz (2.º orden cada uno), como el audio de una radio FM
    def lp(f):
        w = 2 * math.pi * f / RATE; al = math.sin(w) / (2 * 0.707); c = math.cos(w)
        return [(1 - c) / 2, 1 - c, (1 - c) / 2], [1 + al, -2 * c, 1 - al]
    def hp(f):
        w = 2 * math.pi * f / RATE; al = math.sin(w) / (2 * 0.707); c = math.cos(w)
        return [(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al]
    y = biquad(biquad(x, *hp(300)), *lp(3000))
    if emph == "de":        # desénfasis de 750 µs sin preénfasis en origen (radio mal ajustada)
        k = math.exp(-1 / (RATE * 750e-6)); o = 0.0; z = []
        for v in y:
            o = (1 - k) * v + k * o; z.append(o * 4)
        y = z
    if snr_db is not None:
        p = sum(v * v for v in y) / len(y)
        s = math.sqrt(p / 10 ** (snr_db / 10))
        y = [v + rnd.gauss(0, s) for v in y]
    return y


def packet_loss(x, rnd, p, fill):
    out = []
    for i in range(0, len(x), 160):
        f = x[i:i + 160]
        if rnd.random() < p:
            if fill == "silence":
                out += [0.0] * len(f)
            continue                        # "drop": se pierde y el tiempo se comprime
        out += f
    return out


def decode_count(y, cs):
    got = [t for _, t, _ in mod.demodulate(y)]
    ok = 0; rest = list(got)
    for c in cs:
        if c in rest:
            rest.remove(c); ok += 1
    return ok, len(rest)


def main():
    rnd = random.Random(42)
    x, cs = build(rnd)
    chans = {
        "µ-law (referencia)": lambda: via(["sox {a} -e u-law -t raw {t}/u.raw && sox -r 8000 -e u-law -b 8 -c 1 -t raw {t}/u.raw -e signed-integer -b 16 {b}"], x),
        "GSM 06.10 (EchoLink)": lambda: via(["sox {a} {t}/g.gsm && sox {t}/g.gsm -e signed-integer -b 16 {b}"], x),
        "AMR-NB 12,2 kbps": lambda: via(["ffmpeg -y -i {a} -ar 8000 -c:a libopencore_amrnb -b:a 12.2k {t}/a.amr || sox {a} -C 7 {t}/a.amr", "sox {t}/a.amr -r 8000 -e signed-integer -b 16 {b}"], x),
        "Opus 16 kbps voip": lambda: via(["ffmpeg -y -i {a} -c:a libopus -b:a 16k -application voip {t}/o.opus", "ffmpeg -y -i {t}/o.opus -ar 8000 -ac 1 {b}"], x),
        "Opus 8 kbps voip": lambda: via(["ffmpeg -y -i {a} -c:a libopus -b:a 8k -application voip {t}/o.opus", "ffmpeg -y -i {t}/o.opus -ar 8000 -ac 1 {b}"], x),
        "FM 300-3000 Hz": lambda: bandpass_fm(x, rnd),
        "FM 300-3000 + ruido 20 dB": lambda: bandpass_fm(x, rnd, 20),
        "FM 300-3000 + ruido 12 dB": lambda: bandpass_fm(x, rnd, 12),
        "FM con desénfasis sin preénfasis": lambda: bandpass_fm(x, rnd, None, "de"),
        "pérdida 2 % (tramas que faltan)": lambda: packet_loss(x, rnd, 0.02, "drop"),
        "pérdida 5 % (tramas que faltan)": lambda: packet_loss(x, rnd, 0.05, "drop"),
        "pérdida 5 % (rellenas con silencio)": lambda: packet_loss(x, rnd, 0.05, "silence"),
    }
    only = os.environ.get("HCID_CHAN")
    print(f"{N} ráfagas · formato {MOD}")
    for name, fn in chans.items():
        if only and only not in name:
            continue
        try:
            y = fn()
        except subprocess.CalledProcessError:
            print(f"  {name:38s} (canal no disponible)")
            continue
        ok, extra = decode_count(y, cs)
        print(f"  {name:38s} {ok:4d}/{N}  {100 * ok / N:5.1f} %" + (f"  (+{extra} falsas)" if extra else ""))


if __name__ == "__main__":
    main()
