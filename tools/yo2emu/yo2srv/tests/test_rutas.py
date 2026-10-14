"""rutas.py = las fórmulas de run.py/panel.py de hoy (R3): misma ruta en la worktree y en el árbol principal."""
import os
import sys
import unittest

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import rutas  # noqa: E402

PRINCIPAL = r"C:\src\yo2online\tools\yo2emu"


def run_py(HERE, env):
    """Las fórmulas de run.py, copiadas tal cual (líneas 13-29 y 137-139 de run.py)."""
    PORT_OFFSET = int(env.get("YO2_PORT_OFFSET", "0") or 0)
    GAME = os.path.normpath(os.path.join(HERE, "..", "..", "_original", "yo2_2008-11"))
    LOGDIR = env.get("YO2_LOGDIR") or os.path.join(HERE, "logs", *(["prueba"] if PORT_OFFSET else []))
    STATE_PATH = os.path.join(HERE, "state.json")
    if env.get("YO2_STATE"):
        STATE_PATH = os.path.abspath(env["YO2_STATE"])
    PACKINFO = os.path.normpath(os.path.join(HERE, "..", "..", "_original", "extracted", "merge2008", "data", "cardpack",
                                             "packinfo_s.txt"))
    return GAME, LOGDIR, STATE_PATH, PACKINFO


class TestRutas(unittest.TestCase):
    ENTORNOS = [{}, {"YO2_PORT_OFFSET": "6000"}, {"YO2_PORT_OFFSET": "0"}, {"YO2_PORT_OFFSET": ""},
                {"YO2_LOGDIR": r"C:\tmp\logs-x"}, {"YO2_STATE": os.path.join("rel", "estado.json")},
                {"YO2_STATE": r"C:\tmp\estado.json", "YO2_PORT_OFFSET": "1900", "YO2_LOGDIR": r"C:\tmp\l"}]

    def _comparar(self, emu):
        for env in self.ENTORNOS:
            with self.subTest(emu=emu, env=env):
                game, logdir, state, packinfo = run_py(emu, env)
                r = rutas.desde(emu)
                self.assertEqual(r.game, game)
                self.assertEqual(r.packinfo, packinfo)
                self.assertEqual(rutas.logdir(emu, env), logdir)
                self.assertEqual(rutas.state_path(emu, env), state)
                self.assertEqual(rutas.datos_dir(emu, env), os.path.dirname(os.path.abspath(state)))

    def test_formulas_de_run_py_en_esta_copia(self):
        self._comparar(rutas.EMU)

    def test_formulas_de_run_py_en_el_arbol_principal(self):
        # no hace falta que exista: las rutas son aritmética de cadenas desde tools/yo2emu
        self._comparar(PRINCIPAL)
        r = rutas.desde(PRINCIPAL)
        self.assertEqual(r.root, r"C:\src\yo2online")
        self.assertEqual(r.original, r"C:\src\yo2online\_original")
        self.assertEqual(r.game, r"C:\src\yo2online\_original\yo2_2008-11")

    def test_emu_es_la_carpeta_de_run_py(self):
        self.assertTrue(os.path.isfile(os.path.join(rutas.EMU, "run.py")), rutas.EMU)
        self.assertEqual(os.path.normcase(rutas.EMU), os.path.normcase(os.path.dirname(os.path.abspath(
            os.path.join(rutas.EMU, "run.py")))))
        if os.path.isdir(os.path.join(rutas.EMU, "research", "admin")):        # el panel es privado: no va en el público
            self.assertTrue(os.path.isdir(rutas.desde().panel_www), "research/admin/www")
        self.assertTrue(os.path.isfile(rutas.desde().net_hook), "net_hook.js")

    def test_no_depende_del_directorio_de_trabajo(self):
        antes = os.getcwd()
        try:
            os.chdir(os.environ.get("TEMP") or os.path.expanduser("~"))
            self.assertEqual(rutas.desde().emu, rutas.EMU)
            self.assertEqual(rutas.state_path(env={}), os.path.join(rutas.EMU, "state.json"))
        finally:
            os.chdir(antes)

    def test_tokens(self):
        env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}
        self.assertEqual(rutas.token_consola(5799, env=env), r"C:\Users\x\AppData\Local\YO2Online\consola_5799.txt")
        self.assertEqual(rutas.tokens_panel(env), r"C:\Users\x\AppData\Local\YO2Online")
        # sin LOCALAPPDATA los dos respaldos DIFIEREN a propósito (hoy es así: run.py usa HERE; panel.py ~/AppData/Local)
        self.assertEqual(rutas.tokens_consola(r"C:\e", {}), r"C:\e\YO2Online")
        self.assertTrue(rutas.tokens_panel({}).endswith(os.path.join("AppData", "Local", "YO2Online")))

    def test_offset_mal_escrito_lanza_como_run_py(self):
        with self.assertRaises(ValueError):
            rutas.port_offset({"YO2_PORT_OFFSET": "abc"})


if __name__ == "__main__":
    unittest.main()
