"""R4, unitarias de la recarga en caliente con un servidor falso (nada de run.py ni de puertos):

    python yo2srv/tests/hot_unit.py          (sale con 1 si algo falla; imprime «== RESULTADO hot unitarias: a/b bien»)

Qué cubre (lo de S1 sigue en test_hot.py): dependientes en cascada (recargar A vuelve a ejecutar B, que ve el A NUEVO mientras
se ejecuta) y su vuelta atrás conjunta, grupos sin relación que se recargan por separado, banderas del registro de config.py y
claves de dicts cambiadas en vivo que sobreviven (y las que nadie tocó toman el valor nuevo del fichero), huella en el log
(«sha=… antes=…», «sin cambios»), ganchos con `tx` (lo que se toca fuera del módulo se deshace si algo falla después),
`post` tras el cambio, `listar()` y las ayudas de recarga.py (reasignar_clase con el envoltorio de instancia del panel).
Con un fichero de módulo que se reescribe entre recargas; los nombres de las banderas son los REALES del registro
(`lobby.LIMIT_LIST` vive en el módulo `LP`, `duelo.kind_main` en `DEFAULT_CFG` del traductor).

Este fichero NO se llama test_*.py a propósito: `python -m yo2srv.tests` (T10.yo2srv) sigue siendo exactamente el de antes de R4
y esta va como prueba propia en la batería (T10.hot_unit).
"""
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import bus as B, config as C, hot as H, recarga as RC, router as R  # noqa: E402


class FalsoSrv:
    def __init__(self):
        self.ns = {}
        self.lineas = []
        self.router = R.Router(log=self.lineas.append)
        self.bus = B.Bus(log=self.lineas.append)

    def log(self, *a):
        self.lineas.append(" ".join(str(x) for x in a))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hotu-")
        self.srv = FalsoSrv()
        self.hot = H.Hot(self.srv)
        self.sys_nombres = []
        self.addCleanup(self._limpiar)

    def _limpiar(self):
        for n in self.sys_nombres:
            sys.modules.pop(n, None)
        shutil.rmtree(self.dir, True)

    def fichero(self, nombre, txt):
        ruta = os.path.join(self.dir, nombre)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(txt))
        return ruta

    def registrar(self, nombre, ruta, sys_nombre, **kw):
        self.sys_nombres.append(sys_nombre)
        return self.hot.registrar(nombre, ruta, sys_nombre=sys_nombre, **kw)

    def log(self):
        return "\n".join(self.srv.lineas)


class Cascada(Base):
    def montar(self, version_a=1, version_b=1):
        ra = self.fichero("ua.py", f"VERSION = {version_a}\n")
        rb = self.fichero("ub.py", f"import yo2u_a\nVISTO = yo2u_a.VERSION\nVERSION = {version_b}\n")
        sys.modules.pop("yo2u_a", None)
        self.registrar("u.a", ra, "yo2u_a", ns_nombre="UA", dependientes=("u.b",))
        self.registrar("u.b", rb, "yo2u_b", ns_nombre="UB")
        return ra, rb

    def test_recargar_a_vuelve_a_ejecutar_b_que_ve_el_a_nuevo(self):
        ra, rb = self.montar()
        self.assertEqual(self.hot.recargar("u.a"), {"u.a": "ok", "u.b": "ok"})
        self.assertEqual(sys.modules["yo2u_b"].VISTO, 1)
        self.fichero("ua.py", "VERSION = 2\n")
        a1, b1 = sys.modules["yo2u_a"], sys.modules["yo2u_b"]
        self.assertEqual(self.hot.recargar("u.a"), {"u.a": "ok", "u.b": "ok"})
        a2, b2 = sys.modules["yo2u_a"], sys.modules["yo2u_b"]
        self.assertIsNot(a2, a1)
        self.assertIsNot(b2, b1, "el dependiente se vuelve a ejecutar")
        self.assertEqual(b2.VISTO, 2, "y al ejecutarse ve el módulo NUEVO del grupo")
        self.assertIs(self.srv.ns["UA"], a2)
        self.assertIs(self.srv.ns["UB"], b2)
        self.assertEqual(b1.VISTO, 1, "el B viejo conserva el A viejo (lo que está en curso no se mezcla)")
        self.assertIn("(por dependencia)", self.log())

    def test_pedir_los_dos_los_recarga_una_vez(self):
        self.montar()
        self.hot.recargar("u.a")
        n = self.log().count("[recarga] u.b ok")
        self.assertEqual(self.hot.recargar("u.a", "u.b"), {"u.a": "ok", "u.b": "ok"})
        self.assertEqual(self.log().count("[recarga] u.b ok"), n + 1)

    def test_pedir_solo_el_dependiente_no_arrastra_al_otro(self):
        self.montar()
        self.hot.recargar("u.a")
        a = sys.modules["yo2u_a"]
        self.assertEqual(self.hot.recargar("u.b"), {"u.b": "ok"})
        self.assertIs(sys.modules["yo2u_a"], a)

    def test_si_falla_el_dependiente_se_deshace_el_grupo_entero(self):
        ra, rb = self.montar()
        self.hot.recargar("u.a")
        a, b = sys.modules["yo2u_a"], sys.modules["yo2u_b"]
        self.fichero("ua.py", "VERSION = 3\n")
        self.fichero("ub.py", "raise RuntimeError('b roto')\n")
        r = self.hot.recargar("u.a")
        self.assertIn("b roto", r["u.b"])
        self.assertTrue(r["u.a"].startswith("error: no recargado: se deshace el grupo"), r)
        self.assertIs(sys.modules["yo2u_a"], a)
        self.assertIs(sys.modules["yo2u_b"], b)
        self.assertIs(self.srv.ns["UA"], a)
        self.assertEqual(a.VERSION, 1)
        self.assertFalse([k for k in sys.modules if "__hot" in k and k.startswith("yo2u_")])

    def test_si_falla_el_setup_del_dependiente_vuelve_todo(self):
        self.montar()
        self.hot.recargar("u.a")
        a, b = sys.modules["yo2u_a"], sys.modules["yo2u_b"]

        def mal(nuevo, viejo, srv, tx):
            raise RuntimeError("setup de b roto")
        self.hot.registro["u.b"].setup = mal
        r = self.hot.recargar("u.a")
        self.assertIn("setup de b roto", r["u.b"])
        self.assertIs(sys.modules["yo2u_a"], a)
        self.assertIs(self.srv.ns["UB"], b)

    def test_modulos_sin_relacion_se_recargan_por_separado(self):
        rc = self.fichero("uc.py", "X = 1\n")
        rd = self.fichero("ud.py", "X = 1\n")
        self.registrar("u.c", rc, "yo2u_c", ns_nombre="UC")
        self.registrar("u.d", rd, "yo2u_d", ns_nombre="UD")
        self.hot.recargar("u.c", "u.d")
        c, d = sys.modules["yo2u_c"], sys.modules["yo2u_d"]
        self.fichero("uc.py", "def roto(:\n")
        r = self.hot.recargar("u.c", "u.d")
        self.assertTrue(r["u.c"].startswith("error: no compila"))
        self.assertEqual(r["u.d"], "ok", "el fallo de uno no frena al otro")
        self.assertIs(sys.modules["yo2u_c"], c)
        self.assertIsNot(sys.modules["yo2u_d"], d)

    def test_un_noreload_pedido_no_arrastra_ni_se_cuela_como_dependiente(self):
        ra = self.fichero("ua.py", "VERSION = 1\n")
        rb = self.fichero("ub.py", "VERSION = 1\n")
        self.registrar("u.a", ra, "yo2u_a", dependientes=("u.b",))
        self.registrar("u.b", rb, "yo2u_b", noreload=True, motivo="lleva estado")
        r = self.hot.recargar("u.a")
        self.assertEqual(r, {"u.a": "ok"})
        self.assertIn("lleva estado", self.hot.recargar("u.b")["u.b"])


class Ficheros(Base):
    def test_vale_cualquier_extension_y_un_fallo_al_ejecutar_no_se_escapa(self):
        ruta = self.fichero("modulo.txt", "X = 1" + chr(10))
        self.registrar("u.t", ruta, "yo2u_t", ns_nombre="UT")
        self.assertEqual(self.hot.recargar("u.t"), {"u.t": "ok"})
        self.assertEqual(sys.modules["yo2u_t"].X, 1)
        self.assertEqual(sys.modules["yo2u_t"].__file__, ruta)
        self.fichero("modulo.txt", "X = 2" + chr(10) + "raise RuntimeError('al ejecutar')" + chr(10))
        r = self.hot.recargar("u.t")
        self.assertIn("al ejecutar", r["u.t"])
        self.assertEqual(sys.modules["yo2u_t"].X, 1)


class Banderas(Base):
    """Las banderas del registro de config.py cuya liga cae en el módulo, y las claves de los dicts declarados."""

    LP = """
        LIMIT_LIST = True
        WATCH = True
        ROOM_REOPEN = True
        EXTRA = 5
    """
    TR = """
        DEFAULT_CFG = {"kind_main": 3, "lp": 8000, "suelta": 1}
    """

    def montar(self):
        ruta = self.fichero("lp.py", self.LP)
        rt = self.fichero("tr.py", self.TR)
        for n in ("yo2u_lp", "yo2u_tr"):
            sys.modules.pop(n, None)
        # el módulo «ya cargado» (arranque): se carga a mano y se registra con cargado=True
        for nombre, fich, sysn in (("u.lp", ruta, "yo2u_lp"), ("u.tr", rt, "yo2u_tr")):
            import importlib.util
            spec = importlib.util.spec_from_file_location(sysn, fich)
            m = importlib.util.module_from_spec(spec)
            sys.modules[sysn] = m
            spec.loader.exec_module(m)
        self.registrar("u.lp", ruta, "yo2u_lp", ns_nombre="LP", cargado=True)
        self.registrar("u.tr", rt, "yo2u_tr", dicts=("DEFAULT_CFG",), trcfg=True, cargado=True)
        self.srv.ns["LP"] = sys.modules["yo2u_lp"]
        return ruta, rt

    def test_el_registro_real_tiene_estas_banderas(self):
        for fid in ("lobby.LIMIT_LIST", "lobby.WATCH", "lobby.ROOM_REOPEN", "duelo.kind_main", "duelo.lp"):
            self.assertIn(fid, C.BY_ID)

    def test_la_bandera_cambiada_en_vivo_sobrevive_y_la_que_nadie_toco_toma_el_valor_nuevo_del_fichero(self):
        ruta, _ = self.montar()
        sys.modules["yo2u_lp"].LIMIT_LIST = False                      # el owner la apaga desde el panel
        # el fichero cambia dos cosas: el valor por defecto de WATCH y de ROOM_REOPEN (nadie las tocó) y añade EXTRA
        self.fichero("lp.py", self.LP.replace("WATCH = True", "WATCH = False").replace("ROOM_REOPEN = True", "ROOM_REOPEN = False"))
        self.assertEqual(self.hot.recargar("u.lp"), {"u.lp": "ok"})
        nuevo = sys.modules["yo2u_lp"]
        self.assertFalse(nuevo.LIMIT_LIST, "lo cambiado en vivo sobrevive")
        self.assertFalse(nuevo.WATCH, "lo que nadie tocó toma el valor nuevo del fichero")
        self.assertFalse(nuevo.ROOM_REOPEN)
        self.assertIn("banderas vivas conservadas: lobby.LIMIT_LIST", self.log())
        # y la siguiente recarga sigue conservándola (la base es la del fichero, no el valor vivo)
        self.assertEqual(self.hot.recargar("u.lp"), {"u.lp": "ok"})
        self.assertFalse(sys.modules["yo2u_lp"].LIMIT_LIST)
        # si el owner la devuelve al valor del fichero (True) ya no hay nada que conservar
        sys.modules["yo2u_lp"].LIMIT_LIST = True
        self.hot.recargar("u.lp")
        self.assertTrue(sys.modules["yo2u_lp"].LIMIT_LIST)

    def test_default_cfg_banderas_trcfg_y_claves_sueltas(self):
        _, rt = self.montar()
        tr = sys.modules["yo2u_tr"]
        tr.DEFAULT_CFG["kind_main"] = 9                                # bandera «duelo.kind_main» (liga trcfg)
        tr.DEFAULT_CFG["clave_nueva"] = 4                              # sin bandera
        tr.DEFAULT_CFG["lp"] = 4000                                    # bandera «duelo.lp»
        self.fichero("tr.py", self.TR.replace('"suelta": 1', '"suelta": 2'))
        self.hot.recargar("u.tr")
        nuevo = sys.modules["yo2u_tr"]
        self.assertIsNot(nuevo, tr)
        self.assertEqual(nuevo.DEFAULT_CFG, {"kind_main": 9, "lp": 4000, "suelta": 2, "clave_nueva": 4})
        self.assertEqual(tr.DEFAULT_CFG["suelta"], 1, "el viejo no se toca")

    def test_sin_base_conocida_no_se_inventa_nada(self):
        ruta = self.fichero("lp.py", self.LP)
        self.registrar("u.lp", ruta, "yo2u_lp", ns_nombre="LP")         # cargado=False: no se sabe qué es «de fábrica»
        self.hot.recargar("u.lp")
        sys.modules["yo2u_lp"].LIMIT_LIST = False                       # ahora sí: la base es la de la 1.ª recarga
        self.hot.recargar("u.lp")
        self.assertFalse(sys.modules["yo2u_lp"].LIMIT_LIST)

    def test_una_bandera_que_el_modulo_nuevo_ya_no_tiene_no_rompe(self):
        self.montar()
        sys.modules["yo2u_lp"].WATCH = False
        self.fichero("lp.py", "LIMIT_LIST = True\n")
        self.assertEqual(self.hot.recargar("u.lp"), {"u.lp": "ok"})
        self.assertFalse(hasattr(sys.modules["yo2u_lp"], "WATCH"))


class Huella(Base):
    def test_log_con_sha_antes_y_sin_cambios(self):
        ruta = self.fichero("uh.py", "X = 1\n")
        self.registrar("u.h", ruta, "yo2u_h", cargado=False)
        self.hot.recargar("u.h")
        self.assertRegex(self.log(), r"\[recarga\] u\.h ok \([\d.]+ s\) sha=[0-9a-f]{8} antes=\?")
        sha1 = self.hot.registro["u.h"].sha
        self.fichero("uh.py", "X = 2\n")
        self.hot.recargar("u.h")
        sha2 = self.hot.registro["u.h"].sha
        self.assertNotEqual(sha1, sha2)
        self.assertIn(f"sha={sha2[:8]} antes={sha1[:8]}", self.log())
        self.assertNotIn("sin cambios", self.log())
        self.assertEqual(self.hot.recargar("u.h"), {"u.h": "ok"})
        self.assertRegex(self.log().splitlines()[-1], r"sha=%s antes=%s sin cambios" % (sha2[:8], sha2[:8]))
        last = self.srv.ns["_HOT_LAST"]
        self.assertTrue(last["sin_cambios"])
        self.assertEqual((last["sha"], last["antes"]), (sha2[:8], sha2[:8]))

    def test_registrar_cargado_toma_el_sha_del_disco(self):
        ruta = self.fichero("uh.py", "X = 1\n")
        sys.modules["yo2u_h"] = type(sys)("yo2u_h")
        m = self.registrar("u.h", ruta, "yo2u_h", cargado=True)
        self.assertEqual(m.sha, H.sha_fichero(ruta))
        self.hot.recargar("u.h")
        self.assertIn("sin cambios", self.log())

    def test_listar_dice_si_el_disco_ya_no_es_lo_cargado(self):
        ruta = self.fichero("uh.py", "X = 1\n")
        self.registrar("u.h", ruta, "yo2u_h")
        self.registrar("u.n", ruta, "yo2u_n", noreload=True, motivo="porque sí")
        self.hot.recargar("u.h")
        l = self.hot.listar()
        self.assertFalse(l["u.h"]["cambiado"])
        self.assertTrue(l["u.n"]["noreload"] and l["u.n"]["motivo"] == "porque sí")
        self.fichero("uh.py", "X = 2\n")
        self.assertTrue(self.hot.listar()["u.h"]["cambiado"])


class Ganchos(Base):
    def test_tx_deshace_lo_que_el_gancho_toco_fuera_si_falla_despues(self):
        ruta = self.fichero("ug.py", "X = 1\n")
        d = {"a": 1}
        obj = type("O", (), {})()
        obj.v = "viejo"

        def setup(nuevo, viejo, srv, tx):
            tx.set_item(d, "a", 2)
            tx.set_item(d, "b", 3)
            tx.set_attr(obj, "v", "nuevo")
            tx.set_attr(obj, "w", "recien")
            tx.proteger_item(d, "c")
            d["c"] = 9
            tx.pop_item(d, "a")
            raise RuntimeError("al final revienta")
        self.registrar("u.g", ruta, "yo2u_g", setup=setup)
        r = self.hot.recargar("u.g")
        self.assertIn("al final revienta", r["u.g"])
        self.assertEqual(d, {"a": 1})
        self.assertEqual(obj.v, "viejo")
        self.assertFalse(hasattr(obj, "w"))

    def test_proteger_item_y_proteger_attr_devuelven_lo_de_antes(self):
        ruta = self.fichero("ug.py", "X = 1\n")
        d = {"a": 1}
        obj = type("O", (), {})()
        obj.v = 1

        def setup(nuevo, viejo, srv, tx):
            tx.proteger_item(d, "a")
            tx.proteger_item(d, "z")
            tx.proteger_attr(obj, "v")
            tx.proteger_attr(obj, "n")
            d["a"], d["z"], obj.v, obj.n = 2, 3, 2, 3
            raise RuntimeError("x")
        self.registrar("u.g", ruta, "yo2u_g", setup=setup)
        self.hot.recargar("u.g")
        self.assertEqual(d, {"a": 1})
        self.assertEqual(obj.v, 1)
        self.assertFalse(hasattr(obj, "n"))

    def test_orden_keep_setup_cambio_post_y_viejo_en_los_ganchos(self):
        ruta = self.fichero("ug.py", "X = 1\n")
        orden = []

        def keep(nuevo, viejo, srv, tx):
            orden.append(("keep", sys.modules.get("yo2u_g") is viejo, srv.ns.get("UG") is viejo))

        def setup(nuevo, viejo, srv, tx):
            orden.append(("setup", sys.modules.get("yo2u_g") is viejo, srv.ns.get("UG") is viejo))

        def post(nuevo, viejo, srv, tx):
            orden.append(("post", sys.modules.get("yo2u_g") is nuevo, srv.ns.get("UG") is nuevo))
        self.registrar("u.g", ruta, "yo2u_g", ns_nombre="UG", keep=keep, setup=setup, post=post)
        self.hot.recargar("u.g")                                       # 1.ª carga: viejo None, sin keep
        self.assertEqual(orden, [("setup", True, True), ("post", True, True)])    # viejo es None y no hay nada en sys.modules ni en ns
        orden.clear()
        self.hot.recargar("u.g")
        self.assertEqual(orden, [("keep", True, True), ("setup", True, True), ("post", True, True)],
                         "keep y setup ven aún el módulo VIEJO en el namespace; post, el nuevo")

    def test_fallo_en_post_deshace_incluido_el_cambio_de_modulo(self):
        ruta = self.fichero("ug.py", "X = 1\n")
        self.registrar("u.g", ruta, "yo2u_g", ns_nombre="UG")
        self.hot.recargar("u.g")
        v1 = sys.modules["yo2u_g"]

        def post(nuevo, viejo, srv, tx):
            tx.set_item(srv.ns, "COSA", 1)
            raise RuntimeError("post roto")
        self.hot.registro["u.g"].post = post
        r = self.hot.recargar("u.g")
        self.assertIn("post roto", r["u.g"])
        self.assertIs(sys.modules["yo2u_g"], v1)
        self.assertIs(self.srv.ns["UG"], v1)
        self.assertNotIn("COSA", self.srv.ns)

    def test_el_fallo_dice_que_modulo_fallo(self):
        ra = self.fichero("ua.py", "X = 1\n")
        rb = self.fichero("ub.py", "X = 1\n")
        self.registrar("u.a", ra, "yo2u_a", dependientes=("u.b",))

        def mal(nuevo, viejo, srv, tx):
            raise RuntimeError("boom")
        self.registrar("u.b", rb, "yo2u_b", setup=mal)
        r = self.hot.recargar("u.a")
        self.assertTrue(r["u.b"].startswith("error: se deshace"), r)
        self.assertIn("u.b", r["u.a"], "el otro miembro dice por quién se deshizo")


class Ayudas(unittest.TestCase):
    def test_reasignar_clase_conserva_estado_rellena_atributos_y_reapunta_el_envoltorio_de_instancia(self):
        class Vieja:
            def __init__(self, env):
                self.env, self.sesiones = env, {"a": 1}

            def login(self, c):
                return "viejo"

        class Nueva:
            def __init__(self, env):
                self.env, self.sesiones, self.nuevo_attr = env, {}, "por defecto"

            def login(self, c):
                return "nuevo"

        obj = Vieja({"x": 1})
        sesiones = obj.sesiones

        def envoltorio(c, _orig=obj.login):                           # lo que deja panel.install_login_hook
            return ("envuelto", _orig(c))
        obj.login = envoltorio
        tx = H.Tx()
        RC.reasignar_clase(obj, Nueva, {"x": 1}, tx)
        self.assertIsInstance(obj, Nueva)
        self.assertIs(obj.sesiones, sesiones, "su estado sigue siendo el mismo objeto")
        self.assertEqual(obj.nuevo_attr, "por defecto")
        self.assertEqual(obj.login(None), ("envuelto", "nuevo"), "el envoltorio de instancia llama ahora al método nuevo")
        tx.deshacer()
        self.assertIsInstance(obj, Vieja)
        self.assertFalse(hasattr(obj, "nuevo_attr"))
        self.assertEqual(obj.login(None), ("envuelto", "viejo"))

    def test_heredar_mem_mantiene_el_objeto_y_completa_las_claves_nuevas(self):
        viejo = type(sys)("v")
        nuevo = type(sys)("n")
        viejo.MEM = {"rooms": {1: 2}}
        nuevo.MEM = {"rooms": {}, "clave_nueva": 5}
        mem = viejo.MEM
        RC._heredar_mem(nuevo, viejo)
        self.assertIs(nuevo.MEM, mem)
        self.assertEqual(mem, {"rooms": {1: 2}, "clave_nueva": 5})

    def test_parche_rendirse_es_idempotente_y_se_deshace(self):
        srv = FalsoSrv()
        dfm = type("D", (), {"surrender_masks": staticmethod(lambda m: (m or 0) | 0x100)})
        srv.ns.update(DF={"x": 1}, DFM=dfm)

        class SrvConAttr:
            ns = srv.ns

            def __getattr__(self, k):
                return self.ns[k]
        s = SrvConAttr()
        tr = type(sys)("tr")
        llamadas = []
        tr.comid_field = lambda masks=None, extra=None, tail=None: llamadas.append(masks) or masks
        tx = H.Tx()
        self.assertTrue(RC.parche_rendirse(s, tr, tx))
        self.assertTrue(tr._surrender_patched)
        self.assertEqual(tr.comid_field(1), 0x101)
        self.assertFalse(RC.parche_rendirse(s, tr, tx), "no se envuelve dos veces")
        self.assertIn("_tcf", srv.ns)
        tx.deshacer()
        self.assertNotIn("_tcf", srv.ns)
        srv.ns["DF"] = None
        tr2 = type(sys)("tr2")
        tr2.comid_field = lambda *a, **k: 0
        self.assertFalse(RC.parche_rendirse(s, tr2), "sin duelfin no se parchea")


class UnificadosU1(unittest.TestCase):
    """Recarga de lockstep, persistencia y duelfin sobre los módulos REALES (compat.cargar), un Server de verdad y el mínimo del
    namespace de run.py; el cableado inicial repite el de arranque.py (PH, DPC, comid_field, NR). Comprueba lo que U1 dejó
    unificado (DFM.LS is LS, DFM.PS is PP, sys.modules["research_<área>"] = el de ns) y lo que cada recarga recablea."""

    AREAS = (("lockstep", "LS"), ("persistencia", "PP"), ("duelfin", "DFM"))

    def setUp(self):
        import importlib.util
        import threading
        from yo2srv import arranque, compat
        from yo2srv.server import Server
        self.compat, self.arranque = compat, arranque
        self.sys_antes, self.arr_antes = dict(sys.modules), arranque.srv
        self.addCleanup(self._restaurar)
        self.dir = tempfile.mkdtemp(prefix="hotu1-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = []
        self.ns = ns = {}
        self.srv = srv = Server(ns)
        ns.update(log=lambda *a: self.log.append(" ".join(str(x) for x in a)), HERE=EMU,
                  STATE_PATH=os.path.join(self.dir, "state.json"), STATE_LOCK=threading.RLock(), PH_GAME={}, PH_LOBBY={},
                  load_state=lambda: {}, save_state=lambda st: None, load_state_raw=lambda: {"crudo": 1},
                  shop_purchase=lambda *a, **k: None, duel_reply_base=lambda *a, **k: None)
        ns["registrar_ph"] = lambda ph: ns["PH_GAME"].__setitem__(0x3110, "h_dp_saldo") if ph and "h_dp_saldo" in ph else None
        arranque.setup(srv)
        # el cableado de arranque.py, en su orden: persistencia, DPC, lockstep, duelfin (+ «Rendirse»), npcreglas
        PP = ns["PP"] = compat.cargar("persistencia", fresco=True, base=EMU)
        PH = ns["PH"] = arranque.crear_ph(PP)
        ns["wallet"], ns["set_wallet"], ns["player_data_update"] = PH["wallet"], PH["set_wallet"], PH["player_data_update"]
        PP.RAW_STATE = lambda: srv.load_state_raw()
        ns["registrar_ph"](PH)
        spec = importlib.util.spec_from_file_location("yo2_duelpass", os.path.join(EMU, "research", "persistencia", "duelpass.py"))
        DPC = ns["DPC"] = importlib.util.module_from_spec(spec)
        sys.modules["yo2_duelpass"] = DPC
        spec.loader.exec_module(DPC)
        DPC.install_start(PP, self.dir)
        DPC.install_game(ns)
        LS = ns["LS"] = compat.cargar("lockstep", fresco=True, base=EMU)
        LS.duel_reply_base, LS.log = ns["duel_reply_base"], ns["log"]
        LS.DUEL_CFG["stage"] = 4
        DFM = ns["DFM"] = compat.cargar("duelfin", fresco=True, base=EMU)
        ns["DF"] = arranque.crear_df(DFM)
        _cf = ns["_cf"] = LS.comid_field
        LS.comid_field = lambda masks=None, extra=None, tail=None: _cf(srv.DFM.surrender_masks(masks), extra, tail)
        NR = ns["NR"] = compat.cargar("npcreglas", fresco=True, base=EMU)
        NR.install(DFM, lambda: None, ns["log"])
        RC.registrar_modulos(srv)

    def _restaurar(self):
        for k in list(sys.modules):
            if k not in self.sys_antes:
                sys.modules.pop(k, None)
        for k, v in self.sys_antes.items():
            if sys.modules.get(k) is not v:
                sys.modules[k] = v
        self.arranque.srv = self.arr_antes

    def recargar(self, *nombres):
        r = self.srv.hot.recargar(*nombres)
        for n in nombres:
            self.assertEqual(r[n], "ok", (r, self.log[-3:]))
        return r

    def invariantes(self):
        """Lo que U1 dejó unificado y que ninguna recarga puede romper."""
        ns, compat = self.ns, self.compat
        LS, PP, DFM = ns["LS"], ns["PP"], ns["DFM"]
        for area, nombre in self.AREAS:
            self.assertIs(sys.modules["research_" + area], ns[nombre], area)
            self.assertIs(compat.cargar(area), ns[nombre], area)
        self.assertIs(DFM.LS, LS)
        self.assertIs(DFM.PS, PP)
        TR = sys.modules.get("translator")
        if TR is not None:
            self.assertIs(TR.engine, LS.engine)
            self.assertIs(TR.setcommand, LS.setcommand)
        self.assertIs(ns["wallet"], ns["PH"]["wallet"])
        self.assertIs(ns["PH"]["h_playerlist"].__globals__, PP.__dict__)
        self.assertTrue(DFM._npcreglas["orig_rewards"].__globals__ is PP.__dict__
                        and PP.npc_rewards.__globals__ is ns["NR"].__dict__ and DFM.npc_duel_end.__globals__ is ns["NR"].__dict__,
                        "npcreglas envuelve el PP y el duelfin VIVOS, una sola vez")
        self.assertIs(PP.challenge_rights_due.__globals__, PP.__dict__)         # (sin MW en este montaje)
        self.assertTrue(hasattr(PP.blank_profile, "_dp_orig") and PP._DUELPASS_START_DIR == self.dir)
        self.assertEqual(ns["PH_GAME"].get(0x3110), "h_dp_saldo")
        self.assertEqual(ns["PH_LOBBY"].get(0x8101), "h_dpauth")
        self.assertEqual(PP.RAW_STATE(), {"crudo": 1})
        self.assertEqual(LS.DUEL_CFG["stage"], 4)
        self.assertIs(LS.duel_reply_base, ns["duel_reply_base"])
        self.assertIs(LS.log, ns["log"])
        self.assertEqual(LS.comid_field({}), ns["_cf"](DFM.surrender_masks({})), "«Rendirse» puesto en el LS vivo")
        self.assertEqual(len(LS.comid_field({})), 110)

    def test_estado_inicial_cumple_los_invariantes(self):
        self.invariantes()

    def test_lockstep_arrastra_traductor_y_ocg_y_lo_cambiado_en_vivo_sobrevive(self):
        ns = self.ns
        LS0 = ns["LS"]
        LS0.DUEL_CFG["npc_elige"] = "azar"                        # bandera duelo.npc_janken_elige (registro de config.py)
        LS0.DUEL_CFG["_st"] = {"marca": 1}                        # estado a mano de la consola
        LS0.DUEL_CFG["clave_de_prueba_u1"] = 5                    # una clave que ninguna bandera conoce
        LS0.DUEL_CFG["stage"] = 3
        r = self.recargar("duelo.lockstep")
        self.assertEqual(set(r), {"duelo.lockstep", "duelo.traductor", "duelo.ocg"}, "el traductor importa lockstep")
        LS = ns["LS"]
        self.assertIsNot(LS, LS0)
        self.assertEqual(LS.DUEL_CFG["npc_elige"], "azar")
        self.assertEqual(LS.DUEL_CFG["_st"], {"marca": 1})
        self.assertEqual(LS.DUEL_CFG["clave_de_prueba_u1"], 5)
        self.assertEqual(LS.DUEL_CFG["stage"], 3, "el stage del LS viejo (cambiado en vivo) se mantiene")
        LS.DUEL_CFG["stage"] = 4
        self.invariantes()
        self.assertTrue(sys.modules["translator"]._surrender_patched, "el traductor nuevo lleva «Rendirse»")
        self.assertFalse([l for l in self.log if "AVISO" in l], self.log)
        self.assertIn("banderas vivas conservadas", "\n".join(self.log))

    def test_persistencia_recablea_ph_npcreglas_duelpass_y_conserva_estado(self):
        ns = self.ns
        PP0, PH0, DFM = ns["PP"], ns["PH"], ns["DFM"]
        lock0, warned0 = PP0.LOCK, PP0._SCHEMA_WARNED
        PP0.DUEL_DP_COST = 7                                       # bandera persistencia.DUEL_DP_COST
        PP0.NPC_MP_REWARD["win"] = 123                             # y una clave de un dict (persistencia.NPC_MP_WIN)
        PP0.TP_RANK = True
        self.assertEqual(self.recargar("datos.perfiles"), {"datos.perfiles": "ok"})
        PP = ns["PP"]
        self.assertIsNot(PP, PP0)
        self.assertIsNot(ns["PH"], PH0)
        self.assertEqual((PP.DUEL_DP_COST, PP.NPC_MP_REWARD["win"], PP.TP_RANK), (7, 123, True))
        self.assertEqual(DFM.PS.DUEL_DP_COST, 7, "duelfin lee la bandera del PP vivo")
        self.assertIs(PP.LOCK, lock0)
        self.assertIs(PP._SCHEMA_WARNED, warned0)
        self.invariantes()
        self.assertIs(PP0.npc_rewards.__globals__, PP0.__dict__, "el PP viejo queda sin envoltorios de npcreglas")
        self.assertFalse([l for l in self.log if "AVISO" in l], self.log)

    def test_persistencia_si_algo_falla_vuelve_todo(self):
        ns = self.ns
        PP0, PH0, DFM = ns["PP"], ns["PH"], ns["DFM"]
        wallet0, due0, npc_end0 = ns["wallet"], PP0.npc_rewards, DFM.npc_duel_end
        st0 = DFM._npcreglas

        def mal(nuevo, viejo, srv, tx):
            raise RuntimeError("post roto")
        self.srv.hot.registro["datos.perfiles"].post = mal
        r = self.srv.hot.recargar("datos.perfiles")
        self.assertIn("post roto", r["datos.perfiles"])
        self.assertIs(ns["PP"], PP0)
        self.assertIs(ns["PH"], PH0)
        self.assertIs(ns["wallet"], wallet0)
        self.assertIs(DFM.PS, PP0)
        self.assertIs(PP0.npc_rewards, due0)
        self.assertIs(DFM.npc_duel_end, npc_end0)
        self.assertIs(DFM._npcreglas, st0)
        self.assertIs(sys.modules["research_persistencia"], PP0)
        self.srv.hot.registro["datos.perfiles"].post = None
        self.recargar("datos.perfiles")                            # y con el fallo quitado, funciona
        self.invariantes()

    def test_duelfin_recablea_df_npcreglas_sin_apilar_y_el_viejo_sigue_envuelto(self):
        ns = self.ns
        DFM0, DF0, PP = ns["DFM"], ns["DF"], ns["PP"]
        DF0["sess"].update(npc=84, turn=2)                         # un duelo «global» a medias
        self.assertEqual(self.recargar("duelo.duelfin"), {"duelo.duelfin": "ok"})
        DFM, DF = ns["DFM"], ns["DF"]
        self.assertIsNot(DFM, DFM0)
        self.assertIsNot(DF, DF0)
        self.assertEqual((DF["sess"]["npc"], DF["sess"]["turn"]), (84, 2))
        self.invariantes()
        self.assertIs(DFM0.npc_duel_end.__globals__, ns["NR"].__dict__,
                      "los duelos que siguen con el duelfin viejo conservan las reglas de npcreglas")
        self.assertIs(DFM0.PS, PP, "y el viejo ve el PP vivo (comparten el mismo módulo)")
        self.assertIs(DFM.PS, DFM0.PS)
        self.assertFalse([l for l in self.log if "AVISO" in l], self.log)

    def test_duel_state_pasa_la_sesion_al_duelfin_nuevo_solo_cuando_no_hay_duelo_en_curso(self):
        import types
        from yo2srv.duelo import sesion_duelo as SD
        self.addCleanup(setattr, SD, "srv", SD.srv)
        ns = self.ns
        ns.update(SIN_SESION=object(), load_state=lambda: {}, save_state=lambda st: None, live_conn=lambda c: c)
        SD.setup(self.srv)
        s_idle, s_duelo = types.SimpleNamespace(duel=None), types.SimpleNamespace(duel=None)
        DFM0 = ns["DFM"]
        d_idle, d_duelo = SD.duel_state(s_idle), SD.duel_state(s_duelo)
        self.assertIs(d_idle["dfm"], DFM0)
        df_idle, df_duelo = d_idle["df"], d_duelo["df"]
        df_duelo["h_autoduelstart"](84, 0)                         # sess["end"] puesto = duelo en curso
        self.assertIs(SD.duel_state(s_idle)["df"], df_idle, "sin recarga, el mismo DF de siempre")
        self.recargar("duelo.duelfin")
        DFM1 = ns["DFM"]
        self.assertIsNot(DFM1, DFM0)
        self.assertIs(SD.duel_state(s_duelo)["df"], df_duelo, "el duelo en curso sigue con su duelfin (el viejo)")
        self.assertIs(SD.duel_state(s_duelo)["dfm"], DFM0)
        d = SD.duel_state(s_idle)
        self.assertIsNot(d["df"], df_idle, "sin duelo: pasa al duelfin nuevo")
        self.assertIs(d["dfm"], DFM1)
        self.assertIs(d["df"]["finish"].__globals__, DFM1.__dict__)
        df_duelo["sess"]["end"].over = True                       # el duelo acaba sin que llegue el 0x0003 (también pasa)
        d = SD.duel_state(s_duelo)
        self.assertIs(d["dfm"], DFM1, "y en cuanto acaba, esa sesión también pasa al nuevo")
        self.assertIs(d["df"]["finish"].__globals__, DFM1.__dict__)
        d_h = SD.duel_state(s_idle)
        d_h["df"]["h_autoduelstart"](84, 0)                       # un duelo nuevo en la sesión idle y se cierra con 0x0003
        d_h["df"]["h_0003"](None, b"")
        self.assertIs(SD.duel_state(s_idle)["df"], d_h["df"])
        df2 = d["df"]
        df2["sess"]["ask"] = True                                  # «¿te rindes?» abierto cuenta como duelo en curso
        self.recargar("duelo.duelfin")
        self.assertIs(SD.duel_state(s_duelo)["df"], df2)

    def test_cualquier_orden_de_recargas_deja_los_invariantes(self):
        for orden in (("duelo.lockstep", "datos.perfiles", "duelo.duelfin"),
                      ("duelo.duelfin", "datos.perfiles", "duelo.lockstep"),
                      ("datos.perfiles", "duelo.lockstep", "duelo.duelfin"),
                      ("duelo.duelfin", "duelo.lockstep", "datos.perfiles", "duelo.duelfin", "datos.perfiles")):
            for n in orden:
                self.recargar(n)
                self.invariantes()
        self.recargar("duelo.duelfin", "datos.perfiles", "duelo.lockstep")          # y las tres en una llamada
        self.invariantes()
        self.assertFalse([l for l in self.log if "AVISO" in l or "NO recargado" in l], self.log)

    def test_banderas_por_el_registro_de_config_sobreviven_en_los_tres(self):
        ns, cfg = self.ns, self.srv.cfg
        cfg.escribir_liga(("mod", "PP", "DUEL_DP_COST"), 9)
        cfg.escribir_liga(("moddict", "LS", "DUEL_CFG", "npc_elige"), "segundo")
        cfg.escribir_liga(("moddict", "PP", "NPC_MP_REWARD", "lose"), 55)
        self.recargar("duelo.lockstep", "datos.perfiles", "duelo.duelfin")
        self.assertEqual(ns["PP"].DUEL_DP_COST, 9)
        self.assertEqual(ns["LS"].DUEL_CFG["npc_elige"], "segundo")
        self.assertEqual(ns["PP"].NPC_MP_REWARD["lose"], 55)
        self.assertEqual(ns["DFM"].PS.DUEL_DP_COST, 9)


class Registro(unittest.TestCase):
    """recarga.registrar_modulos con un Server de verdad y un namespace vacío (nada cargado: sin base, sin romper)."""

    def setUp(self):
        from yo2srv.server import Server
        self.log = []
        self.srv = Server({"log": lambda *a: self.log.append(" ".join(str(x) for x in a)), "HERE": EMU})
        RC.registrar_modulos(self.srv)

    def test_estan_los_19_con_sus_ficheros_y_sus_dependencias(self):
        reg = self.srv.hot.registro
        self.assertEqual(len([n for n in reg if not reg[n].noreload]), 16)
        self.assertEqual(sorted(n for n in reg if reg[n].noreload), ["duelcore", "red.conexion", "red.proto"])
        for n, m in reg.items():
            self.assertTrue(os.path.isfile(m.fichero), f"{n}: {m.fichero}")
        self.assertEqual(reg["ia.brain"].dependientes, ("duelo.ocg",))
        self.assertEqual(reg["duelo.traductor"].dependientes, ("duelo.ocg",))
        self.assertEqual(reg["duelo.lockstep"].dependientes, ("duelo.traductor",))
        names = list(reg)
        self.assertLess(names.index("duelo.lockstep"), names.index("duelo.traductor"))
        self.assertLess(names.index("datos.perfiles"), names.index("duelo.duelfin"))
        self.assertLess(names.index("ia.brain"), names.index("duelo.ocg"))
        self.assertLess(names.index("duelo.traductor"), names.index("duelo.ocg"))
        self.assertTrue(all(reg[n].motivo for n in reg if reg[n].noreload))

    def test_hot_en_el_namespace(self):
        hot = self.srv.ns["hot"]
        r = hot()
        self.assertEqual(set(r), set(self.srv.hot.registro))
        self.assertTrue(any(l.startswith("[hot] ia.brain") for l in self.log))
        self.assertTrue(any("NO se recarga" in l for l in self.log))
        self.assertIn("reiniciar", hot("red.proto")["red.proto"])
        self.assertEqual(hot("no_existe")["no_existe"][:6], "error:")

    def test_servidor_estado_lista_los_modulos(self):
        self.assertEqual(len(self.srv.estado()["modulos_hot"]), 19)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
    mal = sorted({t.id().split(".", 2)[-1] for t, _ in res.failures + res.errors})
    total = res.testsRun - len(res.skipped)
    bien = total - len(res.failures) - len(res.errors)
    print(f"== RESULTADO hot unitarias: {bien}/{total} bien" + (f"; mal: {mal}" if mal else ""))
    sys.exit(0 if res.wasSuccessful() else 1)
