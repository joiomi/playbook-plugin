"""Task 130: the agy judge path on agy 1.3.1 (it was written and measured on 1.2.17).

The owner's machine runs agy 1.3.1 (owner, 2026-10-07: "vreau upgrade la agy 1.3.1 daca noi
suntem abia la 1.2.17"). `tests/fixtures/agy-1.3.1/` holds 1.3.1 captures made with the 1.2.17
commands. This module re-runs EVERY test class of `tests/test_agy_judge.py` with those captures
in place of the 1.2.17 ones (the files 1.3.1 could not re-capture keep their 1.2.17 version):
each class gets a subclass here whose class and test set-up point the fixture directory at the
merged set, and the token counts the tests expect at agy 1.3.1's own (`USAGE_131`, written
out from the captures by hand — task 138 G3-6: the first version skipped the 10 tests that pin
counts, and those also pin behaviour such as the denied-tool trap, and re-derived the numbers
with the parser under test). Every test runs; none is skipped.

No `load_tests` hook: scripts/verify runs one process per module and cannot split off a module
that binds one.

Run: python3 -m unittest tests.test_agy_131
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
import unittest
from pathlib import Path

import tests.test_agy_judge as T

HERE = Path(__file__).resolve().parent
OLD = HERE / "fixtures" / "agy-1.2.17"
NEW = HERE / "fixtures" / "agy-1.3.1"

# agy 1.3.1's token counts, from each capture's terminal `result` event (2026-10-07 captures)
USAGE_131 = {"success-pong": (12564, 175), "success-tools": (41351, 1818),
             "denied-command": (12588, 1417)}


def _merged() -> Path:
    d = Path(tempfile.mkdtemp(prefix="agy-131-"))
    atexit.register(shutil.rmtree, d, True)
    for p in OLD.iterdir():
        shutil.copyfile(p, d / p.name)
    for p in NEW.iterdir():
        shutil.copyfile(p, d / p.name)
    return d


MERGED = _merged()


def _on_131(base: type) -> type:
    """A subclass of one 1.2.17 test class that runs on the merged 1.3.1 captures — the
    module's fixture directory is swapped only around the class and each test."""

    @classmethod
    def setUpClass(cls):
        cls._pb_fix, cls._pb_usage = T.FIX, T.USAGE
        T.FIX, T.USAGE = MERGED, USAGE_131
        try:
            super(sub, cls).setUpClass()
        finally:
            T.FIX, T.USAGE = cls._pb_fix, cls._pb_usage

    def setUp(self):
        old_fix, old_usage = T.FIX, T.USAGE
        T.FIX, T.USAGE = MERGED, USAGE_131
        self.addCleanup(setattr, T, "USAGE", old_usage)
        self.addCleanup(setattr, T, "FIX", old_fix)
        super(sub, self).setUp()

    attrs = {"setUpClass": setUpClass, "setUp": setUp, "__module__": __name__,
             "__doc__": f"{base.__name__} on the agy 1.3.1 captures (task 130)."}
    sub = type(f"{base.__name__}On131", (base,), attrs)
    return sub


for _name, _cls in list(vars(T).items()):
    if (isinstance(_cls, type) and issubclass(_cls, unittest.TestCase)
            and _cls.__module__ == T.__name__ and not _name.startswith("_")):
        globals()[f"{_name}On131"] = _on_131(_cls)
del _name, _cls


class Table(unittest.TestCase):
    def test_the_1_3_1_table_covers_every_count_the_tests_pin(self):
        self.assertEqual(set(USAGE_131), set(T.USAGE))
        self.assertNotEqual(USAGE_131, T.USAGE)          # really agy 1.3.1's numbers, not a copy


if __name__ == "__main__":
    unittest.main()
