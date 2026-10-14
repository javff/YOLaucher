"""bus.py: emit nunca lanza, ask devuelve el primer valor que no es None, los dueños se quitan y se restauran."""
import os
import sys
import threading
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv.bus import Bus  # noqa: E402


class TestBus(unittest.TestCase):
    def setUp(self):
        self.lineas = []
        self.bus = Bus(log=self.lineas.append)

    def test_sin_suscriptores_no_pasa_nada(self):
        self.assertIsNone(self.bus.emit("on_select", 1))
        self.assertIsNone(self.bus.ask("login_refused", "x"))
        self.assertEqual(self.bus.eventos(), [])

    def test_emit_en_orden_y_con_argumentos(self):
        vistos = []
        self.bus.subscribe("e", lambda *a, **k: vistos.append(("a", a, k)))
        self.bus.subscribe("e", lambda *a, **k: vistos.append(("b", a, k)))
        self.assertIsNone(self.bus.emit("e", 1, 2, x=3))
        self.assertEqual(vistos, [("a", (1, 2), {"x": 3}), ("b", (1, 2), {"x": 3})])

    def test_emit_no_lanza_y_sigue_con_el_siguiente(self):
        vistos = []

        def malo(*a):
            raise RuntimeError("boom")

        self.bus.subscribe("e", malo, dueño="m")
        self.bus.subscribe("e", lambda *a: vistos.append(a))
        self.bus.emit("e", 7)
        self.assertEqual(vistos, [(7,)])
        self.assertEqual(self.bus.fallos, 1)
        self.assertTrue(any("boom" in x and "'m'" in x for x in self.lineas), self.lineas)

    def test_ask_primer_valor_no_none_y_salta_los_que_fallan(self):
        def malo(*a):
            raise ValueError("x")

        self.bus.subscribe("q", lambda *a: None)
        self.bus.subscribe("q", malo)
        self.bus.subscribe("q", lambda *a: 0)          # 0 y False cuentan como respuesta (no son None)
        self.bus.subscribe("q", lambda *a: "tarde")
        self.assertEqual(self.bus.ask("q"), 0)
        self.assertIsNone(self.bus.ask("otra"))

    def test_registro_idempotente(self):
        f = lambda: None  # noqa: E731
        self.bus.subscribe("e", f, dueño="m")
        self.bus.subscribe("e", f, dueño="m")
        self.assertEqual(self.bus.suscriptores("e"), [f])
        with self.assertRaises(TypeError):
            self.bus.subscribe("e", "no invocable")

    def test_unsubscribe(self):
        f, g = (lambda: 1), (lambda: 2)
        self.bus.subscribe("e", f)
        self.bus.subscribe("e", g)
        self.assertEqual(self.bus.unsubscribe("e", f), 1)
        self.assertEqual(self.bus.suscriptores("e"), [g])

    def test_quitar_y_restaurar_por_dueno_en_su_posicion(self):
        a, b, c = (lambda: "a"), (lambda: "b"), (lambda: "c")
        self.bus.subscribe("e", a, dueño="uno")
        self.bus.subscribe("e", b, dueño="dos")
        self.bus.subscribe("e", c, dueño="uno")
        self.bus.subscribe("f", a, dueño="uno")
        snap = self.bus.quitar("uno")
        self.assertEqual(self.bus.suscriptores("e"), [b])
        self.assertEqual(self.bus.suscriptores("f"), [])
        self.assertEqual(self.bus.duenos(), ["dos"])
        self.bus.restaurar(snap)
        self.assertEqual(self.bus.suscriptores("e"), [a, b, c])
        self.assertEqual(self.bus.suscriptores("f"), [a])
        self.bus.restaurar(snap)                      # idempotente: no duplica
        self.assertEqual(self.bus.suscriptores("e"), [a, b, c])

    def test_un_suscriptor_puede_suscribirse_o_quitarse_mientras_se_emite(self):
        vistos = []

        def tonto(*a):
            self.bus.subscribe("e", lambda *x: vistos.append("nuevo"))
            self.bus.quitar("zz")
            vistos.append("tonto")

        self.bus.subscribe("e", tonto)
        self.bus.emit("e")
        self.assertEqual(vistos, ["tonto"])            # el nuevo entra a partir del siguiente emit
        self.bus.emit("e")
        self.assertIn("nuevo", vistos)

    def test_hilos(self):
        cuenta = [0]
        cerrojo = threading.Lock()

        def f(*a):
            with cerrojo:
                cuenta[0] += 1

        self.bus.subscribe("e", f)
        hs = [threading.Thread(target=lambda: [self.bus.emit("e") for _ in range(200)]) for _ in range(4)]
        [h.start() for h in hs]
        [h.join() for h in hs]
        self.assertEqual(cuenta[0], 800)


if __name__ == "__main__":
    unittest.main()
