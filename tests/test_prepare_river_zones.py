import importlib.util
import sys
import unittest
from pathlib import Path

from shapely.geometry import Polygon


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "prepare_river_zones.py"
SPEC = importlib.util.spec_from_file_location("prepare_river_zones", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PrepareRiverZonesTests(unittest.TestCase):
    def test_explicit_zone_filter(self):
        self.assertTrue(MODULE.is_explicit_zone("소하천구역", "국사천"))
        self.assertTrue(MODULE.is_explicit_zone("탑전천구역", "탑전천구역"))
        self.assertFalse(MODULE.is_explicit_zone("소하천예정지", "국사천"))
        self.assertFalse(MODULE.is_explicit_zone("", "국사천"))

    def test_name_normalization(self):
        self.assertEqual(
            MODULE.normalize_river_name("소하천구역", "112_상풍천"), "상풍천"
        )
        self.assertEqual(
            MODULE.normalize_river_name("탑전천구역", "탑전천구역"), "탑전천"
        )
        self.assertEqual(
            MODULE.normalize_river_name("소하천구역", "051-굴탄천"), "굴탄천"
        )

    def test_width_proxy(self):
        geometry = Polygon([(0, 0), (100, 0), (100, 10), (0, 10)])
        item = MODULE.ZoneRecord(
            index=1,
            properties={
                "COL_ADM_SE": "43110",
                "RIVER_NAME": "테스트천",
                "MNUM": "test",
                "NTFDATE": "20260701",
            },
            geometry=geometry,
        )
        zone = MODULE.dissolve_by_river([item])[0]
        self.assertAlmostEqual(zone["area_m2"], 1000.0)
        self.assertAlmostEqual(zone["width_proxy_m"], 100.0 / 11.0)


if __name__ == "__main__":
    unittest.main()
