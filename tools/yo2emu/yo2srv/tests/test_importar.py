"""Importar yo2srv no arranca hilos, no abre ficheros ni toca sys.modules más allá de lo suyo (regla del paquete)."""
import json
import os
import subprocess
import sys
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

SONDA = r'''
import json, os, sys, threading
antes_mod = set(sys.modules); antes_hilos = threading.active_count()
antes_ficheros = set(os.listdir("."))
import yo2srv, yo2srv.server
nuevos = sorted(m for m in set(sys.modules) - antes_mod if not m.startswith("yo2srv"))
print(json.dumps({"fuera": nuevos, "hilos": threading.active_count() - antes_hilos,
                  "ficheros": sorted(set(os.listdir(".")) - antes_ficheros - {"__pycache__"})}))
'''


class TestImportar(unittest.TestCase):
    def test_import_limpio(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-c", SONDA], cwd=EMU, capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(r["hilos"], 0, "importar yo2srv no debe arrancar hilos")
        self.assertEqual(r["ficheros"], [], "importar yo2srv no debe crear ficheros en tools/yo2emu")
        # solo módulos de la biblioteca estándar; nada de run.py, proto, pvp_hub, research… (sin acoplar al servidor)
        propios = [m for m in r["fuera"] if m in ("run", "proto", "pvp_hub", "pvp_match", "ocg_duel", "storage", "frida")
                   or m.startswith(("research", "storage"))]
        self.assertEqual(propios, [], "yo2srv no debe importar código del servidor al cargarse")


if __name__ == "__main__":
    unittest.main()
