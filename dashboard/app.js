const WIDTH_LABELS = {
  wide: "넓음",
  medium: "중간",
  narrow: "좁음",
};

const WIDTH_COLORS = {
  wide: "#d45b3f",
  medium: "#2f6f96",
  narrow: "#37845d",
};

const REGIONAL_COLORS = {
  보강천: "#a35b32",
  백곡천: "#287387",
};

const state = {
  features: [],
  datasetMode: "regional",
  selected: new Set(),
  activeId: null,
  layers: new Map(),
  markers: new Map(),
  geoJsonLayer: null,
  imageryBySite: new Map(),
  imageryMode: "base",
  imageryOpacity: 0.82,
  imageryOverlay: null,
  referenceVisible: true,
  referenceOverlay: null,
  referenceCache: new Map(),
};

const map = L.map("map", {
  zoomControl: true,
  preferCanvas: true,
  minZoom: 7,
}).setView([36.72, 127.72], 9);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const listElement = document.querySelector("#candidate-list");
const controlsElement = document.querySelector(".controls");
const searchElement = document.querySelector("#candidate-search");
const widthFilterElement = document.querySelector("#width-filter");
const selectedCountElement = document.querySelector("#selected-count");
const selectionCountElement = document.querySelector(".selection-count");
const saveButton = document.querySelector("#save-selection");
const saveStatus = document.querySelector("#save-status");
const detailElement = document.querySelector("#site-detail");
const legendElement = document.querySelector(".legend");
const widthFilterField = document.querySelector(".select-field");
const datasetModeButtons = [
  ...document.querySelectorAll("[data-dataset-mode]"),
];
const imageryModeButtons = [
  ...document.querySelectorAll("[data-imagery-mode]"),
];
const imageryOpacityElement = document.querySelector("#imagery-opacity");
const referenceWatercoursesElement = document.querySelector(
  "#reference-watercourses",
);

function featureId(feature) {
  const properties = feature.properties;
  return (
    properties.id ||
    `${properties.admin_code}:${properties.river_name}:${properties.candidate_rank}`
  );
}

function featureDataset(feature) {
  return feature.properties.river_class === "regional" ? "regional" : "small";
}

function featureColor(feature) {
  const properties = feature.properties;
  return properties.river_class === "regional"
    ? REGIONAL_COLORS[properties.river_name] || "#5d6970"
    : WIDTH_COLORS[properties.width_class];
}

function formatNumber(value, digits = 1) {
  return Number(value).toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function styleForFeature(feature) {
  const id = featureId(feature);
  const color = featureColor(feature);
  const selected = state.selected.has(id);
  const active = state.activeId === id;
  return {
    color: active || selected ? "#11181d" : color,
    weight: active ? 4 : selected ? 3 : 2,
    fillColor: color,
    fillOpacity: active ? 0.38 : selected ? 0.3 : 0.2,
    opacity: 1,
  };
}

function refreshLayerStyles() {
  state.layers.forEach((layer, id) => {
    const feature = state.features.find((item) => featureId(item) === id);
    if (feature) {
      layer.setStyle(styleForFeature(feature));
      const marker = state.markers.get(id);
      if (marker) {
        marker.setIcon(markerIcon(feature));
      }
    }
  });
}

function markerIcon(feature) {
  const id = featureId(feature);
  const properties = feature.properties;
  const color = featureColor(feature);
  const classNames = [
    "candidate-marker",
    properties.river_class === "regional"
      ? "candidate-marker-regional"
      : `candidate-marker-${properties.width_class}`,
    state.selected.has(id) ? "selected" : "",
    state.activeId === id ? "active" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return L.divIcon({
    className: "",
    html: `<span class="${classNames}" style="--marker-color:${color}">${properties.candidate_rank}</span>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });
}

function filteredFeatures() {
  const query = searchElement.value.trim().toLowerCase();
  const widthFilter = widthFilterElement.value;
  return state.features.filter((feature) => {
    const properties = feature.properties;
    const searchText = `${properties.river_name} ${properties.admin_name}`.toLowerCase();
    const matchesQuery = !query || searchText.includes(query);
    const matchesWidth =
      widthFilter === "all" || properties.width_class === widthFilter;
    return (
      featureDataset(feature) === state.datasetMode &&
      matchesQuery &&
      (state.datasetMode === "regional" || matchesWidth)
    );
  });
}

function updateMapVisibility(visibleFeatures) {
  const visibleIds = new Set(visibleFeatures.map(featureId));
  state.layers.forEach((layer, id) => {
    const marker = state.markers.get(id);
    if (visibleIds.has(id)) {
      if (!map.hasLayer(layer)) {
        layer.addTo(map);
      }
      if (marker && !map.hasLayer(marker)) {
        marker.addTo(map);
      }
    } else {
      if (map.hasLayer(layer)) {
        map.removeLayer(layer);
      }
      if (marker && map.hasLayer(marker)) {
        map.removeLayer(marker);
      }
    }
  });
}

function renderCandidateList() {
  const features = filteredFeatures();
  if (!features.length) {
    listElement.innerHTML = '<div class="empty-state">조건에 맞는 후보가 없습니다.</div>';
    updateMapVisibility([]);
    return;
  }

  listElement.innerHTML = features
    .map((feature) => {
      const properties = feature.properties;
      const id = featureId(feature);
      const checked = state.selected.has(id);
      const active = state.activeId === id;
      const color = featureColor(feature);
      const isRegional = properties.river_class === "regional";
      const classLabel = isRegional
        ? "지방하천"
        : WIDTH_LABELS[properties.width_class];
      return `
        <button
          class="candidate-item${active ? " active" : ""}"
          type="button"
          data-feature-id="${id}"
          style="--class-color:${color}"
          aria-pressed="${active}"
        >
          <span class="candidate-rank">${String(properties.candidate_rank).padStart(2, "0")}</span>
          <span class="candidate-copy">
            <strong>${properties.river_name}</strong>
            <span class="candidate-meta">
              <span>${properties.admin_name}</span>
              <span class="width-label">${classLabel}</span>
              ${isRegional ? `<span>구역 ${String(properties.segment_index).padStart(2, "0")}</span>` : ""}
              <span>폭 추정 ${formatNumber(properties.width_proxy_m)}m</span>
            </span>
          </span>
          ${
            isRegional
              ? '<span class="candidate-kind">REG</span>'
              : `<input
                  class="candidate-check"
                  type="checkbox"
                  data-select-id="${id}"
                  aria-label="${properties.river_name} 검증 후보 선택"
                  ${checked ? "checked" : ""}
                />`
          }
        </button>
      `;
    })
    .join("");

  listElement.querySelectorAll(".candidate-item").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (event.target.matches(".candidate-check")) {
        return;
      }
      focusFeature(button.dataset.featureId);
    });
  });

  listElement.querySelectorAll(".candidate-check").forEach((checkbox) => {
    checkbox.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    checkbox.addEventListener("change", () => {
      toggleSelection(checkbox.dataset.selectId, checkbox.checked);
    });
  });

  updateMapVisibility(features);
}

function renderDetail(feature) {
  const properties = feature.properties;
  const color = featureColor(feature);
  const isRegional = properties.river_class === "regional";
  const imagery = state.imageryBySite.get(featureId(feature));
  const panReady = imagery?.layers?.pan?.status === "ready";
  const rgbReady = imagery?.layers?.rgb?.status === "ready";
  const reconstructedLayer = Object.values(imagery?.layers || {}).find(
    (layer) =>
      layer?.status === "ready" &&
      layer?.georeferencing_status === "reconstructed",
  );
  const provisional = Object.values(imagery?.layers || {}).some(
    (layer) =>
      layer?.status === "ready" &&
      layer?.georeferencing_status === "provisional",
  );
  const georeferencingLabel = reconstructedLayer
    ? ` · 복원 좌표 RMSE ${formatNumber(reconstructedLayer.georeferencing_rmse_m, 2)}m`
    : provisional
      ? " · 잠정 좌표"
      : "";
  const reference = imagery?.reference_watercourses;
  const sourceCoverage = Math.max(
    0,
    ...(imagery?.imagery_sources || []).map(
      (source) => Number(source.aoi_coverage_ratio) || 0,
    ),
  );
  const coverageLabel =
    sourceCoverage > 0
      ? ` · 500m 검토범위 ${Math.round(sourceCoverage * 100)}% ${sourceCoverage >= 0.95 ? "관측" : "부분 관측"}`
      : "";
  const layers = Object.values(imagery?.layers || {});
  const outOfCoverage =
    layers.length > 0 &&
    layers.every((layer) => layer?.status === "out_of_coverage");
  const imageryStatus = panReady || rgbReady
    ? `PAN ${panReady ? "연결" : "미연결"} · RGB ${rgbReady ? "연결" : "미연결"}${coverageLabel}${georeferencingLabel}`
    : outOfCoverage
      ? "다운로드 영상 범위 밖"
      : "해당 지역 GeoTIFF 미확보";
  const referenceStatus =
    reference?.status === "ready"
      ? `${reference.intersecting_count}/${reference.feature_count} 교차 · 내부 ${formatNumber(reference.inside_zone_length_m / 1000, 2)}km`
      : "기준자료 없음";
  detailElement.innerHTML = `
    <div class="detail-content" style="--class-color:${color}">
      <div class="detail-heading">
        <div>
          <h2>${properties.river_name}</h2>
          <p>${properties.admin_name} · ${isRegional ? "구역" : "후보"} ${String(properties.candidate_rank).padStart(2, "0")}</p>
        </div>
        <span class="detail-class">${isRegional ? "지방하천" : WIDTH_LABELS[properties.width_class]}</span>
      </div>
      <dl class="detail-metrics">
        <div>
          <dt>폭 추정</dt>
          <dd>${formatNumber(properties.width_proxy_m)} m</dd>
        </div>
        <div>
          <dt>구역 면적</dt>
          <dd>${formatNumber(properties.area_m2, 0)} ㎡</dd>
        </div>
        <div>
          <dt>고시일</dt>
          <dd>${properties.latest_notice_date || "미기재"}</dd>
        </div>
      </dl>
      <div class="imagery-status">
        <span>국토위성 영상</span>
        <strong>${imageryStatus}</strong>
      </div>
      <div class="imagery-status">
        <span>기준 수계선</span>
        <strong>${referenceStatus}</strong>
      </div>
    </div>
  `;
}

function activeFeature() {
  return state.features.find((feature) => featureId(feature) === state.activeId);
}

function readyLayer(feature, mode) {
  if (!feature || mode === "base") {
    return null;
  }
  const imagery = state.imageryBySite.get(featureId(feature));
  const layer = imagery?.layers?.[mode];
  return layer?.status === "ready" ? layer : null;
}

function updateImageryControls() {
  const feature = activeFeature();
  imageryModeButtons.forEach((button) => {
    const mode = button.dataset.imageryMode;
    button.disabled = mode !== "base" && !readyLayer(feature, mode);
    button.classList.toggle("active", state.imageryMode === mode);
  });
  imageryOpacityElement.disabled = state.imageryMode === "base";
}

function fitInspectionBounds(bounds, maxZoom = 15) {
  const size = map.getSize();
  const rightPadding = Math.min(
    100,
    Math.max(24, Math.round(size.x * 0.12)),
  );
  const bottomPadding = Math.min(
    120,
    Math.max(24, Math.round(size.y * 0.12)),
  );
  map.fitBounds(bounds, {
    paddingTopLeft: [32, 32],
    paddingBottomRight: [rightPadding, bottomPadding],
    maxZoom,
  });
}

function showImageryMode(mode) {
  if (state.imageryOverlay) {
    map.removeLayer(state.imageryOverlay);
    state.imageryOverlay = null;
  }

  const layer = readyLayer(activeFeature(), mode);
  state.imageryMode = layer ? mode : "base";
  if (layer) {
    const [west, south, east, north] = layer.bounds_wgs84;
    state.imageryOverlay = L.imageOverlay(
      layer.web_path,
      [
        [south, west],
        [north, east],
      ],
      {
        opacity: state.imageryOpacity,
        interactive: false,
      },
    ).addTo(map);
    fitInspectionBounds(
      [
        [south, west],
        [north, east],
      ],
      16,
    );
  }
  updateImageryControls();
}

async function updateReferenceOverlay() {
  if (state.referenceOverlay) {
    map.removeLayer(state.referenceOverlay);
    state.referenceOverlay = null;
  }

  const activeId = state.activeId;
  const reference =
    state.imageryBySite.get(activeId)?.reference_watercourses;
  if (
    !state.referenceVisible ||
    reference?.status !== "ready" ||
    !reference.web_path
  ) {
    return;
  }

  try {
    let featureCollection = state.referenceCache.get(reference.web_path);
    if (!featureCollection) {
      const response = await fetch(reference.web_path);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      featureCollection = await response.json();
      state.referenceCache.set(reference.web_path, featureCollection);
    }
    if (state.activeId !== activeId || !state.referenceVisible) {
      return;
    }
    state.referenceOverlay = L.geoJSON(featureCollection, {
      style: {
        color: "#00bfd2",
        weight: 3,
        opacity: 0.95,
      },
      interactive: false,
    }).addTo(map);
  } catch {
    referenceWatercoursesElement.checked = false;
  }
}

function updateReferenceControl() {
  const reference =
    state.imageryBySite.get(state.activeId)?.reference_watercourses;
  const ready = reference?.status === "ready";
  referenceWatercoursesElement.disabled = !ready;
  referenceWatercoursesElement.checked = ready && state.referenceVisible;
  updateReferenceOverlay();
}

function focusFeature(id) {
  const feature = state.features.find((item) => featureId(item) === id);
  const layer = state.layers.get(id);
  if (!feature || !layer) {
    return;
  }
  state.activeId = id;
  layer.addTo(map);
  fitInspectionBounds(layer.getBounds());
  renderDetail(feature);
  renderCandidateList();
  refreshLayerStyles();
  showImageryMode(state.imageryMode);
  updateReferenceControl();
}

function toggleSelection(id, shouldSelect) {
  if (shouldSelect && state.selected.size >= 5) {
    saveStatus.textContent = "최대 5곳까지 선택할 수 있습니다.";
    renderCandidateList();
    return;
  }
  if (shouldSelect) {
    state.selected.add(id);
  } else {
    state.selected.delete(id);
  }
  updateSelectionUi();
  renderCandidateList();
  refreshLayerStyles();
}

function updateSelectionUi() {
  const count = state.selected.size;
  const isRegional = state.datasetMode === "regional";
  selectionCountElement.hidden = isRegional;
  saveButton.hidden = isRegional;
  widthFilterField.hidden = isRegional;
  controlsElement.classList.toggle("regional-mode", isRegional);
  selectedCountElement.textContent = String(count);
  saveButton.disabled = count !== 5;
  saveStatus.textContent = isRegional
    ? "지방하천 비교 구역 5곳"
    : count === 5
      ? "5곳이 선택되었습니다. 결과를 저장하세요."
      : `최종 검증지 ${5 - count}곳을 더 선택하세요.`;
}

function fitAllCandidates() {
  const layers = filteredFeatures()
    .map((feature) => state.layers.get(featureId(feature)))
    .filter(Boolean);
  if (!layers.length) {
    return;
  }
  map.invalidateSize({ pan: false });
  map.fitBounds(L.featureGroup(layers).getBounds(), { padding: [32, 32] });
}

async function loadSavedSelection() {
  try {
    const response = await fetch("/api/selections");
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    const validIds = new Set(state.features.map(featureId));
    (payload.selected_ids || []).forEach((id) => {
      if (validIds.has(id)) {
        state.selected.add(id);
      }
    });
  } catch {
    saveStatus.textContent = "이전 선택 결과를 불러오지 못했습니다.";
  }
}

async function loadImageryManifests() {
  const paths = [
    "/data/imagery/processed/imagery_manifest.json",
    "/data/imagery/processed/regional/imagery_manifest.json",
  ];
  await Promise.all(
    paths.map(async (path) => {
      try {
        const response = await fetch(path, { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        (payload.sites || []).forEach((site) => {
          state.imageryBySite.set(site.id, site);
        });
      } catch {
        // One missing manifest must not hide imagery from the other dataset.
      }
    }),
  );
}

function updateDatasetUi() {
  datasetModeButtons.forEach((button) => {
    button.classList.toggle(
      "active",
      button.dataset.datasetMode === state.datasetMode,
    );
  });
  if (state.datasetMode === "regional") {
    legendElement.innerHTML = `
      <span><i class="legend-swatch regional-bogang"></i>보강천 4구역</span>
      <span><i class="legend-swatch regional-baekgok"></i>백곡천 1구역</span>
    `;
  } else {
    legendElement.innerHTML = `
      <span><i class="legend-swatch wide"></i>넓음</span>
      <span><i class="legend-swatch medium"></i>중간</span>
      <span><i class="legend-swatch narrow"></i>좁음</span>
    `;
  }
  updateSelectionUi();
}

function setDatasetMode(mode) {
  if (!["small", "regional"].includes(mode) || state.datasetMode === mode) {
    return;
  }
  state.datasetMode = mode;
  state.activeId = null;
  showImageryMode("base");
  updateReferenceControl();
  detailElement.innerHTML = `
    <div class="detail-empty">
      <span class="detail-index">${mode === "regional" ? "01—05" : "01—10"}</span>
      <p>지도 또는 목록에서 구역을 선택하세요.</p>
    </div>
  `;
  updateDatasetUi();
  renderCandidateList();
  refreshLayerStyles();
  requestAnimationFrame(fitAllCandidates);
}

async function loadFeatureCollections() {
  const sources = [
    {
      path: "/data/processed/sample_candidates.geojson",
      riverClass: "small",
    },
    {
      path: "/data/processed/regional_river_controls.geojson",
      riverClass: "regional",
    },
  ];
  const featureCollections = await Promise.all(
    sources.map(async (source) => {
      const response = await fetch(source.path, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${source.path}`);
      }
      const payload = await response.json();
      payload.features.forEach((feature) => {
        feature.properties.river_class =
          feature.properties.river_class || source.riverClass;
      });
      return payload;
    }),
  );
  return {
    type: "FeatureCollection",
    features: featureCollections.flatMap((payload) => payload.features),
  };
}

async function initialize() {
  try {
    const data = await loadFeatureCollections();
    addFeatureLayers(data);
    await Promise.all([loadSavedSelection(), loadImageryManifests()]);
    updateDatasetUi();
    renderCandidateList();
    refreshLayerStyles();
    requestAnimationFrame(() => requestAnimationFrame(fitAllCandidates));
  } catch {
    listElement.innerHTML =
      '<div class="empty-state">후보 데이터를 불러오지 못했습니다.</div>';
    saveStatus.textContent = "대시보드 서버를 다시 실행하세요.";
  }
}

async function saveSelection() {
  if (state.selected.size !== 5) {
    return;
  }
  saveButton.disabled = true;
  saveStatus.textContent = "선택 결과를 저장하는 중입니다.";
  const selectedFeatures = state.features
    .filter((feature) => state.selected.has(featureId(feature)))
    .map((feature) => ({
      id: featureId(feature),
      ...feature.properties,
    }));

  try {
    const response = await fetch("/api/selections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_ids: [...state.selected],
        sites: selectedFeatures,
      }),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    saveStatus.textContent = "선택 결과가 저장되었습니다.";
  } catch {
    saveStatus.textContent = "저장에 실패했습니다. 서버 실행 상태를 확인하세요.";
  } finally {
    saveButton.disabled = state.selected.size !== 5;
  }
}

function addFeatureLayers(featureCollection) {
  state.features = [...featureCollection.features].sort(
    (a, b) => a.properties.candidate_rank - b.properties.candidate_rank,
  );

  state.geoJsonLayer = L.geoJSON(featureCollection, {
    style: styleForFeature,
    onEachFeature(feature, layer) {
      const id = featureId(feature);
      state.layers.set(id, layer);
      layer.bindTooltip(
        `${feature.properties.river_name} · ${feature.properties.admin_name}`,
        {
          className: "river-tooltip",
          sticky: true,
        },
      );
      layer.on("click", () => focusFeature(id));

      const marker = L.marker(
        [feature.properties.center_lat, feature.properties.center_lon],
        {
          icon: markerIcon(feature),
          keyboard: true,
          title: `${feature.properties.river_name} 후보 ${feature.properties.candidate_rank}`,
        },
      ).addTo(map);
      marker.bindTooltip(
        `${feature.properties.river_name} · ${feature.properties.admin_name}`,
        {
          className: "river-tooltip",
          direction: "top",
          offset: [0, -12],
        },
      );
      marker.on("click", () => focusFeature(id));
      state.markers.set(id, marker);
    },
  }).addTo(map);

  map.fitBounds(state.geoJsonLayer.getBounds(), { padding: [32, 32] });
}

searchElement.addEventListener("input", renderCandidateList);
widthFilterElement.addEventListener("change", renderCandidateList);
saveButton.addEventListener("click", saveSelection);
datasetModeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setDatasetMode(button.dataset.datasetMode);
  });
});
imageryModeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    showImageryMode(button.dataset.imageryMode);
  });
});
imageryOpacityElement.addEventListener("input", () => {
  state.imageryOpacity = Number(imageryOpacityElement.value) / 100;
  if (state.imageryOverlay) {
    state.imageryOverlay.setOpacity(state.imageryOpacity);
  }
});
referenceWatercoursesElement.addEventListener("change", () => {
  state.referenceVisible = referenceWatercoursesElement.checked;
  updateReferenceOverlay();
});
document.querySelector("#fit-candidates").addEventListener("click", () => {
  fitAllCandidates();
});
window.addEventListener("resize", () => {
  requestAnimationFrame(() => map.invalidateSize({ pan: false }));
});

initialize();
