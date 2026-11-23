import unittest

from hamcid import burst
from tests.test_burst import ulaw_roundtrip
from tests.test_dtmf import fake_voice, silence


def frames(x, n=160):
    x = x + [0.0] * (-len(x) % n)
    return [x[i:i + n] for i in range(0, len(x), n)]


class StreamTest(unittest.TestCase):
    def run_all(self, x):
        d = burst.StreamDecoder()
        out = []
        for f in frames(x):
            out += d.feed(f)
        return out + d.flush()

    def test_burst_between_voice(self):
        x = fake_voice(600) + burst.modulate("C31AG", amp=0.25) + fake_voice(800, seed=5)
        r = self.run_all(ulaw_roundtrip(x))
        self.assertEqual([t for t, _, _ in r], ["C31AG"])
        _, a, b = r[0]
        self.assertLessEqual(a, 600 // 20)
        self.assertGreaterEqual(b, (600 + 140) // 20 - 1)
        self.assertLessEqual(b, (600 + 140) // 20 + 2)

    def test_end_burst_then_nothing(self):
        r = self.run_all(fake_voice(500) + burst.modulate("EA3IGD"))
        self.assertEqual([t for t, _, _ in r], ["EA3IGD"])

    def test_voice_has_no_candidates(self):
        x = fake_voice(5000, seed=11)
        cand = sum(burst.mark_purity(x[i:i + 80]) > 0.8 for i in range(0, len(x) - 80, 40))
        self.assertLess(cand, 3)
        self.assertEqual(self.run_all(fake_voice(5000, seed=11)), [])


if __name__ == "__main__":
    unittest.main()
