"""yo2srv/duelo (paso E6): puerto de duelo (duel_reply con y sin lockstep, cola por conexión) y estado de duelo por sesión
(duel_state/df_for/duel_rival/duel_begin, abandono, on_conn_closed/on_conn_accepted).

Sin instancia ni puertos: un Server con un namespace falso (srv.ns), que es lo que los módulos ven en run.py. El duelo de
verdad lo prueban T2 (npc/pvp/kill/desconexion), T7.units_desconexion, T8.ocg_test y T8.pvp_test.
"""
import os
import queue
import sys
import threading
import time
import types
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import duelo  # noqa: E402
from yo2srv.duelo import puerto, sesion_duelo  # noqa: E402
from yo2srv.server import Server  # noqa: E402

SIN = object()
_DE_SESION = ("_npc_name", "_admin_npc", "duel_state", "df_for", "df_end_mark", "duel_rival", "duel_begin", "duel_ip_for",
              "on_conn_closed", "_abandon_duel", "on_conn_accepted")
_DE_PUERTO = ("cur_world", "duel_parts", "preparation_result", "duel_reply_base", "duel_reply", "duel_task",
              "_duel_worker", "ocg_session", "npc_deck_id", "ocg_finish")


class Conn:
    def __init__(self, port=5760, sess=None):
        self.port, self.sess, self.sent, self.closed = port, sess, [], False

    def send(self, msg_id, data=b"", **kw):
        self.sent.append((msg_id, bytes(data)))


def espacio(**extra):
    log = []
    ns = {"LOG": log, "log": lambda *a: log.append(" ".join(str(x) for x in a)), "DUEL_PORT": 5760, "PUBLIC_IP": "1.2.3.4",
          "CTX": threading.local(), "SIN_SESION": SIN, "cur_sess": lambda: None, "DF": None, "PH": None, "NPCS": [],
          "OCG": None, "DUEL_MODE": "ocg", "OCG_CLIENT_MSGS": (0x6350,), "LP": None, "current_conn": lambda c: c,
          "HUB": types.SimpleNamespace(on_close=lambda c, why: None), "_admin_ev": lambda *a, **k: None,
          "CLOSE_OLD_DUEL_SOCK": False, "AV_STATE": {"world": 1}}
    ns.update(extra)
    return ns


def enlazar(ns):
    """Lo que hace run.py: setup(SRV) y dejar en el namespace los nombres que los módulos se piden entre sí."""
    s = Server(ns)
    duelo.setup(s)
    for m, nombres in ((sesion_duelo, _DE_SESION), (puerto, _DE_PUERTO)):
        for n in nombres:
            ns[n] = getattr(m, n)
    return s


class Puerto(unittest.TestCase):
    def test_sin_ls_contesta_la_base(self):
        # run.py no define LS si lockstep no carga: duel_reply = la base sola (latido -> 0x0004)
        s = enlazar(espacio())
        c = Conn()
        s.duel_reply(c, 0x0005, 0, b"")
        self.assertEqual(c.sent, [(0x0004, b"\0\0\0\0")])
        s.duel_reply(c, 0x6104, 0, b"")
        self.assertEqual(c.sent[-1], (0x6104, b""))

    def test_con_ls_va_a_duel_reply_v2(self):
        llamadas = []
        ls = types.SimpleNamespace(duel_reply_v2=lambda c, m, p, d: llamadas.append((m, d)) or "v2")
        s = enlazar(espacio(LS=ls))
        c = Conn()
        self.assertEqual(s.duel_reply(c, 0x6666, 0, b"x"), "v2")        # nada que ver con el espectador ni PvP
        self.assertEqual(llamadas, [(0x6666, b"x")])
        # el espectador de NPC (NW) se queda con el mensaje y no sigue
        s.NW = types.SimpleNamespace(on_duel_msg=lambda c, m, d: True)
        self.assertIsNone(s.duel_reply(c, 0x6666, 0, b"y"))
        self.assertEqual(len(llamadas), 1)

    def test_pvp_se_lleva_la_conexion(self):
        ls = types.SimpleNamespace(duel_reply_v2=lambda *a: self.fail("el PvP no pasa por lockstep"))
        m = types.SimpleNamespace(on=lambda c, mid, p, d: ("pvp", mid))
        s = enlazar(espacio(LS=ls, PVP=types.SimpleNamespace(route=lambda c, mid: m)))
        self.assertEqual(s.duel_reply(Conn(), 0x6101, 0, b""), ("pvp", 0x6101))

    def test_0x61fe_repetido_se_ignora(self):
        # sin OCG (modo esqueleto) y sin DF: el primer 0x61fe sigue a lockstep, el segundo se ignora
        vistos = []
        ls = types.SimpleNamespace(duel_reply_v2=lambda c, m, p, d: vistos.append(m))
        s = enlazar(espacio(LS=ls))
        c = Conn()
        s.duel_reply(c, 0x61fe, 0, b"\1")
        s.duel_reply(c, 0x61fe, 0, b"\1")
        self.assertEqual(vistos, [0x61fe])
        self.assertTrue(any("0x61fe repetido" in x for x in s.ns["LOG"]))

    def test_cola_por_conexion_en_orden_y_se_para(self):
        s = enlazar(espacio())
        ses = types.SimpleNamespace(duel=None)
        c, orden = Conn(sess=ses), []
        s.duel_task(c, lambda: orden.append(("sess", s.CTX.sess)))      # CTX es del hilo: se mira desde el trabajador
        for i in range(5):
            s.duel_task(c, orden.append, i)
        s.duel_task(c, lambda: 1 / 0)                      # una excepción va al log y no mata la cola
        s.duel_task(c, orden.append, 99)
        fin = time.time() + 3
        while 99 not in orden and time.time() < fin:
            time.sleep(0.01)
        self.assertEqual(orden, [("sess", ses), 0, 1, 2, 3, 4, 99])      # el perfil de la cuenta de c, nunca el dueño
        self.assertTrue(any("[EXC] duelo ? en <lambda>" in x for x in s.ns["LOG"]))
        s.on_conn_closed(c)                                # y la cola se para
        self.assertTrue(c.closed)

    def test_ocg_session_sin_ocg_o_sin_sesion(self):
        c = Conn()
        s = enlazar(espacio())
        self.assertIsNone(s.ocg_session(c))                              # OCG None
        s2 = enlazar(espacio(OCG=types.SimpleNamespace(), cur_sess=lambda: SIN))
        self.assertIsNone(s2.ocg_session(c))                             # sin sesión no hay duelo
        s3 = enlazar(espacio(OCG=types.SimpleNamespace(), DUEL_MODE="skeleton"))
        self.assertIsNone(s3.ocg_session(c))

    def test_cur_world(self):
        s = enlazar(espacio())
        self.assertEqual(s.cur_world(), 1)
        s.ns["cur_sess"] = lambda: types.SimpleNamespace(world=0)
        self.assertEqual(s.cur_world(), 0)


class Sesion(unittest.TestCase):
    def tearDown(self):
        sesion_duelo.DUEL["rival"] = 0
        sesion_duelo.LOBBY_ROOM["c"] = None

    def test_duel_y_lobby_room_son_el_mismo_dict_que_el_namespace(self):
        d0, l0 = sesion_duelo.DUEL, sesion_duelo.LOBBY_ROOM
        d, lr = {"rival": 7}, {"c": "x"}
        try:
            enlazar(espacio(DUEL=d, LOBBY_ROOM=lr))
            self.assertIs(sesion_duelo.DUEL, d)
            self.assertIs(sesion_duelo.LOBBY_ROOM, lr)
        finally:
            sesion_duelo.DUEL, sesion_duelo.LOBBY_ROOM = d0, l0

    def test_estado_por_sesion_y_global_sin_sesion(self):
        dfm = types.SimpleNamespace(make_handlers=lambda *a, **k: {"sess": {"end": 1}, "h_autoduelstart": lambda r, m: None})
        s = enlazar(espacio(DFM=dfm, DF={"global": 1}, load_state=lambda: {}, save_state=lambda st: None,
                            live_conn=lambda c: c))
        self.assertIsNone(s.duel_state())                          # sin sesión: lo global
        self.assertIsNone(s.duel_state(SIN))
        ses = types.SimpleNamespace(duel=None)
        d = s.duel_state(ses)
        self.assertIs(ses.duel, d)
        self.assertIsNotNone(d["df"])
        c = Conn(sess=ses)
        self.assertEqual(s.df_for(c), {"global": 1})               # d["lobby"] sin apuntar: el global
        s.duel_begin(c, 84, 1)
        self.assertEqual((d["rival"], d["lobby"]), (84, c))
        self.assertIs(s.df_for(c), d["df"])
        self.assertEqual(s.duel_rival(ses), 84)
        self.assertEqual(sesion_duelo.DUEL["rival"], 84)
        self.assertIs(sesion_duelo.LOBBY_ROOM["c"], c)

    def test_df_end_mark_no_con_0x0005(self):
        c = Conn()
        sesion_duelo.df_end_mark(c, {"sess": {"end": "e"}}, 0x0005)
        self.assertFalse(hasattr(c, "df_end"))
        sesion_duelo.df_end_mark(c, {"sess": {"end": "e"}}, 0x6001)
        self.assertEqual(c.df_end, "e")
        sesion_duelo.df_end_mark(c, {"sess": {"end": "otra"}}, 0x6101)      # solo la primera vez
        self.assertEqual(c.df_end, "e")

    def test_duel_ip_for(self):
        s = enlazar(espacio())
        c = Conn(); c.sock = types.SimpleNamespace(getsockname=lambda: ("10.0.0.5", 5760))
        old = os.environ.pop("YO2_PUBLIC_IP", None)
        try:
            self.assertEqual(s.duel_ip_for(c), "10.0.0.5")
            c.sock = types.SimpleNamespace(getsockname=lambda: ("0.0.0.0", 5760))
            self.assertEqual(s.duel_ip_for(c), "1.2.3.4")
            os.environ["YO2_PUBLIC_IP"] = "9.9.9.9"
            c.sock = types.SimpleNamespace(getsockname=lambda: ("10.0.0.5", 5760))
            self.assertEqual(s.duel_ip_for(c), "1.2.3.4")                  # PUBLIC_IP de run.py (ya resuelto)
        finally:
            os.environ.pop("YO2_PUBLIC_IP", None)
            if old is not None:
                os.environ["YO2_PUBLIC_IP"] = old

    def test_on_conn_accepted_abandona_el_duelo_viejo(self):
        cerrado = []
        core = types.SimpleNamespace(end=lambda i: cerrado.append(i))
        s = enlazar(espacio(OCG=types.SimpleNamespace(core=lambda: core)))
        viejo = Conn(); viejo.ocg = types.SimpleNamespace(over=False, id=42); viejo.duel_q = queue.Queue()
        nuevo = Conn()
        s.on_conn_accepted(nuevo, viejo)
        fin = time.time() + 3
        while not cerrado and time.time() < fin:
            time.sleep(0.01)
        self.assertTrue(viejo.ocg.over)
        self.assertEqual(cerrado, [42])
        self.assertIsNone(viejo.duel_q.get_nowait())               # y su hilo trabajador se para
        self.assertFalse(viejo.closed)                             # CLOSE_OLD_DUEL_SOCK apagado: el socket no se toca
        s.on_conn_accepted(nuevo, nuevo)                           # sin cambios
        s.on_conn_accepted(nuevo, None)

    def test_on_conn_closed_de_un_socket_viejo_o_acabado_no_cuenta_derrota(self):
        df = {"sess": {"end": "e"}, "h_disconnect": lambda c: self.fail("no tenía que contar la derrota")}
        s = enlazar(espacio(DF=df))
        c = Conn(); c.df_end = "e"
        c.ocg = types.SimpleNamespace(over=True, id=1)
        s.on_conn_closed(c)                                         # el duelo ya acabó con su resultado
        s2 = enlazar(espacio(DF=df, current_conn=lambda c: object()))
        c2 = Conn(); c2.df_end = "e"
        s2.on_conn_closed(c2)                                       # un socket viejo: ya hay otro
        self.assertTrue(c.closed and c2.closed)

    def test_on_conn_closed_corte_a_mitad_cuenta_derrota(self):
        cuenta, ev = [], []
        ses = types.SimpleNamespace(duel=None)
        s = enlazar(espacio(_admin_ev=lambda *a, **k: ev.append(a), NPCS=[(84, "Jaden", 0, 0)]))
        s.ns["DF"] = {"sess": {"end": "e"}, "h_disconnect": lambda c: cuenta.append((c, s.CTX.sess))}
        c = Conn(sess=ses); c.df_end = "e"
        c.ocg = types.SimpleNamespace(over=False, id=7)
        s.CTX.sess = None
        s.on_conn_closed(c)
        self.assertEqual(cuenta, [(c, ses)])                        # la derrota va al perfil de c
        self.assertIsNone(s.CTX.sess)                               # y se restaura
        self.assertTrue(c.ocg.over)
        self.assertTrue(any(a[0] == "npc_end" and a[3:] == (1, "disconnect") for a in ev))


if __name__ == "__main__":
    unittest.main()
