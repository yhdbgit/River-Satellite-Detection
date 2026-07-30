#!/usr/bin/env python3
"""Create satellite-image request areas for the five selected river zones."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform


DEFAULT_SELECTION = Path("config/selected_sites.json")
DEFAULT_CANDIDATES = Path("data/processed/sample_candidates.geojson")
DEFAULT_OUTPUT = Path("data/imagery/requests")


def load_selected_features(selection_path: Path, candidates_path: Path) -> List[Dict]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selected_ids = set(selection.get("selected_ids", []))
    if len(selected_ids) != 5:
        raise ValueError("Exactly five selected site IDs are required.")

    candidate_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    selected_features = []
    for feature in candidate_data.get("features", []):
        properties = feature["properties"]
        site_id = (
            f"{properties['admin_code']}:{properties['river_name']}:"
            f"{properties['candidate_rank']}"
        )
        if site_id in selected_ids:
            copied = {
                "type": "Feature",
                "properties": dict(properties),
                "geometry": feature["geometry"],
            }
            copied["properties"]["id"] = site_id
            selected_features.append(copied)

    found_ids = {feature["properties"]["id"] for feature in selected_features}
    missing_ids = selected_ids - found_ids
    if missing_ids:
        raise ValueError(f"Selected sites missing from candidate GeoJSON: {missing_ids}")
    return sorted(
        selected_features,
        key=lambda feature: feature["properties"]["candidate_rank"],
    )


def load_all_features(candidates_path: Path) -> List[Dict]:
    candidate_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    features = []
    for feature in candidate_data.get("features", []):
        properties = dict(feature["properties"])
        site_id = properties.get("id")
        if not site_id:
            site_id = (
                f"{properties['admin_code']}:{properties['river_name']}:"
                f"{properties['candidate_rank']}"
            )
        properties["id"] = site_id
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": feature["geometry"],
            }
        )
    if not features:
        raise ValueError(f"No features found in {candidates_path}")
    return sorted(
        features,
        key=lambda feature: feature["properties"]["candidate_rank"],
    )


def prepare_requests(features: List[Dict], buffer_m: float) -> List[Dict]:
    to_local = Transformer.from_crs("EPSG:4326", "EPSG:5174", always_xy=True)
    to_wgs84 = Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True)
    requests = []

    for feature in features:
        properties = feature["properties"]
        source_wgs84 = shape(feature["geometry"])
        source_local = transform(to_local.transform, source_wgs84)
        aoi_local = source_local.buffer(buffer_m)
        aoi_wgs84 = transform(to_wgs84.transform, aoi_local)
        local_bounds = aoi_local.bounds
        wgs84_bounds = aoi_wgs84.bounds
        rank = int(properties["candidate_rank"])
        file_stem = properties.get(
            "imagery_file_stem",
            f"site_{rank:02d}_{properties['admin_code']}",
        )

        requests.append(
            {
                "id": properties["id"],
                "candidate_rank": rank,
                "admin_code": properties["admin_code"],
                "admin_name": properties["admin_name"],
                "river_name": properties["river_name"],
                "river_class": properties.get("river_class", "small"),
                "width_class": properties["width_class"],
                "width_proxy_m": properties["width_proxy_m"],
                "file_stem": file_stem,
                "buffer_m": buffer_m,
                "aoi_bbox_epsg5174": [round(value, 3) for value in local_bounds],
                "aoi_bbox_wgs84": [round(value, 7) for value in wgs84_bounds],
                "aoi_geometry_wgs84": mapping(aoi_wgs84),
                "files": {
                    "pan": f"data/imagery/raw/{file_stem}_pan.tif",
                    "rgb": f"data/imagery/raw/{file_stem}_rgb.tif",
                },
            }
        )
    return requests


def write_aoi_geojson(path: Path, requests: List[Dict]) -> None:
    features = []
    for request in requests:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    key: request[key]
                    for key in (
                        "id",
                        "candidate_rank",
                        "admin_code",
                        "admin_name",
                        "river_name",
                        "river_class",
                        "width_class",
                        "width_proxy_m",
                        "buffer_m",
                    )
                },
                "geometry": request["aoi_geometry_wgs84"],
            }
        )
    path.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def write_request_csv(path: Path, requests: List[Dict]) -> None:
    fieldnames = [
        "candidate_rank",
        "admin_name",
        "river_name",
        "river_class",
        "buffer_m",
        "min_lon",
        "min_lat",
        "max_lon",
        "max_lat",
        "min_x_5174",
        "min_y_5174",
        "max_x_5174",
        "max_y_5174",
        "pan_filename",
        "rgb_filename",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for request in requests:
            min_lon, min_lat, max_lon, max_lat = request["aoi_bbox_wgs84"]
            min_x, min_y, max_x, max_y = request["aoi_bbox_epsg5174"]
            writer.writerow(
                {
                    "candidate_rank": request["candidate_rank"],
                    "admin_name": request["admin_name"],
                    "river_name": request["river_name"],
                    "river_class": request["river_class"],
                    "buffer_m": request["buffer_m"],
                    "min_lon": min_lon,
                    "min_lat": min_lat,
                    "max_lon": max_lon,
                    "max_lat": max_lat,
                    "min_x_5174": min_x,
                    "min_y_5174": min_y,
                    "max_x_5174": max_x,
                    "max_y_5174": max_y,
                    "pan_filename": Path(request["files"]["pan"]).name,
                    "rgb_filename": Path(request["files"]["rgb"]).name,
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create imagery request AOIs for selected river zones."
    )
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--buffer-m", type=float, default=200.0)
    parser.add_argument(
        "--all-features",
        action="store_true",
        help="Prepare requests for every feature without reading a selection file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.buffer_m <= 0:
        raise ValueError("--buffer-m must be positive.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    Path("data/imagery/raw").mkdir(parents=True, exist_ok=True)

    features = (
        load_all_features(args.candidates)
        if args.all_features
        else load_selected_features(args.selection, args.candidates)
    )
    requests = prepare_requests(features, args.buffer_m)
    write_aoi_geojson(args.output_dir / "selected_sites_aoi.geojson", requests)
    write_request_csv(args.output_dir / "imagery_request_bounds.csv", requests)

    manifest = {
        "version": 1,
        "source_crs": "EPSG:4326",
        "buffer_m": args.buffer_m,
        "site_count": len(requests),
        "sites": requests,
    }
    (args.output_dir / "imagery_requests.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "site_count": len(requests),
                "buffer_m": args.buffer_m,
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
