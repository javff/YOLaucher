"""config.py: el registro de banderas (Anexo A) es coherente y está ligado a los sitios de hoy.

Sin instancia: todo se comprueba contra el CÓDIGO FUENTE (ast) y contra un namespace falso. La comparación del valor
vivo con `vanilla` en una instancia de prueba real la hace test_vivo.py.
"""
import ast
import os
import re
import sys
import types
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import config as C  # noqa: E402

# nombre de la liga ("mod", X, ...) en el namespace de run.py -> fichero que lo define
FUENTE = {
    "LP": "research/lobby/proposal.py", "EQ": "research/equipos/proposal.py", "TP": "research/intercambio/proposal.py",
    "TO": "research/torneos/proposal.py", "MW": "research/mundo/proposal.py", "PP": "research/persistencia/proposal.py",
    "DPC": "research/persistencia/duelpass.py", "OCG": "ocg_duel.py", "LS": "research/lockstep/proposal.py",
    "pvp_hub": "pvp_hub.py", "pvp_match": "pvp_match.py",
}
TRADUCTOR = "research/traductor/translator.py"
# research/admin (panel web) es privado del owner y no va en el árbol público (código abierto): sin él, se saltan las
# comprobaciones del panel y las variables que solo lee el panel
HAY_PANEL = os.path.isfile(os.path.join(EMU, "research", "admin", "panel.py"))
SOLO_PANEL = ("YO2_ADMIN_PANEL_PORT", "YO2_ADMIN_TOKEN_FILE")
RUN = "run.py"
# ficheros a los que se ha movido código de run.py (run.py solo conserva el `from ... import`): para "run.py asigna X"
# se miran también ellos (E4: tienda y cartera; E1b: la red)
RUN_MOVIDO = ("yo2srv/tienda/tienda.py", "yo2srv/tienda/cartera.py", "yo2srv/red/servidor.py", "yo2srv/red/puerta.py",
              "yo2srv/red/conexion.py", "yo2srv/red/auto.py", "yo2srv/red/http.py")
_CACHE = {}
# E5 (mundo): NPC_SPOTS_2008, NPC_RULES_2008, MUNDO_2008 y MYROOM_SELF_AVATAR viven ya en yo2srv/mundo/
RUN_MOVIDO_E5 = ("yo2srv/mundo/npc.py", "yo2srv/mundo/mapas.py", "yo2srv/mundo/avatar.py", "yo2srv/mundo/bloques.py")
# E8 (arranque): las cargas (g["X"] = …, _de(modulo, "X", …)), la reserva y el mazo del duelo y la cartera viven ya en yo2srv/
RUN_MOVIDO_E8 = ("yo2srv/arranque.py", "yo2srv/duelo/reserva.py", "yo2srv/duelo/mazo.py", "yo2srv/duelo/sesion_duelo.py",
                 "yo2srv/cuentas/sesion.py", "yo2srv/datos/estado.py", "yo2srv/admin/consola.py", "yo2srv/cliente.py")


def arbol(rel):
    if rel not in _CACHE:
        with open(os.path.join(EMU, rel), encoding="utf-8") as f:
            texto = f.read()
        _CACHE[rel] = (ast.parse(texto), texto)
    return _CACHE[rel]


def _destinos(tg):
    """Nombres a los que asigna un destino: `X`, `srv.X` (E8: el namespace de run.py), `g["X"]` / `ns["X"]` y tuplas."""
    if isinstance(tg, ast.Name):
        yield tg.id
    elif isinstance(tg, ast.Attribute) and isinstance(tg.value, ast.Name) and tg.value.id == "srv":
        yield tg.attr
    elif (isinstance(tg, ast.Subscript) and isinstance(tg.value, ast.Name) and tg.value.id in ("g", "ns")
          and isinstance(tg.slice, ast.Constant) and isinstance(tg.slice.value, str)):
        yield tg.slice.value
    elif isinstance(tg, (ast.Tuple, ast.List)):
        for e in tg.elts:
            yield from _destinos(e)


def asignaciones(rel):
    """{nombre: [nodo valor,...]} de TODAS las asignaciones `NOMBRE = ...` del fichero (cualquier nivel)."""
    out = {}
    for fich in (rel,) + (RUN_MOVIDO + RUN_MOVIDO_E5 + RUN_MOVIDO_E8 if rel == RUN else ()):
        t, _ = arbol(fich)
        for n in ast.walk(t):
            if isinstance(n, ast.Assign):
                for tg in n.targets:
                    for nombre in _destinos(tg):
                        out.setdefault(nombre, []).append(n.value)
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name) and n.value is not None:
                out.setdefault(n.target.id, []).append(n.value)
    return out


def importados(rel):
    """Nombres que el fichero trae con `from x import a, b` (E2: run.py conserva los nombres movidos al paquete así) y, para
    run.py, los que arranque.py deja en el namespace con `_de("modulo", "a", "b")` (E8: el `from x import` de entonces)."""
    t, _ = arbol(rel)
    out = {(a.asname or a.name) for n in ast.walk(t) if isinstance(n, ast.ImportFrom) for a in n.names}
    if rel == RUN:
        for fich in ("yo2srv/arranque.py",):
            ta, _ = arbol(fich)
            for n in ast.walk(ta):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_de"
                        and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in n.args)):
                    out |= {a.value for a in n.args[1:]}
    return out


def claves_dict(valores):
    claves = set()
    for v in valores:
        if isinstance(v, ast.Dict):
            for k in v.keys:
                if isinstance(k, ast.Constant):
                    claves.add(k.value)
    return claves


def literal_panel():
    """panel.FLAGS como datos (es un literal: ast.literal_eval)."""
    with open(os.path.join(EMU, "research", "admin", "panel.py"), encoding="utf-8") as f:
        texto = f.read()
    a = texto.index("\nFLAGS = [") + len("\nFLAGS = ")
    b = texto.index("\n]\n", a) + 2
    return ast.literal_eval(texto[a:b])


class TestRegistro(unittest.TestCase):
    def test_ids_unicos_y_bien_formados(self):
        ids = [f.id for f in C.REGISTRO]
        self.assertEqual(len(ids), len(set(ids)))
        grupos = {g for g, _ in C.GRUPOS}
        for f in C.REGISTRO:
            with self.subTest(f.id):
                self.assertIn(f.grupo, grupos)
                self.assertIn(f.liga[0], ("ns", "nsdict", "mod", "moddict", "trcfg", "env"))
                self.assertTrue(f.desc, "toda bandera lleva su descripción")
                if f.env:
                    self.assertRegex(f.env, r"^YO2_[A-Z0-9_]+$")
                if f.vanilla is not C.DERIVADA and f.tipo in ("bool", "int", "float", "str"):
                    esperado = {"bool": bool, "int": int, "float": float, "str": str}[f.tipo]
                    self.assertIsInstance(f.vanilla, esperado)
                if isinstance(f.tipo, tuple):
                    self.assertIn(f.vanilla, f.tipo)

    def test_hay_todo_el_anexo_A(self):
        """Cada nombre del Anexo A.1 (REESTRUCTURA.md) está en el registro, por la constante a la que se liga."""
        anexo = {
            "run.py": ["SHOP_SELL_PRIZE_PACKS", "SHOP_DECK_RECIPE", "SHOP_DECK_ITEMGET", "SHOP_AVATAR_ITEMGET",
                       "NPC_SPOTS_2008", "NPC_RULES_2008", "MUNDO_2008", "MYROOM_SELF_AVATAR", "CLOSE_OLD_DUEL_SOCK",
                       "DUEL_MODE", "ADMIN_PANEL", "STORAGE", "DEFAULT_COUNTRY", "START_MP", "START_DPP"],
            "pvp_hub": ["LIST_SELF", "NPC_DUEL_STATE", "RIVAL_CARD", "REANNOUNCE_ON_FIRST_POS", "AREA_BLOCKS",
                        "LOGIN_ESTRICTO"],
            "pvp_match": ["PVP_SVRINFO", "PVP_DUELLOG_FINISH", "PVP_DUELLOG_RECORD", "PVP_WATCH", "WATCH_SEND_PREP",
                          "TEST_AUTO", "ATTACH_TIMEOUT"],
            "OCG": ["USE_BRAIN", "SEND_PAUSE", "NPC_THINK", "CORE_ERROR_AUTO"],
            "LP": ["LIMIT_LIST", "ROOM_REOPEN", "TPPRESENT", "TP_PRESENT_CARD", "STARTUP_PRESENT", "AUTODUEL",
                   "AUTODUEL_MATCH", "RESETAUTOWAIT", "USBDPAUTH_REFUSE", "OFFER_TIMEOUT", "PIDNAME_PLAYERS", "WATCH"],
            "EQ": ["NAME_CASEFOLD", "APPY_INVITER_FIRST", "ANNOUNCE_4571_BLOCK", "ANNOUNCE_4571_SELF",
                   "JOIN_NOTICE_TO_JOINER", "PATCH_HUB_LIST5"],
            "TP": ["ADDCARD_REJECT_1010", "RESET_READY_ON_CHANGE", "CLEAR_STATUS_AFTER_NO"],
            "TO": ["TOURNAMENT_ON", "EVENTS_ON", "AUTO_CLOCK", "AUTO_ENTRY", "BIT80_ALIVE", "NOSHOW_ADVANCE_SEAT0",
                   "REPAIR_ON_DRAW"],
            "MW": ["GM_ACCOUNTS"],
            "PP": ["TP_RANK", "TRUNK_START", "CHALLENGE_MODE", "PRIZE_CARDS", "RIGHTS_ON", "NPC_IN_RECORD",
                   "NPC_DP_COST", "NPC_MP_REWARD"],
            "DPC": ["DPAUTH_ON"],
        }
        ligas = [f.liga for f in C.REGISTRO]
        for sitio, nombres in anexo.items():
            for n in nombres:
                with self.subTest(f"{sitio}.{n}"):
                    if sitio == "run.py":
                        ok = any(l[0] in ("ns", "nsdict") and l[1] == n for l in ligas)
                    elif sitio in ("pvp_hub", "pvp_match"):
                        ok = any(l[:3] == ("mod", sitio, n) for l in ligas)
                    else:
                        ok = any(l[0] in ("mod", "moddict") and l[1] == sitio and l[2] == n for l in ligas)
                    self.assertTrue(ok, f"falta en el registro: {sitio} {n}")
        # variables YO2_* del Anexo que se ligan a otra constante o al entorno
        envs = {f.env for f in C.REGISTRO if f.env}
        for v in ("YO2_PIDNAME_PLAYERS", "YO2_MUNDO", "YO2_TRUNK_START", "YO2_NPC_MP_WIN", "YO2_NPC_DP_COST",
                  "YO2_DUELPASS_REARM", "YO2_CHALLENGE", "YO2_RIGHTS", "YO2_DPAUTH", "YO2_RESETAUTOWAIT",
                  "YO2_USBDPAUTH_REFUSE", "YO2_PVP_OFFER_TIMEOUT", "YO2_PRIZE_CARDS", "YO2_PORT_OFFSET", "YO2_BIND",
                  "YO2_PUBLIC_IP", "YO2_NO_CLIENT", "YO2_LOGDIR", "YO2_STATE", "YO2_STORAGE", "YO2_DB", "YO2_DB_CRYPT",
                  "YO2_DB_KEYFILE", "YO2_OWNER_ACCT", "YO2_ADMIN_PANEL", "YO2_ADMIN_PANEL_PORT",
                  "YO2_ADMIN_TOKEN_FILE", "YO2_PVP_TEST", "YO2_DUEL_PAUSE", "YO2_DP_INICIO"):
            self.assertIn(v, envs, f"el Anexo A nombra {v}")

    @unittest.skipUnless(HAY_PANEL, "sin research/admin (panel privado del owner): árbol público")
    def test_todas_las_del_panel_estan_con_la_misma_liga_y_datos(self):
        panel = literal_panel()
        self.assertGreaterEqual(len(panel), 35)
        for fid, donde, tipo, efecto, reiniciar, desc, fijo, despues in panel:
            with self.subTest(fid):
                f = C.BY_ID.get(fid)
                self.assertIsNotNone(f, "una bandera del panel falta en el registro")
                self.assertEqual(f.liga, tuple(donde))
                self.assertEqual(f.efecto, efecto)
                self.assertEqual(f.reiniciar, bool(reiniciar))
                self.assertEqual(f.desc, desc)
                self.assertEqual(f.accion, despues)
                self.assertEqual(f.tipo, "bool" if tipo == "bool" else tuple(tipo))

    def test_las_ligas_existen_en_el_codigo(self):
        for f in C.REGISTRO:
            l = f.liga
            with self.subTest(f.id):
                if l[0] == "ns":
                    self.assertIn(l[1], set(asignaciones(RUN)) | importados(RUN), f"run.py no asigna ni importa {l[1]}")
                elif l[0] == "nsdict":
                    self.assertIn(l[2], claves_dict(asignaciones(RUN).get(l[1], [])), f"{l[1]}[{l[2]!r}] en run.py")
                elif l[0] == "mod":
                    self.assertIn(l[1] in FUENTE or l[1] in ("pvp_hub", "pvp_match"), [True], f"módulo {l[1]} sin fuente")
                    self.assertIn(l[2], asignaciones(FUENTE[l[1]]), f"{FUENTE[l[1]]} no asigna {l[2]}")
                elif l[0] == "moddict":
                    self.assertIn(l[3], claves_dict(asignaciones(FUENTE[l[1]]).get(l[2], [])),
                                  f"{FUENTE[l[1]]}: {l[2]}[{l[3]!r}]")
                elif l[0] == "trcfg":
                    self.assertIn(l[1], claves_dict(asignaciones(TRADUCTOR).get("DEFAULT_CFG", [])),
                                  f"DEFAULT_CFG[{l[1]!r}]")
                    # y los de DEFAULT_CFG están todos en el registro (abajo, test_todo_default_cfg)

    def test_todo_default_cfg_esta_en_el_registro(self):
        claves = claves_dict(asignaciones(TRADUCTOR).get("DEFAULT_CFG", []))
        en_registro = {f.liga[1] for f in C.REGISTRO if f.liga[0] == "trcfg"}
        self.assertEqual(sorted(claves - en_registro, key=str), [], "claves de DEFAULT_CFG sin registrar")
        self.assertEqual(sorted(en_registro - claves, key=str), [])

    def test_las_variables_de_entorno_existen_en_el_codigo(self):
        """Cada env del registro aparece como texto en algún .py del servidor (no es una variable inventada)."""
        texto = ""
        for raiz, dirs, ficheros in os.walk(EMU):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "logs", "_out", "_fixtures", "node_modules",
                                                      "ghidra", "_original", "www", "capturas", ".git")]
            if os.path.basename(raiz) == "paridad":
                continue
            for n in ficheros:
                # (E3: el código movido a yo2srv/ cuenta; solo se excluye config.py, que nombra TODAS las variables)
                if n.endswith(".py") and not n.startswith("test_") and not ("yo2srv" in raiz and n == "config.py"):
                    try:
                        with open(os.path.join(raiz, n), encoding="utf-8", errors="replace") as fh:
                            texto += fh.read()
                    except OSError:
                        pass
        for rel in RUN_MOVIDO:                              # lo que salió de run.py (E4, E1b) sigue siendo «código del servidor»
            with open(os.path.join(EMU, rel), encoding="utf-8", errors="replace") as fh:
                texto += fh.read()
        for f in C.REGISTRO:
            if f.env and f.id != "sistema.PERFIL" and (HAY_PANEL or f.env not in SOLO_PANEL):  # YO2_PERFIL: yo2srv/config.py
                with self.subTest(f.id):
                    self.assertIn(f'"{f.env}"', texto, f"{f.env} no aparece como cadena en el código del servidor")

    def test_el_vanilla_coincide_con_el_default_del_codigo_cuando_es_un_literal(self):
        """Para ("mod", M, N) cuyo valor en el fuente es True/False/número/cadena literal, vanilla == ese literal. Las que
        se calculan con _ON(..., "1") se comprueban mirando el default de _ON. (El resto: test_vivo.)"""
        on = re.compile(r'^\s*(\w+)\s*=\s*_ON\(\s*"(YO2_\w+)"(?:\s*,\s*"([01]?)")?\s*\)', re.M)
        for f in C.REGISTRO:
            if f.liga[0] != "mod" or f.liga[1] not in FUENTE or f.vanilla is C.DERIVADA:
                continue
            rel = FUENTE[f.liga[1]]
            valores = asignaciones(rel).get(f.liga[2], [])
            lits = [v.value for v in valores if isinstance(v, ast.Constant) and isinstance(v.value, (bool, int, float, str))]
            with self.subTest(f.id):
                if len(lits) == 1:
                    self.assertEqual(lits[0], f.vanilla)
                else:
                    m = [x for x in on.finditer(arbol(rel)[1]) if x.group(1) == f.liga[2]]
                    if m:
                        # _ON(k) de torneos: default "1" (def _ON = lambda k, d="1"); de lobby: segundo argumento
                        d = m[0].group(3)
                        if d is None:
                            d = "1" if rel.endswith("torneos/proposal.py") else ""
                        self.assertEqual(d not in ("", "0"), f.vanilla)


class FalsoModulo(types.SimpleNamespace):
    pass


class TestLeer(unittest.TestCase):
    def setUp(self):
        self.ns = {
            "MUNDO_2008": {"spots": True, "prizes": False},
            "NPC_SPOTS_2008": True,
            "LP": FalsoModulo(LIMIT_LIST=True, WATCH=False),
            "OCG": FalsoModulo(TR=FalsoModulo(DEFAULT_CFG={"turn_timer": 99, "counters": True})),
            "PP": FalsoModulo(NPC_MP_REWARD={"win": 5, "lose": 0, "draw": 0}),
        }
        self.mods = {"pvp_hub": FalsoModulo(RIVAL_CARD=False)}
        self.env = {"YO2_DUELPASS_REARM": "0"}
        self.cfg = C.Config(self.ns, modulos=self.mods, entorno=self.env)

    def test_ligas(self):
        self.assertIs(self.cfg.leer("NPC_SPOTS_2008"), True)
        self.assertIs(self.cfg.leer("mundo.prizes"), False)
        self.assertEqual(self.cfg.leer("lobby.WATCH"), False)
        self.assertEqual(self.cfg.leer("duelo.turn_timer"), 99)
        self.assertEqual(self.cfg.leer("persistencia.NPC_MP_WIN"), 5)
        self.assertEqual(self.cfg.leer("hub.RIVAL_CARD"), False, "módulo que solo está en sys.modules")
        self.assertEqual(self.cfg.leer("persistencia.DUELPASS_REARM"), "0", "liga de entorno")

    def test_no_cargada(self):
        self.assertIs(self.cfg.leer("equipos.NAME_CASEFOLD"), C.NO_CARGADA)
        self.assertIs(self.cfg.leer("mundo.challenge"), C.NO_CARGADA)         # clave que falta
        self.assertIsNone(self.cfg.get("equipos.NAME_CASEFOLD"))
        self.assertEqual(self.cfg.get("equipos.NAME_CASEFOLD", 5), 5)
        with self.assertRaises(LookupError):
            self.cfg["equipos.NAME_CASEFOLD"]
        self.assertEqual(self.cfg["mundo.spots"], True)
        with self.assertRaises(KeyError):
            self.cfg.leer("no.existe")

    def test_es_lectura_en_vivo(self):
        self.assertEqual(self.cfg["lobby.WATCH"], False)
        self.ns["LP"].WATCH = True                         # lo que haría el panel con setattr
        self.assertEqual(self.cfg["lobby.WATCH"], True)

    def test_vanilla_perfiles_y_diferencias(self):
        self.assertEqual(self.cfg.vanilla("mundo.spots"), True)
        self.assertEqual(self.cfg.valor_perfil("mundo.spots", "enhanced"), self.cfg.vanilla("mundo.spots"))
        self.assertEqual(self.cfg.perfil, "vanilla")
        with self.assertRaises(ValueError):
            self.cfg.valor_perfil("mundo.spots", "otro")
        dif = self.cfg.diferencias()
        self.assertIn("mundo.prizes", dif)                 # False frente a True
        self.assertIn("lobby.WATCH", dif)
        self.assertIn("duelo.turn_timer", dif)             # 99 frente a 180
        self.assertNotIn("NPC_SPOTS_2008", dif)
        self.assertNotIn("persistencia.DUELPASS_REARM", dif, "las ligas de entorno no se comparan")

    def test_perfil_por_entorno(self):
        self.assertEqual(C.Config({}, entorno={"YO2_PERFIL": "enhanced"}).perfil, "enhanced")
        c = C.Config({}, entorno={"YO2_PERFIL": "raro"})
        self.assertEqual(c.perfil, "vanilla")
        self.assertIn("raro", c.perfil_aviso)

    def test_todas_tiene_los_campos_del_panel(self):
        todas = {d["id"]: d for d in self.cfg.todas()}
        d = todas["lobby.LIMIT_LIST"]
        for k in ("id", "valor", "existe", "tipo", "opciones", "efecto", "reiniciar", "desc", "fijo", "grupo"):
            self.assertIn(k, d)
        self.assertEqual((d["valor"], d["existe"], d["tipo"], d["grupo"]), (True, True, "bool", "lobby"))
        self.assertEqual(todas["duelo.chain_priority"]["opciones"], ["oficial", "evento", "spe", None])
        self.assertFalse(todas["equipos.NAME_CASEFOLD"]["existe"])
        self.assertEqual({x["id"] for x in self.cfg.todas(grupo="mundo")}, {f.id for f in C.REGISTRO if f.grupo == "mundo"})
        self.assertTrue(all(x["deploy"] for x in self.cfg.todas(deploy=True)))

    def test_sin_set_en_caliente(self):
        """R2-corto aplica el perfil al arrancar, pero NO añade `set` en caliente ni «dejar fijo» (eso es R2 completo)."""
        for nombre in ("set", "set_flag", "__setitem__", "guardar", "fijar"):
            self.assertFalse(hasattr(self.cfg, nombre), f"R2-corto no añade {nombre} a Config")


class TestOrigen(unittest.TestCase):
    def test_todas_las_banderas_tienen_origen(self):
        for f in C.REGISTRO:
            with self.subTest(f.id):
                self.assertIn(f.origen, C.ORIGENES)

    def test_origen_es_obligatorio_en_F(self):
        with self.assertRaises(TypeError):
            C.F("x.y", ("ns", "Y"), True)
        with self.assertRaises(ValueError):
            C.F("x.y", ("ns", "Y"), True, origen="inventada")

    def test_las_de_despliegue_son_tecnicas(self):
        for f in C.REGISTRO:
            if f.deploy:
                with self.subTest(f.id):
                    self.assertEqual(f.origen, "tecnico")

    def test_las_aprobadas_del_07_10_que_son_nuestras_estan_marcadas(self):
        """Las «inventadas» que el owner nombró (pose NPC, §24, lp_reconcile, ROOM_LANG_MERGE, tiempos, janken) no pueden pasar por oficiales."""
        # 10-10: la pose NPC (vídeo), turn_timer/turn_limit (texto 7006, TIEMPO.md) pasan a oficiales (owner)
        for i in ("duelo.chain_one_name", "duelo.lp_reconcile", "lobby.ROOM_LANG_MERGE",
                  "duelo.tiempo_respuesta", "duelo.npc_janken_azar"):
            self.assertEqual(C.BY_ID[i].origen, "inventado", i)
        for i in ("hub.NPC_DUEL_STATE", "duelo.turn_timer", "duelo.turn_limit", "duelo.npc_janken_elige"):
            self.assertEqual(C.BY_ID[i].origen, "oficial", i)

    def test_BANDERAS_md_esta_al_dia(self):
        from yo2srv import gen_banderas
        with open(gen_banderas.SALIDA, encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), gen_banderas.generar(), "BANDERAS.md desfasado: python -m yo2srv.gen_banderas")


class _Parche:
    """Cambia `enhanced` de algunas banderas en el registro (REGISTRO y BY_ID) y lo restaura al salir."""

    def __init__(self, **enhanced):
        self.enhanced = {k.replace("__", "."): v for k, v in enhanced.items()}

    def __enter__(self):
        self.antes = list(C.REGISTRO), dict(C.BY_ID)
        for i, v in self.enhanced.items():
            nueva = C.BY_ID[i]._replace(enhanced=v)
            C.BY_ID[i] = nueva
            C.REGISTRO[[f.id for f in C.REGISTRO].index(i)] = nueva
        return self

    def __exit__(self, *a):
        C.REGISTRO[:] = self.antes[0]
        C.BY_ID.clear()
        C.BY_ID.update(self.antes[1])


class TestPerfil(unittest.TestCase):
    """aplicar_perfil: el perfil se escribe por su liga; precedencia perfil < admin_banderas.json < entorno."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="yo2cfg-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.fichero = os.path.join(self.tmp, "admin_banderas.json")
        self.cfgtr = {"turn_timer": 180, "counters": True, "turn_limit": True}
        self.ns = {
            "MUNDO_2008": {"spots": True},
            "LP": FalsoModulo(WATCH=True, LIMIT_LIST=True),
            "pvp_match": FalsoModulo(PVP_WATCH=True),
            "OCG": FalsoModulo(TR=FalsoModulo(DEFAULT_CFG=self.cfgtr)),
        }

    def cfg(self, **env):
        return C.Config(self.ns, modulos={}, entorno=dict(env), fichero=self.fichero)

    def fijar(self, dato):
        import json
        with open(self.fichero, "w", encoding="utf-8") as fh:
            json.dump(dato, fh)

    def test_vanilla_no_escribe_nada(self):
        antes = repr(self.ns)
        r = self.cfg().aplicar_perfil()
        self.assertEqual((r["perfil"], r["escritas"], r["entorno"], r["fichero"]), ("vanilla", [], [], []))
        self.assertEqual(repr(self.ns), antes)

    def test_vanilla_no_escribe_ni_lo_que_el_codigo_desvia(self):
        """Una instancia de prueba puede tener un valor distinto de vanilla (p. ej. janken al azar apagado): vanilla no lo toca."""
        self.cfgtr["turn_timer"] = 30
        r = self.cfg().aplicar_perfil()
        self.assertEqual(r["escritas"], [])
        self.assertEqual(self.cfgtr["turn_timer"], 30)

    def test_enhanced_hoy_da_los_mismos_valores(self):
        # 10-10 (owner): solo chain_one_name difiere (vanilla False = 269 como el oficial; enhanced True)
        DIF = {"duelo.chain_one_name": (False, True)}
        for f in C.REGISTRO:
            if f.id not in DIF:
                self.assertIs(f.enhanced, C.SIN, f"{f.id}: enhanced debe seguir igual que vanilla hasta que el owner decida")
        c = self.cfg(YO2_PERFIL="enhanced")
        self.assertEqual(c.perfil, "enhanced")
        for f in C.REGISTRO:
            if f.id in DIF:
                self.assertEqual((c.valor_perfil(f.id, "vanilla"), c.valor_perfil(f.id)), DIF[f.id], f.id)
            else:
                self.assertEqual(c.valor_perfil(f.id), c.valor_perfil(f.id, "vanilla"), f.id)

    def test_el_perfil_enhanced_se_aplica_por_su_liga(self):
        with _Parche(duelo__turn_timer=90, lobby__LIMIT_LIST=False, mundo__spots=False):
            r = self.cfg(YO2_PERFIL="enhanced").aplicar_perfil()
            self.assertEqual(sorted(r["escritas"]), ["duelo.turn_timer", "lobby.LIMIT_LIST", "mundo.spots"])
            self.assertEqual(self.cfgtr["turn_timer"], 90)               # trcfg
            self.assertIs(self.ns["LP"].LIMIT_LIST, False)               # mod
            self.assertIs(self.ns["MUNDO_2008"]["spots"], False)         # nsdict
            # y con vanilla (por defecto) esas mismas banderas NO se tocan
            self.cfgtr["turn_timer"] = 180
            self.ns["LP"].LIMIT_LIST = True
            self.assertEqual(self.cfg().aplicar_perfil()["escritas"], [])
            self.assertEqual(self.cfgtr["turn_timer"], 180)

    def test_idempotente(self):
        with _Parche(duelo__turn_timer=90):
            c = self.cfg(YO2_PERFIL="enhanced")
            self.assertEqual(c.aplicar_perfil()["escritas"], ["duelo.turn_timer"])
            self.assertEqual(c.aplicar_perfil()["escritas"], [])

    def test_el_entorno_manda_sobre_el_perfil(self):
        with _Parche(duelo__turn_timer=90, lobby__LIMIT_LIST=False):
            r = self.cfg(YO2_PERFIL="enhanced", YO2_TURN_TIMER="30").aplicar_perfil()
            self.assertEqual(r["escritas"], ["lobby.LIMIT_LIST"])
            self.assertEqual(r["entorno"], ["duelo.turn_timer"])
            self.assertEqual(self.cfgtr["turn_timer"], 180, "con la variable puesta no se escribe el valor del perfil")

    def test_el_fichero_manda_sobre_el_perfil_y_el_entorno_sobre_el_fichero(self):
        self.fijar({"duelo.turn_timer": 45, "lobby.LIMIT_LIST": False})
        with _Parche(duelo__turn_timer=90):
            r = self.cfg(YO2_PERFIL="enhanced", YO2_LIMIT_LIST="1").aplicar_perfil()
            self.assertEqual(self.cfgtr["turn_timer"], 45, "fichero > perfil")
            self.assertIs(self.ns["LP"].LIMIT_LIST, True, "entorno > fichero")
            self.assertEqual((r["escritas"], r["fichero"], r["entorno"]),
                             (["duelo.turn_timer"], ["duelo.turn_timer"], ["lobby.LIMIT_LIST"]))

    def test_el_fichero_tambien_vale_con_vanilla(self):
        self.fijar({"banderas": {"duelo.turn_timer": 60}})
        r = self.cfg().aplicar_perfil()
        self.assertEqual((self.cfgtr["turn_timer"], r["fichero"]), (60, ["duelo.turn_timer"]))

    def test_fichero_con_cosas_raras_se_ignora_con_aviso(self):
        self.fijar({"no.existe": 1, "duelo.turn_timer": "mucho", "sistema.PORT_OFFSET": 5, "lobby.WATCH": False})
        r = self.cfg().aplicar_perfil()
        self.assertEqual(r["fichero"], ["lobby.WATCH"])
        self.assertEqual(self.cfgtr["turn_timer"], 180)
        self.assertEqual(len(r["avisos"]), 3)
        with open(self.fichero, "w", encoding="utf-8") as fh:
            fh.write("{roto")
        r = self.cfg().aplicar_perfil()
        self.assertEqual(r["fichero"], [])
        self.assertIn("no se pudo leer", r["avisos"][0])

    def test_nunca_crea_admin_banderas_json(self):
        self.cfg().aplicar_perfil()
        with _Parche(duelo__turn_timer=90):
            self.cfg(YO2_PERFIL="enhanced").aplicar_perfil()
        self.assertFalse(os.path.exists(self.fichero))

    def test_sitio_no_cargado_no_se_escribe(self):
        ns = {"MUNDO_2008": {}}
        with _Parche(equipos__NAME_CASEFOLD=False):
            r = C.Config(ns, modulos={}, entorno={"YO2_PERFIL": "enhanced"}, fichero=self.fichero).aplicar_perfil()
        self.assertEqual(r["escritas"], [])
        self.assertTrue(any("equipos.NAME_CASEFOLD" in a for a in r["avisos"]))

    def test_accion_watch_copia_a_pvp_match(self):
        with _Parche(lobby__WATCH=False):
            r = self.cfg(YO2_PERFIL="enhanced").aplicar_perfil()
        self.assertEqual(r["escritas"], ["lobby.WATCH"])
        self.assertIs(self.ns["pvp_match"].PVP_WATCH, False)

    def test_perfil_desconocido_avisa_y_usa_vanilla(self):
        r = self.cfg(YO2_PERFIL="raro").aplicar_perfil()
        self.assertEqual(r["perfil"], "vanilla")
        self.assertIn("raro", r["avisos"][0])

    def test_las_de_despliegue_no_se_tocan(self):
        ns = {"PORT_OFFSET": 7}
        with _Parche(sistema__PORT_OFFSET=0):
            r = C.Config(ns, modulos={}, entorno={"YO2_PERFIL": "enhanced"}, fichero=self.fichero).aplicar_perfil()
        self.assertEqual((r["escritas"], ns["PORT_OFFSET"]), ([], 7))

    def test_con_entorno_real_y_namespace_vacio_vanilla_no_falla(self):
        r = C.Config({}, modulos={}, entorno={}, fichero=self.fichero).aplicar_perfil()
        self.assertEqual(r["escritas"], [])


if __name__ == "__main__":
    unittest.main()
