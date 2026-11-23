import math
import random
import unittest

from hamcid import burst
from tests.test_dtmf import fake_voice, silence, noise, add

R = burst.RATE


def ulaw_roundtrip(x):
    """Cuantificación µ-law de 8 bits (como IAX/ASL)."""
    mu = 255.0
    out = []
    for v in x:
        v = max(-1.0, min(1.0, v))
        y = math.copysign(math.log1p(mu * abs(v)) / math.log1p(mu), v)
        q = round(y * 127) / 127
        out.append(math.copysign((math.pow(1 + mu, abs(q)) - 1) / mu, q))
    return out


def resample(x, ratio):
    """Desviación de reloj entre emisor y receptor (ratio ~ 1 ± 0,005)."""
    n = int(len(x) / ratio)
    out = []
    for i in range(n):
        p = i * ratio
        k = int(p)
        if k + 1 >= len(x):
            break
        f = p - k
        out.append(x[k] * (1 - f) + x[k + 1] * f)
    return out


class BurstTest(unittest.TestCase):
    def test_duration(self):
        ms = len(burst.modulate("C31AG")) * 1000 / R
        self.assertLess(ms, 230)

    def test_clean(self):
        r = burst.demodulate(silence(50) + burst.modulate("C31AG") + silence(50))
        self.assertEqual([(t, s) for t, s, _ in r], [(burst.TYPE_CALLSIGN, "C31AG")])

    def test_ulaw(self):
        r = burst.demodulate(ulaw_roundtrip(silence(30) + burst.modulate("EA3IGD/P") + silence(30)))
        self.assertEqual([s for _, s, _ in r], ["EA3IGD/P"])

    def test_low_level_ulaw_and_noise(self):
        sig = silence(40) + burst.modulate("HP3ICC", amp=0.08) + silence(40)
        sig = add(sig, noise(len(sig) * 1000 // R, 0.01, seed=5))
        r = burst.demodulate(ulaw_roundtrip(sig))
        self.assertEqual([s for _, s, _ in r], ["HP3ICC"])

    def test_clock_drift(self):
        for ratio in (0.995, 1.005):
            sig = silence(30) + burst.modulate("C31AG") + silence(30)
            r = burst.demodulate(resample(sig, ratio))
            self.assertEqual([s for _, s, _ in r], ["C31AG"], ratio)

    def test_inside_voice(self):
        sig = fake_voice(800) + burst.modulate("C31AG") + fake_voice(800, seed=4)
        r = burst.demodulate(ulaw_roundtrip(sig))
        self.assertEqual([s for _, s, _ in r], ["C31AG"])

    def test_two_bursts(self):
        b = burst.modulate("C31AG")
        r = burst.demodulate(b + fake_voice(1000) + b)
        self.assertEqual([s for _, s, _ in r], ["C31AG", "C31AG"])

    def test_voice_and_noise_give_nothing(self):
        self.assertEqual(burst.demodulate(fake_voice(4000, seed=9)), [])
        self.assertEqual(burst.demodulate(noise(4000, 0.5, seed=3)), [])

    def test_rejects_bad_callsign(self):
        with self.assertRaises(ValueError):
            burst.frame_bits("C31AG!")


if __name__ == "__main__":
    unittest.main()


class GolayTest(unittest.TestCase):
    def test_corrects_three(self):
        import random
        r = random.Random(7)
        for _ in range(500):
            d = r.randrange(4096)
            e = 0
            for p in r.sample(range(24), 3):
                e |= 1 << p
            self.assertEqual(burst.golay_decode(burst.golay_encode(d) ^ e)[0], d)

    def test_survives_20ms_gap_filled_with_silence(self):
        x = burst.modulate("C31AG", amp=0.3)
        for gap_at in (900, 1300, 1600):           # dentro del bloque codificado
            y = x[:gap_at] + [0.0] * 160 + x[gap_at + 160:]
            r = burst.demodulate([0.0] * 300 + y + [0.0] * 300)
            self.assertEqual([t for _, t, _ in r], ["C31AG"], gap_at)
