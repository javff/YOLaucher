"""log.py: la copia de la log() de run.py: máscara antes de escribir, marca [t], nunca lanza, excepthooks."""
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import log as L  # noqa: E402


class TestLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="log-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def contenido(self, lg):
        lg.f.flush()
        with open(lg.path, encoding="utf-8") as f:
            return f.read()

    def test_crea_fichero_con_fecha_y_escribe(self):
        lg = L.Log(self.dir, consola=False)
        self.addCleanup(lg.cerrar)
        self.assertRegex(os.path.basename(lg.path), r"^\d{8}-\d{6}\.log$")
        lg("hola", 3, "mundo")
        txt = self.contenido(lg)
        self.assertIn("hola 3 mundo\n", txt)
        self.assertRegex(txt, r"^\[t\] \d\d:\d\d:\d\d\n")        # B27: marca de segundo antes de la línea

    def test_marca_de_tiempo_solo_cuando_cambia_el_segundo(self):
        lg = L.Log(self.dir, consola=False)
        self.addCleanup(lg.cerrar)
        for i in range(50):
            lg("linea", i)
        marcas = len(re.findall(r"^\[t\] ", self.contenido(lg), re.M))
        self.assertGreaterEqual(marcas, 1)
        self.assertLess(marcas, 50)

    def test_la_mascara_va_antes_de_escribir(self):
        lg = L.Log(self.dir, consola=False, mascara=lambda s: s.replace("secreto", "******"))
        self.addCleanup(lg.cerrar)
        lg("el secreto es 1234")
        txt = self.contenido(lg)
        self.assertIn("el ****** es 1234", txt)
        self.assertNotIn("secreto", txt)

    def test_mascara_que_falla_nunca_deja_la_linea_en_claro(self):
        def rota(s):
            raise RuntimeError("máscara rota")

        lg = L.Log(self.dir, consola=False, mascara=rota)
        self.addCleanup(lg.cerrar)
        lg("contraseña: hunter2")
        txt = self.contenido(lg)
        self.assertNotIn("hunter2", txt)
        self.assertIn("falló la máscara", txt)

    def test_mascara_real_de_logseguro_oculta_el_0x3003(self):
        lg = L.Log(self.dir, consola=False)
        self.addCleanup(lg.cerrar)
        linea = L.rx_line(5731, 0x3003, 0, bytes(range(32)), True)
        self.assertNotIn("00 01 02 03", linea, "0x3003 sin bytes (logseguro)")
        lg(linea)
        self.assertNotIn("00 01 02 03", self.contenido(lg))
        self.assertIn("<- 0x3003", self.contenido(lg))

    def test_mascara_bruta_sin_logseguro(self):
        guardado = list(L._LOGSEG)
        L._LOGSEG[:] = [None]                                   # como si logseguro.py no cargara
        try:
            self.assertIn("[oculto: logseguro.py no cargado]",
                          L.log_mask("[srv:5731] <- 0x3003 #0 len=32 md5=ok 00 11 22"))
            self.assertIn("[contenido oculto]", L.rx_line(5731, 0x3003, 0, b"\x00" * 32, True))
            self.assertIn("[contenido oculto]", L.rx_line(5732, 0x8101, 0, b"abc", True))
            self.assertEqual(L.log_mask("[srv:5732] <- 0x4100"), "[srv:5732] <- 0x4100")
            self.assertEqual(L.log_mask("deadbeef  00 11 22 33"), "deadbeef  [volcado oculto: logseguro.py no cargado]")
            self.assertIn("61 62", L.rx_line(5732, 0x4100, 0, b"ab", True))
        finally:
            L._LOGSEG[:] = guardado

    def test_nunca_lanza(self):
        class Raro:
            def __str__(self):
                raise RuntimeError("no imprimible")

        lg = L.Log(self.dir, consola=False)
        lg(Raro())
        self.assertIn("línea no imprimible", self.contenido(lg))
        lg.cerrar()
        lg("con el fichero cerrado")                             # no lanza

    def test_fichero_ya_abierto_y_consola(self):
        f = io.StringIO()
        f.name = "mem"
        lg = L.Log(fichero=f, consola=False)
        lg("x")
        self.assertIn("x\n", f.getvalue())

    def test_hilos_no_mezclan_lineas(self):
        lg = L.Log(self.dir, consola=False)
        self.addCleanup(lg.cerrar)
        hs = [threading.Thread(target=lambda n=n: [lg(f"hilo{n} " + "x" * 50) for _ in range(100)]) for n in range(4)]
        [h.start() for h in hs]
        [h.join() for h in hs]
        lineas = [x for x in self.contenido(lg).splitlines() if not x.startswith("[t] ")]
        self.assertEqual(len(lineas), 400)
        self.assertTrue(all(re.fullmatch(r"hilo\d x{50}", x) for x in lineas))

    def test_excepthooks_van_al_log(self):
        lg = L.Log(self.dir, consola=False)
        self.addCleanup(lg.cerrar)
        viejos = L.instalar_excepthooks(lg)
        self.addCleanup(L.restaurar_excepthooks, viejos)

        def falla():
            raise ValueError("desde un hilo")

        t = threading.Thread(target=falla, name="hilo-de-prueba")
        t.start()
        t.join()
        try:
            raise KeyError("principal")
        except KeyError:
            sys.excepthook(*sys.exc_info())
        txt = self.contenido(lg)
        self.assertIn("[EXC] hilo hilo-de-prueba:", txt)
        self.assertIn("ValueError('desde un hilo')".split("(")[0], txt)
        self.assertIn("[EXC] ", txt)
        self.assertIn("KeyError", txt)
        self.assertEqual(len([x for x in txt.splitlines() if "[EXC]" in x]), 2, "una línea por excepción")


if __name__ == "__main__":
    unittest.main()
