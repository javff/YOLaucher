"""router.py: despacho (convención de LP.handle), envoltorios, observadores, fallback, dueños, B60 y pausa."""
import os
import sys
import threading
import time
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import router as R  # noqa: E402


class Conn:
    """Conexión falsa: anota lo que se manda."""

    def __init__(self):
        self.enviado = []

    def send(self, mid, data=b""):
        self.enviado.append((mid, data))


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.lineas = []
        self.r = R.Router(log=self.lineas.append)
        self.c = Conn()

    def d(self, tipo="lobby", mid=0x4400, param=0, data=b""):
        return self.r.dispatch(tipo, self.c, mid, param, data)

    def test_sin_nada_no_contesta(self):
        self.assertFalse(self.d())

    def test_manejador_basico_y_false_es_no_mio(self):
        self.r.on("lobby", 0x4400, lambda c, i, p, d: c.send(0x4401), dueño="lp")
        self.r.on("lobby", 0x4500, lambda c, i, p, d: False, dueño="lp")
        self.assertTrue(self.d(mid=0x4400))
        self.assertEqual(self.c.enviado, [(0x4401, b"")])
        self.assertFalse(self.d(mid=0x4500))                # False = no es mío
        self.assertFalse(self.d(tipo="duelo", mid=0x4400))  # otro tipo de puerto

    def test_valida_tipo_y_callable(self):
        with self.assertRaises(ValueError):
            self.r.on("nada", 1, lambda *a: None)
        with self.assertRaises(TypeError):
            self.r.on("lobby", 1, 5)

    def test_ultimo_registrado_manda_y_al_quitarlo_vuelve_el_anterior(self):
        # hoy: LP.HANDLERS.update(TP.HANDLERS) pisa el 0x7000 de LP
        self.r.on("lobby", 0x7000, lambda c, *a: c.send(1), dueño="lobby.lp")
        self.r.on("lobby", 0x7000, lambda c, *a: c.send(2), dueño="intercambio")
        self.d(mid=0x7000)
        self.assertEqual(self.c.enviado[-1][0], 2)
        self.assertEqual(self.r.tabla()[("lobby", 0x7000)], ["lobby.lp", "intercambio"])
        snap = self.r.quitar("intercambio")
        self.d(mid=0x7000)
        self.assertEqual(self.c.enviado[-1][0], 1)
        self.r.restaurar(snap)
        self.d(mid=0x7000)
        self.assertEqual(self.c.enviado[-1][0], 2)

    def test_registro_idempotente_por_dueno(self):
        for _ in range(3):
            self.r.on("lobby", 5, lambda c, *a: None, dueño="m")
            self.r.wrap("lobby", 5, lambda sig, *a: sig(*a), dueño="m")
        self.assertEqual(self.r.tabla()[("lobby", 5)], ["m"])
        self.assertEqual(len(self.r._wrap[("lobby", 5)]), 1)

    def test_envoltorios_el_ultimo_es_el_mas_externo(self):
        orden = []
        self.r.on("lobby", 0x4400, lambda c, i, p, d: orden.append("base"), dueño="lp")
        self.r.wrap("lobby", 0x4400, lambda sig, c, i, p, d: (orden.append("A"), sig(c, i, p, d), orden.append("a")),
                    dueño="a")
        self.r.wrap("lobby", 0x4400, lambda sig, c, i, p, d: (orden.append("B"), sig(c, i, p, d), orden.append("b")),
                    dueño="b")
        self.assertTrue(self.d())
        self.assertEqual(orden, ["B", "A", "base", "a", "b"])

    def test_envoltorio_puede_cortar_o_dejar_pasar(self):
        self.r.on("lobby", 7, lambda c, *a: c.send(1), dueño="lp")
        self.r.wrap("lobby", 7, lambda sig, c, i, p, d: True if d == b"gm" else sig(c, i, p, d), dueño="gm")
        self.assertTrue(self.d(mid=7, data=b"gm"))
        self.assertEqual(self.c.enviado, [])
        self.assertTrue(self.d(mid=7, data=b"otro"))
        self.assertEqual(self.c.enviado, [(1, b"")])

    def test_quitar_dueno_de_un_envoltorio_solo_quita_lo_suyo(self):
        self.r.on("lobby", 7, lambda c, *a: c.send(1), dueño="lp")
        self.r.wrap("lobby", 7, lambda sig, c, i, p, d: True, dueño="mw")
        self.assertTrue(self.d(mid=7))
        self.assertEqual(self.c.enviado, [])
        self.r.quitar("mw")
        self.assertTrue(self.d(mid=7))
        self.assertEqual(self.c.enviado, [(1, b"")])
        self.assertEqual(self.r.duenos(), ["lp"])

    def test_observadores_antes_y_con_filtro_de_ids(self):
        visto = []
        self.r.pre("lobby", lambda c, i, p, d: visto.append(("todos", i)), dueño="hub")
        self.r.pre("lobby", lambda c, i, p, d: visto.append(("solo4100", i)), dueño="hub2", ids=[0x4100])
        self.r.on("lobby", 0x4100, lambda c, *a: visto.append("manejador"), dueño="lp")
        self.d(mid=0x4100)
        self.d(mid=0x4200)
        self.assertEqual(visto, [("todos", 0x4100), ("solo4100", 0x4100), "manejador", ("todos", 0x4200)])

    def test_observador_que_falla_no_para_nada(self):
        def malo(*a):
            raise RuntimeError("obs")

        self.r.pre("lobby", malo, dueño="m")
        self.r.on("lobby", 1, lambda c, *a: c.send(9), dueño="lp")
        self.assertTrue(self.d(mid=1))
        self.assertEqual(self.r.errores, 1)
        self.assertTrue(any("obs" in x for x in self.lineas))

    def test_fallback_si_nadie_contesta(self):
        self.r.fallback("lobby", lambda c, i, p, d: c.send(0xfa), dueño="auto")
        self.r.on("lobby", 1, lambda c, *a: False, dueño="lp")
        self.assertTrue(self.d(mid=1))                    # el manejador dice «no mío» -> fallback
        self.assertTrue(self.d(mid=2))                    # sin manejador -> fallback
        self.assertEqual(self.c.enviado, [(0xfa, b""), (0xfa, b"")])

    def test_B60_error_tras_mandar_cuenta_como_contestado(self):
        def a_medias(c, *a):
            c.send(1)
            raise RuntimeError("a medias")

        self.r.on("lobby", 1, a_medias, dueño="lp")
        self.r.fallback("lobby", lambda c, *a: c.send(0xfa), dueño="auto")
        self.assertTrue(self.d(mid=1))
        self.assertEqual(self.c.enviado, [(1, b"")])      # el fallback NO contesta otra vez
        self.assertTrue(any("tras mandar 1" in x for x in self.lineas))

    def test_B60_error_sin_mandar_deja_seguir_al_fallback(self):
        def roto(c, *a):
            raise RuntimeError("roto")

        self.r.on("lobby", 1, roto, dueño="lp")
        self.r.fallback("lobby", lambda c, *a: c.send(0xfa), dueño="auto")
        self.assertTrue(self.d(mid=1))
        self.assertEqual(self.c.enviado, [(0xfa, b"")])
        r2 = R.Router()
        r2.on("lobby", 1, roto)
        self.assertFalse(r2.dispatch("lobby", Conn(), 1, 0, b""))   # sin fallback: no contestado

    def test_send_de_la_conexion_se_restaura(self):
        self.r.on("lobby", 1, lambda c, *a: c.send(1), dueño="lp")
        self.d(mid=1)
        self.assertNotIn("send", self.c.__dict__)         # no se queda envuelto
        self.c.send = self.c.send                         # un send propio de la instancia se respeta
        propio = self.c.send
        self.d(mid=1)
        self.assertIs(self.c.__dict__["send"], propio)

    def test_manejador_ids_y_tabla(self):
        h = lambda *a: None  # noqa: E731
        self.r.on("duelo", 0x6001, h, dueño="d")
        self.r.on("duelo", 0x6002, h, dueño="d")
        self.assertIs(self.r.manejador("duelo", 0x6001), h)
        self.assertIsNone(self.r.manejador("duelo", 0x9999))
        self.assertEqual(self.r.ids("duelo"), [0x6001, 0x6002])

    def test_orden_documentado_y_puertos(self):
        self.assertEqual(set(R.ORDEN), set(R.TIPOS))
        self.assertEqual(R.TIPO_POR_PUERTO[5732], "lobby")
        self.assertEqual(R.TIPO_POR_PUERTO[5745], "lobby")
        self.assertEqual(R.TIPO_POR_PUERTO[5760], "duelo")

    def test_pausa_espera_a_los_despachos_y_bloquea_los_nuevos(self):
        dentro, soltar, fuera = threading.Event(), threading.Event(), []

        def lento(c, *a):
            dentro.set()
            soltar.wait(5)
            fuera.append("lento")

        self.r.on("lobby", 1, lento, dueño="lp")
        t = threading.Thread(target=lambda: self.d(mid=1))
        t.start()
        self.assertTrue(dentro.wait(5))
        with self.assertRaises(R.PausaVencida):           # hay un despacho en curso: no se puede pausar
            with self.r.pausa(0.2):
                pass
        soltar.set()
        t.join(5)
        with self.r.pausa(2):
            otro = threading.Thread(target=lambda: fuera.append(self.d(mid=1)))
            otro.start()
            time.sleep(0.3)
            self.assertEqual(fuera, ["lento"], "con la pausa tomada no se despacha")
        otro.join(5)
        self.assertEqual(len(fuera), 3)

    def test_pausa_no_deja_morir_de_hambre_a_los_escritores(self):
        self.r.on("lobby", 1, lambda c, *a: time.sleep(0.01), dueño="lp")
        parar = threading.Event()

        def bucle():
            while not parar.is_set():
                self.d(mid=1)

        hs = [threading.Thread(target=bucle) for _ in range(3)]
        [h.start() for h in hs]
        try:
            with self.r.pausa(5):
                pass
        finally:
            parar.set()
            [h.join(5) for h in hs]


if __name__ == "__main__":
    unittest.main()
