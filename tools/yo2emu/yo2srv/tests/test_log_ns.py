"""log.py, parte de run.py (paso E1a): `iniciar(ns, logdir)` enlaza el log al namespace. Lo reasignable en caliente
(`_log_mask`, `log`) se busca en el namespace en CADA llamada; `_LOGSEG` es una sola lista; `_admin_ev` no lanza."""
import io
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import log as L  # noqa: E402


class TestLogNamespace(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="logns-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        guardado = (L._NS, L._VIVO[0], threading.excepthook, sys.excepthook, sys.stdout.encoding)
        self.addCleanup(self._restaurar, guardado)
        salidas = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()      # el log también imprime: que no ensucie la salida
        self.addCleanup(lambda: setattr(sys, 'stdout', salidas[0]) or setattr(sys, 'stderr', salidas[1]))

    @staticmethod
    def _restaurar(g):
        L._NS, L._VIVO[0], threading.excepthook, sys.excepthook = g[:4]

    def iniciar(self, ns=None):
        ns = {} if ns is None else ns
        logf, path, lock, t = L.iniciar(ns, self.dir)
        self.addCleanup(logf.close)
        return ns, logf, path, lock, t

    def texto(self, logf):
        logf.flush()
        with open(logf.name, encoding="utf-8") as f:
            return f.read()

    def test_iniciar_devuelve_los_cuatro_nombres_de_run_py(self):
        ns, logf, path, lock, t = self.iniciar()
        self.assertEqual(os.path.dirname(path), self.dir)
        self.assertRegex(os.path.basename(path), r"^\d{8}-\d{6}\.log$")
        self.assertEqual(logf.name, path)
        self.assertEqual(t, [None])                               # _LOG_T era una lista de un elemento
        self.assertTrue(hasattr(lock, "acquire"))                 # _LOG_LOCK
        L.log("hola", 7)
        self.assertIn("hola 7\n", self.texto(logf))
        self.assertEqual(t[0] is not None, True)                  # la misma lista: ya lleva la marca [t]

    def test_log_mask_del_namespace_manda_en_cada_linea(self):
        """test_logseguro: por la consola se pone `_log_mask = lambda line: line` y el log vuelve a salir en claro."""
        ns, logf, *_ = self.iniciar()
        L.log("clave: hunter2")                                   # sin _log_mask en ns: la de logseguro del módulo
        ns["_log_mask"] = lambda s: s.replace("hunter2", "******")
        L.log("segunda hunter2")
        ns["_log_mask"] = lambda s: s                              # simulación del log viejo
        L.log("tercera hunter2")
        txt = self.texto(logf)
        self.assertIn("segunda ******", txt)
        self.assertIn("tercera hunter2", txt)

    def test_el_log_del_namespace_es_el_que_usan_los_excepthooks(self):
        """hot_logseguro.py sustituye G["log"] por una envoltura: [EXC] tiene que salir por ella."""
        ns, logf, *_ = self.iniciar()
        vistas = []
        ns["log"] = lambda *a: vistas.append(a)

        def falla():
            raise ValueError("desde un hilo")

        t = threading.Thread(target=falla, name="hilo-ns")
        t.start()
        t.join()
        self.assertEqual(len(vistas), 1)
        self.assertIn("[EXC] hilo hilo-ns:", vistas[0][0])
        try:
            raise KeyError("principal")
        except KeyError:
            sys.excepthook(*sys.exc_info())
        self.assertEqual(len(vistas), 2)
        self.assertIn("KeyError", vistas[1][0])
        self.assertNotIn("\n", vistas[1][0])                       # una sola línea (los \n pasan a « | »)
        self.assertNotIn("[EXC]", self.texto(logf))               # no ha ido por el log de aquí

    def test_sin_log_en_el_namespace_cae_al_de_aqui(self):
        ns, logf, *_ = self.iniciar()
        ns.pop("log", None)
        t = threading.Thread(target=lambda: 1 / 0, name="hilo-sin-log")
        t.start()
        t.join()
        self.assertIn("[EXC] hilo hilo-sin-log:", self.texto(logf))

    def test_nombres_de_run_py_existen_y_son_del_tipo_del_contrato(self):
        self.assertEqual(type(L.log).__name__, "function")        # contrato_ns.txt: log = function
        self.assertEqual(type(L.rx_line).__name__, "function")
        self.assertIs(type(L._LOGSEG), list)
        ns, logf, *_ = self.iniciar()
        self.assertEqual(type(logf).__name__, "TextIOWrapper")    # contrato_ns.txt: logf = TextIOWrapper
        self.assertIs(L.logseguro, L.logseguro)
        self.assertTrue(callable(L._thread_excepthook) and callable(L._admin_ev))

    def test_log_antes_de_iniciar_no_lanza(self):
        L._VIVO[0] = None
        L.log("antes de iniciar", object())                       # no lanza

    def test_admin_ev(self):
        antes = sys.modules.get("yo2_eventos")
        self.addCleanup(lambda: sys.modules.__setitem__("yo2_eventos", antes) if antes is not None
                        else sys.modules.pop("yo2_eventos", None))
        sys.modules.pop("yo2_eventos", None)
        self.assertIsNone(L._admin_ev("on_select", 1))             # sin panel: nada
        m = types.ModuleType("yo2_eventos")
        m.on_select = lambda s, extra=None: ("visto", s, extra)
        m.roto = lambda: 1 / 0
        m.no_funcion = 5
        sys.modules["yo2_eventos"] = m
        self.assertEqual(L._admin_ev("on_select", 3, extra="x"), ("visto", 3, "x"))
        self.assertIsNone(L._admin_ev("roto"))                     # un fallo del panel nunca rompe el juego
        self.assertIsNone(L._admin_ev("no_funcion"))
        self.assertIsNone(L._admin_ev("no_existe"))


if __name__ == "__main__":
    unittest.main()
