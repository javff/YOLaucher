"""hot.py: recarga genérica por módulo.

  python -m yo2srv.tests                      las unitarias de siempre, con un servidor falso (nada de run.py): éxito, error de
                                              sintaxis (no toca nada), fallo en setup o en keep (vuelta atrás), noreload, dueños
                                              del router/bus y estado que sobrevive (keep)
  python yo2srv/tests/test_hot.py --vivo      R4: los 4 casos del plan en una instancia de PRUEBA de run.py (banda 1300), ver
                                              abajo (hot_unit.py: las unitarias nuevas de R4: dependientes, banderas, huella, Tx)
"""
import glob
import os
import re
import shutil
import sys
import tempfile
import textwrap
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import bus as B, hot as H, router as R  # noqa: E402


class FalsoSrv:
    """Lo mínimo que usa Hot de un Server: ns, router, bus y log."""

    def __init__(self):
        self.ns = {}
        self.lineas = []
        self.router = R.Router(log=self.lineas.append)
        self.bus = B.Bus(log=self.lineas.append)

    def log(self, *a):
        self.lineas.append(" ".join(str(x) for x in a))


class Conn:
    def __init__(self):
        self.n = []

    def send(self, mid, d=b""):
        self.n.append(mid)


def modulo(version, extra=""):
    return textwrap.dedent(f'''
        VERSION = {version}
        ESTADO = {{"visto": 0}}
        def h(c, mid, p, d):
            c.send({version})
        def setup(srv):
            srv.router.on("lobby", 0x9000, h, dueño="mod_a")
            srv.bus.subscribe("evt", lambda *a: "v{version}", dueño="mod_a")
        def keep(viejo, srv):
            ESTADO["visto"] = viejo.ESTADO["visto"] + 1
        {extra}
    ''')


class TestHot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hot-")
        self.f = os.path.join(self.dir, "mod_a.py")
        self.srv = FalsoSrv()
        self.hot = H.Hot(self.srv)
        self.nombre_sys = "yo2_mod_a_prueba"
        self.addCleanup(lambda: sys.modules.pop(self.nombre_sys, None))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def escribir(self, txt):
        with open(self.f, "w", encoding="utf-8") as f:
            f.write(txt)

    def cargar_v1(self):
        self.escribir(modulo(1))
        self.hot.registrar("mod_a", self.f, ns_nombre="MODA", sys_nombre=self.nombre_sys)
        self.assertEqual(self.hot.recargar("mod_a"), {"mod_a": "ok"})
        return sys.modules[self.nombre_sys]

    def despachar(self):
        c = Conn()
        self.assertTrue(self.srv.router.dispatch("lobby", c, 0x9000, 0, b""))
        return c.n[-1]

    def test_primera_carga_y_recarga(self):
        v1 = self.cargar_v1()
        self.assertEqual(v1.VERSION, 1)
        self.assertIs(self.srv.ns["MODA"], v1)
        self.assertEqual(self.despachar(), 1)
        self.assertEqual(self.srv.bus.ask("evt"), "v1")
        self.escribir(modulo(2))
        self.assertEqual(self.hot.recargar("mod_a"), {"mod_a": "ok"})
        v2 = sys.modules[self.nombre_sys]
        self.assertIsNot(v2, v1, "módulo NUEVO, no importlib.reload")
        self.assertEqual(v1.VERSION, 1, "el viejo no se toca (lo que está en curso conserva sus clases)")
        self.assertIs(self.srv.ns["MODA"], v2)
        self.assertEqual(self.despachar(), 2)
        self.assertEqual(self.srv.bus.ask("evt"), "v2")
        self.assertEqual(self.srv.router.tabla()[("lobby", 0x9000)], ["mod_a"], "solo sus entradas, sin duplicar")
        self.assertEqual(self.srv.bus.suscriptores("evt").__len__(), 1)
        self.assertEqual(v2.__name__, self.nombre_sys)
        self.assertEqual(self.srv.ns["_HOT_LAST"]["resultado"], "ok")
        self.assertTrue(any("[recarga] mod_a ok" in x for x in self.srv.lineas))

    def test_keep_pasa_el_estado(self):
        v1 = self.cargar_v1()
        v1.ESTADO["visto"] = 41
        self.escribir(modulo(2))
        self.hot.recargar("mod_a")
        self.assertEqual(sys.modules[self.nombre_sys].ESTADO["visto"], 42)

    def test_error_de_sintaxis_no_toca_nada(self):
        v1 = self.cargar_v1()
        self.escribir("def roto(:\n")
        r = self.hot.recargar("mod_a")
        self.assertTrue(r["mod_a"].startswith("error: no compila"), r)
        self.assertIs(sys.modules[self.nombre_sys], v1)
        self.assertIs(self.srv.ns["MODA"], v1)
        self.assertEqual(self.despachar(), 1)
        self.assertEqual(self.srv.bus.ask("evt"), "v1")
        self.assertTrue(self.srv.ns["_HOT_LAST"]["resultado"].startswith("error"))

    def test_fichero_ausente(self):
        self.hot.registrar("mod_b", os.path.join(self.dir, "no_existe.py"))
        self.assertTrue(self.hot.recargar("mod_b")["mod_b"].startswith("error: no compila"))

    def test_excepcion_al_ejecutar_el_modulo_no_toca_nada(self):
        v1 = self.cargar_v1()
        self.escribir(modulo(2) + "\nraise RuntimeError('al importar')\n")
        r = self.hot.recargar("mod_a")
        self.assertIn("al importar", r["mod_a"])
        self.assertIs(sys.modules[self.nombre_sys], v1)
        self.assertEqual(self.despachar(), 1)
        self.assertFalse([k for k in sys.modules if k.startswith(self.nombre_sys + "__hot")], "sin nombres privados")

    def test_system_exit_en_el_modulo_no_para_el_servidor(self):
        v1 = self.cargar_v1()
        self.escribir("import sys\nsys.exit(3)\n")
        r = self.hot.recargar("mod_a")
        self.assertTrue(r["mod_a"].startswith("error"), r)
        self.assertIs(sys.modules[self.nombre_sys], v1)

    def test_fallo_en_setup_deshace_todo(self):
        v1 = self.cargar_v1()
        malo = modulo(2).replace('srv.bus.subscribe("evt", lambda *a: "v2", dueño="mod_a")',
                                 'srv.bus.subscribe("evt", lambda *a: "v2", dueño="mod_a"); raise RuntimeError("setup roto")')
        self.escribir(malo)
        r = self.hot.recargar("mod_a")
        self.assertIn("setup roto", r["mod_a"])
        self.assertIs(sys.modules[self.nombre_sys], v1)
        self.assertIs(self.srv.ns["MODA"], v1)
        self.assertEqual(self.despachar(), 1, "vuelve el manejador viejo")
        self.assertEqual(self.srv.bus.ask("evt"), "v1", "vuelve la suscripción vieja, sin la nueva")
        self.assertEqual(self.srv.router.tabla()[("lobby", 0x9000)], ["mod_a"])
        self.assertEqual(len(self.srv.bus.suscriptores("evt")), 1)

    def test_fallo_en_setup_de_la_primera_carga_no_deja_rastro(self):
        self.escribir("def setup(srv):\n    raise RuntimeError('nace roto')\n")
        self.hot.registrar("mod_a", self.f, ns_nombre="MODA", sys_nombre=self.nombre_sys)
        r = self.hot.recargar("mod_a")
        self.assertIn("nace roto", r["mod_a"])
        self.assertNotIn(self.nombre_sys, sys.modules)
        self.assertNotIn("MODA", self.srv.ns)

    def test_fallo_en_keep_no_toca_el_router(self):
        v1 = self.cargar_v1()
        self.escribir(modulo(2).replace("ESTADO[\"visto\"] = viejo.ESTADO[\"visto\"] + 1", "raise RuntimeError('keep roto')"))
        r = self.hot.recargar("mod_a")
        self.assertIn("keep roto", r["mod_a"])
        self.assertIs(sys.modules[self.nombre_sys], v1)
        self.assertEqual(self.despachar(), 1)

    def test_noreload_y_no_registrado(self):
        self.hot.registrar("red.proto", self.f, noreload=True)
        self.assertIn("reiniciar", self.hot.recargar("red.proto")["red.proto"])
        self.assertIn("no registrado", self.hot.recargar("nada")["nada"])

    def test_varios_en_orden(self):
        self.cargar_v1()
        f2 = os.path.join(self.dir, "mod_b.py")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("X = 1\n")
        self.hot.registrar("mod_b", f2, sys_nombre="yo2_mod_b_prueba")
        self.addCleanup(lambda: sys.modules.pop("yo2_mod_b_prueba", None))
        self.assertEqual(self.hot.recargar("mod_a", "mod_b"), {"mod_a": "ok", "mod_b": "ok"})

    def test_la_recarga_espera_a_un_despacho_en_curso_o_se_deshace(self):
        import threading
        v1 = self.cargar_v1()
        dentro, soltar = threading.Event(), threading.Event()

        def lento(c, *a):
            dentro.set()
            soltar.wait(5)

        self.srv.router.on("lobby", 0x9100, lento, dueño="otro")
        t = threading.Thread(target=lambda: self.srv.router.dispatch("lobby", Conn(), 0x9100, 0, b""))
        t.start()
        self.assertTrue(dentro.wait(5))
        self.escribir(modulo(2))
        r = self.hot.recargar("mod_a", pausa_s=0.2)
        soltar.set()
        t.join(5)
        self.assertIn("no se pudo pausar", r["mod_a"])
        self.assertIs(sys.modules[self.nombre_sys], v1)
        self.assertEqual(self.despachar(), 1)


# ====================================================================================================================
# PRUEBA EN VIVO (R4):   python yo2srv/tests/test_hot.py --vivo [--banda 1300 | --offset N]       (sale con 1 si algo falla)
#
# Arranca una instancia de PRUEBA de run.py (nunca la viva; mismos offsets prohibidos que comun.Instancia) con YO2_PVP_TEST=1,
# habla con ella con clientes falsos y recarga módulos por SU consola (`hot(...)`), como se hará de verdad. Los 4 casos del plan
# (REESTRUCTURA.md §3.4, R4) y lo que añade R4:
#   1. recargar lobby.lp en mitad de una sesión de fake_client: nadie se desconecta, un cliente que pide sin parar no pierde ni
#      una respuesta, las respuestas son byte a byte las de antes y el LP nuevo lleva el cableado completo (EQ/TP/TO, chat de
#      mundo, sobres de NPC y de PvP);
#   2. un fichero que no compila / que falla al ejecutarse / un setup que falla a medias -> no cambia nada (ni el módulo, ni
#      sys.modules, ni LP.HANDLERS, ni HUB.list5_body);
#   3. recargar duelo.traductor durante un duelo de NPC: ese duelo acaba con el módulo viejo (su OcgSession y su YO2Duel son los
#      de antes) y el siguiente usa el nuevo;
#   4. _CORE (el mismo node) y OcgSession._next_id heredados;
#   +  una bandera cambiada en vivo (LP, OCG, DEFAULT_CFG del traductor, una clave suelta del dict) sobrevive a la recarga;
#   +  recargar lobby.equipos deja sus manejadores alcanzables por LP.HANDLERS;
#   +  U1: recargar lockstep, persistencia y duelfin en mitad de una sesión: DFM.LS is LS, DFM.PS is PP, sys.modules["research_x"]
#      sigue al cambio, una bandera cambiada en vivo de cada uno sobrevive, un duelo de NPC en curso acaba tras recargar duelfin
#      (con el viejo) y el siguiente usa el nuevo;
#   +  se recargan TODOS los módulos y después pasan intactos fake_client pvp (43 comprobaciones) y npc.
# Las comprobaciones sobre el interior de la instancia se hacen con scripts que ejecuta su consola y que escriben un JSON.
# ====================================================================================================================

INV_U1 = """
    fall = []
    LS, PP, DFM, NR = NS["LS"], NS["PP"], NS["DFM"], NS["NR"]
    for area, n in (("lockstep", "LS"), ("persistencia", "PP"), ("duelfin", "DFM")):
        if sys.modules["research_" + area] is not NS[n]:
            fall.append("sys.modules " + area)
    if DFM.LS is not LS:
        fall.append("DFM.LS")
    if DFM.PS is not PP:
        fall.append("DFM.PS")
    TR = sys.modules["translator"]
    if TR.engine is not LS.engine or TR.setcommand is not LS.setcommand:
        fall.append("traductor sin los nombres del LS vivo")
    if NS["wallet"] is not NS["PH"]["wallet"] or NS["PH"]["h_playerlist"].__globals__ is not PP.__dict__:
        fall.append("PH")
    if not (PP.npc_rewards.__globals__ is NR.__dict__ and DFM.npc_duel_end.__globals__ is NR.__dict__
            and DFM._npcreglas["orig_rewards"].__globals__ is PP.__dict__):
        fall.append("npcreglas")
    MW = NS["MW"]
    if not (PP.challenge_rights_due.__globals__ is MW.__dict__ and PP.on_title.__globals__ is MW.__dict__):
        fall.append("mundo sobre PP")
    if not (hasattr(PP.blank_profile, "_dp_orig") and hasattr(PP.seed_trunk, "_dp_orig")):
        fall.append("DUELPASS (cifras de inicio)")
    if NS["PH_LOBBY"].get(0x8101) != "h_dpauth" or NS["PH"].get("h_dpauth") is None:
        fall.append("DUELPASS 0x8101")
    if not getattr(NS["OCG"].TR, "_surrender_patched", False) or LS.comid_field.__globals__ is LS.__dict__:
        fall.append("Rendirse")
    if LS.DUEL_CFG["stage"] != 4 or LS.duel_reply_base is not NS["duel_reply_base"] or LS.log is not NS["log"]:
        fall.append("LS: stage / duel_reply_base / log")
    if PP.RAW_STATE is None:
        fall.append("PP.RAW_STATE")
    res["inv"] = fall
"""


def inv(extra=""):
    return textwrap.dedent(INV_U1) + textwrap.dedent(extra)

VIVO_SCRIPT_PRE = """
import json, sys, traceback
SRV = NS["SRV"]
T = NS.setdefault("_T", {})
res = {}
try:
"""
VIVO_SCRIPT_POST = """
except BaseException:
    res["error"] = traceback.format_exc()
with open(SALIDA, "w", encoding="utf-8") as _f:
    json.dump(res, _f, ensure_ascii=False, default=repr)
"""


def _consola(off, linea, timeout=120):
    import socket
    tok = open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "YO2Online", f"consola_{5799 + off}.txt"),
               encoding="ascii").read().strip()
    s = socket.create_connection(("127.0.0.1", 5799 + off), timeout=timeout)
    try:
        s.sendall(("AUTH " + tok + "\n" + linea + "\n").encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        return s.recv(65536).decode("utf-8", "replace").strip()
    finally:
        s.close()


def _dentro(off, carpeta, codigo, nombre):
    """Ejecuta `codigo` (en un try) en el namespace de la instancia y devuelve el dict `res` que deje."""
    import json
    import time
    salida = os.path.join(carpeta, nombre + ".json")
    script = os.path.join(carpeta, nombre + ".py")
    if os.path.exists(salida):
        os.remove(salida)
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(VIVO_SCRIPT_PRE + textwrap.indent(textwrap.dedent(codigo), "    ") + VIVO_SCRIPT_POST)
    r = _consola(off, f"exec(compile(open({script!r}, encoding='utf-8').read(), {nombre!r}, 'exec'), "
                      f"{{'NS': globals(), 'SALIDA': {salida!r}}})")
    for _ in range(100):
        if os.path.exists(salida):
            break
        time.sleep(0.1)
    d = json.load(open(salida, encoding="utf-8")) if os.path.exists(salida) else {"error": f"sin salida (consola: {r})"}
    d["_consola"] = r
    return d


def vivo(argv):
    import argparse
    import threading
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("--vivo", action="store_true")
    ap.add_argument("--offset", type=int, default=None)
    ap.add_argument("--banda", type=int, default=1300)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    PAR = os.path.join(EMU, "paridad")
    if PAR not in sys.path:
        sys.path.insert(0, PAR)
    import comun
    FC = comun.fake_client()
    estado = os.path.join(comun.FIX, "state-paridad.json")
    if not os.path.exists(estado):
        sys.exit(f"falta {estado} (python paridad/grabar.py --preparar)")
    if a.offset is not None:
        off = a.offset
    else:
        off = next((a.banda + 10 * k for k in range(20) if not comun.offset_valido(a.banda + 10 * k)
                    and not comun.ocupados(a.banda + 10 * k)), None)
        if off is None:
            sys.exit(f"no queda ningún offset libre en la banda {a.banda}")
    motivo = comun.offset_valido(off)
    if motivo:
        sys.exit(motivo)
    carpeta = os.path.join(comun.OUT, "hot-vivo", f"inst-{off}")
    if os.path.isdir(carpeta):
        shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(carpeta, exist_ok=True)
    entorno = {"YO2_PVP_TEST": "1", "YO2_DUEL_PAUSE": "0.002", "YO2_ADMIN_PANEL": "1",
               "YO2_ADMIN_TOKEN_FILE": os.path.join(carpeta, "admin_token.txt")}
    res = []

    def check(nombre, cond, info=""):
        res.append((nombre, bool(cond)))
        print(f"  {'OK ' if cond else 'MAL'} {nombre}" + (f"  [{info}]" if info and not cond else ""))

    def en(codigo, nombre):
        d = _dentro(off, carpeta, codigo, nombre)
        if d.get("error"):
            print(f"  (script {nombre}) {str(d['error'])[-700:]}")
        return d

    def log_texto():
        out = ""
        for f in sorted(glob.glob(os.path.join(carpeta, "*.log"))):
            out += open(f, encoding="utf-8", errors="replace").read()
        return out

    print(f"== recarga en caliente en vivo: instancia de prueba en el offset {off}")
    with comun.Instancia(off, carpeta, estado, entorno, espera_duelcore=3.0):
        # ---------------------------------------------------------------- registro y consola
        d = en("""
            res["lista"] = SRV.hot.listar()
            res["hot_callable"] = callable(NS.get("hot"))
            res["hot_vacio"] = {k: v["sha"] for k, v in NS["hot"]().items()}
        """, "s0")
        lista = d.get("lista") or {}
        esperados = {"duelcore", "red.proto", "red.conexion", "ia.brain", "duelo.traductor", "duelo.ocg", "duelo.pvp_match",
                     "lobby.hub", "lobby.lp", "lobby.equipos", "lobby.intercambio", "lobby.torneos", "mundo.mapas",
                     "mundo.npcreglas", "mundo.avatar", "tienda.duelpass", "duelo.lockstep", "datos.perfiles", "duelo.duelfin"}
        check("registro: están los 19 módulos (los 16 del plan + lockstep, perfiles y duelfin de U1)", esperados <= set(lista),
              sorted(esperados - set(lista)))
        check("registro: duelcore, red.proto y red.conexion son noreload, con motivo",
              all(lista.get(n, {}).get("noreload") and lista[n].get("motivo") for n in ("duelcore", "red.proto", "red.conexion")))
        check("registro: los demás llevan sha8 y el disco coincide con lo cargado",
              all(v.get("sha") and not v.get("cambiado") for n, v in lista.items() if not v.get("noreload")),
              {n: v for n, v in lista.items() if not v.get("noreload") and (not v.get("sha") or v.get("cambiado"))})
        check("la consola tiene hot() y hot() sin argumentos lista", d.get("hot_callable") is True
              and len(d.get("hot_vacio") or {}) >= 19)
        _consola(off, "hot('red.proto')")
        u = en("res['u'] = NS['_HOT_LAST']", "s0b").get("u") or {}
        check("noreload: hot('red.proto') no hace nada y dice que hay que reiniciar", "reiniciar" in u.get("resultado", ""), u)

        # ---------------------------------------------------------------- 1. lobby.lp en mitad de una sesión
        X = FC.FakeClient("H", bytes([0x5A] * 16 + list(range(16))), "127.0.0.1", off, "127.0.0.9")
        Ys = [FC.FakeClient(f"T{k}", bytes([0x6B + k] * 16 + list(range(16))), "127.0.0.1", off, f"127.0.0.{20 + k}")
              for k in range(3)]
        for c, nm in [(X, "Hot")] + [(y, f"Tras{k}") for k, y in enumerate(Ys)]:
            c.gate()
            c.account(0, create_name=nm)
            c.enter_room()
        PEDIDOS = ((0x4558, b""), (0x4620, b""), (0x4901, b""), (0x4610, b""))

        def respuestas(c):
            out = {}
            for mid, datos in PEDIDOS:
                n = len(c.room.msgs)
                c.room.send(mid, datos)
                time.sleep(0.45)
                out[mid] = [(m, bytes(dd).hex()) for m, _, dd in c.room.msgs[n:]]
            return out
        antes = respuestas(X)
        trafico = {"ok": 0, "mal": 0, "parar": False}

        def bombardeo(Y):
            while not trafico["parar"]:
                n = len(Y.room.msgs)
                try:
                    Y.room.send(0x4558, b"")
                    got = Y.room.wait(0x4565, start=n, timeout=4)
                except OSError:
                    got = None
                trafico["ok" if got is not None else "mal"] += 1
                time.sleep(0.02)
        hilos = [threading.Thread(target=bombardeo, args=(y,), daemon=True) for y in Ys]
        for h in hilos:
            h.start()
        d0 = en("""
            LP = NS["LP"]
            T["LP"], T["MEM"], T["HAND"], T["LOCK"] = LP, LP.MEM, LP.HANDLERS, LP.LOCK
            T["EQ"], T["TP"], T["TO"] = NS["EQ"], NS["TP"], NS["TO"]
            T["HUB"] = NS["HUB"]
            res["sesiones"] = len(NS["HUB"].sessions)
        """, "s1a")
        _consola(off, "hot('lobby.lp')")
        time.sleep(0.5)
        d1 = en("""
            LP, old = NS["LP"], T["LP"]
            h = LP.HANDLERS
            res["r"] = NS["_HOT_LAST"]
            res["nuevo"] = LP is not old and sys.modules["research_lobby"] is LP and LP.__name__ == "research_lobby"
            res["viejo_intacto"] = old.HANDLERS is T["HAND"] and old.MEM is T["MEM"]
            res["estado_heredado"] = LP.MEM is T["MEM"] and LP.LOCK is T["LOCK"]
            res["handlers_nuevos"] = h is not T["HAND"] and h[0x4280].__globals__ is LP.__dict__
            res["eq"] = all(h[k] is T["EQ"].HANDLERS[k] for k in T["EQ"].HANDLERS)
            res["tp"] = h[0x7000] is T["TP"].HANDLERS[0x7000]
            res["to"] = all(h[k] is T["TO"].HANDLERS[k] for k in T["TO"].HANDLERS)
            c = h[0x4400]
            res["chat_mundo"] = bool(getattr(c, "_gm", False)) and c._orig.__globals__ is LP.__dict__
            s = h[0x4802]
            res["sobres"] = bool(getattr(s, "_pvpcarta", 0)) and bool(getattr(s._pvp_orig, "_npcreglas", 0))
            res["sesiones"] = len(NS["HUB"].sessions)
            res["mismo_hub"] = NS["HUB"] is T["HUB"]
        """, "s1b")
        # más recargas con el cliente pesado pidiendo: otro LP, y los módulos que le meten manejadores
        _consola(off, "hot('lobby.lp', 'lobby.equipos', 'lobby.intercambio', 'lobby.torneos', 'mundo.mapas', 'lobby.lp')")
        time.sleep(0.5)
        trafico["parar"] = True
        for h in hilos:
            h.join(8)
        despues = respuestas(X)
        check("LP: la recarga contesta ok y LP es un módulo NUEVO con el nombre de siempre",
              d1.get("r", {}).get("resultado") == "ok" and d1.get("nuevo") is True, str(d1.get("r")))
        check("LP: el módulo viejo queda intacto (sus HANDLERS y su MEM)", d1.get("viejo_intacto") is True)
        check("LP: MEM y LOCK son los MISMOS objetos (salas, correo, amigos…)", d1.get("estado_heredado") is True)
        check("LP: LP.HANDLERS nuevo y completo: EQ, TP (0x7000) y TO", d1.get("handlers_nuevos") is True
              and d1.get("eq") is True and d1.get("tp") is True and d1.get("to") is True, d1)
        check("LP: envoltorios del chat de mundo (0x4400) y de los sobres NPC + PvP (0x4802)",
              d1.get("chat_mundo") is True and d1.get("sobres") is True, d1)
        check("LP: nadie se desconecta (sesiones del HUB y conexiones abiertas)", d1.get("sesiones") == d0.get("sesiones")
              and not X.room.closed and not any(y.room.closed for y in Ys) and d1.get("mismo_hub") is True)
        check(f"LP: el cliente que pide sin parar no pierde respuestas ({trafico['ok']} ok, {trafico['mal']} sin respuesta)",
              trafico["ok"] >= 6 and trafico["mal"] == 0)
        check("LP: las respuestas a 4 peticiones son byte a byte las de antes", despues == antes,
              {k: (antes[k], despues[k]) for k in antes if antes[k] != despues[k]})
        errs = X.errors + [e for y in Ys for e in y.errors]
        check("LP: sin errores de numeración en los clientes", not errs, errs[:3])
        check("LP: el log lleva «[recarga] lobby.lp ok … sha=… antes=…»",
              re.search(r"\[recarga\] lobby\.lp ok \([\d.]+ s\) sha=[0-9a-f]{8} antes=[0-9a-f]{8}", log_texto()) is not None)
        _consola(off, "hot('lobby.lp')")
        check("LP: recargar un fichero idéntico lo dice («sin cambios»)",
              re.search(r"\[recarga\] lobby\.lp ok .* sin cambios", log_texto()) is not None)

        # ---------------------------------------------------------------- 2. nada cambia si algo falla
        d = en("""
            import os
            LP, HUB = NS["LP"], NS["HUB"]
            T["LP2"], T["HAND2"] = LP, LP.HANDLERS
            carpeta = os.path.dirname(SALIDA)
            roto = os.path.join(carpeta, "roto.txt")
            open(roto, "w", encoding="utf-8").write("def roto(:\\n")
            alexec = os.path.join(carpeta, "alexec.txt")
            open(alexec, "w", encoding="utf-8").write("X = 1\\nraise RuntimeError('al importar')\\n")
            m = SRV.hot.registro["lobby.lp"]
            real = m.fichero
            sha = m.sha
            out = {}
            for etiqueta, f in (("sintaxis", roto), ("alexec", alexec), ("ausente", os.path.join(carpeta, "no_existe.py"))):
                m.fichero = f
                out[etiqueta] = SRV.hot.recargar("lobby.lp")["lobby.lp"]
                out[etiqueta + "_igual"] = (NS["LP"] is LP and sys.modules["research_lobby"] is LP and LP.HANDLERS is T["HAND2"]
                                            and m.sha == sha and not [k for k in sys.modules if k.startswith("research_lobby__hot")])
            m.fichero = real
            res["fallos"] = out
            # setup que falla a medias: equipos mete sus manejadores en LP.HANDLERS y HUB.list5_body y LUEGO revienta
            me = SRV.hot.registro["lobby.equipos"]
            orig_setup, EQ = me.setup, NS["EQ"]
            T["EQ_h"], T["l5"] = LP.HANDLERS[0x4550], HUB.__dict__.get("list5_body")
            def mal(nuevo, viejo, srv, tx):
                orig_setup(nuevo, viejo, srv, tx)
                res["durante"] = LP.HANDLERS[0x4550] is nuevo.HANDLERS[0x4550] and HUB.list5_body is nuevo.list5_body
                raise RuntimeError("setup roto")
            me.setup = mal
            try:
                res["setup_roto"] = SRV.hot.recargar("lobby.equipos")["lobby.equipos"]
            finally:
                me.setup = orig_setup
            res["setup_igual"] = (NS["EQ"] is EQ and sys.modules["research_equipos"] is EQ
                                  and LP.HANDLERS[0x4550] is T["EQ_h"] and HUB.__dict__.get("list5_body") is T["l5"])
        """, "s2")
        f = d.get("fallos") or {}
        check("error de sintaxis: «error: no compila» y no cambia nada", str(f.get("sintaxis", "")).startswith("error: no compila")
              and f.get("sintaxis_igual") is True, f)
        check("excepción al ejecutar el módulo: error y no cambia nada", "al importar" in str(f.get("alexec", ""))
              and f.get("alexec_igual") is True, f)
        check("fichero ausente: error y no cambia nada", str(f.get("ausente", "")).startswith("error: no compila")
              and f.get("ausente_igual") is True, f)
        check("setup que falla a medias: se deshace (módulo, sys.modules, LP.HANDLERS, HUB.list5_body)",
              "setup roto" in str(d.get("setup_roto", "")) and d.get("durante") is True and d.get("setup_igual") is True, d)
        n = len(X.room.msgs)
        X.room.send(0x4558, b"")
        check("tras los fallos el cliente sigue atendido", X.room.wait(0x4565, start=n, timeout=4) is not None)

        # ---------------------------------------------------------------- banderas cambiadas en vivo
        en("""
            OCG, TR, LP, HUB = NS["OCG"], NS["OCG"].TR, NS["LP"], NS["HUB"]
            import pvp_hub as _ph
            cfg = SRV.cfg
            T["lp0"], T["th0"], T["cnp0"] = LP.LIMIT_LIST, OCG.NPC_THINK, TR.DEFAULT_CFG["chain_none_prompt"]
            cfg.escribir_liga(("mod", "LP", "LIMIT_LIST"), not T["lp0"])
            cfg.escribir_liga(("mod", "OCG", "NPC_THINK"), 0.123)
            cfg.escribir_liga(("trcfg", "chain_none_prompt"), not T["cnp0"])
            TR.DEFAULT_CFG["clave_de_prueba_r4"] = 77                 # una clave que ninguna bandera conoce
            T["ls0"] = _ph.LIST_SELF
            cfg.escribir_liga(("mod", "pvp_hub", "LIST_SELF"), not T["ls0"])
            T["EQN0"] = NS["EQ"].NAME_CASEFOLD
            cfg.escribir_liga(("mod", "EQ", "NAME_CASEFOLD"), not T["EQN0"])
        """, "s3a")
        _consola(off, "hot('lobby.lp', 'duelo.traductor', 'lobby.hub', 'lobby.equipos')")
        d = en("""
            OCG, TR, LP = NS["OCG"], NS["OCG"].TR, NS["LP"]
            import pvp_hub as _ph
            res["lp"] = LP.LIMIT_LIST == (not T["lp0"])
            res["ocg"] = OCG.NPC_THINK == 0.123
            res["tr"] = TR.DEFAULT_CFG["chain_none_prompt"] == (not T["cnp0"])
            res["clave"] = TR.DEFAULT_CFG.get("clave_de_prueba_r4") == 77
            res["hub"] = _ph.LIST_SELF == (not T["ls0"])
            res["eq"] = NS["EQ"].NAME_CASEFOLD == (not T["EQN0"])
            # y se devuelven a su sitio para no ensuciar lo que sigue
            cfg = SRV.cfg
            cfg.escribir_liga(("mod", "LP", "LIMIT_LIST"), T["lp0"])
            cfg.escribir_liga(("mod", "OCG", "NPC_THINK"), T["th0"])
            cfg.escribir_liga(("trcfg", "chain_none_prompt"), T["cnp0"])
            TR.DEFAULT_CFG.pop("clave_de_prueba_r4", None)
            cfg.escribir_liga(("mod", "pvp_hub", "LIST_SELF"), T["ls0"])
            cfg.escribir_liga(("mod", "EQ", "NAME_CASEFOLD"), T["EQN0"])
        """, "s3b")
        check("banderas vivas: LP.LIMIT_LIST sobrevive a recargar lobby.lp", d.get("lp") is True, d)
        check("banderas vivas: OCG.NPC_THINK sobrevive (el traductor arrastró a ocg_duel)", d.get("ocg") is True, d)
        check("banderas vivas: DEFAULT_CFG['chain_none_prompt'] sobrevive a recargar el traductor", d.get("tr") is True, d)
        check("banderas vivas: una clave suelta de DEFAULT_CFG (sin bandera) también", d.get("clave") is True, d)
        check("banderas vivas: pvp_hub.LIST_SELF (lobby.hub) y EQ.NAME_CASEFOLD (lobby.equipos)",
              d.get("hub") is True and d.get("eq") is True, d)
        check("banderas vivas: el log lo dice («banderas vivas conservadas»)", "banderas vivas conservadas" in log_texto())

        # ---------------------------------------------------------------- lobby.equipos: manejadores alcanzables
        en("""
            T["EQ_ant"] = NS["EQ"]
            T["PEND"] = NS["EQ"].PENDING
        """, "s4a")
        _consola(off, "hot('lobby.equipos')")
        d = en("""
            LP, EQ, HUB = NS["LP"], NS["EQ"], NS["HUB"]
            res["nuevo"] = EQ is not T["EQ_ant"]
            res["ids"] = all(LP.HANDLERS[k] is EQ.HANDLERS[k] and LP.HANDLERS[k].__globals__ is EQ.__dict__ for k in EQ.HANDLERS)
            res["n"] = len(EQ.HANDLERS)
            res["list5"] = HUB.list5_body.__globals__ is EQ.__dict__
            res["pend"] = EQ.PENDING is T["PEND"]
        """, "s4b")
        n = len(X.room.msgs)
        X.room.send(0x4558, b"")
        check("equipos: LP.HANDLERS apunta a los manejadores del EQ NUEVO (7 ids), HUB.list5_body también, PENDING heredado",
              d.get("nuevo") is True and d.get("ids") is True and d.get("n") == 7 and d.get("list5") is True
              and d.get("pend") is True, d)
        check("equipos: el cliente pide la lista de equipos y se la contesta el EQ nuevo",
              X.room.wait(0x4565, start=n, timeout=4) is not None)

        # ---------------------------------------------------------------- 3 y 4. traductor durante un duelo de NPC
        na = len(X.room.msgs)
        ok, nombre, start = FC._npc_start(X, 84)
        d = en("""
            OCG = NS["OCG"]
            T["OCG"], T["TR"] = OCG, OCG.TR
            T["core"], T["nid"] = OCG._CORE, OCG.OcgSession._next_id
            import time
            s = NS["CONNS"][NS["DUEL_PORT"]].ocg
            for _ in range(60):                                      # la sesión arma su YO2Duel en el hilo del duelo
                if s.d is not None:
                    break
                time.sleep(0.1)
            T["s1"] = s
            res["sesion_vieja"] = type(s) is OCG.OcgSession and type(s.d) is OCG.TR.YO2Duel
            res["core_vivo"] = OCG._CORE is not None and OCG.core_alive(OCG._CORE)
            res["ids"] = [s.id, T["nid"]]
        """, "s5a")
        check("NPC: empieza un duelo contra la NPC 84 con ocgcore", ok and d.get("sesion_vieja") is True
              and d.get("core_vivo") is True, d)
        i = FC._npc_turns(X, start, 2)
        _consola(off, "hot('duelo.traductor')")
        d = en("""
            OCG, old_ocg, old_tr = NS["OCG"], T["OCG"], T["TR"]
            s = T["s1"]
            res["resultado"] = NS["_HOT_LAST"]
            res["modulos_nuevos"] = (OCG is not old_ocg and OCG.TR is not old_tr and sys.modules["translator"] is OCG.TR
                                     and sys.modules["ocg_duel"] is OCG)
            res["viejos_intactos"] = (old_ocg.TR is old_tr and type(s) is old_ocg.OcgSession and type(s.d) is old_tr.YO2Duel
                                      and type(s.d) is not OCG.TR.YO2Duel)
            res["sesion_en_curso"] = (not s.ended) and NS["CONNS"][NS["DUEL_PORT"]].ocg is s
            res["core"] = OCG._CORE is T["core"] and OCG._CORE_LOCK is old_ocg._CORE_LOCK
            res["next_id"] = OCG.OcgSession._next_id >= T["nid"] and OCG.PvpSession._next >= old_ocg.PvpSession._next
            res["rendirse"] = bool(getattr(OCG.TR, "_surrender_patched", False))
        """, "s5b")
        check("NPC: recargar el traductor arrastra a ocg_duel y deja los dos módulos NUEVOS (sys.modules y OCG)",
              d.get("modulos_nuevos") is True, d)
        check("NPC: el duelo en curso conserva su OcgSession y su YO2Duel VIEJOS (y sigue sin acabar)",
              d.get("viejos_intactos") is True and d.get("sesion_en_curso") is True, d)
        check("NPC (4): _CORE es el MISMO node (y su cerrojo) y OcgSession._next_id / PvpSession._next no retroceden",
              d.get("core") is True and d.get("next_id") is True, d)
        check("NPC: «Rendirse» (comid_field con las máscaras de duelfin) puesto en el traductor nuevo", d.get("rendirse") is True)
        dlg, p0 = FC._npc_surrender(X, i)
        check("NPC: el duelo en curso ACABA (0x6202 tipo 0x4d) con el módulo viejo", p0 is not None, f"p0={p0}")
        X.room.wait(0x4800, start=na, timeout=8)
        d = en("""
            import time
            s = T["s1"]
            for _ in range(60):
                if s.ended:
                    break
                time.sleep(0.1)
            res["acabo"] = s.ended and type(s) is T["OCG"].OcgSession
        """, "s5c")
        check("NPC: la sesión vieja se da por terminada", d.get("acabo") is True, d)
        ok2, nombre2, start2 = FC._npc_start(X, 84)
        d = en("""
            import time
            OCG = NS["OCG"]
            s2 = NS["CONNS"][NS["DUEL_PORT"]].ocg
            for _ in range(60):
                if s2.d is not None:
                    break
                time.sleep(0.1)
            res["distinta"] = s2 is not T["s1"]
            res["nueva"] = type(s2) is OCG.OcgSession and type(s2) is not T["OCG"].OcgSession
            res["traductor_nuevo"] = type(s2.d) is OCG.TR.YO2Duel and type(s2.d) is not T["TR"].YO2Duel
            res["id_sigue"] = s2.id > T["s1"].id
        """, "s5d")
        check("NPC: el SIGUIENTE duelo usa la OcgSession y el YO2Duel NUEVOS, con numeración que sigue",
              ok2 and all(d.get(k) is True for k in ("distinta", "nueva", "traductor_nuevo", "id_sigue")), d)
        i2 = FC._npc_turns(X, start2, 1)
        dlg, p0 = FC._npc_surrender(X, i2)
        check("NPC: el duelo nuevo también acaba", p0 is not None, f"p0={p0}")
        time.sleep(1.0)

        # ---------------------------------------------------------------- U1: lockstep, persistencia y duelfin en mitad de una sesión
        en("""
            LS, PP, DFM = NS["LS"], NS["PP"], NS["DFM"]
            T["u1"] = {"LS": LS, "PP": PP, "DFM": DFM, "DF": NS["DF"], "PH": NS["PH"]}
            T["u1_vals"] = (LS.DUEL_CFG["npc_elige"], PP.NPC_MP_REWARD["draw"], PP.CHAIN_THRESHOLD)
            cfg = SRV.cfg
            cfg.escribir_liga(("moddict", "LS", "DUEL_CFG", "npc_elige"), "segundo")          # una bandera de cada módulo,
            cfg.escribir_liga(("moddict", "PP", "NPC_MP_REWARD", "draw"), 3)                  # cambiada EN VIVO
            cfg.escribir_liga(("mod", "PP", "CHAIN_THRESHOLD"), 5)
            LS.DUEL_CFG["clave_u1"] = 11                                                      # y una clave suelta del dict
        """, "u1a")
        for modulo in ("duelo.lockstep", "datos.perfiles", "duelo.duelfin"):
            _consola(off, f"hot({modulo!r})")
            d = en(inv("""
                res["r"] = NS["_HOT_LAST"]
                res["nuevos"] = [n for n in ("LS", "PP", "DFM") if NS[n] is not T["u1"][n]]
            """), "u1b")
            check(f"U1 {modulo}: recarga ok y los invariantes se cumplen (DFM.LS is LS, DFM.PS is PP, sys.modules, traductor, PH, "
                  f"npcreglas, mundo, DUELPASS, «Rendirse»)", d.get("r", {}).get("resultado") == "ok" and d.get("inv") == []
                  and len(d.get("nuevos") or []) >= 1, d)
            n = len(X.room.msgs)
            X.room.send(0x4558, b"")
            check(f"U1 {modulo}: el cliente de la sesión sigue conectado y atendido",
                  X.room.wait(0x4565, start=n, timeout=4) is not None and not X.room.closed and not X.errors, X.errors[:3])
        d = en("""
            LS, PP, DFM = NS["LS"], NS["PP"], NS["DFM"]
            res["flags"] = (LS.DUEL_CFG["npc_elige"], PP.NPC_MP_REWARD["draw"], PP.CHAIN_THRESHOLD, DFM.PS.CHAIN_THRESHOLD)
            res["clave"] = LS.DUEL_CFG.get("clave_u1")
            res["todos_nuevos"] = all(NS[n] is not T["u1"][n] for n in ("LS", "PP", "DFM")) and NS["PH"] is not T["u1"]["PH"]
            cfg = SRV.cfg                                                                     # y todo a su valor
            cfg.escribir_liga(("moddict", "LS", "DUEL_CFG", "npc_elige"), T["u1_vals"][0])
            cfg.escribir_liga(("moddict", "PP", "NPC_MP_REWARD", "draw"), T["u1_vals"][1])
            cfg.escribir_liga(("mod", "PP", "CHAIN_THRESHOLD"), T["u1_vals"][2])
            LS.DUEL_CFG.pop("clave_u1", None)
        """, "u1c")
        check("U1: las banderas cambiadas en vivo (LS.DUEL_CFG, PP.NPC_MP_REWARD, PP.CHAIN_THRESHOLD, también vista desde DFM.PS) "
              "y la clave suelta sobreviven a recargar los tres", tuple(d.get("flags") or ()) == ("segundo", 3, 5, 5)
              and d.get("clave") == 11 and d.get("todos_nuevos") is True, d)
        # un duelo de NPC EN CURSO mientras se recarga duelfin (y después persistencia y lockstep): acaba, y el siguiente también
        na = len(X.room.msgs)
        ok, nombre, start = FC._npc_start(X, 84)
        d = en("""
            c = NS["CONNS"][NS["DUEL_PORT"]]
            d = c.sess.duel
            T["u1_df"], T["u1_dfm"], T["u1_dfm_dict"] = d["df"], NS["DFM"], NS["DFM"].__dict__
            T["u1_sess"] = c.sess
            def npcbin():                                   # el perfil de la cuenta de X (la consola ve la raíz, no su vista)
                old = getattr(NS["CTX"], "sess", None)
                NS["CTX"].sess = T["u1_sess"]
                try:
                    st = NS["load_state"]()
                    PP = NS["PP"]
                    return list(PP.npcbin_get(PP.profile(st, PP.active_slot(st)), 84))
                finally:
                    NS["CTX"].sess = old
            T["u1_npcbin"] = npcbin
            res["antes"] = T["u1_antes"] = npcbin()
            res["en_curso"] = d["df"]["sess"]["end"] is not None and d["dfm"] is NS["DFM"]
        """, "u1d")
        check("U1: empieza un duelo contra la NPC 84 y la sesión lleva el DF del duelfin vivo", ok and d.get("en_curso") is True, d)
        i = FC._npc_turns(X, start, 2)
        _consola(off, "hot('duelo.duelfin', 'datos.perfiles', 'duelo.lockstep')")
        d = en(inv("""
            res["r"] = NS["_HOT_LAST"]
            c = NS["CONNS"][NS["DUEL_PORT"]]
            d = c.sess.duel
            res["viejo_sigue"] = d["df"] is T["u1_df"] and d["dfm"] is T["u1_dfm"] and NS["DFM"] is not T["u1_dfm"]
            res["viejo_intacto"] = d["df"]["finish"].__globals__ is T["u1_dfm_dict"]
        """), "u1e")
        check("U1: tras recargar los tres con el duelo en curso, la sesión conserva el DF del duelfin VIEJO y los invariantes se cumplen",
              d.get("inv") == [] and d.get("viejo_sigue") is True and d.get("viejo_intacto") is True, d)
        dlg, p0 = FC._npc_surrender(X, i)
        check("U1: el duelo de NPC en curso ACABA (0x6202 tipo 0x4d) con el duelfin de antes de recargar", p0 is not None, f"p0={p0}")
        X.room.wait(0x4800, start=na, timeout=8)
        time.sleep(0.5)
        d = en("""
            res["despues"] = T["u1_npcbin"]()
            res["cambio"] = res["despues"] != T["u1_antes"]
        """, "u1f")
        check("U1: el resultado se apunta en el perfil (NPCBIN de la NPC 84 cambia) aunque se recargara persistencia a mitad del duelo", d.get("cambio") is True, d)
        ok2, nombre2, start2 = FC._npc_start(X, 84)
        d = en("""
            c = NS["CONNS"][NS["DUEL_PORT"]]
            d = c.sess.duel
            res["nuevo"] = d["dfm"] is NS["DFM"] and d["df"] is not T["u1_df"] and d["df"]["finish"].__globals__ is NS["DFM"].__dict__
        """, "u1g")
        check("U1: el SIGUIENTE duelo de esa sesión usa el duelfin NUEVO", ok2 and d.get("nuevo") is True, d)
        i2 = FC._npc_turns(X, start2, 1)
        dlg, p0 = FC._npc_surrender(X, i2)
        check("U1: y también acaba", p0 is not None, f"p0={p0}")
        time.sleep(1.0)

        # ---------------------------------------------------------------- PvP en curso mientras se recarga pvp_match, hub, lp y traductor
        import struct
        A_, B_ = Ys[0], X                                   # A_ reta, B_ es el dueño de la sala (como en fake_client pvp)
        ok, rid = FC.pvp_open_room(B_, A_)
        off_, early, na, nb = FC.pvp_challenge(A_, B_, rid or 0)
        B_.room.send(0x4303, struct.pack(">i", 1))
        A_.room.wait(0x4306, start=na)
        B_.room.wait(0x4306, start=nb)
        sub = []
        sa, sb = FC.pvp_start_duel(sub, A_, B_, "pvp")      # los dos mandan 0x61fe: empieza, y espera la 1.ª decisión de A_
        time.sleep(1.5)
        d = en("""
            PVP = NS["PVP"]
            import pvp_match
            m = PVP.by_pid(%d)
            T["pm_mod"], T["pm"], T["PVPcls"] = pvp_match, m, type(PVP)
            res["en_curso"] = m is not None and m.ps is not None and not m.ps.over and type(m) is pvp_match.PvpMatch
        """ % B_.pid, "s5e")
        check("PvP: hay una partida en curso (PvpMatch + PvpSession esperando la decisión de A)",
              all(ok for _, ok in sub) and ok and d.get("en_curso") is True, [n for n, k in sub if not k])
        _consola(off, "hot('duelo.pvp_match', 'lobby.hub', 'lobby.lp', 'duelo.traductor')")
        d = en("""
            import pvp_match, pvp_hub
            PVP, m = NS["PVP"], T["pm"]
            res["resultado"] = NS["_HOT_LAST"]
            res["modulo_nuevo"] = pvp_match is not T["pm_mod"] and pvp_match is NS["pvp_match"]
            res["instancia"] = PVP is NS["PVP"] and type(PVP) is pvp_match.Matches and type(PVP) is not T["PVPcls"]
            res["partida_vieja"] = type(m) is T["pm_mod"].PvpMatch and type(m) is not pvp_match.PvpMatch and not m.ps.over
            res["sigue_registrada"] = PVP.by_pid(%d) is m
        """ % B_.pid, "s5f")
        check("PvP: PVP es la MISMA instancia con la clase Matches nueva; la partida en curso conserva su PvpMatch viejo",
              d.get("modulo_nuevo") is True and d.get("instancia") is True and d.get("partida_vieja") is True
              and d.get("sigue_registrada") is True, d)
        oa, ob = {}, {}
        ta = threading.Thread(target=FC.auto_loop, args=(A_.duel, sa, oa))
        tb = threading.Thread(target=FC.auto_loop, args=(B_.duel, sb, ob))
        ta.start()
        tb.start()
        ta.join(120)
        tb.join(120)
        check("PvP: la partida en curso ACABA con el código viejo (uno gana y el otro pierde, 0x4d 1/2)",
              {oa.get("res"), ob.get("res")} in ({1, 2}, {3}) and oa.get("prompts", 0) > 0 and ob.get("prompts", 0) > 0,
              f"A {oa} B {ob}")
        time.sleep(1.0)
        ids = [mm[0] for mm in A_.room.msgs[na:]]
        check("PvP: el lobby de A recibe 0x4140 y luego 0x4800 (fin de duelo con el módulo recargado)",
              0x4140 in ids and 0x4800 in ids and ids.index(0x4140) < ids.index(0x4800))
        for c in (A_, B_):
            c.duel.send(0x0003)
        # y la siguiente partida usa el PvpMatch nuevo
        ok, rid = FC.pvp_open_room(B_, A_)
        off_, early, na, nb = FC.pvp_challenge(A_, B_, rid or 0)
        B_.room.send(0x4303, struct.pack(">i", 1))
        A_.room.wait(0x4306, start=na)
        B_.room.wait(0x4306, start=nb)
        d = en("""
            import pvp_match
            m2 = NS["PVP"].by_pid(%d)
            res["nueva"] = m2 is not None and m2 is not T["pm"] and type(m2) is pvp_match.PvpMatch
        """ % B_.pid, "s5g")
        check("PvP: la siguiente reserva crea un PvpMatch del módulo NUEVO", ok and d.get("nueva") is True, d)
        A_.room.send(0x4304, b"")                           # el retador se retira (CANCELMATCH)
        time.sleep(0.5)

        # ---------------------------------------------------------------- todos los módulos, uno a uno
        nombres = [n for n, v in lista.items() if not v.get("noreload")]
        fallos = {}
        for n in nombres:
            _consola(off, f"hot({n!r})")
            h = en("res['u'] = NS['_HOT_LAST']", "s6x").get("u") or {}
            if h.get("resultado") != "ok":
                fallos[n] = h
        check(f"TODOS los módulos recargables ({len(nombres)}) se recargan uno a uno con ok", not fallos, fallos)
        d = en("""
            LP, HUB, PVP = NS["LP"], NS["HUB"], NS["PVP"]
            import pvp_hub, pvp_match, threading
            res["clases"] = (type(HUB) is pvp_hub.Hub and type(PVP) is pvp_match.Matches and pvp_hub is NS["pvp_hub"]
                             and pvp_match is NS["pvp_match"])
            res["login_panel"] = (bool(getattr(HUB.login, "_admin", False))
                                  and HUB.login.__defaults__[0].__func__.__globals__ is pvp_hub.__dict__)
            MW, PP, NR, DFM = NS["MW"], NS["PP"], NS["NR"], NS["DFM"]
            res["mundo"] = (PP.challenge_rights_due.__globals__ is MW.__dict__ and PP.on_title.__globals__ is MW.__dict__
                            and pvp_match.pvp_record.__globals__ is MW.__dict__ and LP.HANDLERS[0x4400].__globals__ is MW.__dict__)
            res["npcreglas"] = DFM.npc_duel_end.__globals__ is NR.__dict__ and DFM.PS.check_titles.__globals__ is NR.__dict__
            res["duelpass"] = (NS["PH_LOBBY"].get(0x8101) == "h_dpauth"
                               and NS["PH"]["h_dpauth"].__code__.co_filename == NS["DPC"].__file__)
            res["rendirse"] = bool(getattr(NS["OCG"].TR, "_surrender_patched", False))
            tp = NS["TP"]
            res["trade_panel"] = bool(getattr(tp._execute, "_rkb", False)) and tp._execute._orig.__globals__ is tp.__dict__
            res["reloj_torneos"] = [t.name for t in threading.enumerate() if t.name == "torneos-reloj"]
        """, "s6")
        check("tras recargarlo TODO: HUB y PVP son las instancias de siempre con la clase NUEVA y el login del panel apunta al método nuevo",
              d.get("clases") is True and d.get("login_panel") is True, d)
        check("tras recargarlo TODO: envoltorios de mundo, de npcreglas, DUELPASS y «Rendirse» sobre los módulos nuevos",
              all(d.get(k) is True for k in ("mundo", "npcreglas", "duelpass", "rendirse")), d)
        check("tras recargarlo TODO: el envoltorio de TP._execute del panel (Ranking y balance) está sobre el TP nuevo",
              d.get("trade_panel") is True, d)
        check("tras recargarlo TODO: un único reloj de torneos", len(d.get("reloj_torneos") or []) == 1, d.get("reloj_torneos"))
        n = len(X.room.msgs)
        X.room.send(0x4558, b"")
        check("tras recargarlo TODO el cliente sigue conectado y atendido",
              X.room.wait(0x4565, start=n, timeout=4) is not None and not X.room.closed and not X.errors, X.errors[:3])
        txt = log_texto()
        noreg = [ln for ln in txt.splitlines() if "NO recargado" in ln]
        check("el log solo tiene los «NO recargado» provocados a propósito (red.proto, 3 ficheros rotos, 1 setup roto)",
              len(noreg) == 5, noreg)
        avisos = [ln for ln in txt.splitlines() if "[recarga] AVISO" in ln]
        check("el log no avisa de envoltorios ajenos perdidos", not avisos, avisos[:3])
        trazas = [ln for ln in txt.splitlines() if ("[EXC]" in ln or "[hub] 0x" in ln or "error en 0x" in ln)]
        check("el log no tiene [EXC] ni errores de manejadores", not trazas, trazas[:4])
        # los clientes de arriba se van antes de los de fake_client (cuenta en uso)
        for c in [X] + Ys:
            for link in (getattr(c, "duel", None),):
                try:
                    link and link.close()
                except Exception:
                    pass
            try:
                c.leave_room()
            except Exception:
                pass
        time.sleep(1.0)
        # ---------------------------------------------------------------- y todo lo de siempre sigue funcionando
        print("-- fake_client pvp contra la instancia con TODO recargado")
        r_pvp = FC.run_pvp(off)
        bad = [n for n, ok in r_pvp if not ok]
        check(f"fake_client pvp tras recargar todo: {len(r_pvp) - len(bad)}/{len(r_pvp)}", not bad and len(r_pvp) >= 30, bad[:5])
        print("-- fake_client npc contra la instancia con TODO recargado")
        r_npc = FC.run_npc(off)
        bad = [n for n, ok in r_npc if not ok]
        check(f"fake_client npc tras recargar todo: {len(r_npc) - len(bad)}/{len(r_npc)}", not bad and len(r_npc) >= 4, bad[:5])
    mal = [n for n, ok in res if not ok]
    print(f"== RESULTADO hot en vivo: {len(res) - len(mal)}/{len(res)} bien" + (f"; mal: {mal}" if mal else ""))
    return 1 if mal else 0


if __name__ == "__main__":
    if "--vivo" in sys.argv:
        sys.exit(vivo(sys.argv[1:]))
    unittest.main()
