#!/usr/bin/env python3
"""Prepare explicit Chungbuk small-river zones for satellite validation."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Sequence, Tuple

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform, unary_union


ADMIN_NAMES = {
    "43110": "청주시",
    "43130": "충주시",
    "43150": "제천시",
    "43720": "보은군",
    "43730": "옥천군",
    "43740": "영동군",
    "43745": "증평군",
    "43750": "진천군",
    "43760": "괴산군",
    "43770": "음성군",
    "43800": "단양군",
}

DEFAULT_SOURCE = Path("data/LSMD_CONT_UJ301_5174_43_202607")
DEFAULT_OUTPUT = Path("data/processed")


@dataclass(frozen=True)
class DbfField:
    name: str
    field_type: str
    length: int
    decimals: int


@dataclass
class ZoneRecord:
    index: int
    properties: Dict[str, str]
    geometry: Polygon | MultiPolygon


def read_dbf(path: Path, encoding: str = "euc-kr") -> List[Dict[str, str]]:
    """Read DBF rows while preserving record order for SHP alignment."""
    with path.open("rb") as handle:
        header = handle.read(32)
        if len(header) != 32:
            raise ValueError(f"Invalid DBF header: {path}")

        record_count = struct.unpack("<I", header[4:8])[0]
        header_length = struct.unpack("<H", header[8:10])[0]
        record_length = struct.unpack("<H", header[10:12])[0]
        fields: List[DbfField] = []

        while True:
            descriptor = handle.read(32)
            if not descriptor:
                raise ValueError(f"Missing DBF field terminator: {path}")
            if descriptor[0] == 0x0D:
                break
            fields.append(
                DbfField(
                    name=descriptor[:11].split(b"\0", 1)[0].decode("ascii"),
                    field_type=chr(descriptor[11]),
                    length=descriptor[16],
                    decimals=descriptor[17],
                )
            )

        handle.seek(header_length)
        rows: List[Dict[str, str]] = []
        for _ in range(record_count):
            raw_record = handle.read(record_length)
            if len(raw_record) != record_length:
                raise ValueError(f"Unexpected end of DBF records: {path}")

            row: Dict[str, str] = {"_DELETED": raw_record[:1] == b"*"}
            offset = 1
            for field in fields:
                raw_value = raw_record[offset : offset + field.length]
                offset += field.length
                row[field.name] = raw_value.decode(encoding, errors="replace").strip()
            rows.append(row)
        return rows


def polygonal_only(geometry):
    """Discard non-polygonal parts produced by geometry repair."""
    if geometry.is_empty:
        return None
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = [
            item
            for item in geometry.geoms
            if isinstance(item, (Polygon, MultiPolygon)) and not item.is_empty
        ]
        return unary_union(polygons) if polygons else None
    return None


def rings_to_geometry(rings: Sequence[Sequence[Tuple[float, float]]]):
    """Build polygonal geometry from Shapefile rings using parity semantics."""
    geometry = None
    for ring in rings:
        if len(ring) < 4:
            continue
        coordinates = list(ring)
        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])
        if len(set(coordinates[:-1])) < 3:
            continue

        part = polygonal_only(make_valid(Polygon(coordinates)))
        if part is None:
            continue
        geometry = part if geometry is None else geometry.symmetric_difference(part)

    if geometry is None:
        return None
    return polygonal_only(make_valid(geometry))


def parse_polygon_content(content: bytes):
    if len(content) < 44:
        return None
    shape_type = struct.unpack("<i", content[:4])[0]
    if shape_type == 0:
        return None
    if shape_type not in {5, 15, 25}:
        raise ValueError(f"Unsupported Shapefile shape type: {shape_type}")

    part_count, point_count = struct.unpack("<2i", content[36:44])
    parts_offset = 44
    points_offset = parts_offset + (part_count * 4)
    required_length = points_offset + (point_count * 16)
    if part_count <= 0 or point_count <= 0 or len(content) < required_length:
        return None

    part_starts = list(struct.unpack(f"<{part_count}i", content[parts_offset:points_offset]))
    points = [
        struct.unpack("<2d", content[points_offset + (index * 16) : points_offset + ((index + 1) * 16)])
        for index in range(point_count)
    ]
    part_starts.append(point_count)
    rings = [points[start:end] for start, end in zip(part_starts, part_starts[1:])]
    return rings_to_geometry(rings)


def read_shp(path: Path) -> Iterator:
    with path.open("rb") as handle:
        header = handle.read(100)
        if len(header) != 100 or struct.unpack(">i", header[:4])[0] != 9994:
            raise ValueError(f"Invalid Shapefile header: {path}")

        while True:
            record_header = handle.read(8)
            if not record_header:
                return
            if len(record_header) != 8:
                raise ValueError(f"Invalid Shapefile record header: {path}")
            _, content_words = struct.unpack(">2i", record_header)
            content = handle.read(content_words * 2)
            if len(content) != content_words * 2:
                raise ValueError(f"Unexpected end of Shapefile records: {path}")
            yield parse_polygon_content(content)


def is_explicit_zone(alias: str, remark: str) -> bool:
    combined = f"{alias} {remark}"
    return "예정지" not in combined and "구역" in alias


def normalize_river_name(alias: str, remark: str) -> str:
    name = remark.strip() or alias.strip()
    name = re.sub(r"^\s*\d{2,3}\s*[_-]\s*", "", name)
    name = re.sub(r"[\s_]*소하천구역\s*$", "", name)
    name = re.sub(r"\s*구역\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip(" _-")
    return name or "이름없음"


def load_zone_records(source: Path) -> Tuple[List[ZoneRecord], Counter]:
    dbf_rows = read_dbf(source.with_suffix(".dbf"))
    shp_geometries = list(read_shp(source.with_suffix(".shp")))
    if len(dbf_rows) != len(shp_geometries):
        raise ValueError(
            f"DBF/SHP record mismatch: {len(dbf_rows)} attributes, "
            f"{len(shp_geometries)} geometries"
        )

    reasons: Counter = Counter()
    records: List[ZoneRecord] = []
    for index, (properties, geometry) in enumerate(zip(dbf_rows, shp_geometries), start=1):
        alias = properties.get("ALIAS", "").strip()
        remark = properties.get("REMARK", "").strip()

        if properties.get("_DELETED"):
            reasons["deleted"] += 1
            continue
        if "예정지" in f"{alias} {remark}":
            reasons["planned_area"] += 1
            continue
        if not is_explicit_zone(alias, remark):
            reasons["ambiguous_zone_type"] += 1
            continue
        if not remark:
            reasons["missing_river_name"] += 1
            continue
        if geometry is None or geometry.is_empty:
            reasons["invalid_or_empty_geometry"] += 1
            continue

        properties = {key: value for key, value in properties.items() if key != "_DELETED"}
        properties["RIVER_NAME"] = normalize_river_name(alias, remark)
        records.append(ZoneRecord(index=index, properties=properties, geometry=geometry))
        reasons["included"] += 1
    return records, reasons


def dissolve_by_river(records: Iterable[ZoneRecord]) -> List[Dict]:
    grouped: Dict[Tuple[str, str], List[ZoneRecord]] = defaultdict(list)
    for record in records:
        key = (
            record.properties.get("COL_ADM_SE", ""),
            record.properties["RIVER_NAME"],
        )
        grouped[key].append(record)

    zones: List[Dict] = []
    for (admin_code, river_name), items in grouped.items():
        geometry = polygonal_only(make_valid(unary_union([item.geometry for item in items])))
        if geometry is None:
            continue

        area_m2 = float(geometry.area)
        perimeter_m = float(geometry.length)
        width_proxy_m = (2.0 * area_m2 / perimeter_m) if perimeter_m else 0.0
        min_x, min_y, max_x, max_y = geometry.bounds
        dates = [
            item.properties.get("NTFDATE", "")
            for item in items
            if item.properties.get("NTFDATE", "")
        ]
        zones.append(
            {
                "admin_code": admin_code,
                "admin_name": ADMIN_NAMES.get(admin_code, admin_code or "미상"),
                "river_name": river_name,
                "source_record_count": len(items),
                "source_record_ids": [item.properties.get("MNUM", "") for item in items],
                "latest_notice_date": max(dates) if dates else "",
                "area_m2": area_m2,
                "perimeter_m": perimeter_m,
                "width_proxy_m": width_proxy_m,
                "bbox_width_m": max_x - min_x,
                "bbox_height_m": max_y - min_y,
                "geometry": geometry,
            }
        )
    return zones


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def assign_width_classes(zones: List[Dict]) -> Tuple[float, float]:
    usable_widths = [
        zone["width_proxy_m"]
        for zone in zones
        if zone["area_m2"] >= 500 and zone["width_proxy_m"] > 0
    ]
    q25 = percentile(usable_widths, 0.25)
    q75 = percentile(usable_widths, 0.75)
    for zone in zones:
        width = zone["width_proxy_m"]
        if width <= q25:
            zone["width_class"] = "narrow"
        elif width >= q75:
            zone["width_class"] = "wide"
        else:
            zone["width_class"] = "medium"
    return q25, q75


def select_candidates(zones: List[Dict], count: int) -> List[Dict]:
    """Select a diverse review pool; imagery coverage decides the final five."""
    eligible = [
        zone
        for zone in zones
        if zone["area_m2"] >= 500
        and zone["bbox_width_m"] >= 20
        and zone["bbox_height_m"] >= 20
    ]
    by_class: Dict[str, List[Dict]] = defaultdict(list)
    for zone in eligible:
        by_class[zone["width_class"]].append(zone)
    for items in by_class.values():
        items.sort(key=lambda item: (item["area_m2"], item["perimeter_m"]), reverse=True)

    pattern = ["wide", "medium", "narrow", "wide", "medium"]
    selected: List[Dict] = []
    used_keys = set()
    used_admins = set()

    while len(selected) < count:
        width_class = pattern[len(selected) % len(pattern)]
        pool = by_class.get(width_class, [])
        remaining = [
            zone
            for zone in pool
            if (zone["admin_code"], zone["river_name"]) not in used_keys
        ]
        if not remaining:
            remaining = [
                zone
                for zone in eligible
                if (zone["admin_code"], zone["river_name"]) not in used_keys
            ]
        if not remaining:
            break

        diverse = [zone for zone in remaining if zone["admin_code"] not in used_admins]
        chosen = (diverse or remaining)[0]
        selected.append(dict(chosen))
        used_keys.add((chosen["admin_code"], chosen["river_name"]))
        used_admins.add(chosen["admin_code"])

    for rank, zone in enumerate(selected, start=1):
        zone["candidate_rank"] = rank
    return selected


def round_coordinates(value, digits: int = 7):
    if isinstance(value, (list, tuple)):
        return [round_coordinates(item, digits) for item in value]
    if isinstance(value, float):
        return round(value, digits)
    return value


def zone_properties(zone: Mapping, transformer: Transformer) -> Dict:
    centroid = transform(transformer.transform, zone["geometry"]).representative_point()
    return {
        "admin_code": zone["admin_code"],
        "admin_name": zone["admin_name"],
        "river_name": zone["river_name"],
        "source_record_count": zone["source_record_count"],
        "latest_notice_date": zone["latest_notice_date"],
        "area_m2": round(zone["area_m2"], 2),
        "perimeter_m": round(zone["perimeter_m"], 2),
        "width_proxy_m": round(zone["width_proxy_m"], 2),
        "width_class": zone["width_class"],
        "center_lon": round(centroid.x, 7),
        "center_lat": round(centroid.y, 7),
        **(
            {"candidate_rank": zone["candidate_rank"]}
            if "candidate_rank" in zone
            else {}
        ),
    }


def write_geojson(path: Path, zones: Sequence[Dict], transformer: Transformer) -> None:
    features = []
    for zone in zones:
        geometry_wgs84 = transform(transformer.transform, zone["geometry"])
        geometry_mapping = mapping(geometry_wgs84)
        geometry_mapping["coordinates"] = round_coordinates(geometry_mapping["coordinates"])
        rounded_geometry = polygonal_only(make_valid(shape(geometry_mapping)))
        if rounded_geometry is None:
            raise ValueError(
                f"Geometry became empty after coordinate conversion: "
                f"{zone['admin_name']} {zone['river_name']}"
            )
        geometry_mapping = mapping(rounded_geometry)
        features.append(
            {
                "type": "Feature",
                "properties": zone_properties(zone, transformer),
                "geometry": geometry_mapping,
            }
        )
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_candidate_csv(path: Path, zones: Sequence[Dict], transformer: Transformer) -> None:
    fieldnames = [
        "candidate_rank",
        "admin_code",
        "admin_name",
        "river_name",
        "width_class",
        "width_proxy_m",
        "area_m2",
        "perimeter_m",
        "source_record_count",
        "latest_notice_date",
        "center_lon",
        "center_lat",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for zone in zones:
            writer.writerow(zone_properties(zone, transformer))


def write_zone_inventory_csv(
    path: Path, zones: Sequence[Dict], transformer: Transformer
) -> None:
    fieldnames = [
        "admin_code",
        "admin_name",
        "river_name",
        "width_class",
        "width_proxy_m",
        "area_m2",
        "perimeter_m",
        "source_record_count",
        "latest_notice_date",
        "center_lon",
        "center_lat",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for zone in sorted(
            zones, key=lambda item: (item["admin_code"], item["river_name"])
        ):
            writer.writerow(zone_properties(zone, transformer))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare explicit small-river zones and sample candidates."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Shapefile path without an extension.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for generated GeoJSON, CSV, and metadata.",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=10,
        help="Number of review candidates to recommend.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sample_count < 1:
        raise ValueError("--sample-count must be at least 1")
    for suffix in (".shp", ".dbf"):
        if not args.source.with_suffix(suffix).exists():
            raise FileNotFoundError(args.source.with_suffix(suffix))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    records, filter_counts = load_zone_records(args.source)
    zones = dissolve_by_river(records)
    q25, q75 = assign_width_classes(zones)
    candidates = select_candidates(zones, args.sample_count)
    transformer = Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True)

    write_geojson(args.output_dir / "river_zones.geojson", zones, transformer)
    write_geojson(
        args.output_dir / "sample_candidates.geojson", candidates, transformer
    )
    write_zone_inventory_csv(
        args.output_dir / "river_zone_inventory.csv", zones, transformer
    )
    write_candidate_csv(
        args.output_dir / "sample_candidates.csv", candidates, transformer
    )

    metadata = {
        "source": str(args.source),
        "source_crs": "EPSG:5174",
        "output_crs": "EPSG:4326",
        "filter_policy": {
            "exclude": "ALIAS or REMARK contains 예정지",
            "include": "ALIAS contains 구역 and REMARK is not blank",
            "river_name_source": "normalized REMARK, falling back to ALIAS",
        },
        "filter_counts": dict(sorted(filter_counts.items())),
        "explicit_zone_records": len(records),
        "dissolved_river_zones": len(zones),
        "recommended_candidates": len(candidates),
        "width_proxy_formula": "2 * polygon_area / polygon_perimeter",
        "width_class_thresholds_m": {
            "narrow_max": round(q25, 3),
            "wide_min": round(q75, 3),
        },
    }
    (args.output_dir / "preparation_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
