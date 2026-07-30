import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
import rasterio
from rasterio.transform import from_origin
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


REQUEST_MODULE = load_module(
    "prepare_imagery_requests",
    ROOT / "scripts" / "prepare_imagery_requests.py",
)
PROCESS_MODULE = load_module(
    "process_satellite_imagery",
    ROOT / "scripts" / "process_satellite_imagery.py",
)


class ImageryPipelineTests(unittest.TestCase):
    def test_selected_sites_create_five_requests(self):
        features = REQUEST_MODULE.load_selected_features(
            ROOT / "config" / "selected_sites.json",
            ROOT / "data" / "processed" / "sample_candidates.geojson",
        )
        requests = REQUEST_MODULE.prepare_requests(features, 200)
        self.assertEqual(len(requests), 5)
        self.assertEqual(
            [request["candidate_rank"] for request in requests],
            [1, 2, 5, 9, 10],
        )
        self.assertTrue(
            all(request["files"]["pan"].endswith("_pan.tif") for request in requests)
        )

    def test_regional_controls_use_separate_imagery_filenames(self):
        features = REQUEST_MODULE.load_all_features(
            ROOT / "data" / "processed" / "regional_river_controls.geojson"
        )
        requests = REQUEST_MODULE.prepare_requests(features, 200)
        self.assertEqual(len(requests), 5)
        self.assertEqual(
            [request["river_name"] for request in requests],
            ["보강천", "보강천", "보강천", "보강천", "백곡천"],
        )
        self.assertTrue(
            all(
                Path(request["files"]["pan"]).name.startswith("regional_")
                for request in requests
            )
        )

    def test_processes_synthetic_pan_raster(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            source_path = base / "pan.tif"
            output_path = base / "pan.png"
            local_aoi = box(250050, 350050, 250250, 350250)
            to_wgs84 = Transformer.from_crs(
                "EPSG:5174", "EPSG:4326", always_xy=True
            )
            aoi_wgs84 = transform(to_wgs84.transform, local_aoi)

            rows, columns = np.indices((300, 300))
            data = ((rows + columns) * 100).astype(np.uint16)
            with rasterio.open(
                source_path,
                "w",
                driver="GTiff",
                width=300,
                height=300,
                count=1,
                dtype="uint16",
                crs="EPSG:5174",
                transform=from_origin(250000, 350300, 1, 1),
            ) as dataset:
                dataset.write(data, 1)

            result = PROCESS_MODULE.process_raster(
                source_path,
                output_path,
                "pan",
                mapping(aoi_wgs84),
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["source_crs"], "EPSG:5174")
            self.assertEqual(result["georeferencing_status"], "verified")
            self.assertTrue(output_path.exists())
            with Image.open(output_path) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertGreater(image.width, 100)
                self.assertGreater(image.height, 100)

    def test_missing_files_remain_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            request_path = base / "requests.json"
            output_dir = base / "processed"
            request_path.write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "id": "test-site",
                                "candidate_rank": 1,
                                "admin_code": "43110",
                                "admin_name": "청주시",
                                "river_name": "테스트천",
                                "width_class": "medium",
                                "width_proxy_m": 10,
                                "aoi_bbox_wgs84": [127, 36, 127.01, 36.01],
                                "aoi_geometry_wgs84": mapping(
                                    box(127, 36, 127.01, 36.01)
                                ),
                                "files": {
                                    "pan": str(base / "missing_pan.tif"),
                                    "rgb": str(base / "missing_rgb.tif"),
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = PROCESS_MODULE.process_manifest(request_path, output_dir)
            self.assertEqual(result["ready_layers"], 0)
            self.assertEqual(
                result["sites"][0]["layers"]["pan"]["status"], "missing"
            )
            self.assertTrue((output_dir / "imagery_manifest.json").exists())

    def test_empty_sources_are_reported_as_out_of_coverage(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            request_path = base / "requests.json"
            output_dir = base / "processed"
            request_path.write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "id": "outside-scene",
                                "candidate_rank": 2,
                                "admin_code": "43745",
                                "admin_name": "증평군",
                                "river_name": "보강천",
                                "width_class": "medium",
                                "width_proxy_m": 10,
                                "aoi_bbox_wgs84": [127, 36, 127.01, 36.01],
                                "aoi_geometry_wgs84": mapping(
                                    box(127, 36, 127.01, 36.01)
                                ),
                                "imagery_sources": [],
                                "files": {"pan": [], "rgb": []},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = PROCESS_MODULE.process_manifest(request_path, output_dir)

            site = result["sites"][0]
            self.assertEqual(site["imagery_sources"], [])
            self.assertEqual(site["layers"]["pan"]["status"], "out_of_coverage")
            self.assertEqual(site["layers"]["rgb"]["status"], "out_of_coverage")


if __name__ == "__main__":
    unittest.main()
