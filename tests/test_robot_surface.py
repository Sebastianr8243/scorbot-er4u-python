"""The public surface of scorbot.robot, pinned so a refactor cannot drop or rename anything."""

import inspect
import json
import os
import re
import unittest
from pathlib import Path

import scorbot
from scorbot.robot import Scorbot

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "scorbot_surface.json"
SIMPLE = (int, float, str, bool, tuple, type(None))
MODULE_PATH = re.compile(r"\bscorbot(\.[a-z_]+)+\.")   # a class that moves modules keeps its public name


def surface():
    """Every attribute of Scorbot (inherited ones included) with its signature or simple value."""
    out = {}
    for name, member in inspect.getmembers(Scorbot):
        if name.startswith("__") and name != "__init__":
            continue
        if inspect.isfunction(member):
            out[name] = "def" + MODULE_PATH.sub("", str(inspect.signature(member)))
        elif isinstance(member, SIMPLE):
            out[name] = "value " + repr(member)
        elif isinstance(member, (dict, frozenset, set, list)):
            out[name] = "value " + repr(sorted(member, key=repr) if not isinstance(member, dict) else sorted(member.items(), key=repr))
    out["__all__"] = repr(sorted(scorbot.__all__))
    return out


class SurfaceTests(unittest.TestCase):
    def test_the_public_surface_is_unchanged(self):
        current = surface()
        if os.environ.get("UPDATE_SURFACE") == "1":
            SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(set(expected) - set(current)), [], "names removed from Scorbot")
        self.assertEqual(sorted(set(current) - set(expected)), [], "names added to Scorbot")
        changed = {name for name in expected if name in current and expected[name] != current[name]}
        self.assertEqual(sorted(changed), [], "a signature or constant changed")
