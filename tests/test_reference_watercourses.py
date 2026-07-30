import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from pyproj import Transformer
import shapefile
from shapely.geometry import box, mapping
from shapely.ops import transform


ROOT = Path(__file__).parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module(
    "extract_reference_watercourses",
    ROOT / "scripts" / "extract_reference_watercourses.py",
)


class ReferenceWatercourseTests(unittest.TestCase):
    def test_extracts_only_lines_near_candidate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            source = base / "watercourses"
            writer = shapefile.Writer(str(source), shapeType=shapefile.POLYLINE)
            writer.field("NAME", "C", size=40)
            writer.line([[[1050000, 1890000], [1050100, 1890000]]])
            writer.record("inside")
            writer.line([[[1051000, 1891000], [1051100, 1891000]]])
            writer.record("outside")
            writer.close()

            to_wgs84 = Transformer.from_crs(
                "EPSG:5179", "EPSG:4326", always_xy=True
            )
            zone = transform(
                to_wgs84.transform,
                box(1049990, 1889990, 1050110, 1890010),
            )
            candidate = {
                "type": "Feature",
                "properties": {
                    "admin_code": "43150",
                    "river_name": "테스트천",
                    "candidate_rank": 10,
                },
                "geometry": mapping(zone),
            }

            result = MODULE.extract_watercourses(
                source.with_suffix(".shp"),
                candidate,
                max_distance_m=20,
            )

            self.assertEqual(result["metadata"]["feature_count"], 1)
            self.assertEqual(result["metadata"]["intersecting_count"], 1)
            self.assertGreater(
                result["metadata"]["inside_zone_length_m"],
                90,
            )
            self.assertEqual(
                result["features"][0]["properties"]["NAME"],
                "inside",
            )


if __name__ == "__main__":
    unittest.main()
