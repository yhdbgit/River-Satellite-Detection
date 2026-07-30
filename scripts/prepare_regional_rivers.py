#!/usr/bin/env python3
"""Extract Bogangcheon and Baekgokcheon control zones from UJ201."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Sequence

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import mapping
from shapely.ops import transform

from prepare_river_zones import (
    ADMIN_NAMES,
    polygonal_only,
    read_dbf,
    read_shp,
    round_coordinates,
)


DEFAULT_SOURCE = Path(
    "data/regional_rivers/LSMD_CONT_UJ201_5174_43_202607"
)
DEFAULT_OUTPUT = Path("data/processed")
TARGET_ORDER = {"보강천": 0, "백곡천": 1}
SMALL_RIVER_WIDE_THRESHOLD_M = 15.781


def target_name(properties: Dict[str, str]) -> str | None:
    for field in ("REMARK", "ALIAS"):
        value = properties.get(field, "").strip()
        if value in TARGET_ORDER:
            return value
    return None


def load_target_zones(source: Path) -> List[Dict]:
    rows = read_dbf(source.with_suffix(".dbf"))
    geometries = list(read_shp(source.with_suffix(".shp")))
    if len(rows) != len(geometries):
        raise ValueError(
            f"DBF/SHP record mismatch: {len(rows)} attributes, "
            f"{len(geometries)} geometries"
        )

    zones = []
    for source_index, (properties, geometry) in enumerate(
        zip(rows, geometries), start=1
    ):
        river_name = target_name(properties)
        if properties.get("_DELETED") or river_name is None or geometry is None:
            continue

        geometry = polygonal_only(make_valid(geometry))
        if geometry is None or geometry.is_empty:
            continue

        admin_code = properties.get("COL_ADM_SE", "").strip()
        area_m2 = float(geometry.area)
        perimeter_m = float(geometry.length)
        width_proxy_m = 2 * area_m2 / perimeter_m if perimeter_m else 0.0
        zones.append(
            {
                "admin_code": admin_code,
                "admin_name": ADMIN_NAMES.get(admin_code, admin_code or "미상"),
                "river_name": river_name,
                "source_record_count": 1,
                "source_record_id": properties.get("MNUM", ""),
                "source_index": source_index,
                "latest_notice_date": properties.get("NTFDATE", ""),
                "area_m2": area_m2,
                "perimeter_m": perimeter_m,
                "width_proxy_m": width_proxy_m,
                "width_class": (
                    "wide"
                    if width_proxy_m >= SMALL_RIVER_WIDE_THRESHOLD_M
                    else "medium"
                ),
                "river_class": "regional",
                "geometry": geometry,
            }
        )

    zones.sort(
        key=lambda zone: (
            TARGET_ORDER[zone["river_name"]],
            zone["admin_code"],
            zone["source_index"],
        )
    )
    per_river_counts: Dict[str, int] = {}
    for rank, zone in enumerate(zones, start=1):
        river_name = zone["river_name"]
        per_river_counts[river_name] = per_river_counts.get(river_name, 0) + 1
        segment_index = per_river_counts[river_name]
        zone["candidate_rank"] = rank
        zone["segment_index"] = segment_index
        zone["id"] = (
            f"regional:{zone['admin_code']}:{river_name}:{segment_index}"
        )
        zone["imagery_file_stem"] = (
            f"regional_{rank:02d}_{zone['admin_code']}"
        )

    expected = {"보강천": 4, "백곡천": 1}
    actual = {
        name: sum(zone["river_name"] == name for zone in zones)
        for name in expected
    }
    if actual != expected:
        raise ValueError(f"Unexpected target counts: expected {expected}, got {actual}")
    return zones


def feature_properties(zone: Dict, to_wgs84: Transformer) -> Dict:
    geometry_wgs84 = transform(to_wgs84.transform, zone["geometry"])
    center = geometry_wgs84.representative_point()
    return {
        "id": zone["id"],
        "candidate_rank": zone["candidate_rank"],
        "segment_index": zone["segment_index"],
        "admin_code": zone["admin_code"],
        "admin_name": zone["admin_name"],
        "river_name": zone["river_name"],
        "river_class": zone["river_class"],
        "source_record_count": zone["source_record_count"],
        "latest_notice_date": zone["latest_notice_date"],
        "area_m2": round(zone["area_m2"], 2),
        "perimeter_m": round(zone["perimeter_m"], 2),
        "width_proxy_m": round(zone["width_proxy_m"], 2),
        "width_class": zone["width_class"],
        "center_lon": round(center.x, 7),
        "center_lat": round(center.y, 7),
        "imagery_file_stem": zone["imagery_file_stem"],
    }


def write_geojson(
    path: Path, zones: Sequence[Dict], to_wgs84: Transformer
) -> None:
    features = []
    for zone in zones:
        geometry_wgs84 = transform(to_wgs84.transform, zone["geometry"])
        geometry = mapping(geometry_wgs84)
        geometry["coordinates"] = round_coordinates(geometry["coordinates"])
        features.append(
            {
                "type": "Feature",
                "properties": feature_properties(zone, to_wgs84),
                "geometry": geometry,
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


def write_csv(path: Path, zones: Sequence[Dict], to_wgs84: Transformer) -> None:
    fieldnames = [
        "candidate_rank",
        "river_name",
        "segment_index",
        "admin_code",
        "admin_name",
        "river_class",
        "width_proxy_m",
        "area_m2",
        "latest_notice_date",
        "center_lon",
        "center_lat",
        "imagery_file_stem",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for zone in zones:
            properties = feature_properties(zone, to_wgs84)
            writer.writerow({key: properties[key] for key in fieldnames})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract five regional-river control zones from UJ201."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    zones = load_target_zones(args.source)
    to_wgs84 = Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True)

    geojson_path = args.output_dir / "regional_river_controls.geojson"
    csv_path = args.output_dir / "regional_river_controls.csv"
    metadata_path = args.output_dir / "regional_river_metadata.json"
    write_geojson(geojson_path, zones, to_wgs84)
    write_csv(csv_path, zones, to_wgs84)
    metadata_path.write_text(
        json.dumps(
            {
                "source": str(args.source),
                "source_crs": "EPSG:5174",
                "output_crs": "EPSG:4326",
                "target_names": list(TARGET_ORDER),
                "feature_count": len(zones),
                "counts": {
                    name: sum(zone["river_name"] == name for zone in zones)
                    for name in TARGET_ORDER
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "feature_count": len(zones),
                "geojson": str(geojson_path),
                "csv": str(csv_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
