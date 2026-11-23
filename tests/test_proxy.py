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
