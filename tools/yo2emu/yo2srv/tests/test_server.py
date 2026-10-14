"""server.py + compat.py: el namespace vivo detrás de srv, alias, servicios propios y nada que se cuele en el dict."""
import os
import sys
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import compat  # noqa: E402
from yo2srv.server import Server  # noqa: E402


class TestServer(unittest.TestCase):
    def setUp(self):
        self.salida = []
        self.ns = {"HUB": object(), "LP": object(), "PLAYER_ID": 1, "log": lambda *a: self.salida.append(a)}
        self.srv = Server(self.ns)

    def test_ns_es_el_mismo_dict(self):
        self.assertIs(self.srv.ns, self.ns)

    def test_lectura_por_nombre_viejo_y_nuevo(self):
        self.assertIs(self.srv.HUB, self.ns["HUB"])
        self.assertIs(self.srv.hub, self.ns["HUB"])
        self.assertIs(self.srv.lobby, self.ns["LP"])
        self.assertIs(self.srv.LP, self.ns["LP"])

    def test_escritura_va_al_namespace(self):
        self.srv.PLAYER_ID = 7
        self.assertEqual(self.ns["PLAYER_ID"], 7)
        self.srv.player_id = 8                           # alias minúsculo -> PLAYER_ID
        self.assertEqual(self.ns["PLAYER_ID"], 8)
        self.assertNotIn("player_id", self.ns)
        self.srv.NUEVO = 1
        self.assertEqual(self.ns["NUEVO"], 1)

    def test_reasignar_en_caliente_se_ve_en_la_siguiente_lectura(self):
        nuevo = object()
        self.ns["HUB"] = nuevo                           # lo que hace el panel con NS["NPCS"] = load_npcs()
        self.assertIs(self.srv.HUB, nuevo)

    def test_atributo_inexistente(self):
        self.assertFalse(hasattr(self.srv, "NO_EXISTE"))
        with self.assertRaises(AttributeError):
            self.srv.NO_EXISTE
        self.assertFalse(self.srv.existe("NO_EXISTE"))
        self.assertTrue(self.srv.existe("hub"))

    def test_servicios_propios_no_se_cuelan_en_el_namespace(self):
        antes = set(self.ns)
        for s in ("cfg", "bus", "router", "hot", "rutas"):
            self.assertIsNotNone(getattr(self.srv, s))
        self.srv.bus.emit("x")
        self.srv.cfg.todas()
        self.assertEqual(set(self.ns), antes, "Server no añade nada al namespace por su cuenta")

    def test_los_servicios_son_los_de_la_clase(self):
        from yo2srv import bus, config, hot, router
        self.assertIsInstance(self.srv.bus, bus.Bus)
        self.assertIsInstance(self.srv.router, router.Router)
        self.assertIsInstance(self.srv.cfg, config.Config)
        self.assertIsInstance(self.srv.hot, hot.Hot)
        self.assertIs(self.srv.cfg.ns, self.ns)
        self.assertIs(self.srv.hot.srv, self.srv)

    def test_ns_tiene_que_ser_un_dict_de_verdad(self):
        with self.assertRaises(TypeError):
            Server(type("Vista", (), {"__getitem__": lambda s, k: 1})())
        from collections import UserDict
        with self.assertRaises(TypeError):
            Server(UserDict())

    def test_registrar_nombres(self):
        self.srv.registrar_nombres(A=1, B=2)
        self.assertEqual((self.ns["A"], self.ns["B"]), (1, 2))

    def test_log_usa_el_log_vivo_y_nunca_lanza(self):
        self.srv.log("hola", 1)
        self.assertEqual(self.salida, [("hola", 1)])
        self.ns["log"] = lambda *a: 1 / 0
        self.srv.log("x")                                # no lanza
        del self.ns["log"]
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.srv.log("a stderr")                     # sin log: a stderr y no lanza
        self.assertIn("a stderr", err.getvalue())

    def test_el_bus_y_el_router_anotan_por_el_log_vivo(self):
        self.srv.bus.subscribe("e", lambda: 1 / 0)
        self.srv.bus.emit("e")
        self.assertTrue(any("[bus]" in a[0] for a in self.salida), self.salida)

    def test_estado_y_repr(self):
        e = self.srv.estado()
        self.assertEqual(e["perfil"], "vanilla")
        self.assertGreater(e["banderas"], 100)
        self.assertEqual((e["bus"], e["router"], e["modulos_hot"]), ([], {}, []))
        self.assertIn("Server", repr(self.srv))

    def test_idempotencia_de_la_linea_de_run_py(self):
        # SRV = globals().get("SRV") or Server(globals()): una re-ejecución conserva el mismo objeto
        g = dict(self.ns)
        g["SRV"] = Server(g)
        otro = g.get("SRV") or Server(g)
        self.assertIs(otro, g["SRV"])


class TestCompat(unittest.TestCase):
    def test_alias_apuntan_a_nombres_de_hoy(self):
        self.assertEqual(compat.nombre_ns("hub"), "HUB")
        self.assertEqual(compat.nombre_ns("panel"), "ADMIN_WEB")
        self.assertEqual(compat.nombre_ns("load_state"), "load_state")     # lo que no está se busca tal cual
        for nuevo, viejo in compat.ALIAS.items():
            self.assertEqual(nuevo, nuevo.lower(), f"alias nuevo en minúsculas: {nuevo}")
            self.assertNotEqual(nuevo, viejo)

    def test_alias_no_pisan_los_servicios_propios(self):
        self.assertFalse({"ns", "cfg", "bus", "router", "hot", "rutas", "log"} & set(compat.ALIAS))

    def test_los_nombres_viejos_estan_en_el_contrato_del_namespace(self):
        contrato = os.path.join(EMU, "paridad", "contrato_ns.txt")
        if not os.path.exists(contrato):
            self.skipTest("sin paridad/contrato_ns.txt")
        nombres = {ln.split("\t")[0] for ln in open(contrato, encoding="utf-8") if ln.strip() and not ln.startswith("#")}
        falta = sorted(v for v in compat.ALIAS.values() if v not in nombres and v not in
                       ("AUTO", "PH_GAME", "PH_LOBBY", "DF", "LS", "DFM", "EQ", "TP", "TO"))
        self.assertEqual(falta, [], "alias hacia nombres que el contrato no conoce (revisa la tabla)")

    def test_alias_modulo(self):
        import types
        viejo = "yo2srv_prueba_alias_viejo"
        self.addCleanup(lambda: sys.modules.pop(viejo, None))
        m = compat.alias_modulo(viejo, "json")
        self.assertIs(sys.modules[viejo], m)
        self.assertIsInstance(m, types.ModuleType)
        import json
        self.assertIs(m, json)


if __name__ == "__main__":
    unittest.main()
