#!/usr/bin/env python3
"""Register downloaded CAS500 scenes and connect them to imagery requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np
from pyproj import Transformer
import rasterio
from shapely.geometry import Polygon, mapping, shape


DEFAULT_OUTPUT = Path("data/imagery/scenes")
DEFAULT_REQUESTS = Path("data/imagery/requests/regional/imagery_requests.json")
SAMPLE_MAX_DIMENSION = 8000
MIN_AOI_COVERAGE_RATIO = 0.02


def ransac_line(
    x_values: np.ndarray,
    y_values: np.ndarray,
    quantile_low: float,
    quantile_high: float,
    tolerance: float,
) -> np.ndarray:
    selection = (x_values >= np.quantile(x_values, quantile_low)) & (
        x_values <= np.quantile(x_values, quantile_high)
    )
    x_values = x_values[selection].astype(float)
    y_values = y_values[selection].astype(float)
    rng = np.random.default_rng(5179)
    best_mask = None
    best_count = -1

    for _ in range(800):
        first, second = rng.integers(0, len(x_values), 2)
        if first == second or abs(x_values[second] - x_values[first]) < 50:
            continue
        slope = (y_values[second] - y_values[first]) / (
            x_values[second] - x_values[first]
        )
        intercept = y_values[first] - slope * x_values[first]
        mask = (
            np.abs(y_values - (slope * x_values + intercept))
            < tolerance
        )
        count = int(mask.sum())
        if count > best_count:
            best_count = count
            best_mask = mask

    if best_mask is None or best_count < 20:
        raise ValueError("Could not fit a valid image boundary.")

    for _ in range(4):
        coefficients = np.polyfit(
            x_values[best_mask],
            y_values[best_mask],
            1,
        )
        residuals = np.abs(
            y_values - np.polyval(coefficients, x_values)
        )
        best_mask = residuals < tolerance
    return coefficients


def fit_valid_edges(pan_path: Path) -> dict:
    with rasterio.open(pan_path) as dataset:
        scale = max(
            dataset.width / SAMPLE_MAX_DIMENSION,
            dataset.height / SAMPLE_MAX_DIMENSION,
            1,
        )
        sample_width = max(1, round(dataset.width / scale))
        sample_height = max(1, round(dataset.height / scale))
        sample = dataset.read(
            1,
            out_shape=(sample_height, sample_width),
        )
        valid = sample > 0
        if int(valid.sum()) < 100:
            raise ValueError(f"{pan_path} contains too few valid pixels.")

        columns = np.where(valid.any(axis=0))[0]
        top = np.array([np.argmax(valid[:, column]) for column in columns])
        bottom = np.array(
            [
                sample_height
                - 1
                - np.argmax(valid[::-1, column])
                for column in columns
            ]
        )
        rows = np.where(valid.any(axis=1))[0]
        left = np.array([np.argmax(valid[row]) for row in rows])
        right = np.array(
            [
                sample_width
                - 1
                - np.argmax(valid[row, ::-1])
                for row in rows
            ]
        )

        edges = {
            "top": ransac_line(columns, top, 0.05, 0.95, 3),
            "bottom": ransac_line(columns, bottom, 0.05, 0.95, 5),
            "left": ransac_line(rows, left, 0.25, 0.98, 8),
            "right": ransac_line(rows, right, 0.02, 0.75, 8),
        }
        x_scale = dataset.width / sample_width
        y_scale = dataset.height / sample_height

        for edge_name in ("top", "bottom"):
            slope, intercept = edges[edge_name]
            edges[edge_name] = np.array(
                [y_scale * slope / x_scale, y_scale * intercept]
            )
        for edge_name in ("left", "right"):
            slope, intercept = edges[edge_name]
            edges[edge_name] = np.array(
                [x_scale * slope / y_scale, x_scale * intercept]
            )
        edges["width"] = dataset.width
        edges["height"] = dataset.height
        return edges


def xml_coordinate(element) -> tuple:
    return (
        float(element.findtext("Longitude")),
        float(element.findtext("Latitude")),
    )


def scene_metadata(aux_path: Path) -> dict:
    root = ET.parse(aux_path).getroot()
    pan = root.find("./Image/PAN")
    if pan is None:
        raise ValueError(f"PAN metadata is missing in {aux_path}.")
    coordinates = pan.find("ImagingCoordinates")
    keys = ("TL", "TC", "TR", "BL", "BC", "BR")
    geographic = {
        key: xml_coordinate(coordinates.find(f"ImageGeog{key}"))
        for key in keys
    }
    footprint = Polygon(
        [
            geographic["TL"],
            geographic["TR"],
            geographic["BR"],
            geographic["BL"],
        ]
    )
    return {
        "root": root,
        "pan": pan,
        "geographic": geographic,
        "footprint": footprint,
        "date_acquired": root.findtext("./General/DateAcquired"),
        "orbit_number": root.findtext("./General/OrbitNumber"),
        "image_gsd": float(pan.findtext("./ImageGSD/Column")),
    }


def intersect_edges(horizontal: np.ndarray, vertical: np.ndarray) -> np.ndarray:
    horizontal_slope, horizontal_intercept = horizontal
    vertical_slope, vertical_intercept = vertical
    column = (
        vertical_slope * horizontal_intercept + vertical_intercept
    ) / (1 - vertical_slope * horizontal_slope)
    row = horizontal_slope * column + horizontal_intercept
    return np.array([column, row])


def fit_grid_transform(metadata: dict, edges: dict) -> dict:
    transformer = Transformer.from_crs(
        "EPSG:4326",
        "EPSG:5179",
        always_xy=True,
    )
    projected = {
        key: np.asarray(transformer.transform(*coordinate))
        for key, coordinate in metadata["geographic"].items()
    }
    resolution = metadata["image_gsd"]
    pixel_coordinates = {}

    for edge_name, start_key, middle_key, end_key in (
        ("top", "TL", "TC", "TR"),
        ("bottom", "BL", "BC", "BR"),
    ):
        rough_start = intersect_edges(edges[edge_name], edges["left"])
        rough_end = intersect_edges(edges[edge_name], edges["right"])
        center = (rough_start + rough_end) / 2
        slope = edges[edge_name][0]
        direction = np.array([1, slope])
        direction /= np.linalg.norm(direction)
        ground_vector = projected[end_key] - projected[start_key]
        pixel_length = np.linalg.norm(ground_vector) / resolution
        start_pixel = center - direction * pixel_length / 2
        end_pixel = center + direction * pixel_length / 2
        middle_fraction = np.dot(
            projected[middle_key] - projected[start_key],
            ground_vector,
        ) / np.dot(ground_vector, ground_vector)
        pixel_coordinates[start_key] = start_pixel
        pixel_coordinates[middle_key] = (
            start_pixel
            + (end_pixel - start_pixel) * middle_fraction
        )
        pixel_coordinates[end_key] = end_pixel

    keys = ("TL", "TC", "TR", "BL", "BC", "BR")
    matrix = np.asarray(
        [
            [1, pixel_coordinates[key][0], pixel_coordinates[key][1]]
            for key in keys
        ]
    )
    x_coefficients = np.linalg.lstsq(
        matrix,
        np.asarray([projected[key][0] for key in keys]),
        rcond=None,
    )[0]
    y_coefficients = np.linalg.lstsq(
        matrix,
        np.asarray([projected[key][1] for key in keys]),
        rcond=None,
    )[0]
    residuals = []
    for row, key in zip(matrix, keys):
        predicted = np.array(
            [
                np.dot(x_coefficients, row),
                np.dot(y_coefficients, row),
            ]
        )
        residuals.append(np.linalg.norm(predicted - projected[key]))

    return {
        "transform": [
            float(x_coefficients[0]),
            float(x_coefficients[1]),
            float(x_coefficients[2]),
            float(y_coefficients[0]),
            float(y_coefficients[1]),
            float(y_coefficients[2]),
        ],
        "resolution": resolution,
        "control_rmse_m": float(
            np.sqrt(np.mean(np.square(residuals)))
        ),
    }


def vrt_text(
    sources: list,
    width: int,
    height: int,
    geo_transform: list,
    scene_id: str,
    rmse_m: float,
) -> str:
    bands = []
    for band_number, (source_path, color) in enumerate(sources, start=1):
        bands.append(
            f"""  <VRTRasterBand dataType="UInt16" band="{band_number}">
    <ColorInterp>{color}</ColorInterp>
    <NoDataValue>0</NoDataValue>
    <SimpleSource>
      <SourceFilename relativeToVRT="0">{escape(str(source_path))}</SourceFilename>
      <SourceBand>1</SourceBand>
    </SimpleSource>
  </VRTRasterBand>"""
        )
    return (
        f"""<VRTDataset rasterXSize="{width}" rasterYSize="{height}">
  <SRS dataAxisToSRSAxisMapping="1,2">EPSG:5179</SRS>
  <GeoTransform>{",".join(f"{value:.9f}" for value in geo_transform)}</GeoTransform>
  <Metadata>
    <MDI key="GEOREFERENCING_STATUS">reconstructed</MDI>
    <MDI key="GEOREFERENCING_METHOD">CAS500 auxiliary footprint and length-corrected valid-edge affine fit</MDI>
    <MDI key="GEOREFERENCING_RMSE_M">{rmse_m:.3f}</MDI>
    <MDI key="SCENE_ID">{escape(scene_id)}</MDI>
  </Metadata>
"""
        + "\n".join(bands)
        + "\n</VRTDataset>\n"
    )


def register_scene(aux_path: Path, output_dir: Path) -> dict:
    suffix = "_L2G_Aux.xml"
    if not aux_path.name.endswith(suffix):
        raise ValueError(f"Unexpected auxiliary filename: {aux_path.name}")
    scene_id = aux_path.name[: -len(suffix)]
    base_path = aux_path.with_name(f"{scene_id}_L2G")
    source_paths = {
        "pan": Path(f"{base_path}_P.tif"),
        "red": Path(f"{base_path}_R.tif"),
        "green": Path(f"{base_path}_G.tif"),
        "blue": Path(f"{base_path}_B.tif"),
    }
    missing = [path for path in source_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing scene files: " + ", ".join(str(path) for path in missing)
        )

    metadata = scene_metadata(aux_path)
    edges = fit_valid_edges(source_paths["pan"])
    grid = fit_grid_transform(metadata, edges)
    with rasterio.open(source_paths["pan"]) as pan_dataset:
        pan_size = (pan_dataset.width, pan_dataset.height)
    with rasterio.open(source_paths["red"]) as red_dataset:
        rgb_size = (red_dataset.width, red_dataset.height)

    output_dir.mkdir(parents=True, exist_ok=True)
    pan_vrt = output_dir / f"{scene_id}_pan.vrt"
    rgb_vrt = output_dir / f"{scene_id}_rgb.vrt"
    pan_vrt.write_text(
        vrt_text(
            [(source_paths["pan"].resolve(), "Gray")],
            *pan_size,
            grid["transform"],
            scene_id,
            grid["control_rmse_m"],
        ),
        encoding="utf-8",
    )
    rgb_transform = [
        grid["transform"][0],
        grid["transform"][1] * 4,
        grid["transform"][2] * 4,
        grid["transform"][3],
        grid["transform"][4] * 4,
        grid["transform"][5] * 4,
    ]
    rgb_vrt.write_text(
        vrt_text(
            [
                (source_paths["red"].resolve(), "Red"),
                (source_paths["green"].resolve(), "Green"),
                (source_paths["blue"].resolve(), "Blue"),
            ],
            *rgb_size,
            rgb_transform,
            scene_id,
            grid["control_rmse_m"],
        ),
        encoding="utf-8",
    )
    return {
        "scene_id": scene_id,
        "date_acquired": metadata["date_acquired"],
        "orbit_number": metadata["orbit_number"],
        "footprint_wgs84": mapping(metadata["footprint"]),
        "pan_vrt": pan_vrt.as_posix(),
        "rgb_vrt": rgb_vrt.as_posix(),
        "geo_transform_epsg5179": grid["transform"],
        "resolution_m": grid["resolution"],
        "control_rmse_m": grid["control_rmse_m"],
    }


def connect_scenes(request_path: Path, scenes: list) -> dict:
    request_data = json.loads(request_path.read_text(encoding="utf-8"))
    for site in request_data["sites"]:
        aoi = shape(site["aoi_geometry_wgs84"])
        selected = []
        for scene in scenes:
            footprint = shape(scene["footprint_wgs84"])
            coverage_ratio = footprint.intersection(aoi).area / aoi.area
            if coverage_ratio >= MIN_AOI_COVERAGE_RATIO:
                selected.append((scene, coverage_ratio))
        site["files"] = {
            "pan": [scene["pan_vrt"] for scene, _ in selected],
            "rgb": [scene["rgb_vrt"] for scene, _ in selected],
        }
        site["imagery_sources"] = [
            {
                "scene_id": scene["scene_id"],
                "date_acquired": scene["date_acquired"],
                "control_rmse_m": round(scene["control_rmse_m"], 3),
                "aoi_coverage_ratio": round(coverage_ratio, 6),
            }
            for scene, coverage_ratio in selected
        ]
    request_path.write_text(
        json.dumps(request_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return request_data


def write_registry(output_dir: Path, scenes: list) -> None:
    (output_dir / "scene_registry.json").write_text(
        json.dumps(
            {"version": 1, "scene_count": len(scenes), "scenes": scenes},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    features = [
        {
            "type": "Feature",
            "properties": {
                key: scene[key]
                for key in (
                    "scene_id",
                    "date_acquired",
                    "orbit_number",
                    "control_rmse_m",
                )
            },
            "geometry": scene["footprint_wgs84"],
        }
        for scene in scenes
    ]
    (output_dir / "scene_footprints.geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register downloaded CAS500 PAN/RGB scenes."
    )
    parser.add_argument("aux_files", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scenes = [
        register_scene(aux_path.resolve(), args.output_dir)
        for aux_path in args.aux_files
    ]
    write_registry(args.output_dir, scenes)
    request_data = connect_scenes(args.requests, scenes)
    print(
        json.dumps(
            {
                "scene_count": len(scenes),
                "site_source_counts": {
                    site["id"]: len(site["imagery_sources"])
                    for site in request_data["sites"]
                },
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
