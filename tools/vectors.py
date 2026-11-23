"""Genera los vectores de prueba de conformidad (vectors/*.wav + vectors/expected.txt).

Cada WAV es audio de 8 kHz, 16 bits, mono. expected.txt: «fichero<TAB>indicativos esperados
separados por comas» (vacío = no debe descodificarse nada). Además, mod_*.wav son la salida
exacta del modulador (amplitud 0,3) para comprobar los moduladores bit a bit.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hamcid import burst                         # noqa: E402
from tests.test_burst import ulaw_roundtrip      # noqa: E402
from tests.test_dtmf import fake_voice, noise, add, silence  # noqa: E402
from tools.channels import wav_write, via, bandpass_fm  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vectors")
rows = []


def vec(name, x, expect):
    wav_write(os.path.join(OUT, name), x)
    rows.append(f"{name}\t{','.join(expect)}")


for c in ("C31AG", "EA3IGD/P", "XE1ABC-12", "A" * 32):
    vec(f"mod_{c.replace('/', '_')}.wav", burst.modulate(c, amp=0.3), [c])
vec("clean_two.wav", silence(100) + burst.modulate("C31AG", amp=0.3) + fake_voice(800) + burst.modulate("C31AG", amp=0.3) + silence(100), ["C31AG", "C31AG"])
vec("ulaw_low_level.wav", ulaw_roundtrip(silence(50) + burst.modulate("HP3ICC", amp=0.08) + silence(50)), ["HP3ICC"])
x = silence(50) + burst.modulate("EA5GVK", amp=0.3) + silence(50)
vec("noise_12db_fm.wav", bandpass_fm(x, random.Random(4), 12), ["EA5GVK"])
y = burst.modulate("C31AG/M", amp=0.3)
vec("gap_20ms_silence.wav", silence(50) + y[:1100] + [0.0] * 160 + y[1260:] + silence(50), ["C31AG/M"])
vec("inside_voice.wav", fake_voice(700) + burst.modulate("VA3BOC", amp=0.25) + fake_voice(700, seed=8), ["VA3BOC"])
try:
    vec("opus16k.wav", via(["ffmpeg -y -i {a} -c:a libopus -b:a 16k -application voip {t}/o.opus",
                            "ffmpeg -y -i {t}/o.opus -ar 8000 -ac 1 {b}"],
                           silence(100) + burst.modulate("DL1ABC", amp=0.3) + silence(200)), ["DL1ABC"])
except Exception as e:  # sin ffmpeg
    print("opus omitido:", e)
vec("voice_only.wav", fake_voice(4000, seed=12), [])
vec("noise_only.wav", noise(3000, 0.4, seed=2), [])
open(os.path.join(OUT, "expected.txt"), "w").write("\n".join(rows) + "\n")
print(len(rows), "vectores en", OUT)
