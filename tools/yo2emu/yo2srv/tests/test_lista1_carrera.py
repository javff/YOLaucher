"""LIST1 (0x4211-0x4213) y avisos 0x3112 a la vez (research/lobby/NOTES.md §9c, 24-10).

Bug de los jugadores («Sometimes required to exit the current zone in order to duel a player»): la LIST1 se arma antes de
mandarla y cada mensaje espera su pausa, mientras los avisos de los demás (0x3112, por su cola) siguen saliendo. El cliente
vacía su lista con 0x4211 y escribe lo que trae 0x4212: un aviso «B ya está libre» que llega ANTES de una LIST1 hecha
cuando B seguía «en duelo» se pierde, y el estado viejo gana hasta que alguien salga y vuelva a entrar. Aquí se fuerza esa
carrera con conexiones falsas (el aviso sale justo antes del 0x4211) y se mira lo que acaba viendo el cliente.
Sin puertos ni instancia."""
import os
import struct
import sys
import types
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
import pvp_hub  # noqa: E402
from yo2srv.mundo import bloques  # noqa: E402

KEY = (5744, 0)


class Conn:
    """Conexión falsa: apunta en orden todo lo que le llega, por send (hilo del que habla) o por post (cola)."""

    def __init__(self, pid, key=KEY):
        self.port, self.lang, self.pid, self.key = key[0], 5, pid, key
        self.log, self.closed, self.sess = [], False, None
        self.antes = {}                       # id de mensaje -> función que se ejecuta justo ANTES de mandarlo

    def _out(self, mid, data):
        f = self.antes.pop(mid, None)
        if f:
            f()
        self.log.append((mid, bytes(data)))

    def send(self, mid, data=b"", pause=0.0):
        self._out(mid, data)

    def post(self, mid, data=b"", pause=0.0):
        self._out(mid, data)

    def stale(self):
        return False


def entrada(pid, state=0):
    return struct.pack(">I", pid) + bytes(30) + bytes([state, 0])       # 36 B: el estado va en el byte 34


class Mundo:
    """Un Hub de verdad con tres sesiones y un 'srv' mínimo para bloques.send_block_list."""

    def __init__(self):
        env = {"block_player": lambda pid, name=None, sess=None: entrada(pid), "log": lambda *a: None}
        self.hub = pvp_hub.Hub(env)
        self.conn, self.s = {}, {}
        for pid in (1000, 1004, 1008):
            self.add(pid)
        srv = types.SimpleNamespace(HUB=self.hub, block_player=env["block_player"], my_pid=lambda: 1000,
                                    block_key=lambda c: c.key)
        self.srv = srv

    def add(self, pid, entra=True):
        c = Conn(pid)
        s = pvp_hub.Session(f"acct{pid}", pid, False)
        s.pid, s.name, s.lobby = pid, f"J{pid}", c
        c.sess = s
        self.hub.sessions[s.acct] = s
        self.conn[pid], self.s[pid] = c, s
        if entra:
            self.hub.enter(s, KEY, 0)
        return s

    def lista1(self, pid):
        """send_block_list de pid con el 'srv' de la prueba."""
        srv = self.srv
        srv.block_others = lambda c: self.hub.others(c.sess) if (c.sess and c.sess.block == KEY and c.sess.lobby is c) else []
        bloques.srv = srv
        bloques.send_block_list(self.conn[pid])


def cliente(log):
    """Lo que el cliente guarda de los demás, aplicando el registro en orden (FUN_006b88d0, FUN_006b8b70, FUN_006b9480):
    0x4211 vacía; 0x4212 escribe el estado (byte 34) de cada entrada; 0x3112 op 0 quita, op 1 crea con el estado de LIST2,
    op 0x67..0x6a cambia el estado."""
    p = {}
    for mid, d in log:
        if mid == 0x4211:
            p.clear()
        elif mid == 0x4212:
            for i in range(0, len(d) - 35, 36):
                p[struct.unpack(">I", d[i:i + 4])[0]] = d[i + 34]
        elif mid == 0x3112:
            pid, op = struct.unpack(">IB", d[:5])
            if op == 0:
                p.pop(pid, None)
            elif 0x67 <= op <= 0x6a:
                p[pid] = op
            elif op == 1:
                p[pid] = d[6 + 36]                        # LIST2: u8 estado tras los 36 B de LIST1
    return p


class TestCarrera(unittest.TestCase):
    def setUp(self):
        self.m = Mundo()
        self.old = getattr(bloques, "srv", None)

    def tearDown(self):
        bloques.srv = self.old

    def test_sin_carrera_no_manda_correcciones(self):
        m = self.m
        m.hub.set_state(m.s[1004], 100)
        m.conn[1000].log.clear()
        m.lista1(1000)
        self.assertEqual([x[0] for x in m.conn[1000].log], [0x4211, 0x4212, 0x4213])
        self.assertEqual(cliente(m.conn[1000].log), {1000: 0, 1004: 100, 1008: 0})

    def test_libre_antes_de_la_lista_vieja(self):
        """B estaba en duelo al armar la lista y pasa a 100 (reabre la sala) justo antes del 0x4211."""
        m = self.m
        m.hub.set_state(m.s[1004], 0x67, "J1000")
        m.conn[1000].log.clear()
        m.conn[1000].antes[0x4211] = lambda: m.hub.set_state(m.s[1004], 100)          # su aviso sale por la cola, antes
        m.lista1(1000)
        self.assertEqual(cliente(m.conn[1000].log).get(1004), 100,
                         f"la LIST1 vieja pisa el aviso: {[hex(x[0]) for x in m.conn[1000].log]}")

    def test_pasa_a_duelo_durante_la_lista(self):
        m = self.m
        m.hub.set_state(m.s[1004], 100)
        m.conn[1000].log.clear()
        m.conn[1000].antes[0x4212] = lambda: m.hub.set_state(m.s[1004], 0x67, "J1008")
        m.lista1(1000)
        self.assertEqual(cliente(m.conn[1000].log).get(1004), 0x67)

    def test_llega_otro_antes_del_0x4211(self):
        m = self.m
        c = m.add(1012, entra=False)
        m.conn[1000].log.clear()
        m.conn[1000].antes[0x4211] = lambda: m.hub.enter(m.s[1012], KEY, 0)          # su 0x3112 op 1 se pierde en el 0x4211
        m.lista1(1000)
        self.assertIn(1012, cliente(m.conn[1000].log))

    def test_se_va_durante_la_lista(self):
        m = self.m
        m.conn[1000].log.clear()
        m.conn[1000].antes[0x4211] = lambda: m.hub.leave(m.s[1008], "prueba")         # su op 0 llega antes del 0x4211
        m.lista1(1000)
        self.assertNotIn(1008, cliente(m.conn[1000].log))

    def test_el_cambio_de_area_tambien(self):
        m = self.m
        m.conn[1000].log.clear()

        def mueve():
            m.s[1004].ablock = 2
        m.conn[1000].antes[0x4212] = mueve
        m.lista1(1000)
        extra = [x for x in m.conn[1000].log if x[0] == 0x3112]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0][1][5], 2)                                           # byte 5 del 0x3112 = bloque de área

    def test_conexion_que_no_es_la_de_lobby_no_recibe_correcciones(self):
        m = self.m
        otra = Conn(1000)
        otra.sess = m.s[1000]                                                          # no es s.lobby
        m.conn[1000].log.clear()
        otra.antes[0x4211] = lambda: m.hub.set_state(m.s[1004], 100)
        bloques.srv = m.srv
        m.srv.block_others = lambda c: self.m.hub.others(c.sess) if (c.sess and c.sess.block == KEY and c.sess.lobby is c) else []
        bloques.send_block_list(otra)
        self.assertEqual([x[0] for x in otra.log], [0x4211, 0x4212, 0x4213])


if __name__ == "__main__":
    unittest.main()
