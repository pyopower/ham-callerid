import math
import random
import unittest

from hamcid import dtmf

R = dtmf.RATE


def silence(ms):
    return [0.0] * (R * ms // 1000)


def noise(ms, amp, seed=1):
    rnd = random.Random(seed)
    return [rnd.uniform(-amp, amp) for _ in range(R * ms // 1000)]


def fake_voice(ms, seed=2):
    """Armónicos de una fundamental que cambia (imitación grosera de voz)."""
    rnd = random.Random(seed)
    out, f0, t = [], 140.0, 0
    for _ in range(ms // 20):
        f0 = min(260, max(90, f0 + rnd.uniform(-15, 15)))
        for _ in range(R // 50):
            out.append(sum(0.25 / h * math.sin(2 * math.pi * f0 * h * t / R) for h in range(1, 12)))
            t += 1
    return out


def add(a, b):
    n = max(len(a), len(b))
    a, b = a + [0.0] * (n - len(a)), b + [0.0] * (n - len(b))
    return [x + y for x, y in zip(a, b)]


class DtmfTest(unittest.TestCase):
    def test_clean(self):
        r = dtmf.decode(silence(200) + dtmf.encode("2130035") + silence(400))
        self.assertEqual([x.ident for x in r], ["2130035"])

    def test_without_terminator_closed_by_silence(self):
        r = dtmf.decode(dtmf.encode("2130035", terminator=False) + silence(500))
        self.assertEqual([x.ident for x in r], ["2130035"])

    def test_without_terminator_closed_by_flush(self):
        r = dtmf.decode(dtmf.encode("2130035", terminator=False) + silence(50))
        self.assertEqual([x.ident for x in r], ["2130035"])

    def test_repeated_digits(self):
        r = dtmf.decode(dtmf.encode("2110000") + silence(300))
        self.assertEqual([x.ident for x in r], ["2110000"])

    def test_fast_radio_timing(self):
        # radios rápidas: 40 ms de tono y 40 ms de pausa
        r = dtmf.decode(dtmf.encode("3341001", tone_ms=45, gap_ms=40) + silence(300))
        self.assertEqual([x.ident for x in r], ["3341001"])

    def test_with_noise(self):
        sig = silence(100) + dtmf.encode("2130035") + silence(300)
        r = dtmf.decode(add(sig, noise(len(sig) * 1000 // R, 0.03)))
        self.assertEqual([x.ident for x in r], ["2130035"])

    def test_after_voice(self):
        r = dtmf.decode(fake_voice(1500) + silence(100) + dtmf.encode("2130035") + silence(300))
        self.assertEqual([x.ident for x in r], ["2130035"])

    def test_voice_alone_gives_nothing(self):
        self.assertEqual(dtmf.decode(fake_voice(5000, seed=7)), [])

    def test_lookup_rejects_unknown(self):
        known = {"2130035": "C31AG"}
        r = dtmf.decode(dtmf.encode("9999999") + silence(300), lookup=known.get)
        self.assertEqual(r, [])

    def test_command_is_not_ptt_id(self):
        # *3 2000 (comando de enlace de app_rpt) no debe tomarse como PTT-ID
        audio = []
        for k in "*32000":
            audio += dtmf.tone(k) + silence(50)
        self.assertEqual(dtmf.decode(audio + silence(300)), [])

    def test_mute_covers_tones(self):
        sig = silence(200) + dtmf.encode("2130035") + silence(300)
        dec = dtmf.Decoder()
        for i in range(0, len(sig) - dtmf.BLOCK + 1, dtmf.BLOCK):
            dec.feed(sig[i:i + dtmf.BLOCK])
        first_tone = 200 // 10
        self.assertIn(first_tone + 2, dec.mute)
        self.assertNotIn(5, dec.mute)


if __name__ == "__main__":
    unittest.main()
