# Chungbuk Small-River Satellite Validation

This workspace prepares legal small-river zone polygons for comparison with
National Land Satellite imagery.

## Stage 1: prepare review candidates

The source layer mixes explicit small-river zones, planned areas, named
features, and blank aliases. The first validation stage uses a conservative
filter:

- exclude records whose `ALIAS` or `REMARK` contains `예정지`;
- include records whose `ALIAS` contains `구역`;
- require a non-empty `REMARK`;
- use a normalized `REMARK` as the river name.

Run:

```bash
python3 scripts/prepare_river_zones.py
```

Generated files:

- `data/processed/river_zones.geojson`: explicit zones dissolved by river;
- `data/processed/river_zone_inventory.csv`: all eligible river zones;
- `data/processed/sample_candidates.geojson`: ten review candidates;
- `data/processed/sample_candidates.csv`: candidate metrics and coordinates;
- `data/processed/preparation_metadata.json`: filters and record counts.

The width value is a polygon-based proxy (`2 * area / perimeter`), not a
surveyed channel width. Satellite coverage and visual inspection determine the
final five validation sites.

Run unit tests:

```bash
python3 -m unittest discover -s tests -v
```

## Stage 2: review and select five sites

Start the local dashboard:

```bash
python3 scripts/run_dashboard.py
```

Open `http://127.0.0.1:8765/dashboard/`. The dashboard displays the ten review
candidates, filters them by name and width class, and saves exactly five sites
to `config/selected_sites.json`.

## Stage 3: connect National Land Satellite imagery

Generate 200 m imagery request areas for the five saved sites:

```bash
python3 scripts/prepare_imagery_requests.py
```

Use `data/imagery/requests/imagery_request_bounds.csv` when searching the
National Geographic Information Platform. Download orthorectified GeoTIFFs and
place them in `data/imagery/raw` using the filenames listed in that CSV.

Create a project environment and install the imagery dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/process_satellite_imagery.py
```

PAN and RGB previews are written to `data/imagery/processed`. Missing files are
recorded as `missing`; they do not stop the other sites from processing.

The downloaded `site_10` scene currently has no CRS or transform embedded in
its TIFF headers. Its VRT files use a reconstructed EPSG:5179 affine transform
fitted from the auxiliary XML footprint and robust regressions of all four
valid-data edges. The fit has a 3.553 m control-point RMSE. This is sufficient
for prototype validation against the local topographic watercourse layer, but
supplier-provided GeoTIFF/world-file georeferencing remains a production
requirement.

## Regional-river comparison controls

The UJ201 river-zone layer is also available as a comparison dataset. Generate
the four Bogangcheon administrative records and one Baekgokcheon record:

```bash
python3 scripts/prepare_regional_rivers.py
python3 scripts/prepare_imagery_requests.py \
  --all-features \
  --candidates data/processed/regional_river_controls.geojson \
  --output-dir data/imagery/requests/regional \
  --buffer-m 500
.venv/bin/python scripts/register_cas500_scenes.py \
  /path/to/C1_*_L2G_Aux.xml
.venv/bin/python scripts/process_satellite_imagery.py \
  --requests data/imagery/requests/regional/imagery_requests.json \
  --output-dir data/imagery/processed/regional
```

The scene registration command finds the matching PAN/R/G/B files beside each
auxiliary XML, reconstructs their georeferencing, and connects only scenes that
overlap each 500 m inspection area. The dashboard enables PAN/RGB for covered
sites and labels uncovered sites as `다운로드 영상 범위 밖`; an uncovered site
does not stop the remaining sites from processing.

The currently registered January 27 scene provides partial PAN/RGB coverage for
the Cheongju and Jincheon Bogangcheon controls and the Jincheon Baekgokcheon
control. The Jeungpyeong and Goesan Bogangcheon controls are outside the
downloaded scene footprints, so their imagery controls intentionally remain
disabled.

Extract the local reference watercourses before processing the imagery:

```bash
.venv/bin/python scripts/extract_reference_watercourses.py \
  /path/to/N3L_C0050000/N3L_C0050000.shp
```

## Data attribution

- River-zone data: National Geographic Information Institute land-use
  regulation spatial data (`LSMD_CONT_UJ301`, `LSMD_CONT_UJ201`).
- Satellite imagery: National Land Satellite Center / National Geographic
  Information Institute, downloaded through the National Geographic
  Information Platform.

This repository does not include the multi-gigabyte source GeoTIFFs. It includes
web-sized validation previews and metadata derived from the downloaded scenes.
Check the public-work license marker attached to each downloaded product before
redistributing imagery outside this validation project.
