# National Land Satellite GeoTIFF input

Download orthorectified imagery covering each AOI listed in
`data/imagery/requests/imagery_request_bounds.csv`.

Place the files in this directory using these exact names:

```text
site_01_43150_pan.tif  # 제천시 방도교천, PAN 0.5 m
site_01_43150_rgb.tif  # 제천시 방도교천, RGB 2 m
site_02_43730_pan.tif  # 옥천군 삼남천, PAN 0.5 m
site_02_43730_rgb.tif  # 옥천군 삼남천, RGB 2 m
site_05_43110_pan.tif  # 청주시 산막천, PAN 0.5 m
site_05_43110_rgb.tif  # 청주시 산막천, RGB 2 m
site_09_43770_pan.tif  # 음성군 오신천, PAN 0.5 m
site_09_43770_rgb.tif  # 음성군 오신천, RGB 2 m
site_10_43150_pan.tif  # 제천시 다락골천, PAN 0.5 m
site_10_43150_rgb.tif  # 제천시 다락골천, RGB 2 m
```

Do not convert GeoTIFFs to ordinary images before processing. The CRS and
geotransform metadata are required.

After adding any available files, run:

```bash
.venv/bin/python scripts/process_satellite_imagery.py
```

Reload the dashboard. Ready PAN/RGB buttons become enabled for each site.
