import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import countries110m from 'world-atlas/countries-110m.json';
import { feature } from 'topojson-client';
import {
  buildGlobalMarketGlobeMarkers,
  buildTerminatorPath,
  computeSubsolarPoint,
} from './globalMarketsGlobeUtils.js';

const globeCountries = feature(countries110m, countries110m.objects.countries).features.map((country) => ({
  ...country,
  globeCentroid: getFeatureCentroid(country),
}));
const Globe = lazy(() => import('react-globe.gl'));
const UTC_CLOCK_INTERVAL_MS = 60000;
const TERMINATOR_ALTITUDE = 0.014;
const GLOBE_DESKTOP_HEIGHT = 520;
const GLOBE_MOBILE_HEIGHT = 360;
const GLOBE_MOBILE_QUERY = '(max-width: 760px)';
const GLOBE_IDLE_RESUME_MS = 5000;
const GLOBE_CAMERA_VIEW = { lat: 12, lng: 105, altitude: 2.25 };
const GLOBE_CAMERA_MIN_DISTANCE = 155;
const GLOBE_CAMERA_MAX_DISTANCE = 520;
const CAMERA_INTERACTION_DELTA = 0.45;
const CAMERA_INTERACTION_INTENT_MS = 1200;
const GLOBE_HOVER_INTENT_MS = 280;
const GLOBE_LAYER_TRANSITION_MS = 0;
const GLOBE_POINT_ALTITUDE = 0;
const GLOBE_UNAVAILABLE_POINT_ALTITUDE = 0;
const GLOBE_POINT_RADIUS = 0.12;
const GLOBE_LABEL_ALTITUDE = 0.08;
const GLOBE_LABEL_SIZE = 0.72;
const GLOBE_LABEL_DOT_RADIUS = 0.22;
const GLOBE_DOM_LABEL_ALTITUDE = 0.12;
const GLOBE_DOM_LABEL_FRAME_MS = 33;
const GLOBE_DOM_LABEL_MIN_WIDTH = 132;
const GLOBE_DOM_LABEL_MAX_WIDTH = 240;
const GLOBE_DOM_LABEL_HEIGHT = 28;
const GLOBE_DOM_LABEL_CHAR_WIDTH = 12;
const GLOBE_DOM_LABEL_HORIZONTAL_PADDING = 22;
const GLOBE_DOM_LABEL_COLLISION_GAP = 6;
const GLOBE_DOM_LABEL_STAGE_PADDING = 8;
const GLOBE_CONNECTOR_LABEL_GAP = 0;
const GLOBE_LABEL_STRATEGY = 'dom-full-label-svg-connectors-collision-filter';

const globeShortLabelById = {
  nasdaq_composite: '纳指',
  sp500: '标普',
  dow_jones: '道指',
  hang_seng: '恒指',
  shanghai_composite: '上证',
  shenzhen_component: '深成',
  nikkei_225: '日经',
  kospi: '韩指',
  ftse_100: '富时',
  dax: '德指',
  cac40: '法指',
  moex_russia: '俄指',
  sensex: '印指',
  ibovespa: '巴指',
};

const globeLabelOffsetsById = {
  nasdaq_composite: { x: -26, y: -22 },
  sp500: { x: 24, y: -28 },
  dow_jones: { x: 38, y: 2 },
  hang_seng: { x: -22, y: -26 },
  shanghai_composite: { x: 26, y: -26 },
  shenzhen_component: { x: -42, y: 10 },
  nikkei_225: { x: 26, y: -18 },
  kospi: { x: -22, y: -20 },
  ftse_100: { x: -28, y: -18 },
  dax: { x: 24, y: -24 },
  cac40: { x: 30, y: 6 },
  moex_russia: { x: 18, y: -20 },
  sensex: { x: -18, y: -22 },
  ibovespa: { x: 22, y: -24 },
};

const toneColors = {
  positive: '#ff8a8a',
  negative: '#6fe5b3',
  neutral: '#8fa6d8',
  stale: '#ffd58c',
  unavailable: '#7f889b',
};

const marketStatusLabels = {
  trading: '交易中',
  break: '休市中',
  closed: '已收盘',
  unavailable: '暂无可用行情',
};

const globeLabelPriorityBoostById = {
  shenzhen_component: 10000,
};

const componentStyles = `
  .global-markets-globe-shell {
    --globe-panel: rgba(4, 8, 16, 0.92);
    --globe-panel-soft: rgba(8, 14, 26, 0.78);
    --globe-border: rgba(157, 180, 255, 0.14);
    --globe-text: #eff4ff;
    --globe-muted: #8fa6d8;
    --globe-blue: #8fa6d8;
    --globe-positive: #ff8a8a;
    --globe-negative: #6fe5b3;
    --globe-warning: #ffd58c;
    --globe-unavailable: #7f889b;
    --globe-radius: 16px;
    --globe-space-2: 8px;
    --globe-space-3: 12px;
    --globe-space-4: 16px;
    --globe-space-5: 20px;
    --globe-stage-height: 520px;
    position: relative;
    display: grid;
    gap: var(--globe-space-3);
    min-width: 0;
  }

  .global-markets-globe-stage {
    position: relative;
    display: grid;
    place-items: center;
    width: 100%;
    height: var(--globe-stage-height);
    min-height: var(--globe-stage-height);
    overflow: hidden;
    border-radius: var(--globe-radius);
    border: 1px solid var(--globe-border);
    background:
      radial-gradient(circle at 72% 20%, rgba(129, 164, 255, 0.18), transparent 30%),
      radial-gradient(circle at 18% 80%, rgba(111, 229, 179, 0.12), transparent 28%),
      linear-gradient(180deg, rgba(8, 14, 26, 0.96), var(--globe-panel));
  }

  .global-markets-globe-canvas {
    position: relative;
    z-index: 0;
    width: 100%;
    height: var(--globe-stage-height);
    min-height: var(--globe-stage-height);
    display: block;
  }

  .global-markets-globe-canvas canvas {
    position: relative;
    z-index: 0;
    display: block;
    margin: 0 auto;
  }

  .global-markets-globe-dom-label-layer {
    position: absolute;
    inset: 0;
    z-index: 3;
    pointer-events: none;
  }

  .global-markets-globe-connector-layer {
    position: absolute;
    inset: 0;
    z-index: 2;
    width: 100%;
    height: 100%;
    overflow: visible;
    pointer-events: none;
  }

  .global-markets-globe-connector {
    fill: none;
    stroke: rgba(239, 244, 255, 0.92);
    stroke-width: 2.4;
    stroke-linecap: round;
    stroke-linejoin: round;
    filter: drop-shadow(0 0 5px rgba(143, 166, 216, 0.72)) drop-shadow(0 0 2px rgba(0, 0, 0, 0.8));
    vector-effect: non-scaling-stroke;
  }

  .global-markets-globe-anchor-dot {
    fill: #eff4ff;
    stroke: rgba(6, 11, 22, 0.9);
    stroke-width: 1.6;
    filter: drop-shadow(0 0 6px rgba(239, 244, 255, 0.74)) drop-shadow(0 0 2px rgba(0, 0, 0, 0.86));
    vector-effect: non-scaling-stroke;
  }

  .global-markets-globe-connector.positive {
    stroke: rgba(255, 138, 138, 0.95);
  }

  .global-markets-globe-connector.negative {
    stroke: rgba(111, 229, 179, 0.95);
  }

  .global-markets-globe-connector.neutral {
    stroke: rgba(143, 166, 216, 0.9);
  }

  .global-markets-globe-connector.unavailable {
    stroke: rgba(190, 198, 213, 0.86);
  }

  .global-markets-globe-anchor-dot.positive {
    fill: var(--globe-positive);
  }

  .global-markets-globe-anchor-dot.negative {
    fill: var(--globe-negative);
  }

  .global-markets-globe-anchor-dot.neutral {
    fill: var(--globe-blue);
  }

  .global-markets-globe-anchor-dot.unavailable {
    fill: var(--globe-unavailable);
  }

  .global-markets-globe-dom-label {
    position: absolute;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 240px;
    max-width: 240px;
    height: 28px;
    padding: 0 11px;
    border: 1px solid rgba(239, 244, 255, 0.28);
    border-radius: 999px;
    background: rgba(6, 11, 22, 0.72);
    color: var(--globe-text);
    font-size: 0.72rem;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.02em;
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.24);
    transform: translate(-50%, -50%);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    backdrop-filter: blur(8px);
  }

  .global-markets-globe-dom-label.positive {
    border-color: rgba(255, 138, 138, 0.38);
    color: var(--globe-positive);
  }

  .global-markets-globe-dom-label.negative {
    border-color: rgba(111, 229, 179, 0.38);
    color: var(--globe-negative);
  }

  .global-markets-globe-dom-label.neutral {
    border-color: rgba(143, 166, 216, 0.34);
    color: var(--globe-blue);
  }

  .global-markets-globe-dom-label.unavailable {
    border-color: rgba(127, 136, 155, 0.34);
    color: var(--globe-unavailable);
  }

  .global-markets-globe-hint {
    position: absolute;
    left: var(--globe-space-4);
    bottom: var(--globe-space-4);
    z-index: 2;
    display: inline-flex;
    align-items: center;
    min-height: 34px;
    border-radius: 999px;
    border: 1px solid rgba(129, 164, 255, 0.2);
    background: rgba(6, 11, 22, 0.76);
    padding: 7px 11px;
    color: #dbe6ff;
    font-size: 0.82rem;
    backdrop-filter: blur(10px);
  }

  .global-markets-globe-meta {
    display: flex;
    flex-wrap: wrap;
    gap: var(--globe-space-2);
    color: var(--globe-muted);
    font-size: 0.82rem;
  }

  .global-markets-globe-meta span {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    border: 1px solid rgba(129, 164, 255, 0.14);
    background: rgba(6, 11, 22, 0.64);
    padding: 6px 10px;
  }

  .global-markets-globe-tooltip {
    position: absolute;
    right: var(--globe-space-4);
    top: var(--globe-space-4);
    z-index: 2;
    width: min(300px, calc(100% - var(--globe-space-5) * 2));
    border-radius: var(--globe-radius);
    border: 1px solid var(--globe-border);
    background: rgba(6, 11, 22, 0.9);
    backdrop-filter: blur(12px);
    padding: var(--globe-space-4);
    box-shadow: 0 18px 46px rgba(0, 0, 0, 0.28);
  }

  .global-markets-globe-tooltip h4 {
    margin: 0 0 var(--globe-space-2);
    color: var(--globe-text);
  }

  .global-markets-globe-tooltip dl {
    display: grid;
    gap: var(--globe-space-2);
    margin: 0;
  }

  .global-markets-globe-tooltip div {
    display: flex;
    justify-content: space-between;
    gap: var(--globe-space-3);
  }

  .global-markets-globe-tooltip dt {
    color: var(--globe-muted);
  }

  .global-markets-globe-tooltip dd {
    margin: 0;
    color: var(--globe-text);
    text-align: right;
  }

  .global-markets-globe-tooltip .positive,
  .global-markets-globe-marker-button.positive {
    color: var(--globe-positive);
  }

  .global-markets-globe-tooltip .negative,
  .global-markets-globe-marker-button.negative {
    color: var(--globe-negative);
  }

  .global-markets-globe-tooltip .neutral,
  .global-markets-globe-marker-button.neutral {
    color: var(--globe-blue);
  }

  .global-markets-globe-tooltip .stale,
  .global-markets-globe-marker-button.stale {
    color: var(--globe-warning);
  }

  .global-markets-globe-tooltip .unavailable,
  .global-markets-globe-marker-button.unavailable {
    color: var(--globe-unavailable);
  }

  .global-markets-globe-empty {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    padding: var(--globe-space-5);
    color: var(--globe-muted);
    text-align: center;
  }

  .global-markets-globe-fallback {
    align-self: stretch;
    display: grid;
    align-content: center;
    width: min(100%, 720px);
    min-height: calc(var(--globe-stage-height) - var(--globe-space-5) * 2);
    gap: var(--globe-space-4);
    padding: var(--globe-space-5);
    color: var(--globe-text);
  }

  .global-markets-globe-fallback-header {
    display: grid;
    gap: var(--globe-space-2);
  }

  .global-markets-globe-fallback-kicker {
    margin: 0;
    color: var(--globe-warning);
    font-size: 0.78rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }

  .global-markets-globe-fallback-title {
    margin: 0;
    font-size: clamp(1.25rem, 3vw, 1.9rem);
    line-height: 1.22;
  }

  .global-markets-globe-fallback-copy {
    margin: 0;
    color: var(--globe-muted);
    line-height: 1.7;
  }

  .global-markets-globe-fallback-stats,
  .global-markets-globe-fallback-list {
    display: grid;
    gap: var(--globe-space-2);
  }

  .global-markets-globe-fallback-stats {
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  }

  .global-markets-globe-fallback-stat,
  .global-markets-globe-fallback-item {
    border: 1px solid var(--globe-border);
    border-radius: var(--globe-radius);
    background: rgba(6, 11, 22, 0.7);
    padding: var(--globe-space-3);
  }

  .global-markets-globe-fallback-stat span,
  .global-markets-globe-fallback-item small {
    color: var(--globe-muted);
    font-size: 0.76rem;
  }

  .global-markets-globe-fallback-stat strong {
    display: block;
    margin-top: 4px;
    font-size: 1.15rem;
  }

  .global-markets-globe-fallback-list {
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  }

  .global-markets-globe-fallback-item {
    display: grid;
    gap: 4px;
  }

  .global-markets-globe-fallback-item strong {
    display: flex;
    justify-content: space-between;
    gap: var(--globe-space-3);
    font-size: 0.9rem;
  }

  .global-markets-globe-marker-rail {
    display: flex;
    flex-wrap: wrap;
    gap: var(--globe-space-2);
  }

  .global-markets-globe-marker-button {
    border: 1px solid var(--globe-border);
    border-radius: 999px;
    padding: 6px 10px;
    background: var(--globe-panel-soft);
    cursor: pointer;
    font: inherit;
    font-size: 0.78rem;
    transition: border-color 140ms ease, transform 140ms ease, background 140ms ease;
  }

  .global-markets-globe-marker-button:hover,
  .global-markets-globe-marker-button:focus-visible,
  .global-markets-globe-marker-button.active {
    border-color: rgba(129, 164, 255, 0.38);
    background: rgba(43, 96, 255, 0.14);
    outline: none;
    transform: translateY(-1px);
  }

  @media (max-width: 760px) {
    .global-markets-globe-shell {
      --globe-stage-height: 360px;
    }

    .global-markets-globe-stage,
    .global-markets-globe-canvas {
      height: var(--globe-stage-height);
      min-height: var(--globe-stage-height);
    }

    .global-markets-globe-hint {
      left: var(--globe-space-3);
      right: var(--globe-space-3);
      bottom: var(--globe-space-3);
      justify-content: center;
    }

    .global-markets-globe-tooltip {
      position: static;
      top: auto;
      right: auto;
      width: auto;
      margin: 0;
    }

    .global-markets-globe-marker-rail {
      max-height: 168px;
      overflow-y: auto;
      padding-right: 2px;
    }

    .global-markets-globe-fallback {
      min-height: calc(var(--globe-stage-height) - var(--globe-space-4) * 2);
      padding: var(--globe-space-4);
    }
  }
`;

function formatPrice(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }

  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatSignedPercent(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }

  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function formatDateTime(value) {
  if (!value) {
    return '--';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '--';
  }

  return date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
}

function getResponsiveGlobeHeight() {
  if (typeof window === 'undefined') {
    return GLOBE_DESKTOP_HEIGHT;
  }

  return window.matchMedia(GLOBE_MOBILE_QUERY).matches ? GLOBE_MOBILE_HEIGHT : GLOBE_DESKTOP_HEIGHT;
}

function getCameraDebugState(globe) {
  const camera = globe?.camera?.();
  const controls = globe?.controls?.();

  if (!camera) {
    return {
      distance: '',
      x: '',
      y: '',
      z: '',
      autoRotate: 'false',
    };
  }

  return {
    distance: camera.position.length().toFixed(2),
    x: camera.position.x.toFixed(2),
    y: camera.position.y.toFixed(2),
    z: camera.position.z.toFixed(2),
    autoRotate: controls?.autoRotate ? 'true' : 'false',
  };
}

function getCameraSignature(globe) {
  const camera = globe?.camera?.();
  if (!camera) {
    return null;
  }

  return {
    distance: camera.position.length(),
    x: camera.position.x,
    y: camera.position.y,
    z: camera.position.z,
  };
}

function didCameraSignatureChange(previousSignature, nextSignature) {
  if (!previousSignature || !nextSignature) {
    return false;
  }

  return ['distance', 'x', 'y', 'z'].some((key) => Math.abs(nextSignature[key] - previousSignature[key]) > CAMERA_INTERACTION_DELTA);
}

function getMarkerVisualTone(marker) {
  if (marker?.unavailable) {
    return 'unavailable';
  }

  if (marker?.stale) {
    return 'stale';
  }

  return marker?.tone || 'neutral';
}

function getMarkerDirectionTone(marker) {
  if (marker?.unavailable) {
    return 'unavailable';
  }

  return marker?.tone || 'neutral';
}

function getMarkerStatus(marker) {
  if (marker?.unavailable) {
    return '暂无可用行情';
  }

  if (marker?.stale) {
    return '可能延迟';
  }

  return marketStatusLabels[marker?.marketStatus] || marker?.marketStatus || '快照';
}

function getRegionLabel(regions, marker) {
  const region = Array.isArray(regions) ? regions.find((item) => item?.id === marker?.region) : null;
  return region?.displayName || marker?.region || '--';
}

function getMarkerLocation(regions, marker) {
  return [getRegionLabel(regions, marker), marker?.country, marker?.exchange].filter(Boolean).join(' · ');
}

function getMarkerLabel(marker) {
  return `${marker?.name || '未知指数'} ${formatSignedPercent(marker?.changePercent)}`;
}

function getAsciiGlobeLabelBase(marker) {
  const candidates = [marker?.exchange, marker?.symbol, marker?.id];
  const label = candidates.find((value) => typeof value === 'string' && value.trim());
  if (!label) {
    return 'MARKET';
  }

  const asciiLabel = label
    .replace(/[^\x20-\x7E]/g, '')
    .replace(/[^A-Za-z0-9._-]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ')
    .toUpperCase();

  return asciiLabel || 'MARKET';
}

function getAsciiGlobeLabel(marker) {
  return `${getAsciiGlobeLabelBase(marker)} ${formatSignedPercent(marker?.changePercent)}`;
}

function getShortGlobeLabelBase(marker) {
  if (marker?.id && globeShortLabelById[marker.id]) {
    return globeShortLabelById[marker.id];
  }

  const fallbackName = marker?.name || marker?.displayName || marker?.exchange || marker?.id || '指数';
  return String(fallbackName).replace(/指数|综合|成份|平均|工业|韩国|英国|德国|法国|俄罗斯|印度|巴西/g, '').slice(0, 3) || '指数';
}

function getShortGlobeLabel(marker) {
  return getShortGlobeLabelBase(marker);
}

function getDomLabelWidth(label) {
  const estimatedWidth = String(label || '').length * GLOBE_DOM_LABEL_CHAR_WIDTH + GLOBE_DOM_LABEL_HORIZONTAL_PADDING;
  return Math.min(Math.max(estimatedWidth, GLOBE_DOM_LABEL_MIN_WIDTH), GLOBE_DOM_LABEL_MAX_WIDTH);
}

function getGlobeLabelOffset(marker) {
  return globeLabelOffsetsById[marker?.id] || { x: 0, y: -20 };
}

function isPointInsideLayer(point, width, height) {
  return point.x >= 0 && point.x <= width && point.y >= 0 && point.y <= height;
}

function getLayerLocalCoords(screenCoords, layerRect, width, height) {
  if (isPointInsideLayer(screenCoords, width, height)) {
    return screenCoords;
  }

  if (!layerRect) {
    return screenCoords;
  }

  const viewportLocalCoords = {
    x: screenCoords.x - layerRect.left,
    y: screenCoords.y - layerRect.top,
  };

  if (isPointInsideLayer(viewportLocalCoords, width, height)) {
    return viewportLocalCoords;
  }

  return viewportLocalCoords;
}

function getProjectedMarkerLabel(globe, marker, width, height, layerRect) {
  const labelScreenCoords = globe?.getScreenCoords?.(marker.lat, marker.lng, GLOBE_DOM_LABEL_ALTITUDE);
  const surfaceScreenCoords = globe?.getScreenCoords?.(marker.lat, marker.lng, GLOBE_POINT_ALTITUDE);
  if (
    !labelScreenCoords ||
    !surfaceScreenCoords ||
    !Number.isFinite(labelScreenCoords.x) ||
    !Number.isFinite(labelScreenCoords.y) ||
    !Number.isFinite(surfaceScreenCoords.x) ||
    !Number.isFinite(surfaceScreenCoords.y)
  ) {
    return null;
  }

  const camera = globe?.camera?.();
  if (camera) {
    const markerVector = getSurfaceVector(marker.lat, marker.lng);
    const cameraLength = camera.position.length();
    if (cameraLength > 0) {
      const cameraVector = {
        x: camera.position.x / cameraLength,
        y: camera.position.y / cameraLength,
        z: camera.position.z / cameraLength,
      };
      const visibilityScore = markerVector.x * cameraVector.x + markerVector.y * cameraVector.y + markerVector.z * cameraVector.z;
      if (visibilityScore < -0.08) {
        return null;
      }
    }
  }

  const offset = getGlobeLabelOffset(marker);
  const labelLocalCoords = getLayerLocalCoords(labelScreenCoords, layerRect, width, height);
  const surfaceLocalCoords = getLayerLocalCoords(surfaceScreenCoords, layerRect, width, height);
  const label = getMarkerLabel(marker);
  const labelWidth = getDomLabelWidth(label);
  const left = labelLocalCoords.x + offset.x;
  const top = labelLocalCoords.y + offset.y;
  const halfWidth = labelWidth / 2;
  const halfHeight = GLOBE_DOM_LABEL_HEIGHT / 2;
  const minLeft = halfWidth + GLOBE_DOM_LABEL_STAGE_PADDING;
  const maxLeft = width - halfWidth - GLOBE_DOM_LABEL_STAGE_PADDING;
  const minTop = halfHeight + GLOBE_DOM_LABEL_STAGE_PADDING;
  const maxTop = height - halfHeight - GLOBE_DOM_LABEL_STAGE_PADDING;
  if (left < minLeft || left > maxLeft || top < minTop || top > maxTop) {
    return null;
  }

  const labelBounds = {
    left: left - halfWidth,
    right: left + halfWidth,
    top: top - halfHeight,
    bottom: top + halfHeight,
  };
  const labelAnchorX = surfaceLocalCoords.x <= labelBounds.left ? labelBounds.left : labelBounds.right;
  const labelAnchorY = Math.min(Math.max(surfaceLocalCoords.y, labelBounds.top), labelBounds.bottom);
  const connectorVectorX = labelAnchorX - surfaceLocalCoords.x;
  const connectorVectorY = labelAnchorY - surfaceLocalCoords.y;
  const connectorLength = Math.hypot(connectorVectorX, connectorVectorY) || 1;
  const connectorEndX = labelAnchorX - (connectorVectorX / connectorLength) * GLOBE_CONNECTOR_LABEL_GAP;
  const connectorEndY = labelAnchorY - (connectorVectorY / connectorLength) * GLOBE_CONNECTOR_LABEL_GAP;

  return {
    id: marker.id,
    label,
    title: label,
    tone: getMarkerDirectionTone(marker),
    left,
    top,
    width: labelWidth,
    bounds: labelBounds,
    connector: {
      anchorAltitude: GLOBE_POINT_ALTITUDE,
      labelAltitude: GLOBE_DOM_LABEL_ALTITUDE,
      surfaceX: surfaceLocalCoords.x,
      surfaceY: surfaceLocalCoords.y,
      labelGuideX: labelLocalCoords.x,
      labelGuideY: labelLocalCoords.y,
      x1: surfaceLocalCoords.x,
      y1: surfaceLocalCoords.y,
      x2: connectorEndX,
      y2: connectorEndY,
    },
  };
}

function doLabelBoundsOverlap(leftBounds, rightBounds, gap = 0) {
  return !(
    leftBounds.right + gap <= rightBounds.left ||
    rightBounds.right + gap <= leftBounds.left ||
    leftBounds.bottom + gap <= rightBounds.top ||
    rightBounds.bottom + gap <= leftBounds.top
  );
}

function getLabelPriority(marker, activeMarkerId) {
  if (marker.id === activeMarkerId) {
    return Number.MAX_SAFE_INTEGER;
  }

  const magnitude = typeof marker.changePercent === 'number' && Number.isFinite(marker.changePercent)
    ? Math.abs(marker.changePercent)
    : 0;
  return magnitude * 1000 + (globeLabelPriorityBoostById[marker.id] || 0);
}

function buildVisibleGlobeLabels(globe, markers, activeMarkerId, width, height, layerRect) {
  const projectedLabels = markers
    .map((marker, index) => ({
      marker,
      index,
      priority: getLabelPriority(marker, activeMarkerId),
      projected: getProjectedMarkerLabel(globe, marker, width, height, layerRect),
    }))
    .filter((entry) => entry.projected)
    .sort((left, right) => right.priority - left.priority || left.index - right.index);

  const accepted = [];
  projectedLabels.forEach((entry) => {
    const overlapsAccepted = accepted.some((label) => doLabelBoundsOverlap(label.bounds, entry.projected.bounds, GLOBE_DOM_LABEL_COLLISION_GAP));
    if (!overlapsAccepted) {
      accepted.push(entry.projected);
    }
  });

  return accepted.sort((left, right) => markers.findIndex((marker) => marker.id === left.id) - markers.findIndex((marker) => marker.id === right.id));
}

function countLabelOverlaps(labels) {
  let overlapCount = 0;
  labels.forEach((label, index) => {
    labels.slice(index + 1).forEach((nextLabel) => {
      if (doLabelBoundsOverlap(label.bounds, nextLabel.bounds)) {
        overlapCount += 1;
      }
    });
  });
  return overlapCount;
}

function degreesToRadians(value) {
  return (value * Math.PI) / 180;
}

function getSurfaceVector(lat, lng) {
  const latRadians = degreesToRadians(lat);
  const lngRadians = degreesToRadians(lng);
  const cosLat = Math.cos(latRadians);

  return {
    x: cosLat * Math.sin(lngRadians),
    y: Math.sin(latRadians),
    z: cosLat * Math.cos(lngRadians),
  };
}

function addCoordinateToVectorSum(coordinate, vectorSum) {
  if (!Array.isArray(coordinate) || coordinate.length < 2) {
    return;
  }

  const [lng, lat] = coordinate;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return;
  }

  const vector = getSurfaceVector(lat, lng);
  vectorSum.x += vector.x;
  vectorSum.y += vector.y;
  vectorSum.z += vector.z;
  vectorSum.count += 1;
}

function walkGeoCoordinates(coordinates, vectorSum) {
  if (!Array.isArray(coordinates)) {
    return;
  }

  if (typeof coordinates[0] === 'number') {
    addCoordinateToVectorSum(coordinates, vectorSum);
    return;
  }

  coordinates.forEach((entry) => walkGeoCoordinates(entry, vectorSum));
}

function getFeatureCentroid(country) {
  const vectorSum = { x: 0, y: 0, z: 0, count: 0 };
  walkGeoCoordinates(country?.geometry?.coordinates, vectorSum);

  if (!vectorSum.count) {
    return getSurfaceVector(0, 0);
  }

  const length = Math.hypot(vectorSum.x, vectorSum.y, vectorSum.z);
  if (!Number.isFinite(length) || length === 0) {
    return getSurfaceVector(0, 0);
  }

  return {
    x: vectorSum.x / length,
    y: vectorSum.y / length,
    z: vectorSum.z / length,
  };
}

function getSunScore(country, subsolarVector) {
  const centroid = country?.globeCentroid || getSurfaceVector(0, 0);
  return centroid.x * subsolarVector.x + centroid.y * subsolarVector.y + centroid.z * subsolarVector.z;
}

function getCountryCapColor(country, subsolarVector) {
  const sunScore = getSunScore(country, subsolarVector);
  if (sunScore >= 0) {
    const opacity = 0.16 + Math.min(sunScore, 1) * 0.16;
    return `rgba(129,164,255,${opacity.toFixed(3)})`;
  }

  const opacity = 0.52 + Math.min(Math.abs(sunScore), 1) * 0.18;
  return `rgba(3,7,16,${opacity.toFixed(3)})`;
}

function getCountrySideColor(country, subsolarVector) {
  const sunScore = getSunScore(country, subsolarVector);
  return sunScore >= 0 ? 'rgba(129,164,255,0.06)' : 'rgba(1,4,12,0.28)';
}

function getSolarCountryCounts(subsolarVector) {
  return globeCountries.reduce(
    (counts, country) => {
      const sunScore = getSunScore(country, subsolarVector);
      if (sunScore > 0.04) {
        counts.day += 1;
      } else if (sunScore < -0.04) {
        counts.night += 1;
      } else {
        counts.twilight += 1;
      }
      return counts;
    },
    { day: 0, night: 0, twilight: 0 },
  );
}

function setLightPosition(light, subsolarVector) {
  light.position.set(subsolarVector.x * 500, subsolarVector.y * 500, subsolarVector.z * 500);
}

function GlobalMarketsGlobe({
  displayItems,
  regions,
  sourceLabel,
  delayLabel,
  updatedAt,
  stale,
  requestState,
  forceFallback = false,
}) {
  const globeRef = useRef(null);
  const shellRef = useRef(null);
  const stageRef = useRef(null);
  const ambientLightRef = useRef(null);
  const directionalLightRef = useRef(null);
  const sceneRef = useRef(null);
  const idleResumeTimerRef = useRef(null);
  const controlsConfiguredRef = useRef(false);
  const cameraDebugUpdateRef = useRef({ frame: 0, last: 0 });
  const domLabelUpdateRef = useRef({ frame: 0, last: 0 });
  const autoRotatePausedRef = useRef(false);
  const lastCameraSignatureRef = useRef(null);
  const interactionIntentUntilRef = useRef(0);
  const globeHoverIntentUntilRef = useRef(0);
  const shouldForceFallback = forceFallback || (typeof window !== 'undefined' && window.__MONEYRUSH_FORCE_GLOBE_FALLBACK__ === true);
  const { markers, skipped } = useMemo(() => buildGlobalMarketGlobeMarkers(displayItems), [displayItems]);
  const [stageSize, setStageSize] = useState(() => ({ width: 0, height: GLOBE_DESKTOP_HEIGHT }));
  const [solarDate, setSolarDate] = useState(() => new Date());
  const [globeReady, setGlobeReady] = useState(false);
  const [hoveredMarker, setHoveredMarker] = useState(null);
  const [selectedMarker, setSelectedMarker] = useState(null);
  const [autoRotatePaused, setAutoRotatePaused] = useState(false);
  const [cameraDebugState, setCameraDebugState] = useState(() => getCameraDebugState(null));
  const [visibleGlobeLabels, setVisibleGlobeLabels] = useState([]);
  const subsolarPoint = useMemo(() => computeSubsolarPoint(solarDate), [solarDate]);
  const terminatorPath = useMemo(() => buildTerminatorPath(solarDate), [solarDate]);
  const subsolarVector = useMemo(() => getSurfaceVector(subsolarPoint.lat, subsolarPoint.lng), [subsolarPoint]);
  const solarCountryCounts = useMemo(() => getSolarCountryCounts(subsolarVector), [subsolarVector]);
  const globeMaterial = useMemo(
    () => {
      if (shouldForceFallback) {
        return null;
      }

      return new THREE.MeshPhongMaterial({
        color: new THREE.Color('#101a33'),
        emissive: new THREE.Color('#01040b'),
        shininess: 8,
        transparent: true,
        opacity: 0.98,
      });
    },
    [shouldForceFallback],
  );
  const activeMarker = hoveredMarker || selectedMarker;
  const selectedMarkerId = selectedMarker?.id || '';
  const fallbackStatus = requestState === 'loading' ? '全球行情加载中…' : '等待全球行情坐标';
  const globeWidth = Math.max(stageSize.width, 1);
  const globeHeight = stageSize.height;
  const fallbackMarkers = markers.slice(0, 6);
  const asciiGlobeLabels = useMemo(() => markers.map(getAsciiGlobeLabel), [markers]);
  const shortGlobeLabels = useMemo(() => markers.map(getShortGlobeLabel), [markers]);
  const visibleGlobeLabelText = useMemo(() => visibleGlobeLabels.map((label) => label.label), [visibleGlobeLabels]);
  const labelOverlapCount = useMemo(() => countLabelOverlaps(visibleGlobeLabels), [visibleGlobeLabels]);
  const connectorCount = visibleGlobeLabels.length;

  useEffect(() => {
    if (shouldForceFallback) {
      return undefined;
    }

    const intervalId = window.setInterval(() => setSolarDate(new Date()), UTC_CLOCK_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [shouldForceFallback]);

  useEffect(() => {
    if (shouldForceFallback) {
      return undefined;
    }

    const stage = stageRef.current;
    if (!stage) {
      return undefined;
    }

    const updateStageSize = () => {
      const rect = stage.getBoundingClientRect();
      setStageSize({
        width: Math.round(rect.width),
        height: getResponsiveGlobeHeight(),
      });
    };

    updateStageSize();
    const resizeObserver = new ResizeObserver(updateStageSize);
    resizeObserver.observe(stage);
    window.addEventListener('resize', updateStageSize);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener('resize', updateStageSize);
    };
  }, [shouldForceFallback]);

  useEffect(() => {
    if (shouldForceFallback || !globeReady || !globeRef.current?.scene) {
      return;
    }

    const scene = globeRef.current.scene();
    sceneRef.current = scene;

    if (!ambientLightRef.current) {
      ambientLightRef.current = new THREE.AmbientLight(0xb7c6ff, 0.22);
      scene.add(ambientLightRef.current);
    }

    if (!directionalLightRef.current) {
      directionalLightRef.current = new THREE.DirectionalLight(0xffd58c, 1.55);
      scene.add(directionalLightRef.current);
    }

    setLightPosition(directionalLightRef.current, subsolarVector);
  }, [shouldForceFallback, globeReady, subsolarVector]);

  useEffect(
    () => () => {
      const scene = sceneRef.current;
      if (scene && ambientLightRef.current) {
        scene.remove(ambientLightRef.current);
      }
      if (scene && directionalLightRef.current) {
        scene.remove(directionalLightRef.current);
      }
      ambientLightRef.current = null;
      directionalLightRef.current = null;
      sceneRef.current = null;
    },
    [],
  );

  useEffect(() => {
    if (shouldForceFallback || !globeReady || !globeRef.current) {
      setVisibleGlobeLabels([]);
      return undefined;
    }

    let stopped = false;
    const updateDomLabels = (force = false) => {
      const now = window.performance.now();
      if (!force && now - domLabelUpdateRef.current.last < GLOBE_DOM_LABEL_FRAME_MS) {
        domLabelUpdateRef.current.frame = window.requestAnimationFrame(() => updateDomLabels(false));
        return;
      }
      domLabelUpdateRef.current.last = now;
      const stageRect = stageRef.current?.getBoundingClientRect?.() || null;
      setVisibleGlobeLabels(buildVisibleGlobeLabels(globeRef.current, markers, activeMarker?.id || '', globeWidth, globeHeight, stageRect));
      if (!stopped) {
        domLabelUpdateRef.current.frame = window.requestAnimationFrame(() => updateDomLabels(false));
      }
    };

    updateDomLabels(true);

    return () => {
      stopped = true;
      window.cancelAnimationFrame(domLabelUpdateRef.current.frame);
    };
  }, [activeMarker?.id, globeHeight, globeReady, globeWidth, markers, shouldForceFallback]);

  useEffect(() => {
    if (shouldForceFallback || !globeReady || !globeRef.current?.controls) {
      return undefined;
    }

    const globe = globeRef.current;
    const controls = globe.controls();
    if (!controls) {
      return undefined;
    }

    const writePauseDataset = (paused) => {
      const shell = shellRef.current;
      if (!shell) {
        return;
      }
      shell.dataset.controlsAutoRotate = paused ? 'false' : 'true';
      shell.dataset.autoRotatePaused = paused ? 'true' : 'false';
    };

    const updateCameraDebugState = (force = false) => {
      const now = window.performance.now();
      if (!force && now - cameraDebugUpdateRef.current.last < 250) {
        return;
      }
      cameraDebugUpdateRef.current.last = now;
      window.cancelAnimationFrame(cameraDebugUpdateRef.current.frame);
      cameraDebugUpdateRef.current.frame = window.requestAnimationFrame(() => {
        setCameraDebugState(getCameraDebugState(globe));
      });
    };

    const setControlsAutoRotate = (enabled) => {
      controls.autoRotate = enabled;
      autoRotatePausedRef.current = !enabled;
      writePauseDataset(!enabled);
      setAutoRotatePaused(!enabled);
      updateCameraDebugState(true);
    };

    const pauseForInteraction = () => {
      interactionIntentUntilRef.current = window.performance.now() + CAMERA_INTERACTION_INTENT_MS;
      window.clearTimeout(idleResumeTimerRef.current);
      setControlsAutoRotate(false);
      idleResumeTimerRef.current = window.setTimeout(() => setControlsAutoRotate(true), GLOBE_IDLE_RESUME_MS);
    };

    const handleControlsChange = () => {
      const nextCameraSignature = getCameraSignature(globe);
      const hasRecentInteractionIntent = window.performance.now() <= interactionIntentUntilRef.current;
      if (!autoRotatePausedRef.current && hasRecentInteractionIntent && didCameraSignatureChange(lastCameraSignatureRef.current, nextCameraSignature)) {
        pauseForInteraction();
      }
      lastCameraSignatureRef.current = nextCameraSignature;
      updateCameraDebugState(false);
    };
    const handleInteractionStart = () => pauseForInteraction();
    const handlePointerMove = () => {
      globeHoverIntentUntilRef.current = window.performance.now() + GLOBE_HOVER_INTENT_MS;
      pauseForInteraction();
    };

    if (!controlsConfiguredRef.current) {
      controls.enableRotate = true;
      controls.enableZoom = true;
      controls.enablePan = false;
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.minDistance = GLOBE_CAMERA_MIN_DISTANCE;
      controls.maxDistance = GLOBE_CAMERA_MAX_DISTANCE;
      controls.autoRotateSpeed = 0.42;
      controls.target.set(0, 0, 0);
      globe.pointOfView(GLOBE_CAMERA_VIEW, 0);
      controls.update();
      lastCameraSignatureRef.current = getCameraSignature(globe);
      controlsConfiguredRef.current = true;
    }

    setControlsAutoRotate(true);
    lastCameraSignatureRef.current = getCameraSignature(globe);
    controls.addEventListener('start', handleInteractionStart);
    controls.addEventListener('change', handleControlsChange);
    updateCameraDebugState(true);

    const shell = shellRef.current;
    const stage = stageRef.current;
    const canvas = globe.renderer?.()?.domElement || stage?.querySelector('canvas');
    const interactionTargets = [shell, stage, canvas, document, window].filter(Boolean);
    const interactionEvents = ['pointerdown', 'mousedown', 'touchstart', 'touchmove', 'wheel'];
    interactionTargets.forEach((target) => {
      interactionEvents.forEach((eventName) => {
        target.addEventListener(eventName, handleInteractionStart, { passive: true });
      });
    });
    [stage, canvas].filter(Boolean).forEach((target) => {
      target.addEventListener('pointermove', handlePointerMove, { passive: true });
      target.addEventListener('mousemove', handlePointerMove, { passive: true });
    });

    return () => {
      window.clearTimeout(idleResumeTimerRef.current);
      window.cancelAnimationFrame(cameraDebugUpdateRef.current.frame);
      controls.removeEventListener('start', handleInteractionStart);
      controls.removeEventListener('change', handleControlsChange);
      interactionTargets.forEach((target) => {
        interactionEvents.forEach((eventName) => {
          target.removeEventListener(eventName, handleInteractionStart);
        });
      });
      [stage, canvas].filter(Boolean).forEach((target) => {
        target.removeEventListener('pointermove', handlePointerMove);
        target.removeEventListener('mousemove', handlePointerMove);
      });
    };
  }, [shouldForceFallback, globeReady]);

  const handleGlobePointHover = (marker) => {
    if (!marker) {
      setHoveredMarker(null);
      return;
    }

    if (window.performance.now() > globeHoverIntentUntilRef.current) {
      return;
    }

    setHoveredMarker(marker);
  };

  if (shouldForceFallback) {
    return (
      <div
        className="global-markets-globe-shell"
        ref={shellRef}
        data-testid="global-markets-globe"
        data-marker-count={markers.length}
        data-skipped-count={skipped.length}
        data-selected-marker-id=""
        data-source-label={sourceLabel || '--'}
        data-delay-label={delayLabel || '--'}
        data-updated-at={updatedAt || ''}
        data-stale={stale ? 'true' : 'false'}
        data-terminator-count={terminatorPath.length}
        data-terminator-finite={terminatorPath.every(([lat, lng]) => Number.isFinite(lat) && Number.isFinite(lng)) ? 'true' : 'false'}
        data-subsolar-lat={subsolarPoint.lat.toFixed(4)}
        data-subsolar-lng={subsolarPoint.lng.toFixed(4)}
        data-solar-timestamp={solarDate.toISOString()}
        data-day-country-count={solarCountryCounts.day}
        data-night-country-count={solarCountryCounts.night}
        data-twilight-country-count={solarCountryCounts.twilight}
        data-camera-distance=""
        data-camera-x=""
        data-camera-y=""
        data-camera-z=""
        data-controls-auto-rotate="false"
        data-controls-rotate-enabled="false"
        data-controls-zoom-enabled="false"
        data-controls-pan-enabled="false"
        data-controls-damping-enabled="false"
        data-controls-min-distance={GLOBE_CAMERA_MIN_DISTANCE}
        data-controls-max-distance={GLOBE_CAMERA_MAX_DISTANCE}
        data-auto-rotate-paused="true"
        data-stage-width={globeWidth}
        data-stage-height={globeHeight}
        data-force-fallback="true"
        data-globe-fallback="true"
        data-ascii-globe-labels={asciiGlobeLabels.join('|')}
        data-globe-label-strategy={GLOBE_LABEL_STRATEGY}
        data-globe-short-labels={shortGlobeLabels.join('|')}
        data-visible-globe-labels=""
        data-visible-globe-label-count="0"
        data-label-overlap-count="0"
        data-connector-count="0"
        data-points-transition-duration={GLOBE_LAYER_TRANSITION_MS}
        data-labels-transition-duration={GLOBE_LAYER_TRANSITION_MS}
        data-point-radius={GLOBE_POINT_RADIUS}
        data-point-altitude={GLOBE_POINT_ALTITUDE}
        data-unavailable-point-altitude={GLOBE_UNAVAILABLE_POINT_ALTITUDE}
        data-label-altitude={GLOBE_LABEL_ALTITUDE}
        data-label-size={GLOBE_LABEL_SIZE}
        data-label-dot-radius={GLOBE_LABEL_DOT_RADIUS}
      >
        <style>{componentStyles}</style>
        <div className="global-markets-globe-stage" ref={stageRef} data-testid="global-markets-globe-stage">
          <section className="global-markets-globe-fallback" data-testid="global-markets-globe-fallback" aria-live="polite">
            <div className="global-markets-globe-fallback-header">
              <p className="global-markets-globe-fallback-kicker">备用模式 · 无 WebGL</p>
              <h4 className="global-markets-globe-fallback-title">地球视图暂不可用，已切换为指数列表摘要。</h4>
              <p className="global-markets-globe-fallback-copy">保留全球市场快照、数据源和延迟状态，便于在无 GPU 或自动化环境中验证。</p>
            </div>
            <div className="global-markets-globe-fallback-stats" aria-label="全球股市备用摘要">
              <div className="global-markets-globe-fallback-stat">
                <span>可显示指数</span>
                <strong>{markers.length}</strong>
              </div>
              <div className="global-markets-globe-fallback-stat">
                <span>跳过坐标</span>
                <strong>{skipped.length}</strong>
              </div>
              <div className="global-markets-globe-fallback-stat">
                <span>数据源</span>
                <strong>{sourceLabel || '--'}</strong>
              </div>
              <div className="global-markets-globe-fallback-stat">
                <span>延迟状态</span>
                <strong>{delayLabel || '--'}</strong>
              </div>
            </div>
            <div className="global-markets-globe-fallback-list" data-testid="global-markets-globe-fallback-list">
              {fallbackMarkers.map((marker) => {
                const visualTone = getMarkerVisualTone(marker);
                const directionTone = getMarkerDirectionTone(marker);
                return (
                  <article
                    key={marker.id}
                    className="global-markets-globe-fallback-item"
                    data-marker-id={marker.id}
                    data-marker-tone={visualTone}
                    data-marker-direction-tone={directionTone}
                    data-marker-status={getMarkerStatus(marker)}
                  >
                    <strong>
                      <span>{marker.name}</span>
                      <b className={directionTone}>{formatSignedPercent(marker.changePercent)}</b>
                    </strong>
                    <small>{getMarkerLocation(regions, marker)} · {getMarkerStatus(marker)}</small>
                  </article>
                );
              })}
            </div>
          </section>
        </div>
        <div className="global-markets-globe-meta" aria-label="全球股市地球元数据">
          <span>数据源 {sourceLabel || '--'}</span>
          <span>{delayLabel || '--'}</span>
          <span>更新时间 {formatDateTime(updatedAt)}</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="global-markets-globe-shell"
      ref={shellRef}
      data-testid="global-markets-globe"
      data-marker-count={markers.length}
      data-skipped-count={skipped.length}
      data-selected-marker-id={selectedMarkerId}
      data-source-label={sourceLabel || '--'}
      data-delay-label={delayLabel || '--'}
      data-updated-at={updatedAt || ''}
      data-stale={stale ? 'true' : 'false'}
      data-terminator-count={terminatorPath.length}
      data-terminator-finite={terminatorPath.every(([lat, lng]) => Number.isFinite(lat) && Number.isFinite(lng)) ? 'true' : 'false'}
      data-subsolar-lat={subsolarPoint.lat.toFixed(4)}
      data-subsolar-lng={subsolarPoint.lng.toFixed(4)}
      data-solar-timestamp={solarDate.toISOString()}
      data-day-country-count={solarCountryCounts.day}
      data-night-country-count={solarCountryCounts.night}
      data-twilight-country-count={solarCountryCounts.twilight}
      data-camera-distance={cameraDebugState.distance}
      data-camera-x={cameraDebugState.x}
      data-camera-y={cameraDebugState.y}
      data-camera-z={cameraDebugState.z}
      data-controls-auto-rotate={autoRotatePaused ? 'false' : 'true'}
      data-controls-rotate-enabled="true"
      data-controls-zoom-enabled="true"
      data-controls-pan-enabled="false"
      data-controls-damping-enabled="true"
      data-controls-min-distance={GLOBE_CAMERA_MIN_DISTANCE}
      data-controls-max-distance={GLOBE_CAMERA_MAX_DISTANCE}
      data-auto-rotate-paused={autoRotatePaused ? 'true' : 'false'}
      data-stage-width={globeWidth}
      data-stage-height={globeHeight}
      data-force-fallback="false"
      data-globe-fallback="false"
      data-ascii-globe-labels={asciiGlobeLabels.join('|')}
      data-globe-label-strategy={GLOBE_LABEL_STRATEGY}
      data-globe-short-labels={shortGlobeLabels.join('|')}
      data-visible-globe-labels={visibleGlobeLabelText.join('|')}
      data-visible-globe-label-count={visibleGlobeLabels.length}
      data-label-overlap-count={labelOverlapCount}
      data-connector-count={connectorCount}
      data-points-transition-duration={GLOBE_LAYER_TRANSITION_MS}
      data-labels-transition-duration={GLOBE_LAYER_TRANSITION_MS}
      data-point-radius={GLOBE_POINT_RADIUS}
      data-point-altitude={GLOBE_POINT_ALTITUDE}
      data-unavailable-point-altitude={GLOBE_UNAVAILABLE_POINT_ALTITUDE}
      data-label-altitude={GLOBE_LABEL_ALTITUDE}
      data-label-size={GLOBE_LABEL_SIZE}
      data-label-dot-radius={GLOBE_LABEL_DOT_RADIUS}
    >
      <style>{componentStyles}</style>
      <div className="global-markets-globe-stage" ref={stageRef} data-testid="global-markets-globe-stage">
        <Suspense fallback={<div className="global-markets-globe-empty">地球视图加载中…</div>}>
          <Globe
            ref={globeRef}
            width={globeWidth}
            height={globeHeight}
            backgroundColor="rgba(0,0,0,0)"
            globeMaterial={globeMaterial}
            globeColor="rgba(7,12,24,0.98)"
            atmosphereColor="rgba(129,164,255,0.44)"
            atmosphereAltitude={0.18}
            polygonsData={globeCountries}
            polygonCapColor={(country) => getCountryCapColor(country, subsolarVector)}
            polygonSideColor={(country) => getCountrySideColor(country, subsolarVector)}
            polygonStrokeColor={() => 'rgba(201,216,255,0.18)'}
            polygonsTransitionDuration={0}
            pathsData={[{ id: 'utc-solar-terminator', coordinates: terminatorPath }]}
            pathPoints="coordinates"
            pathPointLat={(point) => point[0]}
            pathPointLng={(point) => point[1]}
            pathPointAlt={() => TERMINATOR_ALTITUDE}
            pathColor={() => 'rgba(255,213,140,0.95)'}
            pathStroke={1.6}
            pathTransitionDuration={0}
            pointsData={markers}
            pointLat="lat"
            pointLng="lng"
            pointAltitude={(marker) => (marker.unavailable ? GLOBE_UNAVAILABLE_POINT_ALTITUDE : GLOBE_POINT_ALTITUDE)}
            pointRadius={GLOBE_POINT_RADIUS}
            pointColor={(marker) => toneColors[getMarkerDirectionTone(marker)]}
            pointLabel={getMarkerLabel}
            pointsTransitionDuration={GLOBE_LAYER_TRANSITION_MS}
            onPointHover={handleGlobePointHover}
            onPointClick={setSelectedMarker}
            labelsData={[]}
            labelLat="lat"
            labelLng="lng"
            labelAltitude={GLOBE_LABEL_ALTITUDE}
            labelText={() => ''}
            labelSize={GLOBE_LABEL_SIZE}
            labelDotRadius={GLOBE_LABEL_DOT_RADIUS}
            labelColor={(marker) => toneColors[getMarkerDirectionTone(marker)]}
            labelResolution={2}
            labelsTransitionDuration={GLOBE_LAYER_TRANSITION_MS}
            enablePointerInteraction
            onGlobeReady={() => setGlobeReady(true)}
            className="global-markets-globe-canvas"
          />
        </Suspense>
        <svg
          className="global-markets-globe-connector-layer"
          data-testid="global-markets-globe-connector-layer"
          width={globeWidth}
          height={globeHeight}
          viewBox={`0 0 ${globeWidth} ${globeHeight}`}
          aria-hidden="true"
        >
          {visibleGlobeLabels.map((label) => (
            <g key={label.id} data-marker-id={label.id}>
              <path
                className={`global-markets-globe-connector ${label.tone}`}
                data-testid="global-markets-globe-connector"
                data-marker-id={label.id}
                data-x1={label.connector.x1.toFixed(2)}
                data-y1={label.connector.y1.toFixed(2)}
                data-x2={label.connector.x2.toFixed(2)}
                data-y2={label.connector.y2.toFixed(2)}
                data-anchor-altitude={label.connector.anchorAltitude}
                data-label-altitude={label.connector.labelAltitude}
                data-surface-x={label.connector.surfaceX.toFixed(2)}
                data-surface-y={label.connector.surfaceY.toFixed(2)}
                data-label-guide-x={label.connector.labelGuideX.toFixed(2)}
                data-label-guide-y={label.connector.labelGuideY.toFixed(2)}
                d={`M ${label.connector.x1.toFixed(2)} ${label.connector.y1.toFixed(2)} L ${label.connector.x2.toFixed(2)} ${label.connector.y2.toFixed(2)}`}
              />
              <circle
                className={`global-markets-globe-anchor-dot ${label.tone}`}
                data-testid="global-markets-globe-anchor-dot"
                data-marker-id={label.id}
                data-x={label.connector.x1.toFixed(2)}
                data-y={label.connector.y1.toFixed(2)}
                data-anchor-altitude={label.connector.anchorAltitude}
                data-surface-x={label.connector.surfaceX.toFixed(2)}
                data-surface-y={label.connector.surfaceY.toFixed(2)}
                cx={label.connector.x1.toFixed(2)}
                cy={label.connector.y1.toFixed(2)}
                r="4.2"
              />
            </g>
          ))}
        </svg>
        <div className="global-markets-globe-dom-label-layer" data-testid="global-markets-globe-dom-label-layer" aria-hidden="true">
          {visibleGlobeLabels.map((label) => (
            <span
              key={label.id}
              className={`global-markets-globe-dom-label ${label.tone}`}
              data-testid="global-markets-globe-dom-label"
              data-marker-id={label.id}
              data-label-text={label.label}
              title={label.title}
              style={{ left: `${label.left}px`, top: `${label.top}px`, width: `${label.width}px` }}
            >
              {label.label}
            </span>
          ))}
        </div>
        <div className="global-markets-globe-hint">拖拽旋转 · 滚轮/双指缩放</div>
        {!markers.length ? <div className="global-markets-globe-empty">{fallbackStatus}</div> : null}
      </div>
      <div className="global-markets-globe-meta" aria-label="全球股市地球元数据">
        <span>数据源 {sourceLabel || '--'}</span>
        <span>{delayLabel || '--'}</span>
        <span>更新时间 {formatDateTime(updatedAt)}</span>
      </div>
      {activeMarker ? (
        <aside
          className="global-markets-globe-tooltip"
          data-testid="global-markets-globe-tooltip"
          data-marker-id={activeMarker.id}
          aria-live="polite"
        >
          <h4>{activeMarker.name}</h4>
          <dl>
            <div>
              <dt>地区/国家</dt>
              <dd>{getMarkerLocation(regions, activeMarker)}</dd>
            </div>
            <div>
              <dt>价格</dt>
              <dd>{activeMarker.unavailable ? '--' : formatPrice(activeMarker.price)}</dd>
            </div>
          <div>
            <dt>涨跌幅</dt>
              <dd className={getMarkerDirectionTone(activeMarker)}>{formatSignedPercent(activeMarker.changePercent)}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd className={getMarkerVisualTone(activeMarker)}>{getMarkerStatus(activeMarker)}</dd>
            </div>
            <div>
              <dt>更新时间</dt>
              <dd>{formatDateTime(activeMarker.updatedAt || updatedAt)}</dd>
            </div>
            <div>
              <dt>数据源</dt>
              <dd>{activeMarker.sourceLabel || sourceLabel || '--'}</dd>
            </div>
          </dl>
        </aside>
      ) : null}
      <div className="global-markets-globe-marker-rail" data-testid="global-markets-globe-marker-rail">
        {markers.map((marker) => {
          const visualTone = getMarkerVisualTone(marker);
          const directionTone = getMarkerDirectionTone(marker);
          return (
            <button
              key={marker.id}
              type="button"
              className={`global-markets-globe-marker-button ${directionTone}${selectedMarker?.id === marker.id ? ' active' : ''}`}
              data-testid={`global-market-marker-debug-${marker.id}`}
              data-marker-id={marker.id}
              data-marker-tone={visualTone}
              data-marker-direction-tone={directionTone}
              data-marker-status={getMarkerStatus(marker)}
              onMouseEnter={() => setHoveredMarker(marker)}
              onMouseLeave={() => setHoveredMarker(null)}
              onClick={() => setSelectedMarker(marker)}
            >
              {marker.name} {formatSignedPercent(marker.changePercent)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default GlobalMarketsGlobe;
