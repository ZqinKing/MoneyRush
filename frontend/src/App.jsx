import { useEffect, useMemo, useState } from 'react';

function getBrowserHostname() {
  if (typeof window === 'undefined') {
    return null;
  }

  return window.location.hostname;
}

function isLoopbackHostname(hostname) {
  return hostname === 'localhost' || hostname === '127.0.0.1';
}

function isBindableLocalHostname(hostname) {
  return isLoopbackHostname(hostname) || hostname === '0.0.0.0';
}

function normalizeLoopbackUrl(configuredUrl, fallbackUrl) {
  const browserHostname = getBrowserHostname();
  const rawUrl = configuredUrl || fallbackUrl;

  try {
    const parsedUrl = new URL(rawUrl);
    if (browserHostname && isBindableLocalHostname(parsedUrl.hostname)) {
      parsedUrl.hostname = browserHostname;
    }
    return parsedUrl.toString().replace(/\/$/, '');
  } catch {
    return rawUrl.replace(/\/$/, '');
  }
}

const apiBaseUrl = normalizeLoopbackUrl(import.meta.env.VITE_API_BASE_URL, 'http://localhost:8000');
const wsBaseUrl = normalizeLoopbackUrl(import.meta.env.VITE_WS_BASE_URL, 'ws://localhost:8000');

const requestStateLabels = {
  idle: '等待操作',
  submitting: '正在提交请求…',
  accepted: '已加入监控队列',
  already_active: '该股票已在监控列表中',
  invalid_symbol: '股票代码不存在',
};

const symbolInputPattern = /^\d{6}$/;

const connectionStateLabels = {
  connecting: '连接中',
  connected: '已连接',
  disconnected: '连接已断开',
  error: '连接异常',
};

const contentTypeLabels = {
  all: '全部',
  report: '研报',
  news: '新闻',
  announcement: '公告',
};

const contentTimeRangeLabels = {
  today: '当日',
  week: '一周',
};

const contentLaneLabels = {
  'symbol-report': '个股研报',
  'symbol-news': '个股新闻',
  'symbol-announcement': '个股公告',
  'market-news': '市场快讯',
};

function buildContentFeedUrl({ symbol = '', type = 'all', timeRange = 'today', limit = 20 }) {
  const params = new URLSearchParams();
  if (symbol) {
    params.set('symbol', symbol);
  }
  if (type && type !== 'all') {
    params.set('type', type);
  }
  if (timeRange) {
    params.set('time_range', timeRange);
  }
  params.set('limit', String(limit));
  return `${apiBaseUrl}/api/v1/content/items?${params.toString()}`;
}

function buildContentStatusUrl(symbol = '') {
  const params = new URLSearchParams();
  if (symbol) {
    params.set('symbol', symbol);
  }
  return `${apiBaseUrl}/api/v1/content/status?${params.toString()}`;
}

async function parseJsonOrThrow(response, fallbackMessage) {
  if (!response.ok) {
    throw new Error(fallbackMessage);
  }
  return response.json();
}

function formatRelativeDateTime(value) {
  if (!value) {
    return '--';
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return '--';
  }
  const diffMinutes = Math.round((Date.now() - timestamp) / 60000);
  if (Math.abs(diffMinutes) < 1) {
    return '刚刚';
  }
  if (Math.abs(diffMinutes) < 60) {
    return `${diffMinutes} 分钟前`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) {
    return `${diffHours} 小时前`;
  }
  const diffDays = Math.round(diffHours / 24);
  return `${diffDays} 天前`;
}

function getContentItemMeta(item) {
  if (!item || typeof item !== 'object') {
    return [];
  }
  if (item.type === 'report') {
    return [item.details?.rating, item.details?.institution, item.details?.analyst].filter(Boolean);
  }
  if (item.type === 'announcement') {
    return [item.details?.announcementType, item.symbol].filter(Boolean);
  }
  return [item.details?.articleSource, item.symbol || (item.scope === 'market' ? '市场快讯' : null)].filter(Boolean);
}

function getLaneStatusTone(job) {
  if (job?.isCoolingDown) {
    return 'cooldown';
  }
  if (job?.isStale || job?.lastError) {
    return 'stale';
  }
  return 'healthy';
}

function buildContentSymbolLabel(symbol, snapshot) {
  if (!symbol) {
    return '全市场';
  }

  const companyName = typeof snapshot?.companyName === 'string' ? snapshot.companyName.trim() : '';
  return companyName ? `${companyName} (${symbol})` : symbol;
}

function serializeIdentityValue(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => serializeIdentityValue(item)).join(',')}]`;
  }

  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${key}:${serializeIdentityValue(value[key])}`)
      .join(',')}}`;
  }

  return JSON.stringify(value);
}

function dedupeTicks(ticks) {
  if (!Array.isArray(ticks)) {
    return [];
  }

  const seen = new Set();
  return ticks.filter((tick) => {
    const identity = [tick?.ts, tick?.price, tick?.volume, tick?.amount, tick?.side, tick?.source].join('|');
    if (seen.has(identity)) {
      return false;
    }
    seen.add(identity);
    return true;
  });
}

function dedupeEvents(events) {
  if (!Array.isArray(events)) {
    return [];
  }

  const seen = new Set();
  return events.filter((event) => {
    const identity = [event?.ts, event?.eventType, event?.source, serializeIdentityValue(event?.payload ?? null)].join('|');
    if (seen.has(identity)) {
      return false;
    }
    seen.add(identity);
    return true;
  });
}

function formatCompactNumber(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return new Intl.NumberFormat('zh-CN', {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value);
}

function formatSignedPercent(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  const fixed = value.toFixed(2);
  return `${value > 0 ? '+' : ''}${fixed}%`;
}

function formatTime(value) {
  if (!value) {
    return '--:--:--';
  }

  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
}

function formatDateTime(value) {
  if (!value) {
    return '--';
  }

  return new Date(value).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
}

function formatDate(value) {
  if (!value) {
    return '--';
  }

  return new Date(value).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' });
}

function getChinaTradeDayKey(value) {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  const chinaDate = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  const month = `${chinaDate.getUTCMonth() + 1}`.padStart(2, '0');
  const day = `${chinaDate.getUTCDate()}`.padStart(2, '0');
  return `${chinaDate.getUTCFullYear()}-${month}-${day}`;
}

function formatPlainNumber(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPrice(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function getRequestStatusLabel(value) {
  if (typeof value !== 'string') {
    return '等待操作';
  }

  return requestStateLabels[value] || value;
}

function getRequestStatusTone(value) {
  if (value === 'accepted' || (typeof value === 'string' && value.startsWith('已停止监控'))) {
    return 'success';
  }

  if (value === 'submitting' || (typeof value === 'string' && value.startsWith('正在移除'))) {
    return 'pending';
  }

  if (value === 'already_active') {
    return 'pending';
  }

  if (value === 'idle' || value == null) {
    return 'neutral';
  }

  return 'error';
}

function formatTickVolume(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return `${formatCompactNumber(value)}股`;
}

function formatTurnoverAmount(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return `${formatCompactNumber(value)}元`;
}

function getConnectionStatusLabel(value) {
  return connectionStateLabels[value] || '状态未知';
}

function formatAxisDate(value) {
  if (!value) {
    return '--';
  }

  const date = new Date(value);
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${month}/${day}`;
}

function sortItemsAscendingByTime(items, ...keys) {
  if (!Array.isArray(items)) {
    return [];
  }

  return [...items].sort((left, right) => {
    const leftValue = keys.map((key) => left?.[key]).find(Boolean);
    const rightValue = keys.map((key) => right?.[key]).find(Boolean);
    const leftTime = leftValue ? new Date(leftValue).getTime() : 0;
    const rightTime = rightValue ? new Date(rightValue).getTime() : 0;
    return leftTime - rightTime;
  });
}

function sortBarsAscendingByBucketTs(bars) {
  return sortItemsAscendingByTime(bars, 'bucketTs');
}

function isSameChinaTradeDay(value, referenceValue = new Date()) {
  if (!value) {
    return false;
  }

  const date = new Date(value);
  const reference = new Date(referenceValue);
  if (Number.isNaN(date.getTime()) || Number.isNaN(reference.getTime())) {
    return false;
  }

  const toChinaDay = (item) => {
    const chinaDate = new Date(item.getTime() + 8 * 60 * 60 * 1000);
    return `${chinaDate.getUTCFullYear()}-${chinaDate.getUTCMonth()}-${chinaDate.getUTCDate()}`;
  };

  return toChinaDay(date) === toChinaDay(reference);
}

function getLatestChinaTradeDayValue(items, key) {
  if (!Array.isArray(items) || !items.length) {
    return null;
  }

  let latestItem = null;
  let latestTime = Number.NEGATIVE_INFINITY;

  items.forEach((item) => {
    const rawValue = item?.[key];
    const parsedTime = rawValue ? new Date(rawValue).getTime() : Number.NaN;
    if (Number.isNaN(parsedTime) || parsedTime <= latestTime) {
      return;
    }

    latestItem = item;
    latestTime = parsedTime;
  });

  return latestItem?.[key] ?? null;
}

function buildAxisLabels(items, maxLabels = 6, getLabel = (item) => item?.label ?? '') {
  if (!Array.isArray(items) || items.length === 0) {
    return [];
  }

  if (items.length <= maxLabels) {
    return items.map((item, index) => ({ key: `${index}-${getLabel(item)}`, label: getLabel(item) }));
  }

  const selectedIndexes = new Set([0, items.length - 1]);
  const segmentCount = Math.max(maxLabels - 1, 1);
  for (let step = 1; step < segmentCount; step += 1) {
    selectedIndexes.add(Math.round((step * (items.length - 1)) / segmentCount));
  }

  return [...selectedIndexes]
    .sort((left, right) => left - right)
    .map((index) => ({ key: `${index}-${getLabel(items[index])}`, label: getLabel(items[index]) }));
}

function buildReadableEventItems(events) {
  if (!Array.isArray(events)) {
    return [];
  }

  return events
    .map((event) => {
      const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {};
      const tick = payload?.tick && typeof payload.tick === 'object' ? payload.tick : {};
      const kline = payload?.kline && typeof payload.kline === 'object' ? payload.kline : {};
      const snapshot = payload?.snapshot && typeof payload.snapshot === 'object' ? payload.snapshot : {};

      return {
        time: event?.ts,
        eventType: event?.eventType || '--',
        price: tick?.price,
        volume: tick?.volume,
        side: tick?.side,
        close: kline?.close,
        high: kline?.high,
        low: kline?.low,
        changePct: snapshot?.changePct,
      };
    })
    .slice(0, 8);
}

function formatSignedAxisPercent(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function formatSignedPriceDelta(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}`;
}

function estimatePreviousClose(snapshot, latestKline, intradaySampledBars) {
  if (typeof snapshot?.lastPrice === 'number' && typeof snapshot?.changePct === 'number' && snapshot.changePct > -100) {
    const divisor = 1 + snapshot.changePct / 100;
    if (divisor !== 0) {
      return snapshot.lastPrice / divisor;
    }
  }

  if (typeof latestKline?.open === 'number') {
    return latestKline.open;
  }

  if (Array.isArray(intradaySampledBars) && intradaySampledBars.length) {
    const firstBar = intradaySampledBars[0];
    if (typeof firstBar?.open === 'number') {
      return firstBar.open;
    }
  }

  return null;
}

const CHINA_TRADING_SESSION = {
  morningOpen: 9 * 60 + 30,
  morningClose: 11 * 60 + 30,
  afternoonOpen: 13 * 60,
  afternoonClose: 15 * 60,
};

function getChinaTradingMinute(value) {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  const chinaDate = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return chinaDate.getUTCHours() * 60
    + chinaDate.getUTCMinutes()
    + chinaDate.getUTCSeconds() / 60
    + chinaDate.getUTCMilliseconds() / 60000;
}

function formatChinaMinuteLabel(totalMinutes) {
  if (typeof totalMinutes !== 'number' || Number.isNaN(totalMinutes)) {
    return '--:--';
  }

  const normalizedMinutes = Math.max(0, Math.round(totalMinutes));
  const hours = `${Math.floor(normalizedMinutes / 60)}`.padStart(2, '0');
  const minutes = `${normalizedMinutes % 60}`.padStart(2, '0');
  return `${hours}:${minutes}`;
}

function buildLinearTimeMarks(startMinute, endMinute, maxMarks = 6) {
  if (typeof startMinute !== 'number' || typeof endMinute !== 'number') {
    return [];
  }

  if (Math.abs(endMinute - startMinute) < 0.01) {
    return [{ label: formatChinaMinuteLabel(startMinute), x: 0 }];
  }

  const markCount = Math.min(maxMarks, Math.max(2, Math.round(endMinute - startMinute) + 1));
  const lastIndex = markCount - 1;

  return Array.from({ length: markCount }, (_, index) => {
    const progress = lastIndex === 0 ? 0 : index / lastIndex;
    const minuteValue = startMinute + (endMinute - startMinute) * progress;
    return {
      label: formatChinaMinuteLabel(minuteValue),
      x: progress,
    };
  }).filter((mark, index, marks) => index === 0 || mark.label !== marks[index - 1].label);
}

function buildDynamicIntradayTimeScale(points, width) {
  const minuteValues = points
    .map((point) => getChinaTradingMinute(point?.bucketTs))
    .filter((value) => typeof value === 'number' && value >= CHINA_TRADING_SESSION.morningOpen && value <= CHINA_TRADING_SESSION.afternoonClose);

  if (!minuteValues.length) {
    return null;
  }

  const coversMorning = minuteValues.some((value) => value <= CHINA_TRADING_SESSION.morningClose);
  const coversAfternoon = minuteValues.some((value) => value >= CHINA_TRADING_SESSION.afternoonOpen);
  if (coversMorning && coversAfternoon) {
    return null;
  }

  const segmentStart = coversAfternoon ? CHINA_TRADING_SESSION.afternoonOpen : CHINA_TRADING_SESSION.morningOpen;
  const segmentEnd = coversAfternoon ? CHINA_TRADING_SESSION.afternoonClose : CHINA_TRADING_SESSION.morningClose;
  const minMinute = Math.max(Math.min(...minuteValues), segmentStart);
  const maxMinute = Math.min(Math.max(...minuteValues), segmentEnd);
  const rawSpan = Math.max(maxMinute - minMinute, 0);
  const minimumVisibleSpan = rawSpan < 1 ? 2 : Math.min(Math.max(rawSpan * 1.4, 4), segmentEnd - segmentStart);
  const preferredCenter = (minMinute + maxMinute) / 2;
  let domainStart = preferredCenter - minimumVisibleSpan / 2;
  let domainEnd = preferredCenter + minimumVisibleSpan / 2;

  if (domainStart < segmentStart) {
    domainEnd = Math.min(segmentEnd, domainEnd + (segmentStart - domainStart));
    domainStart = segmentStart;
  }
  if (domainEnd > segmentEnd) {
    domainStart = Math.max(segmentStart, domainStart - (domainEnd - segmentEnd));
    domainEnd = segmentEnd;
  }

  const visibleSpan = Math.max(domainEnd - domainStart, 1);
  const marks = buildLinearTimeMarks(domainStart, domainEnd).map((mark) => ({
    label: mark.label,
    x: mark.x * width,
  }));

  return {
    gapStart: null,
    gapEnd: null,
    marks,
    positionToX: (position) => {
      if (!position || typeof position.minutes !== 'number') {
        return null;
      }

      const clampedMinute = Math.min(Math.max(position.minutes, domainStart), domainEnd);
      return ((clampedMinute - domainStart) / visibleSpan) * width;
    },
  };
}

function getChinaSessionPosition(value) {
  if (!value) {
    return null;
  }

  const minutes = getChinaTradingMinute(value);
  if (minutes === null) {
    return null;
  }

  if (minutes < CHINA_TRADING_SESSION.morningOpen || minutes > CHINA_TRADING_SESSION.afternoonClose) {
    return null;
  }

  if (minutes <= CHINA_TRADING_SESSION.morningClose) {
    return {
      segment: 'morning',
      minutes,
      progress: (minutes - CHINA_TRADING_SESSION.morningOpen) / (CHINA_TRADING_SESSION.morningClose - CHINA_TRADING_SESSION.morningOpen),
    };
  }

  if (minutes >= CHINA_TRADING_SESSION.afternoonOpen) {
    return {
      segment: 'afternoon',
      minutes,
      progress: (minutes - CHINA_TRADING_SESSION.afternoonOpen) / (CHINA_TRADING_SESSION.afternoonClose - CHINA_TRADING_SESSION.afternoonOpen),
    };
  }

  return {
    segment: 'break',
    minutes,
    progress: 0,
  };
}

function buildIntradayTimeScale(width, points = [], gapWidth = 26) {
  const dynamicScale = Array.isArray(points) && points.length ? buildDynamicIntradayTimeScale(points, width) : null;
  if (dynamicScale) {
    return dynamicScale;
  }

  const morningWidth = (width - gapWidth) / 2;
  const afternoonStart = morningWidth + gapWidth;

  const positionToX = (position) => {
    if (!position) {
      return null;
    }

    if (position.segment === 'morning') {
      return morningWidth * position.progress;
    }

    if (position.segment === 'afternoon') {
      return afternoonStart + morningWidth * position.progress;
    }

    return morningWidth + gapWidth / 2;
  };

  return {
    gapStart: morningWidth,
    gapEnd: afternoonStart,
    positionToX,
    marks: [
      { label: '09:30', x: 0 },
      { label: '10:30', x: morningWidth * 0.5 },
      { label: '11:30', x: morningWidth },
      { label: '13:00', x: afternoonStart },
      { label: '14:00', x: afternoonStart + morningWidth * 0.5 },
      { label: '15:00', x: width },
    ],
  };
}

function buildMovingAverageOverlays(bars, candles, scaleY) {
  if (!Array.isArray(bars) || !Array.isArray(candles) || bars.length !== candles.length) {
    return [];
  }

  const periods = [
    { period: 5, colorClass: 'ma-line ma5', label: 'MA5' },
    { period: 10, colorClass: 'ma-line ma10', label: 'MA10' },
    { period: 20, colorClass: 'ma-line ma20', label: 'MA20' },
  ];

  return periods.map(({ period, colorClass, label }) => {
    const points = [];

    for (let index = period - 1; index < bars.length; index += 1) {
      const window = bars.slice(index - period + 1, index + 1);
      const closes = window.map((bar) => bar.close).filter((value) => typeof value === 'number');
      if (closes.length !== period) {
        continue;
      }

      const average = closes.reduce((sum, value) => sum + value, 0) / period;
      points.push({
        x: candles[index].x,
        y: scaleY(average),
        value: average,
      });
    }

    return {
      period,
      label,
      colorClass,
      polyline: points.map((point) => `${point.x},${point.y}`).join(' '),
      latestValue: points.length ? points[points.length - 1].value : null,
      points,
    };
  }).filter((item) => item.points.length >= 2);
}

function buildCandlestickChartGeometry(bars, width = 860, height = 320) {
  if (!Array.isArray(bars) || bars.length === 0) {
    return null;
  }

  const highs = bars.map((bar) => bar.high).filter((value) => typeof value === 'number');
  const lows = bars.map((bar) => bar.low).filter((value) => typeof value === 'number');
  if (!highs.length || !lows.length) {
    return null;
  }

  const maxHigh = Math.max(...highs);
  const minLow = Math.min(...lows);
  const priceRange = maxHigh - minLow || Math.max(maxHigh * 0.02, 1);
  const topPadding = 18;
  const bottomPadding = 26;
  const usableHeight = height - topPadding - bottomPadding;
  const slotCount = Math.max(bars.length, 12);
  const stepX = width / slotCount;
  const candleWidth = Math.min(Math.max(stepX * 0.58, 4), 40);
  const spanWidth = bars.length > 1 ? stepX * (bars.length - 1) : 0;
  const startX = bars.length > 1 ? (width - spanWidth) / 2 : width / 2;

  const scaleY = (price) => topPadding + ((maxHigh - price) / priceRange) * usableHeight;

  const candles = bars.map((bar, index) => {
    const x = bars.length > 1 ? startX + index * stepX : startX;
    const openY = scaleY(bar.open);
    const closeY = scaleY(bar.close);
    const highY = scaleY(bar.high);
    const lowY = scaleY(bar.low);
    const rising = bar.close >= bar.open;
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(Math.abs(closeY - openY), 1.5);

    return {
      index,
      x,
      rising,
      highY,
      lowY,
      bodyTop,
      bodyHeight,
      bodyLeft: x - candleWidth / 2,
      candleWidth,
      label: formatAxisDate(bar.bucketTs),
      close: bar.close,
      open: bar.open,
    };
  });

  const ticks = [maxHigh, maxHigh - priceRange / 2, minLow].map((value) => ({
    value,
    y: scaleY(value),
  }));

  return { width, height, candles, ticks, minLow, maxHigh, scaleY };
}

function buildVolumeChartGeometry(bars, width = 860, height = 120) {
  if (!Array.isArray(bars) || bars.length === 0) {
    return null;
  }

  const volumes = bars.map((bar) => (typeof bar.volume === 'number' ? bar.volume : 0));
  const maxVolume = Math.max(...volumes, 0);
  if (maxVolume <= 0) {
    return null;
  }

  const topPadding = 8;
  const bottomPadding = 20;
  const usableHeight = height - topPadding - bottomPadding;
  const slotCount = Math.max(bars.length, 12);
  const stepX = width / slotCount;
  const barWidth = Math.min(Math.max(stepX * 0.58, 3), 36);
  const spanWidth = bars.length > 1 ? stepX * (bars.length - 1) : 0;
  const startX = bars.length > 1 ? (width - spanWidth) / 2 : width / 2;

  const items = bars.map((bar, index) => {
    const volume = typeof bar.volume === 'number' ? bar.volume : 0;
    const barHeight = (volume / maxVolume) * usableHeight;
    const centerX = bars.length > 1 ? startX + index * stepX : startX;
    const x = centerX - barWidth / 2;
    const y = height - bottomPadding - barHeight;
    return {
      index,
      volume,
      x,
      y,
      width: barWidth,
      height: barHeight,
      rising: bar.close >= bar.open,
    };
  });

  return { width, height, items, maxVolume };
}

function buildLineChartGeometry(points, width = 860, height = 320, referencePrice = null) {
  if (!Array.isArray(points) || points.length === 0) {
    return null;
  }

  const closes = points.map((point) => point.close).filter((value) => typeof value === 'number');
  if (!closes.length) {
    return null;
  }

  const maxValueFromData = Math.max(...closes);
  const minValueFromData = Math.min(...closes);
  const previousClose = typeof referencePrice === 'number' ? referencePrice : null;
  const dataSpan = maxValueFromData - minValueFromData;
  const effectiveSpan = dataSpan || Math.max(Math.abs(maxValueFromData) * 0.002, 0.6);
  const outerPadding = Math.max(effectiveSpan * 0.18, Math.abs(maxValueFromData) * 0.0012, 0.18);
  let maxValue = maxValueFromData + outerPadding;
  let minValue = minValueFromData - outerPadding;
  let referenceVisible = false;

  if (previousClose !== null) {
    const distanceToRange = previousClose < minValue
      ? minValue - previousClose
      : previousClose > maxValue
        ? previousClose - maxValue
        : 0;
    const inclusionThreshold = Math.max(effectiveSpan * 0.65, Math.abs(previousClose) * 0.004, 0.8);

    if (distanceToRange <= inclusionThreshold) {
      const referencePadding = Math.max(effectiveSpan * 0.08, 0.2);
      maxValue = Math.max(maxValue, previousClose + referencePadding);
      minValue = Math.min(minValue, previousClose - referencePadding);
      referenceVisible = true;
    }
  }

  const range = maxValue - minValue || Math.max(maxValue * 0.02, 1);
  const topPadding = 18;
  const bottomPadding = 26;
  const usableHeight = height - topPadding - bottomPadding;
  const timeScale = buildIntradayTimeScale(width, points);

  const scaleY = (value) => topPadding + ((maxValue - value) / range) * usableHeight;
  const fallbackStep = points.length > 1 ? width / (points.length - 1) : 0;
  const minimumStep = fallbackStep > 0 ? Math.min(fallbackStep * 0.12, 0.9) : 0.6;
  let previousX = null;

  const chartPoints = points.map((point, index) => {
    const mappedX = timeScale.positionToX(getChinaSessionPosition(point.bucketTs));
    const fallbackX = points.length > 1 ? fallbackStep * index : width / 2;
    const rawX = mappedX ?? fallbackX;
    const x = previousX !== null && rawX <= previousX
      ? Math.min(previousX + minimumStep, width)
      : rawX;

    previousX = x;

    return {
      x,
      y: scaleY(point.close),
      label: formatTime(point.bucketTs),
      close: point.close,
      volume: point.volume,
      amount: point.amount,
    };
  });

  const polyline = chartPoints.map((point) => `${point.x},${point.y}`).join(' ');
  const area = `0,${height - bottomPadding} ${polyline} ${width},${height - bottomPadding}`;
  let cumulativeAmount = 0;
  let cumulativeVolume = 0;
  const averagePoints = chartPoints
    .map((point) => {
      if (typeof point.amount === 'number' && typeof point.volume === 'number' && point.volume > 0) {
        cumulativeAmount += point.amount;
        cumulativeVolume += point.volume;
      }

      if (cumulativeVolume <= 0) {
        return null;
      }

      const averagePrice = cumulativeAmount / cumulativeVolume;
      return `${point.x},${scaleY(averagePrice)}`;
    })
    .filter(Boolean)
    .join(' ');
  const tickLevels = 5;
  const ticks = Array.from({ length: tickLevels }, (_, index) => {
    const value = maxValue - (range / (tickLevels - 1)) * index;
    const delta = previousClose !== null ? value - previousClose : null;

    return {
      value,
      delta,
      y: scaleY(value),
      percent: previousClose !== null && previousClose !== 0 ? (delta / previousClose) * 100 : null,
    };
  });
  const latestPoint = chartPoints.length ? chartPoints[chartPoints.length - 1] : null;
  const highPoint = chartPoints.reduce((best, point) => (best === null || point.close > best.close ? point : best), null);
  const lowPoint = chartPoints.reduce((best, point) => (best === null || point.close < best.close ? point : best), null);

  return {
    width,
    height,
    chartPoints,
    averagePolyline: averagePoints,
    polyline,
    area,
    ticks,
    maxValue,
    minValue,
    previousClose,
    baselineY: referenceVisible && previousClose !== null ? scaleY(previousClose) : null,
    referenceVisible,
    latestPoint,
    highPoint,
    lowPoint,
    timeMarks: timeScale.marks,
    lunchBreakStartX: timeScale.gapStart,
    lunchBreakEndX: timeScale.gapEnd,
  };
}

function getLineChartTone(chart, fallbackPrice = null) {
  if (!chart) {
    return 'neutral';
  }

  const lastClose = chart.chartPoints?.length ? chart.chartPoints[chart.chartPoints.length - 1]?.close : null;
  const baseline = chart.previousClose ?? fallbackPrice;
  if (typeof lastClose !== 'number' || typeof baseline !== 'number') {
    return 'neutral';
  }

  if (lastClose > baseline) {
    return 'positive';
  }

  if (lastClose < baseline) {
    return 'negative';
  }

  return 'neutral';
}

function buildEmptyIntradayChartGeometry(referencePrice, width = 860, height = 320) {
  const numericReference = typeof referencePrice === 'number' ? referencePrice : null;
  const range = numericReference !== null ? Math.max(Math.abs(numericReference) * 0.02, 1) : 2;
  const midValue = numericReference ?? 0;
  const maxValue = midValue + range;
  const minValue = midValue - range;
  const topPadding = 18;
  const bottomPadding = 26;
  const usableHeight = height - topPadding - bottomPadding;
  const timeScale = buildIntradayTimeScale(width);

  const scaleY = (value) => topPadding + ((maxValue - value) / (maxValue - minValue || 1)) * usableHeight;

  return {
    width,
    height,
    ticks: [maxValue, midValue, minValue].map((value) => ({
      value,
      y: scaleY(value),
      label: numericReference !== null ? value.toFixed(2) : '--',
      percent: numericReference !== null && numericReference !== 0 ? ((value - numericReference) / numericReference) * 100 : null,
    })),
    timeMarks: timeScale.marks,
    lunchBreakStartX: timeScale.gapStart,
    lunchBreakEndX: timeScale.gapEnd,
    guideY: scaleY(midValue),
    previousClose: numericReference,
  };
}

function App() {
  const [symbol, setSymbol] = useState('');
  const [connectionState, setConnectionState] = useState('connecting');
  const [requestState, setRequestState] = useState('idle');
  const [removingSymbol, setRemovingSymbol] = useState('');
  const [activeSymbols, setActiveSymbols] = useState([]);
  const [snapshots, setSnapshots] = useState({});
  const [messages, setMessages] = useState([]);
  const [activeView, setActiveView] = useState('overview');
  const [controlView, setControlView] = useState('activate');
  const [selectedSnapshotSymbol, setSelectedSnapshotSymbol] = useState(null);
  const [snapshotDetails, setSnapshotDetails] = useState({});
  const [detailRequestState, setDetailRequestState] = useState('idle');
  const [detailChartView, setDetailChartView] = useState('intraday');
  const [contentType, setContentType] = useState('all');
  const [contentTimeRange, setContentTimeRange] = useState('today');
  const [contentSymbolFilter, setContentSymbolFilter] = useState('');
  const [contentFeed, setContentFeed] = useState([]);
  const [contentStatus, setContentStatus] = useState({ jobs: [], latestIngestedAt: null, summary: null });
  const [contentRequestState, setContentRequestState] = useState('idle');

  const wsUrl = useMemo(() => `${wsBaseUrl}/ws/market`, []);
  const eventCards = useMemo(
    () =>
      activeSymbols
        .map((currentSymbol) => {
          const eventPayload = messages.find((message) => message.events?.[currentSymbol]);

          return {
            symbol: currentSymbol,
            snapshot: snapshots[currentSymbol],
            event: eventPayload?.events?.[currentSymbol] || null,
          };
        })
        .filter((item) => item.snapshot || item.event),
    [activeSymbols, messages, snapshots],
  );

  useEffect(() => {
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      setConnectionState('connected');
    };

    socket.onclose = () => {
      setConnectionState('disconnected');
    };

    socket.onerror = () => {
      setConnectionState('error');
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (Array.isArray(payload.activeSymbols)) {
          setActiveSymbols(payload.activeSymbols);
        }
        if (payload.snapshots && typeof payload.snapshots === 'object') {
          setSnapshots(payload.snapshots);
        }
        setMessages((current) => [payload, ...current].slice(0, 20));
      } catch {
        setMessages((current) => [{ type: 'parse_error', raw: event.data }, ...current].slice(0, 20));
      }
    };

    return () => {
      socket.close();
    };
  }, [wsUrl]);

  useEffect(() => {
    fetch(`${apiBaseUrl}/api/v1/symbols/snapshots`)
      .then((response) => response.json())
      .then((payload) => {
        if (payload.snapshots && typeof payload.snapshots === 'object') {
          setSnapshots(payload.snapshots);
        }
      })
      .catch(() => undefined);

    fetch(`${apiBaseUrl}/api/v1/symbols/active`)
      .then((response) => response.json())
      .then((payload) => {
        if (Array.isArray(payload.symbols)) {
          setActiveSymbols(payload.symbols);
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (activeView !== 'content') {
      return undefined;
    }

    let cancelled = false;

    async function loadContent() {
      setContentRequestState('loading');
      try {
        const [feedResponse, statusResponse] = await Promise.all([
          fetch(buildContentFeedUrl({ symbol: contentSymbolFilter, type: contentType, timeRange: contentTimeRange, limit: 60 })),
          fetch(buildContentStatusUrl(contentSymbolFilter)),
        ]);
        const [feedPayload, statusPayload] = await Promise.all([
          parseJsonOrThrow(feedResponse, 'content feed fetch failed'),
          parseJsonOrThrow(statusResponse, 'content status fetch failed'),
        ]);
        if (cancelled) {
          return;
        }
        setContentFeed(Array.isArray(feedPayload.items) ? feedPayload.items : []);
        setContentStatus(statusPayload && typeof statusPayload === 'object' ? statusPayload : { jobs: [], latestIngestedAt: null, summary: null });
        setContentRequestState('ready');
      } catch {
        if (!cancelled) {
          setContentRequestState('error');
        }
      }
    }

    loadContent();
    const intervalId = window.setInterval(loadContent, 90000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeView, contentSymbolFilter, contentTimeRange, contentType]);

  const selectedSnapshot = selectedSnapshotSymbol ? snapshots[selectedSnapshotSymbol] : null;
  const selectedDetail = selectedSnapshotSymbol ? snapshotDetails[selectedSnapshotSymbol] : null;
  const selectedSnapshotTradeDay = getChinaTradeDayKey(selectedSnapshot?.updatedAt);
  const contentSymbolOptions = useMemo(
    () => [
      { value: '', label: '全市场' },
      ...activeSymbols.map((currentSymbol) => ({
        value: currentSymbol,
        label: buildContentSymbolLabel(currentSymbol, snapshots[currentSymbol]),
      })),
    ],
    [activeSymbols, snapshots],
  );

  useEffect(() => {
    if (contentSymbolFilter && !activeSymbols.includes(contentSymbolFilter)) {
      setContentSymbolFilter('');
    }
  }, [activeSymbols, contentSymbolFilter]);

  useEffect(() => {
    if (!selectedSnapshotSymbol) {
      return undefined;
    }

    let cancelled = false;

    async function loadDetails({ keepReadyState = false } = {}) {
      if (!keepReadyState) {
        setDetailRequestState('loading');
      }

      try {
        const [detailResponse, ticksResponse, eventsResponse, klineResponse] = await Promise.all([
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/detail`),
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/ticks?limit=60`),
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/events?limit=10`),
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/kline?period=1d&limit=60`),
        ]);

        if (!detailResponse.ok) {
          throw new Error('detail fetch failed');
        }

        const detailPayload = await detailResponse.json();
        const [ticksPayload, eventsPayload, klinePayload] = await Promise.all([
          ticksResponse.ok ? ticksResponse.json() : Promise.resolve({ ticks: [] }),
          eventsResponse.ok ? eventsResponse.json() : Promise.resolve({ events: [] }),
          klineResponse.ok ? klineResponse.json() : Promise.resolve({ klines: [] }),
        ]);

        if (cancelled) {
          return;
        }

        setSnapshotDetails((current) => ({
          ...current,
          [selectedSnapshotSymbol]: {
            detail: detailPayload,
            ticks: Array.isArray(ticksPayload.ticks) ? ticksPayload.ticks : [],
            events: Array.isArray(eventsPayload.events) ? eventsPayload.events : [],
            klines: Array.isArray(klinePayload.klines) ? klinePayload.klines : [],
          },
        }));
        setDetailRequestState('ready');
      } catch {
        if (!cancelled) {
          setDetailRequestState('error');
        }
      }
    }

    loadDetails();
    const intervalId = window.setInterval(() => {
      loadDetails({ keepReadyState: true });
    }, 60000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [apiBaseUrl, selectedSnapshotSymbol, selectedSnapshotTradeDay]);

  function removeSymbolFromState(symbolToRemove) {
    setActiveSymbols((current) => current.filter((item) => item !== symbolToRemove));
    setSnapshots((current) => {
      const next = { ...current };
      delete next[symbolToRemove];
      return next;
    });
    setSnapshotDetails((current) => {
      const next = { ...current };
      delete next[symbolToRemove];
      return next;
    });
    setSelectedSnapshotSymbol((current) => (current === symbolToRemove ? null : current));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const normalizedSymbol = symbol.trim().toUpperCase();

    if (!normalizedSymbol) {
      setRequestState('请输入6位股票代码');
      return;
    }

    if (!symbolInputPattern.test(normalizedSymbol)) {
      setRequestState('请输入6位股票代码');
      return;
    }

    setRequestState('submitting');

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/symbols/activate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ symbol: normalizedSymbol }),
      });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(payload.detail || payload.message || `激活失败（HTTP ${response.status}）`);
      }

      setRequestState(typeof payload.status === 'string' ? payload.status : 'accepted');
      setSymbol('');
    } catch (error) {
      setRequestState(error.message);
    }
  }

  async function handleRemoveSymbol(symbolToRemove) {
    if (removingSymbol) {
      return;
    }

    setRemovingSymbol(symbolToRemove);
    setRequestState(`正在移除 ${symbolToRemove}...`);

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(symbolToRemove)}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error(`移除失败（HTTP ${response.status}）`);
      }

      removeSymbolFromState(symbolToRemove);
      setRequestState(`已停止监控 ${symbolToRemove}`);
    } catch (error) {
      setRequestState(error.message);
    } finally {
      setRemovingSymbol('');
    }
  }

  function handleOpenSnapshotDetail(symbolToOpen) {
    setSelectedSnapshotSymbol(symbolToOpen);
    setDetailRequestState(snapshotDetails[symbolToOpen] ? 'ready' : 'loading');
    setDetailChartView('intraday');
  }

  function handleCloseSnapshotDetail() {
    setSelectedSnapshotSymbol(null);
    setDetailRequestState('idle');
    setDetailChartView('intraday');
  }

  function renderOverviewCard(item, snapshot) {
    return (
      <section
        className="snapshot-card clickable"
        key={item}
        role="button"
        tabIndex={0}
        onClick={() => handleOpenSnapshotDetail(item)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            handleOpenSnapshotDetail(item);
          }
        }}
      >
        <header>
          <div>
            <strong>{snapshot?.companyName || '待识别公司'}</strong>
            <p className="snapshot-subtitle">
              {item} · {snapshot?.exchange || '--'}
            </p>
          </div>
          <span>{snapshot?.source || '等待采集'}</span>
        </header>
        <div className="snapshot-metric">{formatPrice(snapshot?.lastPrice)}</div>
        <dl>
          <div>
            <dt>涨跌幅</dt>
            <dd className={snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}>
              {formatSignedPercent(snapshot?.changePct)}
            </dd>
          </div>
          <div>
            <dt>换手率</dt>
            <dd>{snapshot?.turnoverRate ?? '--'}</dd>
          </div>
          <div>
            <dt>PE / PB</dt>
            <dd>{snapshot ? `${snapshot.pe} / ${snapshot.pb}` : '--'}</dd>
          </div>
          <div>
            <dt>总市值</dt>
            <dd>{formatCompactNumber(snapshot?.marketCap)}</dd>
          </div>
          <div>
            <dt>涨停 / 跌停</dt>
            <dd>{snapshot ? `${snapshot.limitUp} / ${snapshot.limitDown}` : '--'}</dd>
          </div>
        </dl>
      </section>
    );
  }

  function renderWatchlistCard(item) {
    const snapshot = snapshots[item];
    const lastUpdate = snapshot?.updatedAt || null;
    return (
      <article className="watchlist-card" key={item}>
        <div className="watchlist-card-header">
          <div>
            <strong>{snapshot?.companyName || '待识别公司'}</strong>
            <p className="snapshot-subtitle">
              {item} · {snapshot?.exchange || '--'}
            </p>
          </div>
          <span className="watchlist-source-chip">{snapshot?.source || '等待采集'}</span>
        </div>
        <div className="watchlist-price-row">
          <div className="snapshot-metric small">{formatPrice(snapshot?.lastPrice)}</div>
          <span className={snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}>
            {formatSignedPercent(snapshot?.changePct)}
          </span>
        </div>
        <dl className="watchlist-meta-grid">
          <div>
            <dt>更新时间</dt>
            <dd>{formatTime(snapshot?.updatedAt)}</dd>
          </div>
          <div>
            <dt>换手率</dt>
            <dd>{snapshot?.turnoverRate ?? '--'}</dd>
          </div>
        </dl>
        <div className="watchlist-card-footer">
          <span className="panel-tip compact">{lastUpdate ? formatDateTime(lastUpdate) : '等待首个快照'}</span>
          <button
            type="button"
            className="remove-symbol-button"
            onClick={() => handleRemoveSymbol(item)}
            disabled={Boolean(removingSymbol)}
          >
            {removingSymbol === item ? '移除中…' : '移除'}
          </button>
        </div>
      </article>
    );
  }

  function renderContentCard(item) {
    const meta = getContentItemMeta(item);
    const isClickable = Boolean(item.url);
    const openContent = () => {
      if (!item.url) {
        return;
      }
      window.open(item.url, '_blank', 'noopener,noreferrer');
    };

    return (
      <section
        className={`content-card ${item.type === 'announcement' ? 'content-card-announcement' : ''} ${isClickable ? 'clickable' : ''}`}
        key={`${item.type}-${item.id}`}
        role={isClickable ? 'button' : undefined}
        tabIndex={isClickable ? 0 : undefined}
        onClick={isClickable ? openContent : undefined}
        onKeyDown={isClickable ? (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openContent();
          }
        } : undefined}
      >
        <header className="content-card-header">
          <div>
            <div className="content-badge-row">
              <span className="content-type-badge">{contentTypeLabels[item.type] || '资讯'}</span>
              {item.scope === 'market' ? <span className="content-scope-badge">市场</span> : null}
              {item.stale ? <span className="content-health-badge stale">待刷新</span> : null}
            </div>
            <h3>{item.title || '未命名内容'}</h3>
            <p className="snapshot-subtitle">{meta.join(' · ') || item.symbol || '内容情报'}</p>
          </div>
          <div className="content-card-time">
            <span>{formatRelativeDateTime(item.publishedAt || item.firstSeenAt)}</span>
            <span>{formatDateTime(item.publishedAt || item.firstSeenAt)}</span>
          </div>
        </header>
        <p className="content-card-summary">{item.summary || '当前仅同步到标题与基础元信息。'}</p>
        <div className="content-card-footer">
          <span className="content-source">{item.provider || '--'} · {item.source || '--'}</span>
          {item.url ? (
            <a className="content-link" href={item.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
              查看原文
            </a>
          ) : null}
        </div>
      </section>
    );
  }

  function renderContentView() {
    const hasCooldown = Array.isArray(contentStatus.jobs) && contentStatus.jobs.some((job) => job?.isCoolingDown);
    const degradedJobs = Array.isArray(contentStatus.jobs) ? contentStatus.jobs.filter((job) => !job?.isHealthy) : [];
    return (
      <article className="panel wide content-panel">
        <div className="panel-heading">
          <div>
            <h2>资讯情报</h2>
            <p className="panel-tip compact">研报、新闻与公告统一入库，优先保证可回溯与后续回测，而不是伪实时抓取。</p>
          </div>
          <div className="content-status-summary">
            <span className="symbol-count-badge">最近入库 {formatRelativeDateTime(contentStatus.latestIngestedAt)}</span>
            {contentStatus.summary?.degradedJobs ? <span className="symbol-count-badge warning">异常 lane {contentStatus.summary.degradedJobs}</span> : null}
          </div>
        </div>

        <div className="submenu-row content-toolbar-row">
          <div className="content-toolbar-groups">
            <div className="content-switch-group" aria-label="资讯时间范围">
              {Object.entries(contentTimeRangeLabels).map(([value, label]) => (
                <button
                  key={value}
                  className={contentTimeRange === value ? 'content-switch-option active' : 'content-switch-option'}
                  type="button"
                  onClick={() => setContentTimeRange(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="content-filter-row">
              <label className="content-symbol-select-label" htmlFor="content-type-filter">
                内容类型
              </label>
              <select
                id="content-type-filter"
                className="content-symbol-select"
                value={contentType}
                onChange={(event) => setContentType(event.target.value)}
              >
                {Object.entries(contentTypeLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="content-filter-row">
              <label className="content-symbol-select-label" htmlFor="content-symbol-filter">
                股票筛选
              </label>
              <select
                id="content-symbol-filter"
                className="content-symbol-select"
                value={contentSymbolFilter}
                onChange={(event) => setContentSymbolFilter(event.target.value)}
              >
                {contentSymbolOptions.map((option) => (
                  <option key={option.value || 'all'} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {hasCooldown ? <p className="panel-tip compact">当前部分内容 lane 处于冷却期，页面继续使用已入库数据并稍后自动刷新。</p> : null}
        {degradedJobs.length ? (
          <div className="content-health-grid">
            {degradedJobs.map((job) => (
              <article className={`content-health-card ${getLaneStatusTone(job)}`} key={`${job.lane}-${job.symbol || 'market'}`}>
                <div className="content-health-card-header">
                  <strong>{contentLaneLabels[job.lane] || job.lane}</strong>
                  <span>{job.symbol || '全市场'}</span>
                </div>
                <p>
                  {job.lastError
                    ? `最近抓取失败：${job.lastError}`
                    : job.isStale
                      ? '当前 lane 已超过健康刷新窗口，页面继续显示已入库旧数据。'
                      : '当前 lane 状态异常。'}
                </p>
                <div className="content-health-meta">
                  <span>上次成功 {formatRelativeDateTime(job.lastSuccessAt)}</span>
                  <span>失败次数 {job.failureCount || 0}</span>
                </div>
              </article>
            ))}
          </div>
        ) : null}
        {contentRequestState === 'loading' ? <p className="status-line">资讯数据加载中...</p> : null}
        {contentRequestState === 'error' ? <p className="status-line">资讯数据加载失败，请稍后重试。</p> : null}

        <div className="content-feed-scroll-area">
          <div className="content-grid">
            {contentFeed.length ? contentFeed.map((item) => renderContentCard(item)) : <p className="content-empty-state">当前筛选条件下暂无内容情报。</p>}
          </div>
        </div>
      </article>
    );
  }

  function renderDetailView() {
    const detailPayload = selectedDetail?.detail || null;
    const previewBars =
      detailPayload?.dailyBarsPreview && Array.isArray(detailPayload.dailyBarsPreview)
        ? detailPayload.dailyBarsPreview
        : [];
    const fetchedKlines = Array.isArray(selectedDetail?.klines) ? selectedDetail.klines : [];
    const dailyBars = sortBarsAscendingByBucketTs(fetchedKlines.length > previewBars.length ? fetchedKlines : previewBars);
    const intradayMinuteBars =
      detailPayload?.intradayMinuteBars && Array.isArray(detailPayload.intradayMinuteBars)
        ? detailPayload.intradayMinuteBars
        : [];
    const intradaySampledBars =
      detailPayload?.intradaySampledBars && Array.isArray(detailPayload.intradaySampledBars)
        ? detailPayload.intradaySampledBars
        : [];
    const ticks = dedupeTicks(selectedDetail?.ticks || []);
    const latestTickTradeDay = getLatestChinaTradeDayValue(ticks, 'ts');
    const intradayTickPoints = sortItemsAscendingByTime(
      ticks
        .filter((tick) => isSameChinaTradeDay(tick?.ts, latestTickTradeDay || new Date()))
        .map((tick) => ({
          bucketTs: tick.ts,
          close: tick.price,
          open: tick.price,
          high: tick.price,
          low: tick.price,
          volume: tick.volume,
          amount: tick.amount,
          source: tick.source,
        })),
      'bucketTs',
    );
    const intradayChartPoints = intradayMinuteBars.length
      ? intradayMinuteBars
      : intradaySampledBars.length
        ? intradaySampledBars
        : intradayTickPoints;
    const latestKline = dailyBars.length
      ? dailyBars[dailyBars.length - 1]
      : intradayChartPoints.length
        ? intradayChartPoints[intradayChartPoints.length - 1]
        : detailPayload?.latestKline || null;
    const latestEvent = detailPayload?.latestEvent || null;
    const orderBook = detailPayload?.orderBook || {};
    const capabilities = detailPayload?.capabilities || {};
    const events = dedupeEvents(selectedDetail?.events || []);
    const readableEvents = buildReadableEventItems(events);
    const snapshot = selectedSnapshot || detailPayload?.snapshot || null;
    const previousClose = estimatePreviousClose(snapshot, latestKline, intradayChartPoints);
    const candleChart = dailyBars.length ? buildCandlestickChartGeometry(dailyBars, 860, 248) : null;
    const movingAverageOverlays = candleChart ? buildMovingAverageOverlays(dailyBars, candleChart.candles, candleChart.scaleY) : [];
    const volumeChart = dailyBars.length ? buildVolumeChartGeometry(dailyBars, 860, 84) : null;
    const intradayLineChart = intradayChartPoints.length ? buildLineChartGeometry(intradayChartPoints, 860, 320, previousClose ?? snapshot?.lastPrice ?? null) : null;
    const emptyIntradayChart = !intradayLineChart ? buildEmptyIntradayChartGeometry(previousClose ?? snapshot?.lastPrice) : null;
    const showingIntraday = detailChartView === 'intraday';
    const activeChartTitle = showingIntraday ? '分时走势' : '日 K 走势';
    const intradayAxisLabels = intradayLineChart?.timeMarks || [];
    const showIntradayLunchBreak = typeof intradayLineChart?.lunchBreakStartX === 'number' && typeof intradayLineChart?.lunchBreakEndX === 'number';
    const dailyAxisLabels = buildAxisLabels(candleChart?.candles || [], 6, (item) => item?.label || '');
    const recentTicks = sortItemsAscendingByTime(ticks, 'ts').slice(-12).reverse();
    const intradayTone = getLineChartTone(intradayLineChart, snapshot?.lastPrice ?? previousClose ?? null);
    const intradayDataDate = getLatestChinaTradeDayValue(intradayChartPoints, 'bucketTs') || latestTickTradeDay;
    const intradayDateLabel = intradayDataDate ? formatDate(intradayDataDate) : null;
    const intradayDataMode = intradayMinuteBars.length
      ? 'minute'
      : intradaySampledBars.length
        ? 'sampled'
        : intradayTickPoints.length
          ? 'tick'
          : 'empty';
    const intradayDataModeLabel = intradayDataMode === 'minute'
      ? '1 分钟精度'
      : intradayDataMode === 'sampled'
        ? '5 分钟聚合'
        : intradayDataMode === 'tick'
          ? 'Tick 回退'
          : '暂无数据';

    return (
      <div className="detail-layout">
        <div className="panel-heading detail-heading">
          <div>
            <button type="button" className="back-button" onClick={handleCloseSnapshotDetail}>
              返回总览
            </button>
            <h2>{snapshot?.companyName || '个股详情'}</h2>
            <p className="snapshot-subtitle">
              {selectedSnapshotSymbol} · {snapshot?.exchange || '--'}
            </p>
          </div>
          <div className="detail-meta">
            <span className="detail-price">{formatPrice(snapshot?.lastPrice)}</span>
            <span className={snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}>
              {formatSignedPercent(snapshot?.changePct)}
            </span>
          </div>
        </div>

        {detailRequestState === 'loading' ? <p className="status-line">详情数据加载中...</p> : null}
        {detailRequestState === 'error' ? <p className="status-line">详情数据加载失败，请稍后重试。</p> : null}

        <div className="detail-hero-grid">
          <section className="detail-card chart-card">
            <div className="chart-card-header">
              <div>
                <h3>{activeChartTitle}</h3>
                <p className="panel-tip compact">
                  {showingIntraday
                    ? `${intradayMinuteBars.length ? '优先展示已入库的 1 分钟精度分时线' : intradaySampledBars.length ? '当前展示 5 分钟聚合分时线' : '当前回退为 Tick 分时线'}，并按日内波动自动缩放${intradayDateLabel ? `；当前展示 ${intradayDateLabel} 数据。` : '。'}`
                    : '日 K 优先展示详情接口返回的 60 根日线，并与当前已入库历史保持一致。'}
                </p>
              </div>
              <div className="chart-summary-badge">
                <span className="chart-summary-label">最新价</span>
                <strong className="chart-summary-price">{formatPrice(snapshot?.lastPrice)}</strong>
                <span className={`chart-summary-change ${snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}`}>
                  {formatSignedPercent(snapshot?.changePct)}
                </span>
              </div>
            </div>

            <div className="chart-view-tabs" role="tablist" aria-label="详情图表切换">
              <button
                type="button"
                className={showingIntraday ? 'view-tab active' : 'view-tab'}
                onClick={() => setDetailChartView('intraday')}
              >
                分时
              </button>
              <button
                type="button"
                className={!showingIntraday ? 'view-tab active' : 'view-tab'}
                onClick={() => setDetailChartView('daily')}
              >
                日K
              </button>
            </div>

            {showingIntraday ? (
              <div className="chart-stack">
                {intradayLineChart ? (
                  <div className="chart-section">
                    <div className="chart-section-heading">
                      <h4>
                        分时线
                        <span className={`intraday-render-badge intraday-render-badge-${intradayDataMode}`}>{intradayDataModeLabel}</span>
                      </h4>
                      <span>{intradayDateLabel ? `${intradayDateLabel} · ` : ''}{intradayChartPoints.length} 个点位 · {intradayDataModeLabel} · 白线价格 / 黄线均价</span>
                    </div>
                    <svg className={`kline-chart line-chart-surface ${intradayTone}-tone`} viewBox={`0 0 ${intradayLineChart.width} ${intradayLineChart.height}`} role="img" aria-label="分时采样走势">
                      <defs>
                        <linearGradient id="intradayAreaGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" className="line-chart-area-stop start" />
                          <stop offset="100%" className="line-chart-area-stop end" />
                        </linearGradient>
                        <linearGradient id="intradayPathGradient" x1="0" y1="0" x2="1" y2="0">
                          <stop offset="0%" className="line-chart-path-stop start" />
                          <stop offset="100%" className="line-chart-path-stop end" />
                        </linearGradient>
                        <filter id="intradayLineGlow" x="-10%" y="-10%" width="120%" height="120%">
                          <feGaussianBlur stdDeviation="3.5" result="blur" />
                          <feMerge>
                            <feMergeNode in="blur" />
                            <feMergeNode in="SourceGraphic" />
                          </feMerge>
                        </filter>
                      </defs>
                      {intradayLineChart.ticks.map((tick) => (
                        <g key={`${tick.value}`}>
                          <line x1="0" x2={intradayLineChart.width} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                          <text x={intradayLineChart.width - 6} y={tick.y - 4} textAnchor="end" className="chart-axis-label chart-axis-price-label">
                            {tick.value.toFixed(2)}
                          </text>
                          <text x={intradayLineChart.width - 72} y={tick.y - 4} textAnchor="end" className={`chart-axis-label ${tick.delta > 0 ? 'axis-positive' : tick.delta < 0 ? 'axis-negative' : ''}`}>
                            {formatSignedPriceDelta(tick.delta)}
                          </text>
                          <text x={intradayLineChart.width - 136} y={tick.y - 4} textAnchor="end" className={`chart-axis-label ${tick.percent > 0 ? 'axis-positive' : tick.percent < 0 ? 'axis-negative' : ''}`}>
                            {formatSignedAxisPercent(tick.percent)}
                          </text>
                        </g>
                      ))}
                      {intradayLineChart.baselineY !== null ? (
                        <g>
                          <line x1="0" x2={intradayLineChart.width} y1={intradayLineChart.baselineY} y2={intradayLineChart.baselineY} className="empty-chart-guide" />
                          <text x="6" y={intradayLineChart.baselineY - 6} className="chart-axis-label baseline-label">昨收 {formatPrice(intradayLineChart.previousClose)}</text>
                        </g>
                      ) : null}
                      {showIntradayLunchBreak ? (
                        <>
                          <rect
                            x={intradayLineChart.lunchBreakStartX}
                            y="0"
                            width={Math.max(intradayLineChart.lunchBreakEndX - intradayLineChart.lunchBreakStartX, 0)}
                            height={intradayLineChart.height}
                            className="lunch-break-mask"
                          />
                          <text
                            x={(intradayLineChart.lunchBreakStartX + intradayLineChart.lunchBreakEndX) / 2}
                            y="18"
                            textAnchor="middle"
                            className="chart-axis-label lunch-break-label"
                          >
                            午间休市
                          </text>
                        </>
                      ) : null}
                      <polygon points={intradayLineChart.area} className="line-chart-area" />
                      {intradayLineChart.averagePolyline ? <polyline points={intradayLineChart.averagePolyline} className="line-chart-average-path" /> : null}
                      <polyline points={intradayLineChart.polyline} className="line-chart-path line-chart-path-glow" />
                      <polyline points={intradayLineChart.polyline} className="line-chart-path" />
                      {intradayLineChart.highPoint ? (
                        <text x={Math.min(intradayLineChart.highPoint.x + 8, intradayLineChart.width - 70)} y={Math.max(intradayLineChart.highPoint.y - 10, 18)} className="chart-extrema-label">
                          高 {formatPrice(intradayLineChart.highPoint.close)}
                        </text>
                      ) : null}
                      {intradayLineChart.lowPoint ? (
                        <text x={Math.min(intradayLineChart.lowPoint.x + 8, intradayLineChart.width - 70)} y={Math.min(intradayLineChart.lowPoint.y + 18, intradayLineChart.height - 10)} className="chart-extrema-label">
                          低 {formatPrice(intradayLineChart.lowPoint.close)}
                        </text>
                      ) : null}
                      {intradayLineChart.latestPoint ? (
                        <g>
                          <line
                            x1="0"
                            x2={intradayLineChart.width}
                            y1={intradayLineChart.latestPoint.y}
                            y2={intradayLineChart.latestPoint.y}
                            className="current-price-guide"
                          />
                          <rect
                            x={intradayLineChart.width - 82}
                            y={Math.max(intradayLineChart.latestPoint.y - 13, 4)}
                            width="76"
                            height="22"
                            rx="10"
                            className="current-price-badge"
                          />
                          <text
                            x={intradayLineChart.width - 44}
                            y={Math.max(intradayLineChart.latestPoint.y + 2, 18)}
                            textAnchor="middle"
                            className="current-price-badge-text"
                          >
                            {formatPrice(intradayLineChart.latestPoint.close)}
                          </text>
                        </g>
                      ) : null}
                      {intradayLineChart.chartPoints.length ? (
                        <circle
                          cx={intradayLineChart.chartPoints[intradayLineChart.chartPoints.length - 1].x}
                          cy={intradayLineChart.chartPoints[intradayLineChart.chartPoints.length - 1].y}
                          r="4.4"
                          className="line-chart-point"
                        />
                      ) : null}
                    </svg>
                    <div className="chart-axis-row">
                      {intradayAxisLabels.map((item) => <span key={item.label}>{item.label}</span>)}
                    </div>
                    <div className="intraday-legend-row">
                      <span className="intraday-legend-chip"><span className="intraday-legend-line price" aria-hidden="true" />价格线</span>
                      <span className="intraday-legend-chip"><span className="intraday-legend-line average" aria-hidden="true" />均价线</span>
                      <span className="intraday-legend-chip">右轴：价格 / 涨跌额 / 涨跌幅</span>
                    </div>
                    {!intradayLineChart.referenceVisible && intradayLineChart.previousClose !== null ? (
                      <p className="panel-tip compact">昨收 {formatPrice(intradayLineChart.previousClose)} · 当前图表按日内价格波动缩放以突出趋势。</p>
                    ) : null}
                  </div>
                ) : (
                  <div className="chart-section">
                    <div className="chart-section-heading">
                      <h4>
                        分时线
                        <span className="intraday-render-badge intraday-render-badge-empty">暂无数据</span>
                      </h4>
                      <span>暂无可用精确分时数据</span>
                    </div>
                    <svg className="kline-chart empty-chart" viewBox={`0 0 ${emptyIntradayChart.width} ${emptyIntradayChart.height}`} role="img" aria-label="空白分时走势骨架">
                      {emptyIntradayChart.ticks.map((tick) => (
                        <g key={`${tick.value}-${tick.y}`}>
                          <line x1="0" x2={emptyIntradayChart.width} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                          <text x={emptyIntradayChart.width - 6} y={tick.y - 4} textAnchor="end" className="chart-axis-label">
                            {tick.label}
                          </text>
                          <text x={emptyIntradayChart.width - 64} y={tick.y - 4} textAnchor="end" className={`chart-axis-label ${tick.percent > 0 ? 'axis-positive' : tick.percent < 0 ? 'axis-negative' : ''}`}>
                            {formatSignedAxisPercent(tick.percent)}
                          </text>
                        </g>
                      ))}
                      <rect
                        x={emptyIntradayChart.lunchBreakStartX}
                        y="0"
                        width={Math.max(emptyIntradayChart.lunchBreakEndX - emptyIntradayChart.lunchBreakStartX, 0)}
                        height={emptyIntradayChart.height}
                        className="lunch-break-mask"
                      />
                      <line x1="0" x2={emptyIntradayChart.width} y1={emptyIntradayChart.guideY} y2={emptyIntradayChart.guideY} className="empty-chart-guide" />
                    </svg>
                    <div className="chart-axis-row">
                      {emptyIntradayChart.timeMarks.map((item) => <span key={item.label}>{item.label}</span>)}
                    </div>
                    <p className="panel-tip compact">数据库中还没有可聚合的历史分时采样点。</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="chart-stack">
                {candleChart ? (
                  <div className="chart-section">
                    <div className="chart-section-heading">
                      <h4>日 K</h4>
                      <span>{dailyBars.length} 根日线</span>
                    </div>
                    <svg className="kline-chart daily-kline-chart" viewBox={`0 0 ${candleChart.width} ${candleChart.height}`} role="img" aria-label="日K蜡烛图">
                      {candleChart.ticks.map((tick) => (
                        <g key={`${tick.value}`}>
                          <line x1="0" x2={candleChart.width} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                          <text x={candleChart.width - 6} y={tick.y - 4} textAnchor="end" className="chart-axis-label">
                            {tick.value.toFixed(2)}
                          </text>
                        </g>
                      ))}
                      {movingAverageOverlays.map((overlay) =>
                        overlay.polyline ? (
                          <polyline key={overlay.label} points={overlay.polyline} className={overlay.colorClass} />
                        ) : null,
                      )}
                      {candleChart.candles.map((candle) => (
                        <g key={`${candle.index}-${candle.label}`}>
                          <line x1={candle.x} x2={candle.x} y1={candle.highY} y2={candle.lowY} className={candle.rising ? 'candle-wick up' : 'candle-wick down'} />
                          <rect
                            x={candle.bodyLeft}
                            y={candle.bodyTop}
                            width={candle.candleWidth}
                            height={candle.bodyHeight}
                            rx="1"
                            className={candle.rising ? 'candle-body up' : 'candle-body down'}
                          />
                        </g>
                      ))}
                    </svg>
                    {volumeChart ? (
                        <svg className="volume-chart daily-volume-chart" viewBox={`0 0 ${volumeChart.width} ${volumeChart.height}`} role="img" aria-label="日K成交量柱图">
                        <line x1="0" x2={volumeChart.width} y1={volumeChart.height - 20} y2={volumeChart.height - 20} className="chart-grid-line" />
                        {volumeChart.items.map((item) => (
                          <rect
                            key={`volume-${item.index}`}
                            x={item.x}
                            y={item.y}
                            width={item.width}
                            height={item.height}
                            rx="1"
                            className={item.rising ? 'volume-bar up' : 'volume-bar down'}
                          />
                        ))}
                        <text x={volumeChart.width - 6} y="14" textAnchor="end" className="chart-axis-label">
                          Vol {formatCompactNumber(volumeChart.maxVolume)}
                        </text>
                      </svg>
                    ) : (
                      <p className="panel-tip compact">当前日 K 缺少可用成交量数据。</p>
                    )}
                     {movingAverageOverlays.some((overlay) => overlay.latestValue !== null) ? (
                       <div className="ma-legend-row">
                         {movingAverageOverlays.map((overlay) =>
                           overlay.latestValue !== null ? (
                            <span key={overlay.label} className="ma-chip">
                              <span className={`ma-dot ${overlay.colorClass.replace('ma-line ', '')}`} aria-hidden="true" />
                              <span className="ma-legend">{overlay.label} {formatPrice(overlay.latestValue)}</span>
                            </span>
                           ) : null,
                         )}
                       </div>
                    ) : null}
                    <div className="chart-axis-row">
                      {dailyAxisLabels.map((item) => <span key={item.key}>{item.label}</span>)}
                    </div>
                  </div>
                ) : (
                  <p className="panel-tip compact">数据库中暂时没有可展示的日 K 数据。</p>
                )}
              </div>
            )}
          </section>

          <section className="detail-card market-summary-card">
            <h3>行情摘要</h3>
            <div className="market-summary-grid">
              <article className="summary-metric summary-metric-strong">
                <span>最新价</span>
                <strong>{formatPrice(snapshot?.lastPrice)}</strong>
                <em className={snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}>
                  {formatSignedPercent(snapshot?.changePct)}
                </em>
              </article>
              <article className="summary-metric">
                <span>今日振幅</span>
                <strong>
                  {latestKline
                    ? `${formatPrice(latestKline.high)} / ${formatPrice(latestKline.low)}`
                    : '--'}
                </strong>
                <em>高 / 低</em>
              </article>
              <article className="summary-metric">
                <span>开 / 收</span>
                <strong>
                  {latestKline
                    ? `${formatPrice(latestKline.open)} / ${formatPrice(latestKline.close)}`
                    : '--'}
                </strong>
                <em>开盘 / 最新收盘</em>
              </article>
              <article className="summary-metric">
                <span>成交量</span>
                <strong>{formatTickVolume(latestKline?.volume)}</strong>
                <em>最新成交量（股）</em>
              </article>
              <article className="summary-metric">
                <span>成交额</span>
                <strong>{formatTurnoverAmount(latestKline?.amount)}</strong>
                <em>最新成交额（元）</em>
              </article>
              <article className="summary-metric">
                <span>更新时间</span>
                <strong>{formatTime(snapshot?.updatedAt)}</strong>
                <em>{formatDateTime(snapshot?.updatedAt)}</em>
              </article>
            </div>
          </section>
        </div>

        <div className="detail-grid">
          <section className="detail-card">
               <h3>基础指标</h3>
            <dl>
              <div>
                <dt>更新时间</dt>
                <dd>{formatDateTime(snapshot?.updatedAt)}</dd>
              </div>
              <div>
                <dt>数据来源</dt>
                <dd>{snapshot?.source || '--'}</dd>
              </div>
              <div>
                <dt>PE / PB</dt>
                <dd>{snapshot ? `${snapshot.pe ?? '--'} / ${snapshot.pb ?? '--'}` : '--'}</dd>
              </div>
              <div>
                <dt>换手率</dt>
                <dd>{snapshot?.turnoverRate ?? '--'}</dd>
              </div>
              <div>
                <dt>总市值</dt>
                <dd>{formatCompactNumber(snapshot?.marketCap)}</dd>
              </div>
            </dl>
          </section>

          <section className="detail-card">
             <h3>盘口摘要</h3>
             <dl>
              <div>
                <dt>买一</dt>
                <dd>
                  {orderBook?.bid1 != null || orderBook?.bidVolume1 != null
                    ? `${formatPrice(orderBook?.bid1)} / ${formatTickVolume(orderBook?.bidVolume1)}`
                    : '--'}
                </dd>
              </div>
              <div>
                <dt>卖一</dt>
                <dd>
                  {orderBook?.ask1 != null || orderBook?.askVolume1 != null
                    ? `${formatPrice(orderBook?.ask1)} / ${formatTickVolume(orderBook?.askVolume1)}`
                    : '--'}
                </dd>
              </div>
            </dl>
            <p className="panel-tip compact">
              {!capabilities?.supportsBestBidAsk ? '当前数据源未稳定提供买一卖一；' : ''} 暂不提供五档盘口。
            </p>
          </section>

          <section className="detail-card wide-card">
            <div className="detail-card-header compact-card-header">
              <div>
                <h3>最近 Tick</h3>
                <p className="panel-tip compact">默认只展示最新 12 条；成交量按股展示，成交额按元展示，避免单位误读。</p>
              </div>
              {ticks.length > recentTicks.length ? <span className="table-meta-badge">共 {ticks.length} 条</span> : null}
            </div>
            {recentTicks.length ? (
              <div className="detail-table-wrap detail-table-wrap-scrollable">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>时间</th>
                      <th>价格</th>
                      <th>成交量</th>
                      <th>成交额</th>
                      <th>方向</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentTicks.map((tick, index) => (
                      <tr key={`${tick.ts}-${tick.price}-${index}`}>
                        <td>{formatDate(tick.ts)}</td>
                        <td>{formatTime(tick.ts)}</td>
                        <td>{formatPrice(tick.price)}</td>
                        <td>{formatTickVolume(tick.volume)}</td>
                        <td>{formatTurnoverAmount(tick.amount)}</td>
                        <td>{tick.side === 'buy' ? '买盘' : tick.side === 'sell' ? '卖盘' : '--'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="panel-tip compact">暂无 tick 数据。</p>
            )}
          </section>

          <section className="detail-card wide-card">
            <h3>最近事件</h3>
            {readableEvents.length ? (
              <ul className="detail-event-list">
                {readableEvents.map((event, index) => (
                  <li key={`${event.time}-${event.identity}-${index}`}>
                    <div className="detail-event-main">
                      <strong>{event.eventType}</strong>
                      <span>{formatDateTime(event.time)}</span>
                    </div>
                    <div className="detail-event-meta">
                      <span>{typeof event.price === 'number' ? `价格 ${formatPrice(event.price)}` : '价格 --'}</span>
                      <span>{event.side === 'buy' ? '买盘' : event.side === 'sell' ? '卖盘' : '方向 --'}</span>
                       <span>{typeof event.volume === 'number' ? `量 ${formatTickVolume(event.volume)}` : '量 --'}</span>
                      <span>
                        {typeof event.low === 'number' && typeof event.high === 'number'
                          ? `区间 ${formatPrice(event.low)} ~ ${formatPrice(event.high)}`
                          : '区间 --'}
                      </span>
                      <span>{typeof event.close === 'number' ? `收 ${formatPrice(event.close)}` : '收 --'}</span>
                      <span>{typeof event.changePct === 'number' ? `涨跌 ${formatSignedPercent(event.changePct)}` : '涨跌 --'}</span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="panel-tip compact">暂无事件数据。</p>
            )}
          </section>
        </div>
      </div>
    );
  }

  return (
    <main className="layout">
      <section className="panel hero">
        <p className="eyebrow">项目 · MoneyRush</p>
        <h1>实时行情看板</h1>
        <p className="lede">
          用总览看全局，用事件看异动，用管理页维护监控标的。
        </p>
        <div className="hero-toolbar">
          <button
            className={activeView === 'overview' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('overview')}
          >
            总览
          </button>
          <button
            className={activeView === 'events' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('events')}
          >
            事件
          </button>
          <button
            className={activeView === 'content' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('content')}
          >
            资讯
          </button>
          <button
            className={activeView === 'management' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('management')}
          >
            管理
          </button>
        </div>
      </section>

      <section className="grid single-column">
        {activeView === 'overview' ? (
          <article className="panel wide">
            {selectedSnapshotSymbol ? (
              renderDetailView()
            ) : (
              <>
                <div className="section-heading">
                  <div>
                    <h2>快照总览</h2>
                    <p className="panel-tip compact">按标的汇总最新价格、涨跌幅和关键估值指标，点击卡片可查看详情。</p>
                  </div>
                </div>
                <div className="snapshot-grid">
                  {activeSymbols.length ? (
                    activeSymbols.map((item) => {
                      const snapshot = snapshots[item];
                      return renderOverviewCard(item, snapshot);
                    })
                  ) : (
                    <p>尚未生成快照数据。</p>
                  )}
                </div>
              </>
            )}
          </article>
        ) : activeView === 'content' ? (
          renderContentView()
        ) : activeView === 'management' ? (
          <article className="panel wide management-panel">
            <div className="panel-heading">
              <div>
                <h2>监控管理</h2>
                <p className="panel-tip compact">新增、查看和移除监控标的都集中在这里。</p>
              </div>
              <div className="management-meta">
                <span className="symbol-count-badge">监控中 {activeSymbols.length}</span>
              </div>
            </div>
            <div className="submenu-row">
              <div className="submenu-tabs">
                <button
                  className={controlView === 'activate' ? 'view-tab active' : 'view-tab'}
                  type="button"
                  onClick={() => setControlView('activate')}
                >
                  添加标的
                </button>
                <button
                  className={controlView === 'watchlist' ? 'view-tab active' : 'view-tab'}
                  type="button"
                  onClick={() => setControlView('watchlist')}
                >
                  监控列表
                </button>
              </div>
            </div>

            {controlView === 'activate' ? (
              <form className="symbol-form compact-form" onSubmit={handleSubmit}>
                <label htmlFor="symbol">股票代码</label>
                <div className="inline-form-row">
                  <input id="symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 000001" />
                  <button type="submit" disabled={requestState === 'submitting'}>{requestState === 'submitting' ? '提交中…' : '激活监控'}</button>
                </div>
                <p className="panel-tip compact">请输入 6 位股票代码，如 000001。</p>
              </form>
            ) : (
              <div className="watchlist-panel">
                <div className="watchlist-grid">
                  {activeSymbols.length ? (
                    activeSymbols.map((item) => renderWatchlistCard(item))
                  ) : (
                    <p className="panel-tip compact">尚无激活标的。</p>
                  )}
                </div>
              </div>
            )}

            <div className="management-status-row">
               <p className={`status-line ${getRequestStatusTone(requestState)}`}>请求状态：{getRequestStatusLabel(requestState)}</p>
                <p className="status-line">实时连接：{getConnectionStatusLabel(connectionState)}</p>
              </div>
          </article>
        ) : (
          <article className="panel wide">
            <div className="section-heading">
              <div>
                <h2>实时事件</h2>
                <p className="panel-tip compact">按标的查看最近一条结构化事件，避免原始流消息干扰主看板。</p>
              </div>
            </div>
            <div className="event-grid">
              {eventCards.length ? (
                eventCards.map(({ symbol: currentSymbol, snapshot, event }) => (
                  <section className="event-card" key={currentSymbol}>
                    <header>
                      <div>
                        <strong>{snapshot?.companyName || event?.companyName || '待识别公司'}</strong>
                        <p className="snapshot-subtitle">
                          {currentSymbol} · {snapshot?.exchange || event?.exchange || '--'}
                        </p>
                      </div>
                      <span>{formatTime(event?.generatedAt || snapshot?.updatedAt)}</span>
                    </header>
                    <dl>
                      <div>
                        <dt>最新价</dt>
                        <dd>{formatPrice(event?.tick?.price ?? snapshot?.lastPrice)}</dd>
                      </div>
                      <div>
                        <dt>成交量</dt>
                        <dd>{formatTickVolume(event?.tick?.volume)}</dd>
                      </div>
                      <div>
                        <dt>买卖方向</dt>
                        <dd>{event?.tick?.side === 'buy' ? '买盘' : event?.tick?.side === 'sell' ? '卖盘' : '--'}</dd>
                      </div>
                      <div>
                        <dt>K线周期</dt>
                        <dd>{event?.kline?.period || '--'}</dd>
                      </div>
                      <div>
                        <dt>K线区间</dt>
                        <dd>{event?.kline ? `${formatPrice(event.kline.low)} ~ ${formatPrice(event.kline.high)}` : '--'}</dd>
                      </div>
                      <div>
                        <dt>收盘价</dt>
                        <dd>{formatPrice(event?.kline?.close)}</dd>
                      </div>
                    </dl>
                  </section>
                ))
              ) : (
                <p>暂无结构化事件数据。</p>
              )}
            </div>
          </article>
        )}
      </section>
    </main>
  );
}

export default App;
