import importlib.util
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

MODULE_PATH = SCRIPTS / "prepare_regional_rivers.py"
SPEC = importlib.util.spec_from_file_location("prepare_regional_rivers", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RegionalRiverTests(unittest.TestCase):
    def test_extracts_four_bogangcheon_and_one_baekgokcheon(self):
        zones = MODULE.load_target_zones(
            ROOT
            / "data"
            / "regional_rivers"
            / "LSMD_CONT_UJ201_5174_43_202607"
        )
        self.assertEqual(
            Counter(zone["river_name"] for zone in zones),
            {"보강천": 4, "백곡천": 1},
        )
        self.assertEqual(
            [zone["admin_code"] for zone in zones if zone["river_name"] == "보강천"],
            ["43110", "43745", "43750", "43760"],
        )
        self.assertEqual(
            [zone["admin_code"] for zone in zones if zone["river_name"] == "백곡천"],
            ["43750"],
        )
        self.assertEqual(len({zone["id"] for zone in zones}), 5)
        self.assertTrue(
            all(zone["imagery_file_stem"].startswith("regional_") for zone in zones)
        )


if __name__ == "__main__":
    unittest.main()
