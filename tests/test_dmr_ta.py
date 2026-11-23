import os
import sys
import unittest

from hamcid import dmr_ta

GOLD = os.environ.get("HCID_TA_GOLD")   # ruta a talker_alias.py de junio (necesita bitarray + dmr_utils3)


class DmrTaTest(unittest.TestCase):
    def test_lcs(self):
        lcs = dmr_ta.build_ta_lcs("C31AG/ASL")
        self.assertEqual(len(lcs), 2)
        self.assertEqual(lcs[0][:3], bytes([0x04, 0x00, (1 << 6) | (9 << 1)]))
        self.assertEqual(lcs[0][3:], b"C31AG/")
        self.assertEqual(lcs[1], bytes([0x05, 0x00]) + b"ASL\0\0\0\0")

    @unittest.skipUnless(GOLD, "sin referencia")
    def test_matches_june_encoder_bit_for_bit(self):
        sys.path.insert(0, os.path.dirname(GOLD))
        import talker_alias as gold
        for text in ("C31AG/ASL", "ASL 60596", "EA3IGD/P via EchoLink X", "A"):
            for lc in dmr_ta.build_ta_lcs(text):
                self.assertEqual(lc, gold.build_ta_lcs(text, 1)[dmr_ta.build_ta_lcs(text).index(lc)])
                g = gold.encode_emblc(lc)
                m = dmr_ta.encode_emblc(lc)
                for v in range(1, 5):
                    self.assertEqual(m[v], int(g[v].to01(), 2), (text, v))
            # y el decodificador de referencia recupera el texto de nuestros fragmentos
            from bitarray import bitarray
            lcs = []
            for sf in dmr_ta.ta_superframes(text):
                b = bitarray()
                for v in range(1, 5):
                    b += bitarray(format(sf[v], "032b"))
                lcs.append(gold.decode_emblc(b))
            self.assertEqual(gold.decode_ta_lcs(lcs)["text"], text[:27])

    def test_injector_alternates_superframes(self):
        inj = dmr_ta.TaInjector()
        sid = b"\x01\x02\x03\x04"
        inj.start(sid, "C31AG/ASL")
        orig = bytes(range(33))

        def pkt(ftype, vseq):
            return b"DMRD" + b"\0" * 11 + bytes([(ftype << 4) | vseq]) + sid + orig + b"\0\0"

        seen = []
        for sf in range(3):
            frame_a = inj.process(pkt(1, 0))
            self.assertEqual(frame_a[20:53], orig)             # ráfaga A (sync) intacta
            embs = [dmr_ta._get_emb(inj.process(pkt(0, v))[20:53]) for v in range(1, 5)]
            f = inj.process(pkt(0, 5))
            self.assertEqual(f[20:53], orig)                     # ráfaga F intacta
            seen.append(embs)
        self.assertEqual(seen[0], [dmr_ta._get_emb(orig)] * 4)  # sin trama de referencia: LC original
        lcs = dmr_ta.build_ta_lcs("C31AG/ASL")
        self.assertEqual(seen[1], [dmr_ta.encode_emblc(lcs[0])[v] for v in range(1, 5)])
        self.assertEqual(seen[2], [dmr_ta.encode_emblc(lcs[1])[v] for v in range(1, 5)])
        # el resto de bits de la trama no cambia
        out = inj.process(pkt(0, 2))
        mask = ~(0xFFFFFFFF << (264 - 148))
        self.assertEqual(int.from_bytes(out[20:53], "big") & mask, int.from_bytes(orig, "big") & mask)

    def test_other_streams_untouched(self):
        inj = dmr_ta.TaInjector()
        p = b"DMRD" + b"\0" * 11 + bytes([1]) + b"\x09\x09\x09\x09" + bytes(33) + b"\0\0"
        self.assertEqual(inj.process(p), p)


if __name__ == "__main__":
    unittest.main()


class GroupLcTest(unittest.TestCase):
    def test_group_lc_rebuilt_from_header(self):
        inj = dmr_ta.TaInjector()
        sid = b"\x01\x02\x03\x04"
        hdr = b"DMRD" + b"\x00" + bytes.fromhex("208073") + bytes.fromhex("0000d5") + b"\0\0\0\1" + bytes([0x21]) + sid
        inj.start(sid, "C31AG/ASL", hdr + bytes(33) + b"\0\0")
        p = lambda v: hdr[:15] + bytes([v]) + sid + bytes(33) + b"\0\0"
        embs = [dmr_ta._get_emb(inj.process(p(v))[20:53]) for v in range(1, 5)]
        lc = bytes([0, 0, 0]) + bytes.fromhex("0000d5") + bytes.fromhex("208073")
        self.assertEqual(embs, [dmr_ta.encode_emblc(lc)[v] for v in range(1, 5)])
