#!/usr/bin/env python3
"""Validate and prepare National Land Satellite GeoTIFFs for the dashboard."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds, from_bounds as transform_from_bounds
from rasterio.warp import reproject, transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds


DEFAULT_REQUESTS = Path("data/imagery/requests/imagery_requests.json")
DEFAULT_OUTPUT = Path("data/imagery/processed")
MAX_PREVIEW_DIMENSION = 4096


def clamp_window(window: Window, width: int, height: int) -> Window:
    col_start = max(0, math.floor(window.col_off))
    row_start = max(0, math.floor(window.row_off))
    col_end = min(width, math.ceil(window.col_off + window.width))
    row_end = min(height, math.ceil(window.row_off + window.height))
    if col_end <= col_start or row_end <= row_start:
        raise ValueError("AOI does not intersect the raster.")
    return Window(col_start, row_start, col_end - col_start, row_end - row_start)


def choose_indexes(dataset, mode: str) -> List[int]:
    if mode == "pan" or dataset.count == 1:
        return [1]

    color_map = {
        color: index
        for index, color in enumerate(dataset.colorinterp, start=1)
    }
    required = [ColorInterp.red, ColorInterp.green, ColorInterp.blue]
    if all(color in color_map for color in required):
        return [color_map[color] for color in required]
    return list(range(1, min(dataset.count, 3) + 1))


def scale_band(band: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    values = band[valid_mask & np.isfinite(band)]
    if values.size == 0:
        return np.zeros(band.shape, dtype=np.uint8)
    low, high = np.percentile(values, [2, 98])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low = float(values.min())
        high = float(values.max())
    if high <= low:
        return np.zeros(band.shape, dtype=np.uint8)
    scaled = np.clip((band.astype(np.float32) - low) / (high - low), 0, 1)
    return np.round(scaled * 255).astype(np.uint8)


def web_dimensions(window: Window) -> Sequence[int]:
    scale = max(window.width, window.height) / MAX_PREVIEW_DIMENSION
    if scale <= 1:
        return int(window.height), int(window.width)
    return (
        max(1, round(window.height / scale)),
        max(1, round(window.width / scale)),
    )


def process_raster(
    source_path: Path,
    output_path: Path,
    mode: str,
    aoi_geometry_wgs84: Dict,
) -> Dict:
    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError("GeoTIFF has no CRS.")

        aoi_dataset = transform_geom(
            "EPSG:4326",
            dataset.crs,
            aoi_geometry_wgs84,
            precision=9,
        )
        coordinates = aoi_dataset["coordinates"]

        def iter_coordinates(value):
            if isinstance(value[0], (int, float)):
                yield value
            else:
                for item in value:
                    yield from iter_coordinates(item)

        points = list(iter_coordinates(coordinates))
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        window = clamp_window(
            from_bounds(min(xs), min(ys), max(xs), max(ys), dataset.transform),
            dataset.width,
            dataset.height,
        )
        output_height, output_width = web_dimensions(window)
        indexes = choose_indexes(dataset, mode)
        data = dataset.read(
            indexes,
            window=window,
            out_shape=(len(indexes), output_height, output_width),
            resampling=Resampling.bilinear,
            masked=False,
        )
        output_transform = dataset.window_transform(window) * rasterio.Affine.scale(
            window.width / output_width,
            window.height / output_height,
        )
        valid_mask = geometry_mask(
            [aoi_dataset],
            out_shape=(output_height, output_width),
            transform=output_transform,
            invert=True,
        )
        if dataset.nodata is not None:
            valid_mask &= np.all(data != dataset.nodata, axis=0)
        valid_mask &= np.any(data != 0, axis=0)
        if not np.any(valid_mask):
            raise ValueError("AOI contains no valid raster pixels.")

        scaled_bands = [scale_band(band, valid_mask) for band in data]
        if len(scaled_bands) == 1:
            rgb = np.repeat(scaled_bands[0][..., np.newaxis], 3, axis=2)
        else:
            while len(scaled_bands) < 3:
                scaled_bands.append(scaled_bands[-1])
            rgb = np.stack(scaled_bands[:3], axis=2)
        alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
        rgba = np.dstack([rgb, alpha])

        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba).save(output_path, optimize=True)

        raster_bounds = array_bounds(output_height, output_width, output_transform)
        west, south, east, north = transform_bounds(
            dataset.crs,
            "EPSG:4326",
            *raster_bounds,
            densify_pts=21,
        )
        source_res = [abs(float(dataset.res[0])), abs(float(dataset.res[1]))]
        source_tags = dataset.tags()
        expected_max = 0.75 if mode == "pan" else 2.5
        resolution_warning = None
        if dataset.crs.is_projected and max(source_res) > expected_max:
            resolution_warning = (
                f"Source resolution {source_res} exceeds the expected "
                f"{expected_max} m threshold for {mode}."
            )

        return {
            "status": "ready",
            "source_path": str(source_path),
            "web_path": f"/{output_path.as_posix()}",
            "source_crs": str(dataset.crs),
            "source_size": [dataset.width, dataset.height],
            "source_band_count": dataset.count,
            "source_resolution": source_res,
            "source_dtype": list(dataset.dtypes),
            "georeferencing_status": source_tags.get(
                "GEOREFERENCING_STATUS", "verified"
            ),
            "georeferencing_method": source_tags.get("GEOREFERENCING_METHOD"),
            "georeferencing_rmse_m": (
                float(source_tags["GEOREFERENCING_RMSE_M"])
                if source_tags.get("GEOREFERENCING_RMSE_M")
                else None
            ),
            "preview_size": [output_width, output_height],
            "bounds_wgs84": [
                round(west, 9),
                round(south, 9),
                round(east, 9),
                round(north, 9),
            ],
            "resolution_warning": resolution_warning,
        }


def normalize_source_paths(value) -> List[Path]:
    if isinstance(value, str):
        return [Path(value)]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [Path(item) for item in value]
    raise ValueError("Imagery source must be a path or a path list.")


def process_raster_mosaic(
    source_paths: Sequence[Path],
    output_path: Path,
    mode: str,
    aoi_geometry_wgs84: Dict,
) -> Dict:
    datasets = [rasterio.open(path) for path in source_paths]
    try:
        target_crs = datasets[0].crs
        if target_crs is None:
            raise ValueError(f"{source_paths[0]} has no CRS.")
        if any(dataset.crs != target_crs for dataset in datasets):
            raise ValueError("All mosaic sources must use the same CRS.")

        aoi_target = transform_geom(
            "EPSG:4326",
            target_crs,
            aoi_geometry_wgs84,
            precision=9,
        )

        def iter_coordinates(value):
            if isinstance(value[0], (int, float)):
                yield value
            else:
                for item in value:
                    yield from iter_coordinates(item)

        points = list(iter_coordinates(aoi_target["coordinates"]))
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        west, south, east, north = min(xs), min(ys), max(xs), max(ys)
        source_resolution = min(
            min(abs(float(dataset.res[0])), abs(float(dataset.res[1])))
            for dataset in datasets
        )
        nominal_window = Window(
            0,
            0,
            max(1, (east - west) / source_resolution),
            max(1, (north - south) / source_resolution),
        )
        output_height, output_width = web_dimensions(nominal_window)
        output_transform = transform_from_bounds(
            west,
            south,
            east,
            north,
            output_width,
            output_height,
        )

        source_indexes = [choose_indexes(dataset, mode) for dataset in datasets]
        band_count = max(len(indexes) for indexes in source_indexes)
        output_dtype = np.result_type(
            *[np.dtype(dataset.dtypes[0]) for dataset in datasets]
        )
        data = np.zeros(
            (band_count, output_height, output_width),
            dtype=output_dtype,
        )

        for dataset, indexes in zip(datasets, source_indexes):
            for output_index, source_index in enumerate(indexes):
                temporary = np.zeros(
                    (output_height, output_width),
                    dtype=output_dtype,
                )
                reproject(
                    source=rasterio.band(dataset, source_index),
                    destination=temporary,
                    src_transform=dataset.transform,
                    src_crs=dataset.crs,
                    src_nodata=dataset.nodata if dataset.nodata is not None else 0,
                    dst_transform=output_transform,
                    dst_crs=target_crs,
                    dst_nodata=0,
                    resampling=Resampling.bilinear,
                )
                fill_mask = (data[output_index] == 0) & (temporary != 0)
                data[output_index][fill_mask] = temporary[fill_mask]

        valid_mask = geometry_mask(
            [aoi_target],
            out_shape=(output_height, output_width),
            transform=output_transform,
            invert=True,
        )
        valid_mask &= np.any(data != 0, axis=0)
        if not np.any(valid_mask):
            raise ValueError("AOI contains no valid raster pixels.")

        scaled_bands = [scale_band(band, valid_mask) for band in data]
        if len(scaled_bands) == 1:
            rgb = np.repeat(scaled_bands[0][..., np.newaxis], 3, axis=2)
        else:
            while len(scaled_bands) < 3:
                scaled_bands.append(scaled_bands[-1])
            rgb = np.stack(scaled_bands[:3], axis=2)
        alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
        rgba = np.dstack([rgb, alpha])

        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba).save(output_path, optimize=True)

        raster_bounds = array_bounds(
            output_height,
            output_width,
            output_transform,
        )
        out_west, out_south, out_east, out_north = transform_bounds(
            target_crs,
            "EPSG:4326",
            *raster_bounds,
            densify_pts=21,
        )
        source_tags = [dataset.tags() for dataset in datasets]
        rmse_values = [
            float(tags["GEOREFERENCING_RMSE_M"])
            for tags in source_tags
            if tags.get("GEOREFERENCING_RMSE_M")
        ]
        expected_max = 0.75 if mode == "pan" else 2.5
        resolution_warning = None
        if target_crs.is_projected and source_resolution > expected_max:
            resolution_warning = (
                f"Source resolution {source_resolution} exceeds the expected "
                f"{expected_max} m threshold for {mode}."
            )

        return {
            "status": "ready",
            "source_path": [str(path) for path in source_paths],
            "web_path": f"/{output_path.as_posix()}",
            "source_crs": str(target_crs),
            "source_size": [
                [dataset.width, dataset.height] for dataset in datasets
            ],
            "source_band_count": band_count,
            "source_resolution": [source_resolution, source_resolution],
            "source_dtype": [str(output_dtype)],
            "georeferencing_status": (
                "reconstructed"
                if any(
                    tags.get("GEOREFERENCING_STATUS") == "reconstructed"
                    for tags in source_tags
                )
                else "verified"
            ),
            "georeferencing_method": "CAS500 scene VRT mosaic",
            "georeferencing_rmse_m": max(rmse_values) if rmse_values else None,
            "preview_size": [output_width, output_height],
            "bounds_wgs84": [
                round(out_west, 9),
                round(out_south, 9),
                round(out_east, 9),
                round(out_north, 9),
            ],
            "resolution_warning": resolution_warning,
        }
    finally:
        for dataset in datasets:
            dataset.close()


def process_manifest(request_path: Path, output_dir: Path) -> Dict:
    request_manifest = json.loads(request_path.read_text(encoding="utf-8"))
    result_sites = []

    for site in request_manifest["sites"]:
        result_site = {
            key: site[key]
            for key in (
                "id",
                "candidate_rank",
                "admin_code",
                "admin_name",
                "river_name",
                "width_class",
                "width_proxy_m",
                "aoi_bbox_wgs84",
            )
        }
        result_site["river_class"] = site.get("river_class", "small")
        result_site["imagery_sources"] = site.get("imagery_sources", [])
        result_site["file_stem"] = site.get(
            "file_stem",
            f"site_{int(site['candidate_rank']):02d}_{site['admin_code']}",
        )
        result_site["layers"] = {}

        for mode in ("pan", "rgb"):
            try:
                source_paths = normalize_source_paths(site["files"][mode])
            except ValueError as error:
                result_site["layers"][mode] = {
                    "status": "error",
                    "error": str(error),
                }
                continue
            if not source_paths:
                result_site["layers"][mode] = {
                    "status": "out_of_coverage",
                    "source_path": [],
                }
                continue
            missing_paths = [path for path in source_paths if not path.exists()]
            if missing_paths:
                result_site["layers"][mode] = {
                    "status": "missing",
                    "source_path": [str(path) for path in source_paths],
                    "missing_paths": [str(path) for path in missing_paths],
                }
                continue
            output_path = (
                output_dir
                / result_site["file_stem"]
                / f"{mode}.png"
            )
            try:
                if len(source_paths) == 1:
                    layer = process_raster(
                        source_paths[0],
                        output_path,
                        mode,
                        site["aoi_geometry_wgs84"],
                    )
                else:
                    layer = process_raster_mosaic(
                        source_paths,
                        output_path,
                        mode,
                        site["aoi_geometry_wgs84"],
                    )
                result_site["layers"][mode] = layer
            except Exception as error:
                result_site["layers"][mode] = {
                    "status": "error",
                    "source_path": [str(path) for path in source_paths],
                    "error": str(error),
                }

        reference_value = site.get("reference_watercourses")
        if reference_value:
            reference_path = Path(reference_value)
            if reference_path.exists():
                reference_data = json.loads(
                    reference_path.read_text(encoding="utf-8")
                )
                result_site["reference_watercourses"] = {
                    "status": "ready",
                    "web_path": f"/{reference_path.as_posix()}",
                    **reference_data.get("metadata", {}),
                }
            else:
                result_site["reference_watercourses"] = {
                    "status": "missing",
                    "source_path": str(reference_path),
                }
        result_sites.append(result_site)

    ready_layers = sum(
        layer["status"] == "ready"
        for site in result_sites
        for layer in site["layers"].values()
    )
    result = {
        "version": 1,
        "site_count": len(result_sites),
        "ready_layers": ready_layers,
        "expected_layers": len(result_sites) * 2,
        "sites": result_sites,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "imagery_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare PAN/RGB GeoTIFFs for the validation dashboard."
    )
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = process_manifest(args.requests, args.output_dir)
    print(
        json.dumps(
            {
                "site_count": result["site_count"],
                "ready_layers": result["ready_layers"],
                "expected_layers": result["expected_layers"],
                "manifest": str(args.output_dir / "imagery_manifest.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
