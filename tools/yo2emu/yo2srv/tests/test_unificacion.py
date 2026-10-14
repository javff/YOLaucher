"""Paso U1: lockstep y persistencia son UN solo módulo por proceso (yo2srv.compat.cargar).

Antes, lockstep se cargaba tres veces (`research_lockstep` en el arranque = srv.LS, `lockstep_proposal` en duelfin = DFM.LS y el
`proposal` pelado del traductor) y persistencia dos (`research_persistencia` = srv.PP y `persistencia_proposal` = DFM.PS), cada
copia con su propio estado de módulo. Ahora todos piden el módulo a compat.cargar(área) y reciben el de sys.modules.

Cada comprobación corre en un intérprete NUEVO (subprocess): así no dependen de lo que hayan importado otras pruebas ni ensucian
su sys.modules. La última ejecuta la carga entera de run.py (la misma de ocg_test.py: hasta «FIN DE LA CARGA», sin puertos ni
hilos, con una copia de state.json y su log en una carpeta temporal) y mira los nombres del servidor de verdad.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import arranque, compat  # noqa: E402


def correr(guion, entorno=None, tiempo=120):
    """Ejecuta `guion` en un intérprete nuevo desde tools/yo2emu; devuelve (código, salida, error)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("YO2_")}      # valores por defecto de siempre
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env.update(entorno or {})
    p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (EMU, textwrap.dedent(guion))],
                       cwd=EMU, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=tiempo)
    return p.returncode, p.stdout, p.stderr


class Cargar(unittest.TestCase):
    def test_una_sola_vez_con_el_nombre_canonico(self):
        rc, out, err = correr('''
            from yo2srv import compat
            import sys
            a = compat.cargar("lockstep")
            assert a.__name__ == "research_lockstep" and sys.modules["research_lockstep"] is a
            assert compat.cargar("lockstep") is a
            p = compat.cargar("persistencia")
            assert p is not a and compat.cargar("persistencia") is p and sys.modules["research_persistencia"] is p
            print("ok")
        ''')
        self.assertEqual((rc, out.strip()), (0, "ok"), err)

    def test_traductor_y_duelfin_cargados_por_ruta_comparten_los_modulos(self):
        # lo que hacen las pruebas y herramientas sin servidor: cargan translator.py y duelfin/proposal.py por ruta
        rc, out, err = correr('''
            import importlib.util, os, sys
            sys.path.insert(0, os.path.join("research", "traductor"))
            import translator as TR
            def por_ruta(nombre, ruta):
                spec = importlib.util.spec_from_file_location(nombre, ruta)
                m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
            d1 = por_ruta("duelfin_u1_a", os.path.join("research", "duelfin", "proposal.py"))
            d2 = por_ruta("duelfin_u1_b", os.path.join("research", "duelfin", "proposal.py"))
            LS, PS = sys.modules["research_lockstep"], sys.modules["research_persistencia"]
            assert d1.LS is LS and d2.LS is LS and d1.PS is PS and d2.PS is PS
            for n in ("engine", "setcommand", "comid_field", "request_input", "duel_finish", "timeup_warning", "loc",
                      "set_card", "C_DRAW", "C_DUEL_END", "ZONE_DECK", "T_VIEW"):
                assert getattr(TR, n) is getattr(LS, n), n
            assert "proposal" not in sys.modules, "el nombre pelado «proposal» ya no debe quedar en sys.modules"
            assert not [k for k in sys.modules if k in ("lockstep_proposal", "persistencia_proposal")]
            assert not any(os.path.normcase(os.path.abspath(q)) == os.path.normcase(os.path.abspath(
                os.path.join("research", "lockstep"))) for q in sys.path), "research/lockstep no debe quedar en sys.path"
            print("ok")
        ''')
        self.assertEqual((rc, out.strip()), (0, "ok"), err)

    def test_fresco_sustituye_y_lo_que_se_cargue_despues_recibe_el_nuevo(self):
        rc, out, err = correr('''
            import importlib.util, os, sys
            from yo2srv import compat, arranque
            a = compat.cargar("lockstep")
            b = compat.cargar("lockstep", fresco=True)
            assert b is not a and sys.modules["research_lockstep"] is b and compat.cargar("lockstep") is b
            # arranque.load_research es esa misma carga fresca (la usa el servidor al arrancar)
            import types
            arranque.setup(types.SimpleNamespace(HERE=os.getcwd()))
            c = arranque.load_research("lockstep")
            assert c is not b and sys.modules["research_lockstep"] is c
            spec = importlib.util.spec_from_file_location("duelfin_u1", os.path.join("research", "duelfin", "proposal.py"))
            d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
            assert d.LS is c, "duelfin cargado DESPUÉS recibe el lockstep del arranque"
            print("ok")
        ''')
        self.assertEqual((rc, out.strip()), (0, "ok"), err)

    def test_si_el_fichero_falla_no_queda_nada_a_medias(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "research", "u1roto"))
            with open(os.path.join(base, "research", "u1roto", "proposal.py"), "w", encoding="utf-8") as f:
                f.write("X = 1\nraise RuntimeError('roto')\n")
            with self.assertRaises(RuntimeError):
                compat.cargar("u1roto", base=base)
            self.assertNotIn("research_u1roto", sys.modules)
            # un módulo bueno que ya estaba sobrevive a una recarga fresca que falla
            os.makedirs(os.path.join(base, "research", "u1bueno"))
            ruta = os.path.join(base, "research", "u1bueno", "proposal.py")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("V = 1\n")
            try:
                bueno = compat.cargar("u1bueno", base=base)
                with open(ruta, "w", encoding="utf-8") as f:
                    f.write("raise RuntimeError('roto')\n")
                with self.assertRaises(RuntimeError):
                    compat.cargar("u1bueno", fresco=True, base=base)
                self.assertIs(sys.modules["research_u1bueno"], bueno)
            finally:
                sys.modules.pop("research_u1bueno", None)

    def test_nombre_research(self):
        self.assertEqual(compat.nombre_research("lockstep"), "research_lockstep")
        self.assertTrue(callable(arranque.load_research))


class ServidorEntero(unittest.TestCase):
    """La carga entera de run.py (arranque.cargar): lo que ven srv.LS, srv.PP, DFM y el traductor del OCG."""

    @unittest.skipUnless(os.path.isfile(os.path.join(EMU, "state.json")), "falta state.json para la carga de run.py")
    def test_dfm_ls_es_ls_y_dfm_ps_es_pp(self):
        with tempfile.TemporaryDirectory() as tmp:
            guion = '''
                import os, shutil, sys, types
                sys.modules["frida"] = types.ModuleType("frida")
                src = open("run.py", encoding="utf-8").read()
                g = {"__file__": os.path.abspath("run.py"), "__name__": "u1"}
                exec(compile(src[:src.index("# ---- FIN DE LA CARGA")], "run.py", "exec"), g)
                LS, PP, DFM, OCG = g["LS"], g["PP"], g["DFM"], g["OCG"]
                TR = OCG.TR
                bad = []
                def chk(nombre, cond):
                    if not cond:
                        bad.append(nombre)
                chk("DFM.LS is LS", DFM.LS is LS)
                chk("DFM.PS is PP", DFM.PS is PP)
                chk("srv.LS / srv.PP", g["SRV"].LS is LS and g["SRV"].PP is PP)
                chk("sys.modules research_lockstep is LS", sys.modules["research_lockstep"] is LS)
                chk("sys.modules research_persistencia is PP", sys.modules["research_persistencia"] is PP)
                for n in ("engine", "setcommand", "request_input", "duel_finish", "timeup_warning", "loc", "set_card",
                          "C_DRAW", "ZONE_DECK", "T_VIEW"):
                    chk("TR." + n + " is LS." + n, getattr(TR, n) is getattr(LS, n))
                chk("sin «proposal» pelado", "proposal" not in sys.modules)
                chk("sin copias viejas", not [k for k in sys.modules if k in ("lockstep_proposal", "persistencia_proposal")])
                # el estado que el arranque escribe en LS / PP lo ve cualquiera que llegue por otro camino
                chk("DFM.LS.DUEL_CFG is LS.DUEL_CFG y stage 4", DFM.LS.DUEL_CFG is LS.DUEL_CFG and LS.DUEL_CFG["stage"] == 4)
                chk("DFM.LS.duel_reply_base is la base de run.py", DFM.LS.duel_reply_base is g["duel_reply_base"])
                chk("DFM.PS.RAW_STATE puesto", callable(DFM.PS.RAW_STATE))
                # los parches de NR (antes solo en DFM.PS), de MW y de DPC (antes solo en PP) caen ahora en el MISMO módulo
                chk("NR parchea PP.check_titles/npc_rewards/rights_due",
                    all(getattr(PP, n).__module__ == "research_npcreglas" for n in ("check_titles", "npc_rewards", "rights_due")))
                chk("MW parchea PP.challenge_rights_due/on_title",
                    all(getattr(PP, n).__module__ == "research_mundo" for n in ("challenge_rights_due", "on_title")))
                chk("DPC envuelve PP.blank_profile/seed_trunk",
                    hasattr(PP.blank_profile, "_dp_orig") and hasattr(PP.seed_trunk, "_dp_orig"))
                # «Rendirse»: lockstep y traductor siguen envueltos cada uno por su lado (dos referencias a la función)
                chk("LS.comid_field envuelto", LS.comid_field.__name__ == "<lambda>")
                chk("TR.comid_field envuelto una vez", TR._surrender_patched and TR.comid_field.__name__ == "<lambda>")
                print("MAL" if bad else "OK", bad)
                sys.stdout.flush(); os._exit(0)
            '''
            shutil_dir = os.path.join(tmp, "state.json")
            import shutil
            shutil.copy(os.path.join(EMU, "state.json"), shutil_dir)
            rc, out, err = correr(guion, {"YO2_STATE": shutil_dir, "YO2_LOGDIR": os.path.join(tmp, "logs"),
                                          "YO2_JANKEN_NPC_AZAR": "0"})
            ultima = (out.strip().splitlines() or [""])[-1]
            self.assertEqual((rc, ultima), (0, "OK []"), out[-1500:] + err[-1500:])


if __name__ == "__main__":
    unittest.main()
