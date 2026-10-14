"""Pasa las pruebas unitarias de yo2srv/tests (sin puertos, sin instancia): python -m yo2srv.tests [-v]

test_vivo.py (instancia de prueba por su consola) NO entra aquí: se pasa aparte (python yo2srv/tests/test_vivo.py
--offset N) y la batería (paridad/bateria.py) lo corre como T10.yo2srv_vivo.
Imprime «RESULTADO yo2srv: a/b bien; mal: [...]» (el formato que cuenta la batería) y sale con 1 si algo falla.
"""
import os
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(AQUI, "..", ".."))


def main(argv):
    if EMU not in sys.path:
        sys.path.insert(0, EMU)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    verbose = 2 if "-v" in argv else 1
    suite = unittest.defaultTestLoader.discover(AQUI, pattern="test_*.py", top_level_dir=EMU)
    res = unittest.TextTestRunner(verbosity=verbose, stream=sys.stdout).run(suite)
    mal = sorted({t.id().split(".", 2)[-1] for t, _ in res.failures + res.errors})
    total = res.testsRun - len(res.skipped)
    bien = total - len(res.failures) - len(res.errors)
    print(f"== RESULTADO yo2srv: {bien}/{total} bien" + (f"; mal: {mal}" if mal else "")
          + (f" ({len(res.skipped)} omitidas)" if res.skipped else ""))
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
