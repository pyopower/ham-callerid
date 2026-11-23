import os
import tempfile
import unittest

from hamcid.usrp_proxy import IdLookup, parse_set_info, set_info


class ProxyTest(unittest.TestCase):
    def test_lookup(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("2130035,C31AG,Adan\n2130099,C31AG,Otro\n1023007,VA3BOC,Hans\n9999,BAD,x\n3341001,XE1ABC,Y\n")
        try:
            ids = IdLookup(f.name)
            self.assertEqual(ids.id_for("C31AG"), 2130035)       # el menor de los suyos
            self.assertEqual(ids.id_for("c31ag/P"), 2130035)     # sufijo fuera
            self.assertEqual(ids.id_for("VA3BOC"), 1023007)
            self.assertIsNone(ids.id_for("C31A"))                # prefijo no basta
            self.assertIsNone(ids.id_for("BAD"))                 # ID de menos de 7 dígitos
            self.assertEqual(ids.call_for(3341001), "XE1ABC")
            self.assertIsNone(ids.call_for(1234567))
        finally:
            os.unlink(f.name)

    def test_set_info_roundtrip(self):
        self.assertEqual(parse_set_info(set_info(2130035, "C31AG", 213, 2)), (2130035, 213, "C31AG"))


if __name__ == "__main__":
    unittest.main()


class HbpRelayTest(unittest.TestCase):
    def test_dmra_packets_layout(self):
        from hamcid.usrp_proxy import dmra_packets
        p = dmra_packets(bytes.fromhex("208073"), "C31AG/ASL")
        self.assertEqual(len(p), 2)                       # 1 + 9 bytes -> 2 bloques de 7
        self.assertTrue(all(len(x) == 15 and x[:4] == b"DMRA" for x in p))
        buf = p[0][8:] + p[1][8:]
        self.assertEqual(buf[0], (2 << 6) | (9 << 1))     # UTF-8, longitud 9
        self.assertEqual(buf[1:10], b"C31AG/ASL")
        self.assertEqual([x[7] for x in p], [0, 1])

    def test_relay_injects_once_per_stream(self):
        import socket
        import time
        from hamcid.usrp_proxy import HbpRelay
        master = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); master.bind(("127.0.0.1", 0))
        master.settimeout(0.5)
        bridge = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); bridge.bind(("127.0.0.1", 0))
        bridge.settimeout(0.5)
        r = HbpRelay("127.0.0.1:0", "127.0.0.1:%d" % master.getsockname()[1], lambda: "C31AG/ASL", dmra=True)
        r.run()
        relay = r.local.getsockname()
        dmrd = lambda sid: b"DMRD" + b"\x01" + bytes.fromhex("208073") + bytes.fromhex("0000d5") + b"\0\0\0\1" + b"\x00" + sid + b"\0" * 33
        bridge.sendto(b"RPTL\0\0\0\1", relay)
        for _ in range(3):
            bridge.sendto(dmrd(b"\xaa\xbb\xcc\xdd"), relay)
        got = []
        try:
            while True:
                d, a = master.recvfrom(100); got.append(d[:4])
        except socket.timeout:
            pass
        self.assertEqual(got.count(b"DMRD"), 3)
        self.assertEqual(got.count(b"DMRA"), 2)          # una vez por llamada
        self.assertEqual(got[0], b"RPTL")
        master.sendto(b"RPTACK", a)                      # vuelta: llega a MMDVM_Bridge
        self.assertEqual(bridge.recvfrom(100)[0], b"RPTACK")


class PendingAliasTest(unittest.TestCase):
    def test_alias_set_after_stream_started(self):
        import socket
        from hamcid import dmr_ta
        from hamcid.usrp_proxy import HbpRelay
        master = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); master.bind(("127.0.0.1", 0))
        r = HbpRelay("127.0.0.1:0", "127.0.0.1:%d" % master.getsockname()[1], lambda: "")
        r.master = master.getsockname()
        sid = b"\xaa\xbb\xcc\xdd"
        hdr = b"DMRD" + b"\x00" + bytes.fromhex("208073") + bytes.fromhex("0000d5") + b"\0\0\0\1"
        pkt = lambda v: hdr + bytes([v]) + sid + bytes(33) + b"\0\0"
        r.from_bridge(pkt(0x21))
        self.assertIsNotNone(r.pending)                      # de ASL, sin indicativo todavía
        group = dmr_ta.encode_emblc(bytes(3) + bytes.fromhex("0000d5") + bytes.fromhex("208073"))
        self.assertEqual(dmr_ta._get_emb(r.injector.process(pkt(1))[20:53]), group[1])
        r.set_alias("C31AG/ASL", "ráfaga")
        self.assertIsNone(r.pending)
        for v in range(2, 5):
            r.injector.process(pkt(v))
        out = [dmr_ta._get_emb(r.injector.process(pkt(v))[20:53]) for v in range(1, 5)]
        ta = dmr_ta.encode_emblc(dmr_ta.build_ta_lcs("C31AG/ASL")[0])
        self.assertEqual(out, [ta[v] for v in range(1, 5)])  # la supertrama siguiente ya lleva el alias
