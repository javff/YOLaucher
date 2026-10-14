"""Puntuación y LV tras un PvP (rx/XP): tabla puntuación -> LV, puntos por partida/match (cifras de la guía de Duel
Evolution: https://duelevolution.wordpress.com/level-list/), 36 duelos para salir del LV 1, LV puesto por el administrador
como suelo, XP_ON apagable y el enganche de pvp_match (LV de los dos leído ANTES de tocar ningún perfil, rendición y LP final
en apply_duel_stats, NPC sin tocar). Sin puertos ni instancia."""
import os
import sys
import threading
import types
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
import pvp_match  # noqa: E402
from yo2srv import compat  # noqa: E402

PS = compat.cargar("persistencia")


def perfil(lv=1, score=0, duels=0, **rec):
    return {"rec": dict({"lv": lv, "score": score, "duels": duels}, **rec)}


class TestTabla(unittest.TestCase):
    def test_umbrales_de_la_guia(self):
        for score, lv in ((0, 2), (49, 2), (50, 3), (149, 3), (150, 4), (1699, 8), (1700, 9), (2400, 10), (40000, 20),
                          (469999, 27), (470000, 28)):
            self.assertEqual(PS.xp_level_for_score(score), lv, score)
        self.assertEqual(len(PS.XP_LEVEL_FLOORS), 29)                # LV 2..30

    def test_ejemplos_de_la_guia(self):
        d = PS.xp_game_delta
        self.assertEqual(d(17, 17, "win"), 40)
        self.assertEqual(d(17, 18, "win"), 47)                        # duelo suelto contra uno por encima
        self.assertEqual(d(17, 21, "win"), 48)                        # 4 por encima: +1
        # match 2-0: 2 x (40 + 7) + 1 + 1 = 96 contra LV 21, 95 contra 18-20, 80 contra el mismo LV
        for opp, total in ((21, 96), (19, 95), (17, 80)):
            tot = d(17, opp, "win", True, "win", False) + d(17, opp, "win", True, "win", True)
            self.assertEqual(tot, total, opp)

    def test_derrotas_y_empates(self):
        d = PS.xp_game_delta
        self.assertEqual(d(17, 10, "lose"), -16)                      # contra uno de LV menor
        self.assertEqual(d(17, 17, "lose"), -1)
        self.assertEqual(d(17, 19, "lose"), -1)                       # 1-3 por encima: -1 (duelo suelto)
        self.assertEqual(d(17, 21, "lose"), 0)                        # 4 por encima: 0
        self.assertEqual(d(17, 19, "lose", True, "lose", False), 0)   # en match, el -1 va al acabar el match
        self.assertEqual(d(17, 19, "lose", True, "lose", True), -1)
        self.assertEqual(d(17, 17, "draw"), 0)


class TestPerfil(unittest.TestCase):
    def test_principiante_se_queda_en_lv1_hasta_36_duelos(self):
        p = perfil(1, 0, 0)
        for i in range(35):
            r = PS.pvp_xp(p, 1, "win")
            self.assertEqual(p["rec"]["lv"], 1, i)
        self.assertEqual(p["rec"]["score"], 35 * 40)
        r = PS.pvp_xp(p, 1, "win")                                     # el duelo 36
        self.assertEqual(p["rec"]["score"], 1440)
        self.assertEqual(p["rec"]["lv"], PS.xp_level_for_score(1440))  # 8
        self.assertEqual((r["lv_old"], r["lv"], p["pvp"]["n"]), (1, 8, 36))

    def test_el_contador_arranca_de_rec_duels_y_el_lv_baja_con_la_puntuacion(self):
        p = perfil(3, 60, 50)
        PS.pvp_xp(p, 1, "lose")                                        # rival de LV menor: -16 -> 44 -> LV 2
        self.assertEqual((p["rec"]["score"], p["rec"]["lv"], p["pvp"]["n"]), (44, 2, 51))
        q = perfil(2, 5, 50)
        PS.pvp_xp(q, 1, "lose")
        PS.pvp_xp(q, 1, "lose")
        self.assertEqual(q["rec"]["score"], 0)                          # no baja de 0
        self.assertEqual(q["rec"]["lv"], 2)

    def test_lv_puesto_por_el_admin_es_suelo(self):
        p = perfil(10, 0, 100)                                          # LV 10 a mano con puntuación 0
        PS.pvp_xp(p, 10, "lose")
        self.assertEqual(p["rec"]["lv"], 10)
        PS.pvp_xp(p, 10, "win")
        self.assertEqual(p["rec"]["lv"], 10)
        p["rec"]["score"] = 2390
        PS.pvp_xp(p, 10, "win")                                        # 2430 = LV 10 por puntuación
        PS.pvp_xp(p, 10, "win")
        self.assertEqual(p["rec"]["lv"], 10)
        p["rec"]["score"] = 3290
        PS.pvp_xp(p, 10, "win")                                        # 3330 -> LV 11
        self.assertEqual(p["rec"]["lv"], 11)

    def test_apagado(self):
        old = PS.XP_ON
        PS.XP_ON = False
        try:
            p = perfil(1, 0, 40)
            self.assertIsNone(PS.pvp_xp(p, 1, "win"))
            self.assertEqual(p["rec"]["score"], 0)
            self.assertNotIn("pvp", p)
        finally:
            PS.XP_ON = old

    def test_el_npc_no_toca_lv_ni_puntuacion(self):
        p = perfil(5, 400, 10)
        PS.npc_duel_result(p, 18, "win", turns=3)
        PS.npc_duel_result(p, 18, "lose", turns=3)
        self.assertEqual((p["rec"]["lv"], p["rec"]["score"]), (5, 400))

    def test_tope_u32(self):
        p = perfil(30, 0xfffffff0, 100)
        PS.pvp_xp(p, 30, "win")
        self.assertEqual(p["rec"]["score"], 0xffffffff)


class FakeDFM:
    RESULT_WIN, RESULT_LOSE, RESULT_DRAW = 1, 2, 3
    PS = PS


def partida(perfiles):
    """PvpMatch sin red: __new__ + lo mínimo que usan _xp_levels/_xp (perfiles por jugador)."""
    m = object.__new__(pvp_match.PvpMatch)
    m.name, m.log = "t", lambda *a: None
    m.sess = [types.SimpleNamespace(i=i) for i in (0, 1)]
    cur = {}
    m.env = {"DFM": FakeDFM, "STATE_LOCK": threading.RLock(), "CTX": types.SimpleNamespace(sess=None),
             "load_state": lambda: {"profiles": {"0": perfiles[cur["ctx"].sess.i]}, "active": 0}}
    cur["ctx"] = m.env["CTX"]
    FakeDFM.PS = types.SimpleNamespace(profile=lambda st, slot: st["profiles"]["0"], active_slot=lambda st: 0,
                                       pvp_xp=PS.pvp_xp, apply_duel_stats=PS.apply_duel_stats)
    return m


class TestEnganche(unittest.TestCase):
    def test_los_dos_ven_el_lv_previo_y_se_cuentan_lp_y_rendicion(self):
        a, b = perfil(5, 400, 60), perfil(3, 150, 60)
        m = partida([a, b])
        self.assertEqual(m._xp_levels(), [5, 3])
        m._bo3 = lambda: False
        m.ps = types.SimpleNamespace(last_lp=[2500, 0])
        m.surr = 1                                                       # se rinde el jugador 1
        xa = m._xp(FakeDFM.PS, a, 0, FakeDFM.RESULT_WIN, FakeDFM, True, None)
        # el LV de b NO se ha leído de nuevo: b sigue siendo 3 aunque a ya haya cambiado
        a["rec"]["lv"] = 9
        xb = m._xp(FakeDFM.PS, b, 1, FakeDFM.RESULT_LOSE, FakeDFM, True, None)
        self.assertEqual(xa["delta"], 40)                                # rival de LV menor: 40 sin extras
        self.assertEqual(xb["delta"], -1)                                # b (LV 3) pierde ante LV 5: -1
        self.assertEqual(b["rec"]["score"], 150 + PS.xp_game_delta(3, 5, "lose"))        # LV 5 > 3: -1
        self.assertEqual((a["rec"]["lp_total"], b["rec"]["lp_total"]), (2500, 0))
        self.assertEqual((a["rec"].get("surrender_opp"), b["rec"].get("surrender_me")), (1, 1))

    def test_fallo_no_tumba(self):
        m = partida([perfil(), perfil()])
        m._bo3 = lambda: False
        self.assertIsNone(m._xp(types.SimpleNamespace(apply_duel_stats=lambda *a: 1 / 0), {}, 0, 1, FakeDFM, True, None))


if __name__ == "__main__":
    unittest.main()
