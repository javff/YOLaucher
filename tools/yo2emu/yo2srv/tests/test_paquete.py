"""P1 (empaquetado, 11-10): rutas del modo paquete, OWNER_ACCT, rotación de logs, token de la consola y la parada en
inglés de yo2srv/paquete.py. Sin puertos ni node: lo que necesita un servidor de verdad lo prueba el arranque simulado
del informe de P1 (paquete/RUTAS.md)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import log as L  # noqa: E402
from yo2srv import paquete as P  # noqa: E402
from yo2srv import rutas  # noqa: E402

LOCAL = r"C:\Users\x\AppData\Local"


class Rutas(unittest.TestCase):
    def test_sin_paquete_todo_como_antes(self):
        for env in ({}, {"YO2_PAQUETE": "0"}, {"YO2_PAQUETE": ""}):
            with self.subTest(env=env):
                self.assertFalse(rutas.paquete(env))
                self.assertEqual(rutas.state_path(env=env), os.path.join(rutas.EMU, "state.json"))
                self.assertEqual(rutas.logdir(env=env), os.path.join(rutas.EMU, "logs"))
                self.assertEqual(rutas.trazas_ocg(env=env), os.path.join(rutas.EMU, "logs", "ocg"))
        # las trazas, sin YO2_LOGDIR, siguen en logs/ocg aunque haya offset (como ocg_duel.py desde el 06-10)
        self.assertEqual(rutas.trazas_ocg(env={"YO2_PORT_OFFSET": "1300"}), os.path.join(rutas.EMU, "logs", "ocg"))
        self.assertEqual(rutas.trazas_ocg(env={"YO2_LOGDIR": r"C:\l"}), os.path.join(r"C:\l", "ocg"))

    def test_datos_del_juego_vendor_y_motor_por_defecto_son_los_de_hoy(self):
        root = os.path.normpath(os.path.join(rutas.EMU, "..", ".."))
        self.assertEqual(rutas.juego_datos({}), os.path.join(root, "_original", "extracted", "merge2008", "data"))
        self.assertEqual(rutas.packinfo({}), rutas.desde().packinfo)          # la fórmula de run.py (tienda)
        self.assertEqual(rutas.vendor({}), os.path.join(root, "_vendor"))
        self.assertEqual(rutas.duelcore({}), os.path.join(rutas.EMU, "duelcore", "duelcore.mts"))
        which = shutil.which("node")
        n = rutas.node({})
        if which:
            # PATH con node ≥ 18: igual que siempre. Si el del PATH es viejo, se prefiere nvm/Homebrew.
            ver = rutas._version_node(which)
            if ver is not None and ver >= rutas._NODE_MIN:
                self.assertEqual(n, which)
            else:
                self.assertTrue(n)
        else:
            self.assertEqual(n, rutas._node_conocido())

    def test_node_sin_which_usa_rutas_conocidas(self):
        import unittest.mock
        with unittest.mock.patch("shutil.which", return_value=None):
            n = rutas.node({})
        self.assertEqual(n, rutas._node_conocido())

    def test_variables(self):
        env = {"YO2_JUEGO_DATOS": r"C:\pq\juego", "YO2_VENDOR": r"C:\pq\_vendor", "YO2_DUELCORE": r"C:\pq\duelcore.mjs",
               "YO2_NODE": r"C:\pq\node\node.exe"}
        self.assertEqual(rutas.juego("bin#", "card_prop.bin", env=env), r"C:\pq\juego\bin#\card_prop.bin")
        self.assertEqual(rutas.packinfo(env), r"C:\pq\juego\cardpack\packinfo_s.txt")
        self.assertEqual(rutas.vendor(env), r"C:\pq\_vendor")
        self.assertEqual(rutas.duelcore(env), r"C:\pq\duelcore.mjs")
        self.assertEqual(rutas.node(env), r"C:\pq\node\node.exe")

    def test_modo_paquete_estado_y_logs_en_localappdata(self):
        env = {"YO2_PAQUETE": "1", "LOCALAPPDATA": LOCAL}
        base = LOCAL + r"\YO2Online\servidor\5730"
        self.assertEqual(rutas.datos_paquete(env), base)
        self.assertEqual(rutas.state_path(env=env), base + r"\state.json")
        self.assertEqual(rutas.datos_dir(env=env), base)
        self.assertEqual(rutas.logdir(env=env), base + r"\logs")
        self.assertEqual(rutas.trazas_ocg(env=env), base + r"\logs\ocg")
        env2 = dict(env, YO2_PORT_OFFSET="1300")
        self.assertEqual(rutas.logdir(env=env2), LOCAL + r"\YO2Online\servidor\7030\logs")
        env3 = dict(env, YO2_STATE=r"C:\t\s.json", YO2_LOGDIR=r"C:\t\l")     # las variables siguen mandando
        self.assertEqual(rutas.state_path(env=env3), r"C:\t\s.json")
        self.assertEqual(rutas.logdir(env=env3), r"C:\t\l")
        self.assertEqual(rutas.trazas_ocg(env=env3), r"C:\t\l\ocg")

    def test_faltan_datos(self):
        d = tempfile.mkdtemp(prefix="paq-")
        self.addCleanup(shutil.rmtree, d, True)
        env = {"YO2_JUEGO_DATOS": os.path.join(d, "j"), "YO2_VENDOR": os.path.join(d, "v"),
               "YO2_DUELCORE": os.path.join(d, "duelcore.mjs"), "YO2_NODE": os.path.join(d, "node.exe")}
        # tablas del cliente: en el repo privado están; en el árbol público (código abierto) las genera
        # paquete/generar_tablas.py (y faltan hasta entonces)
        tablas = [os.path.join(rutas.EMU, *t.split("/")) for t in rutas.TABLAS_GENERADAS]
        sin_csv = [t for t in tablas if not os.path.isfile(t)]
        self.assertEqual(len(rutas.faltan_datos(env)), len(rutas.JUEGO_FICHEROS) + 5 + len(sin_csv))
        for f in rutas.JUEGO_FICHEROS:
            os.makedirs(os.path.dirname(rutas.juego(*f.split("/"), env=env)), exist_ok=True)
            open(rutas.juego(*f.split("/"), env=env), "wb").close()
        os.makedirs(os.path.join(d, "v", "CardScripts"))
        for f in ("v/cards.cdb", "v/strings.conf", "duelcore.mjs", "node.exe"):
            open(os.path.join(d, *f.split("/")), "wb").close()
        self.assertEqual(rutas.faltan_datos(env), sin_csv)

    def test_tablas_generadas_son_las_de_generar_tablas(self):
        sys.path.insert(0, os.path.join(rutas.EMU, "paquete"))
        try:
            import generar_tablas
        finally:
            sys.path.pop(0)
        self.assertEqual(tuple(generar_tablas.TABLAS), tuple(rutas.TABLAS_GENERADAS))

    def test_rutas_md_dice_lo_mismo_que_rutas_py(self):
        with open(os.path.join(rutas.EMU, "paquete", "RUTAS.md"), encoding="utf-8") as fh:
            txt = fh.read()
        for f in rutas.JUEGO_FICHEROS + rutas.JUEGO_CARPETAS:
            self.assertIn(f"`{f}", txt, f)
        for v in ("YO2_PAQUETE", "YO2_JUEGO_DATOS", "YO2_VENDOR", "YO2_NODE", "YO2_DUELCORE", "YO2_STATE", "YO2_LOGDIR"):
            self.assertIn(f"`{v}`", txt, v)


class OwnerAcct(unittest.TestCase):
    def _owner(self, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("YO2_PAQUETE", "YO2_OWNER_ACCT")}
        e.update(env)
        r = subprocess.run([sys.executable, "-c", "import yo2srv.cuentas.sesion as s; print(repr(s.OWNER_ACCT))"],
                           cwd=EMU, env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_desarrollo_el_de_siempre_y_paquete_sin_dueno(self):
        self.assertEqual(self._owner(), "'0000000000000000'")
        self.assertEqual(self._owner(YO2_PAQUETE="1"), "None")
        self.assertEqual(self._owner(YO2_PAQUETE="1", YO2_OWNER_ACCT="abc"), "'abc'")
        self.assertEqual(self._owner(YO2_OWNER_ACCT="abc"), "'abc'")

    def test_register_account_sin_dueno_nunca_es_el_dueno(self):
        from yo2srv.cuentas import sesion
        import threading

        class Srv:
            OWNER_ACCT = None
            HUB_KEYS = sesion.HUB_KEYS
            MAX_SAVED_ACCOUNTS = 64
            STATE_LOCK = threading.RLock()
            STATE_PATH = os.path.join(tempfile.gettempdir(), "no-existe-p1.json")
            pvp_hub = None
            st = {"players": {}}

            def load_state_raw(self):
                return dict(self.st)

            def save_state_raw(self, st):
                self.st = st

            def _hub_write(self, st, **kv):
                st.update(kv)
                self.save_state_raw(st)

            def log(self, *a):
                pass
        viejo = sesion.srv
        srv = sesion.srv = Srv()
        try:
            self.assertEqual(sesion.register_account("0000000000000000"), (1, False))
            self.assertEqual(sesion.register_account("aaaa"), (2, False))
            self.assertEqual(sesion.register_account("0000000000000000"), (1, False))
            self.assertNotIn("owner_acct", srv.st)
        finally:
            sesion.srv = viejo


class Rotacion(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rot-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_limites(self):
        self.assertEqual(L.limites({}), (0, 0, 0))                      # desarrollo: sin tope (como antes)
        self.assertEqual(L.limites({"YO2_PAQUETE": "1"}), (20 * 1024 * 1024, 10, 200))
        self.assertEqual(L.limites({"YO2_LOG_MAX_MB": "1", "YO2_LOG_KEEP": "3", "YO2_LOG_KEEP_OCG": "x"}),
                         (1024 * 1024, 3, 0))

    def test_sin_tope_un_solo_fichero(self):
        lg = L.Log(self.dir, consola=False)
        self.addCleanup(lg.cerrar)
        for i in range(2000):
            lg("x" * 100, i)
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_rota_al_pasar_el_tope_y_guarda_los_mas_recientes(self):
        for n in ("20200101-000000.log", "20200101-000001.log", "otro.log", "20200101-000002.txt"):
            open(os.path.join(self.dir, n), "w").close()
        vistos = []
        lg = L.Log(self.dir, consola=False, max_bytes=2000, guardar=3, al_rotar=lambda f, r: vistos.append(r))
        self.addCleanup(lg.cerrar)
        # al abrir: se quedan 3 logs con nombre de log (los 2 viejos + el nuevo); lo demás no se toca
        self.assertEqual(sorted(f for f in os.listdir(self.dir) if L.LOG_RE.match(f))[-1], os.path.basename(lg.path))
        primero = lg.path
        for i in range(100):
            lg("y" * 90, i)
        self.assertGreaterEqual(len(vistos), 3)
        self.assertEqual(lg.path, vistos[-1])
        logs = sorted(f for f in os.listdir(self.dir) if L.LOG_RE.match(f))
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[-1], os.path.basename(lg.path))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "otro.log")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "20200101-000002.txt")))
        self.assertFalse(os.path.exists(primero))
        txt = open(lg.path, encoding="utf-8").read()
        self.assertRegex(txt, r"^\[t\] \d\d:\d\d:\d\d\n")              # el fichero nuevo empieza con su marca
        for f in logs:
            self.assertLess(os.path.getsize(os.path.join(self.dir, f)), 2000 + 400)

    def test_podar_por_fecha(self):
        for i, n in enumerate(("20261011-000000-duelo10.jsonl", "20261011-000000-duelo2.jsonl",
                               "20261011-000000-duelopvp1.jsonl", "notas.jsonl")):
            p = os.path.join(self.dir, n)
            open(p, "w").close()
            os.utime(p, (1000 + i, 1000 + i))
        borr = L.podar(self.dir, L.TRAZA_RE, 2, por_fecha=True)
        self.assertEqual(borr, ["20261011-000000-duelo10.jsonl"])
        self.assertEqual(L.podar(self.dir, L.TRAZA_RE, 0), [])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "notas.jsonl")))


class Paquete(unittest.TestCase):
    class Srv:
        def __init__(self, **ns):
            self.ns = dict(ns)
            self.lineas = []

        def log(self, *a):
            self.lineas.append(" ".join(str(x) for x in a))

    def test_borrar_token_solo_el_propio(self):
        d = tempfile.mkdtemp(prefix="tok-")
        self.addCleanup(shutil.rmtree, d, True)
        viejo = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = d
        self.addCleanup(lambda: os.environ.__setitem__("LOCALAPPDATA", viejo) if viejo else os.environ.pop("LOCALAPPDATA"))
        ruta = rutas.token_consola(7099)
        os.makedirs(os.path.dirname(ruta))
        open(ruta, "w").close()
        os.utime(ruta, (time.time() - 3600, time.time() - 3600))      # de otra instancia (anterior a este proceso)
        self.assertFalse(P.borrar_token(7099))
        self.assertTrue(os.path.exists(ruta))
        open(ruta, "w").close()                                         # escrito ahora: es el nuestro
        self.assertTrue(P.borrar_token(7099))
        self.assertFalse(os.path.exists(ruta))
        self.assertFalse(P.borrar_token(7099))                          # ya no está: nada
        self.assertFalse(P.borrar_token(None))

    def test_desarrollo_no_para(self):
        s = self.Srv(OCG=None)
        self.assertFalse(P.comprobar(s, env={}))
        self.assertEqual(s.lineas, [])

    def test_paquete_sin_datos_para_en_ingles(self):
        d = tempfile.mkdtemp(prefix="paq-")
        self.addCleanup(shutil.rmtree, d, True)
        s = self.Srv(OCG=None)
        env = {"YO2_PAQUETE": "1", "YO2_JUEGO_DATOS": d, "YO2_VENDOR": d}
        with self.assertRaises(SystemExit) as cm:
            P.comprobar(s, env=env)
        self.assertEqual(cm.exception.code, P.SALIDA_FALTAN)
        self.assertTrue(any("[paquete] PARADA: some files the server needs are missing" in x for x in s.lineas), s.lineas)
        self.assertTrue(any("card_prop.bin" in x for x in s.lineas))

    def test_paquete_motor_que_no_arranca_para(self):
        class OcgRoto:
            @staticmethod
            def core(log=None):
                raise RuntimeError("node no arranca")

            @staticmethod
            def cardmap():
                return None
        s = self.Srv(OCG=OcgRoto)
        orig = rutas.faltan_datos
        rutas.faltan_datos = lambda env=None: []
        self.addCleanup(setattr, rutas, "faltan_datos", orig)
        with self.assertRaises(SystemExit) as cm:
            P.comprobar(s, env={"YO2_PAQUETE": "1"})
        self.assertEqual(cm.exception.code, P.SALIDA_OCG)
        self.assertTrue(any("duel rules engine" in x and "node no arranca" in x for x in s.lineas), s.lineas)


if __name__ == "__main__":
    unittest.main()
