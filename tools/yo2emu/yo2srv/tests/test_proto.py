"""proto.py (raíz) es un alias del MISMO módulo que yo2srv/red/proto.py (paso E1a)."""
import os
import subprocess
import sys
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

SONDA = r'''
import sys
import proto
import yo2srv.red.proto as P
from proto import W, Reader, pack, server_entry
print(proto is P, W is P.W, Reader is P.Reader, pack is P.pack, sys.modules["proto"] is P, proto.__name__)
r = Reader().feed(pack(0x2005, b"abc", 7))
print(r == [(0x2005, 7, b"abc", True)], len(server_entry(1, 1, "x", "127.0.0.1", 5731)))
'''


class TestProtoAlias(unittest.TestCase):
    def test_import_proto_es_el_modulo_del_paquete(self):
        p = subprocess.run([sys.executable, "-c", SONDA], cwd=EMU, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(p.returncode, 0, p.stderr)
        l1, l2 = p.stdout.strip().splitlines()[-2:]
        self.assertEqual(l1, "True True True True True yo2srv.red.proto")
        self.assertEqual(l2, "True 48")                              # server_entry mide 48 bytes (0x2003)

    def test_importar_el_paquete_no_carga_proto(self):
        """yo2srv no depende de proto de la raíz; solo se carga si alguien lo pide (test_importar lo vigila)."""
        sonda = "import sys, yo2srv, yo2srv.server; print('proto' in sys.modules, 'yo2srv.red.proto' in sys.modules)"
        p = subprocess.run([sys.executable, "-c", sonda], cwd=EMU, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
