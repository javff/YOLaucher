"""Pruebas unitarias de las operaciones que usa la pestaña avanzada del launcher."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest


ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_duelpass():
    p = os.path.join(ROOT, "research", "persistencia", "duelpass.py")
    spec = importlib.util.spec_from_file_location("test_duelpass_module", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAdminFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="yo2-admin-features-")
        self.dp = load_duelpass()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_duelpass_create_void_and_take(self):
        codes = self.dp.create(self.tmp, 2, dpp=30, carta=12, mazo=1, nota="evento")
        self.assertEqual(len(codes), 2)
        rows = self.dp.load(self.tmp)
        self.assertEqual(rows[codes[0]]["estado"], "libre")
        self.assertEqual(self.dp.take(self.tmp, codes[0], {"cuenta": "a", "hueco": 0})[0], self.dp.RES_OK)
        self.assertEqual(self.dp.take(self.tmp, codes[0], {})[0], self.dp.RES_USED)
        self.assertEqual(self.dp.void(self.tmp, codes[1]), "libre")
        self.assertEqual(self.dp.take(self.tmp, codes[1], {})[0], self.dp.RES_VOID)

    def test_start_gifts_are_read_with_limits(self):
        with open(self.dp.start_path(self.tmp), "w", encoding="utf-8") as f:
            json.dump({"mp": 42, "dpp": 17, "baul": "deck", "extra": "ignored"}, f)
        self.assertEqual(self.dp.start_values(self.tmp), {"mp": 42, "dpp": 17, "baul": "deck"})


if __name__ == "__main__":
    unittest.main()
