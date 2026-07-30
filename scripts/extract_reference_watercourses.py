#!/usr/bin/env python3
"""Extract local topographic watercourses around one validation candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from pyproj import Transformer
import shapefile
from shapely.geometry import mapping, shape
from shapely.ops import transform


DEFAULT_CANDIDATES = Path("data/processed/sample_candidates.geojson")
DEFAULT_OUTPUT = Path("data/reference/site_10_43150_watercourses.geojson")


def load_candidate(path: Path, candidate_rank: int) -> Dict:
    feature_collection = json.loads(path.read_text(encoding="utf-8"))
    matches = [
        feature
        for feature in feature_collection["features"]
        if feature["properties"]["candidate_rank"] == candidate_rank
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one candidate with rank {candidate_rank}, found {len(matches)}."
        )
    return matches[0]


def extract_watercourses(
    source_path: Path,
    candidate: Dict,
    max_distance_m: float,
) -> Dict:
    to_epsg5179 = Transformer.from_crs(
        "EPSG:4326", "EPSG:5179", always_xy=True
    )
    to_wgs84 = Transformer.from_crs(
        "EPSG:5179", "EPSG:4326", always_xy=True
    )
    zone = transform(to_epsg5179.transform, shape(candidate["geometry"]))
    search_bounds = tuple(
        value + offset
        for value, offset in zip(
            zone.bounds,
            (
                -max_distance_m,
                -max_distance_m,
                max_distance_m,
                max_distance_m,
            ),
        )
    )

    reader = shapefile.Reader(
        str(source_path),
        encoding="cp949",
        encodingErrors="replace",
    )
    features: List[Dict] = []
    intersecting_count = 0
    total_length_m = 0.0
    inside_zone_length_m = 0.0

    for shape_record in reader.iterShapeRecords(bbox=search_bounds):
        geometry = shape(shape_record.shape.__geo_interface__)
        distance_m = geometry.distance(zone)
        if distance_m > max_distance_m:
            continue

        inside_length_m = geometry.intersection(zone).length
        if inside_length_m > 0:
            intersecting_count += 1
        total_length_m += geometry.length
        inside_zone_length_m += inside_length_m
        properties = shape_record.record.as_dict()
        properties.update(
            {
                "distance_to_zone_m": round(distance_m, 3),
                "inside_zone_length_m": round(inside_length_m, 3),
            }
        )
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": mapping(
                    transform(to_wgs84.transform, geometry)
                ),
            }
        )

    candidate_properties = candidate["properties"]
    feature_count = len(features)
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": str(source_path),
            "source_crs": "EPSG:5179",
            "candidate_id": (
                f"{candidate_properties['admin_code']}:"
                f"{candidate_properties['river_name']}:"
                f"{candidate_properties['candidate_rank']}"
            ),
            "candidate_rank": candidate_properties["candidate_rank"],
            "max_distance_m": max_distance_m,
            "feature_count": feature_count,
            "intersecting_count": intersecting_count,
            "total_length_m": round(total_length_m, 3),
            "inside_zone_length_m": round(inside_zone_length_m, 3),
            "inside_length_ratio": round(
                inside_zone_length_m / total_length_m
                if total_length_m
                else 0.0,
                4,
            ),
        },
        "features": features,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract topographic watercourses around a candidate."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--candidate-rank", type=int, default=10)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--max-distance-m", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidate = load_candidate(args.candidates, args.candidate_rank)
    result = extract_watercourses(
        args.source,
        candidate,
        args.max_distance_m,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                **result["metadata"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
