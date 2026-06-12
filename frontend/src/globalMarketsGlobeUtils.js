const DUPLICATE_COORDINATE_RADIUS_DEGREES = 1.6;
const DUPLICATE_COORDINATE_KEY_PRECISION = 3;
const MAX_DISPLAY_LATITUDE = 70;
const MIN_DISPLAY_LATITUDE = -70;
const MILLISECONDS_PER_DAY = 86400000;
const UNIX_EPOCH_JULIAN_DAY = 2440587.5;
const J2000_JULIAN_DAY = 2451545.0;

function toFiniteNumber(value) {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function normalizeLongitude(value) {
  const normalized = ((((value + 180) % 360) + 360) % 360) - 180;
  return Object.is(normalized, -0) ? 0 : normalized;
}

function degreesToRadians(value) {
  return (value * Math.PI) / 180;
}

function radiansToDegrees(value) {
  return (value * 180) / Math.PI;
}

function normalizeDegrees(value) {
  return ((value % 360) + 360) % 360;
}

function getRawLatitude(item) {
  return toFiniteNumber(item?.latitude ?? item?.lat);
}

function getRawLongitude(item) {
  return toFiniteNumber(item?.longitude ?? item?.lng ?? item?.lon);
}

function formatGlobeSourceLabel(source, fallback = '--') {
  if (typeof source !== 'string' || !source.trim()) {
    return fallback;
  }

  const seen = new Set();
  const labels = source
    .split('+')
    .map((token) => token.trim())
    .filter(Boolean)
    .map((token) => (token.startsWith('yahoo-finance') ? 'yahoo' : token))
    .filter((token) => {
      if (seen.has(token)) {
        return false;
      }
      seen.add(token);
      return true;
    });

  return labels.length ? labels.join('+') : fallback;
}

function getMarkerTone(changePercent) {
  if (typeof changePercent !== 'number' || Number.isNaN(changePercent)) {
    return 'neutral';
  }

  if (changePercent > 0) {
    return 'positive';
  }

  if (changePercent < 0) {
    return 'negative';
  }

  return 'neutral';
}

function coordinateGroupKey(marker) {
  return `${marker.rawLat.toFixed(DUPLICATE_COORDINATE_KEY_PRECISION)}:${marker.rawLng.toFixed(DUPLICATE_COORDINATE_KEY_PRECISION)}`;
}

function buildSkippedEntry(item, index, reason) {
  return {
    item: item && typeof item === 'object' ? { ...item } : item,
    index,
    reason,
  };
}

function compareMarkerIds(left, right) {
  return String(left.id).localeCompare(String(right.id), 'en', { numeric: true });
}

function toSolarDate(date) {
  const solarDate = date instanceof Date ? new Date(date.getTime()) : new Date(date);
  if (Number.isNaN(solarDate.getTime())) {
    throw new TypeError('A valid date is required for globe solar helpers.');
  }
  return solarDate;
}

function getSolarCoordinates(date) {
  const solarDate = toSolarDate(date);
  const julianDay = solarDate.getTime() / MILLISECONDS_PER_DAY + UNIX_EPOCH_JULIAN_DAY;
  const j2000Centuries = (julianDay - J2000_JULIAN_DAY) / 36525;
  const geometricMeanLongitude = normalizeDegrees(
    280.46646 + j2000Centuries * (36000.76983 + j2000Centuries * 0.0003032),
  );
  const geometricMeanAnomaly = 357.52911 + j2000Centuries * (35999.05029 - 0.0001537 * j2000Centuries);
  const eccentricity = 0.016708634 - j2000Centuries * (0.000042037 + 0.0000001267 * j2000Centuries);
  const anomalyRadians = degreesToRadians(geometricMeanAnomaly);
  const equationOfCenter =
    Math.sin(anomalyRadians) * (1.914602 - j2000Centuries * (0.004817 + 0.000014 * j2000Centuries)) +
    Math.sin(2 * anomalyRadians) * (0.019993 - 0.000101 * j2000Centuries) +
    Math.sin(3 * anomalyRadians) * 0.000289;
  const trueLongitude = geometricMeanLongitude + equationOfCenter;
  const omega = 125.04 - 1934.136 * j2000Centuries;
  const apparentLongitude = trueLongitude - 0.00569 - 0.00478 * Math.sin(degreesToRadians(omega));
  const meanObliquity =
    23 +
    (26 +
      (21.448 -
        j2000Centuries * (46.815 + j2000Centuries * (0.00059 - j2000Centuries * 0.001813))) /
        60) /
      60;
  const correctedObliquity = meanObliquity + 0.00256 * Math.cos(degreesToRadians(omega));
  const correctedObliquityRadians = degreesToRadians(correctedObliquity);
  const apparentLongitudeRadians = degreesToRadians(apparentLongitude);
  const declination = radiansToDegrees(
    Math.asin(Math.sin(correctedObliquityRadians) * Math.sin(apparentLongitudeRadians)),
  );
  const tangentHalfObliquity = Math.tan(correctedObliquityRadians / 2);
  const obliquityVariance = tangentHalfObliquity * tangentHalfObliquity;
  const longitudeRadians = degreesToRadians(geometricMeanLongitude);
  const equationOfTime =
    4 *
    radiansToDegrees(
      obliquityVariance * Math.sin(2 * longitudeRadians) -
        2 * eccentricity * Math.sin(anomalyRadians) +
        4 * eccentricity * obliquityVariance * Math.sin(anomalyRadians) * Math.cos(2 * longitudeRadians) -
        0.5 * obliquityVariance * obliquityVariance * Math.sin(4 * longitudeRadians) -
        1.25 * eccentricity * eccentricity * Math.sin(2 * anomalyRadians),
    );
  const utcMinutes =
    solarDate.getUTCHours() * 60 +
    solarDate.getUTCMinutes() +
    solarDate.getUTCSeconds() / 60 +
    solarDate.getUTCMilliseconds() / 60000;
  const longitude = normalizeLongitude((720 - equationOfTime - utcMinutes) / 4);

  return {
    lat: declination,
    lng: longitude,
  };
}

function vectorFromLatLng(lat, lng) {
  const latRadians = degreesToRadians(lat);
  const lngRadians = degreesToRadians(lng);
  const cosLat = Math.cos(latRadians);

  return [cosLat * Math.cos(lngRadians), cosLat * Math.sin(lngRadians), Math.sin(latRadians)];
}

function crossProduct(left, right) {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ];
}

function normalizeVector(vector) {
  const length = Math.hypot(vector[0], vector[1], vector[2]);
  if (!Number.isFinite(length) || length === 0) {
    return [1, 0, 0];
  }

  return vector.map((coordinate) => coordinate / length);
}

function coordinateFromVector(vector) {
  return [
    radiansToDegrees(Math.asin(clamp(vector[2], -1, 1))),
    normalizeLongitude(radiansToDegrees(Math.atan2(vector[1], vector[0]))),
  ];
}

export function hasValidGlobeCoordinate(item) {
  const rawLat = getRawLatitude(item);
  const rawLng = getRawLongitude(item);
  return rawLat !== null && rawLng !== null && rawLat >= -90 && rawLat <= 90 && rawLng >= -180 && rawLng <= 180;
}

export function buildGlobalMarketGlobeMarkers(items) {
  const sourceItems = Array.isArray(items) ? items : [];
  const skipped = [];
  const markers = [];

  sourceItems.forEach((item, index) => {
    if (!item || typeof item !== 'object') {
      skipped.push(buildSkippedEntry(item, index, 'invalid-item'));
      return;
    }

    if (!hasValidGlobeCoordinate(item)) {
      skipped.push(buildSkippedEntry(item, index, 'invalid-coordinate'));
      return;
    }

    const rawLat = getRawLatitude(item);
    const rawLng = getRawLongitude(item);
    const id = item.id ?? `global-market-${index}`;
    const name = item.name || item.displayName || id;
    const sourceLabel = formatGlobeSourceLabel(item.source);
    const unavailable =
      item.price === null ||
      item.changePercent === null ||
      item.marketStatus === 'unavailable' ||
      item.source === 'unavailable';

    markers.push({
      ...item,
      id,
      name,
      country: item.country ?? '',
      region: item.region ?? '',
      exchange: item.exchange ?? '',
      lat: rawLat,
      lng: rawLng,
      rawLat,
      rawLng,
      changePercent: item.changePercent ?? null,
      tone: getMarkerTone(item.changePercent),
      stale: Boolean(item.stale),
      unavailable,
      price: item.price ?? null,
      sourceLabel,
      updatedAt: item.updatedAt ?? null,
    });
  });

  const groups = markers.reduce((result, marker) => {
    const key = coordinateGroupKey(marker);
    const group = result.get(key) || [];
    group.push(marker);
    result.set(key, group);
    return result;
  }, new Map());

  groups.forEach((group) => {
    if (group.length < 2) {
      return;
    }

    group.sort(compareMarkerIds).forEach((marker, index) => {
      const angle = (2 * Math.PI * index) / group.length;
      marker.lat = clamp(
        marker.rawLat + DUPLICATE_COORDINATE_RADIUS_DEGREES * Math.sin(angle),
        MIN_DISPLAY_LATITUDE,
        MAX_DISPLAY_LATITUDE,
      );
      marker.lng = normalizeLongitude(marker.rawLng + DUPLICATE_COORDINATE_RADIUS_DEGREES * Math.cos(angle));
    });
  });

  return {
    markers,
    skipped,
  };
}

export function formatGlobeMarkerLabel(marker) {
  if (!marker || typeof marker !== 'object') {
    return '';
  }

  const name = marker.name || marker.displayName || marker.id || '未知指数';
  const location = [marker.country, marker.exchange].filter(Boolean).join(' · ');
  const price = marker.price === null || marker.price === undefined ? '--' : marker.price;
  const changePercent =
    typeof marker.changePercent === 'number' && Number.isFinite(marker.changePercent)
      ? `${marker.changePercent > 0 ? '+' : ''}${marker.changePercent.toFixed(2)}%`
      : '--';
  const sourceLabel = marker.sourceLabel || formatGlobeSourceLabel(marker.source);

  return [name, location, `${price} / ${changePercent}`, sourceLabel].filter(Boolean).join(' | ');
}

export function computeSubsolarPoint(date) {
  const point = getSolarCoordinates(date);
  return {
    lat: Number.isFinite(point.lat) ? point.lat : 0,
    lng: Number.isFinite(point.lng) ? normalizeLongitude(point.lng) : 0,
  };
}

// Terminator path points are returned as [lat, lng] pairs for react-globe.gl path helpers.
export function buildTerminatorPath(date, segments = 180) {
  const pointCount = Math.max(2, Math.ceil(toFiniteNumber(segments) ?? 180));
  const subsolarPoint = computeSubsolarPoint(date);
  const subsolarVector = vectorFromLatLng(subsolarPoint.lat, subsolarPoint.lng);
  const referenceVector = Math.abs(subsolarVector[2]) > 0.9 ? [0, 1, 0] : [0, 0, 1];
  const firstBasisVector = normalizeVector(crossProduct(referenceVector, subsolarVector));
  const secondBasisVector = normalizeVector(crossProduct(subsolarVector, firstBasisVector));

  return Array.from({ length: pointCount + 1 }, (_, index) => {
    const angle = (2 * Math.PI * index) / pointCount;
    const pointVector = [
      firstBasisVector[0] * Math.cos(angle) + secondBasisVector[0] * Math.sin(angle),
      firstBasisVector[1] * Math.cos(angle) + secondBasisVector[1] * Math.sin(angle),
      firstBasisVector[2] * Math.cos(angle) + secondBasisVector[2] * Math.sin(angle),
    ];

    return coordinateFromVector(pointVector);
  }).filter(([lat, lng]) => Number.isFinite(lat) && Number.isFinite(lng));
}
