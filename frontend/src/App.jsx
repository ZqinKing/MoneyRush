import { useEffect, useMemo, useState } from 'react';

function getBrowserHostname() {
  if (typeof window === 'undefined') {
    return null;
  }

  return window.location.hostname;
}

function getBrowserOrigin() {
  if (typeof window === 'undefined') {
    return null;
  }

  return window.location.origin;
}

function isLoopbackHostname(hostname) {
  return hostname === 'localhost' || hostname === '127.0.0.1';
}

function isBindableLocalHostname(hostname) {
  return isLoopbackHostname(hostname) || hostname === '0.0.0.0';
}

function normalizeConfiguredUrl(configuredUrl) {
  const browserHostname = getBrowserHostname();
  const rawUrl = configuredUrl || '';

  if (!rawUrl) {
    return '';
  }

  try {
    const parsedUrl = new URL(rawUrl);
    if (browserHostname && isBindableLocalHostname(parsedUrl.hostname)) {
      parsedUrl.hostname = browserHostname;
    }
    if (browserHostname && isBindableLocalHostname(browserHostname) && parsedUrl.hostname !== browserHostname) {
      parsedUrl.hostname = browserHostname;
    }
    return parsedUrl.toString().replace(/\/$/, '');
  } catch {
    return rawUrl.replace(/\/$/, '');
  }
}

function getDefaultWebSocketBaseUrl() {
  const browserOrigin = getBrowserOrigin();
  if (!browserOrigin) {
    return 'ws://localhost:5173';
  }

  const parsedOrigin = new URL(browserOrigin);
  parsedOrigin.protocol = parsedOrigin.protocol === 'https:' ? 'wss:' : 'ws:';
  return parsedOrigin.toString().replace(/\/$/, '');
}

const apiBaseUrl = normalizeConfiguredUrl(import.meta.env.VITE_API_BASE_URL);
const wsBaseUrl = normalizeConfiguredUrl(import.meta.env.VITE_WS_BASE_URL) || getDefaultWebSocketBaseUrl();

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

const marketStatusLabels = {
  trading: '交易中',
  break: '休市中',
  closed: '已收盘',
  disconnected: '连接断开',
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

const dragonTigerDefaultStockDisplayLimit = 100;
const dragonTigerDefaultPageSize = 20;

const dragonTigerDailyTabs = [
  { value: 'single', label: '单日榜' },
  { value: 'three', label: '三日榜' },
  { value: 'ten', label: '十日榜' },
  { value: 'month', label: '月榜' },
];

const dragonTigerDailyTabLabels = {
  single: '单日榜',
  three: '三日榜',
  ten: '十日榜',
  month: '月榜',
};

const dragonTigerDailyTabPriority = {
  single: 0,
  three: 1,
  ten: 2,
  month: 3,
};

const llmAuditModuleLabels = {
  content: '资讯',
  events: '异动日报',
  funds: '基金',
  macro: '美债宏观',
};

const llmAuditCategoryLabels = {
  ai_summary: 'AI摘要',
  anomaly_reason: '异动归因',
  fund_portfolio_risk_analysis: '基金风险解读',
  macro_analysis: '宏观解读',
};

const llmAuditStatusLabels = {
  completed: '完成',
  failed: '失败',
  skipped: '跳过',
};

const dragonTigerSortOptions = {
  daily: [
    { value: 'netBuyAmount', label: '按净买额' },
    { value: 'dealAmount', label: '按成交额' },
    { value: 'changePercent', label: '按涨跌幅' },
    { value: 'closePrice', label: '按收盘价' },
    { value: 'tradeDate', label: '按上榜日' },
  ],
  stocks: [
    { value: 'billboardTimes', label: '按上榜次数' },
    { value: 'netBuyAmount', label: '按净买额' },
    { value: 'orgNetBuyAmount', label: '按机构净买额' },
    { value: 'latestTradeDate', label: '按最近上榜日' },
  ],
};

function buildContentFeedUrl({ symbol = '', type = 'all', timeRange = 'today', limit = 20, before = '' }) {
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
  if (before) {
    params.set('before', before);
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

function buildDailyAnomalyReportUrl({ portfolioOnly = false, sortBy = 'relevance' } = {}) {
  const params = new URLSearchParams();
  params.set('sort_by', sortBy);
  if (portfolioOnly) {
    params.set('portfolio_only', 'true');
  }
  return `${apiBaseUrl}/api/v1/anomaly/daily?${params.toString()}`;
}

function buildMarketOverviewUrl() {
  return `${apiBaseUrl}/api/v1/market/overview`;
}

function buildGoldDashboardUrl() {
  return `${apiBaseUrl}/api/v1/gold/dashboard`;
}

function buildMacroCapabilitiesUrl() {
  return `${apiBaseUrl}/api/v1/macro/capabilities`;
}

function buildMacroSnapshotUrl() {
  return `${apiBaseUrl}/api/v1/macro/snapshot`;
}

function buildMacroAnalysisLatestUrl() {
  return `${apiBaseUrl}/api/v1/macro/analysis/latest`;
}

function buildMacroAnalysisGenerateUrl() {
  return `${apiBaseUrl}/api/v1/macro/analysis/generate`;
}

function buildFundPortfolioUrl() {
  return `${apiBaseUrl}/api/v1/funds/portfolio`;
}

function buildFundPortfolioAnalysisUrl() {
  return `${apiBaseUrl}/api/v1/funds/portfolio/analysis`;
}

function buildLlmAuditCapabilitiesUrl() {
  return `${apiBaseUrl}/api/v1/llm-audit/capabilities`;
}

function buildLlmAuditDailyUrl() {
  return `${apiBaseUrl}/api/v1/llm-audit/daily`;
}

function buildDragonTigerDailyUrl(tradeDate = '') {
  const params = new URLSearchParams();
  if (tradeDate) {
    params.set('date', tradeDate);
  }
  const query = params.toString();
  return `${apiBaseUrl}/api/v1/dragon-tiger/daily${query ? `?${query}` : ''}`;
}

function buildDragonTigerStocksUrl(range = '1month') {
  const params = new URLSearchParams();
  params.set('range', range);
  return `${apiBaseUrl}/api/v1/dragon-tiger/stocks?${params.toString()}`;
}

function buildDragonTigerInstitutionUrl(startDate = '', endDate = '') {
  const params = new URLSearchParams();
  if (startDate) {
    params.set('startDate', startDate);
  }
  if (endDate) {
    params.set('endDate', endDate);
  }
  return `${apiBaseUrl}/api/v1/dragon-tiger/institution?${params.toString()}`;
}

function buildDragonTigerBranchRankUrl(range = '1month') {
  const params = new URLSearchParams();
  params.set('range', range);
  return `${apiBaseUrl}/api/v1/dragon-tiger/branch-rank?${params.toString()}`;
}

function buildDragonTigerSeatDatesUrl(symbol = '') {
  return `${apiBaseUrl}/api/v1/dragon-tiger/stock/${encodeURIComponent(symbol)}/seat-dates`;
}

function buildDragonTigerSeatDetailUrl(symbol = '', tradeDate = '', side = 'buy') {
  const params = new URLSearchParams();
  if (tradeDate) {
    params.set('date', tradeDate);
  }
  params.set('side', side);
  return `${apiBaseUrl}/api/v1/dragon-tiger/stock/${encodeURIComponent(symbol)}/seat-detail?${params.toString()}`;
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

function formatAgeLabel(value) {
  if (!value) {
    return '等待首个快照';
  }

  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return '时间未知';
  }

  const ageSeconds = Math.max(Math.floor((Date.now() - timestamp) / 1000), 0);
  if (ageSeconds < 5) {
    return '刚更新';
  }
  if (ageSeconds < 60) {
    return `${ageSeconds} 秒前更新`;
  }

  const ageMinutes = Math.floor(ageSeconds / 60);
  if (ageMinutes < 60) {
    return `${ageMinutes} 分钟前更新`;
  }

  const ageHours = Math.floor(ageMinutes / 60);
  return `${ageHours} 小时前更新`;
}

function getSnapshotCardTone(changePct) {
  if (typeof changePct !== 'number' || Number.isNaN(changePct)) {
    return 'neutral';
  }

  if (changePct > 0) {
    return 'positive';
  }

  if (changePct < 0) {
    return 'negative';
  }

  return 'neutral';
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

function dedupeContentItems(items) {
  if (!Array.isArray(items)) {
    return [];
  }

  const seen = new Set();
  return items.filter((item) => {
    const identity = `${item?.type || 'content'}-${item?.id || item?.publishedAt || item?.firstSeenAt || item?.title || ''}`;
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

function formatSignedNumber(value, unit = '') {
  if (typeof value !== 'number') {
    return '--';
  }
  const fixed = Math.abs(value) >= 10 ? value.toFixed(1) : value.toFixed(2);
  return `${value > 0 ? '+' : ''}${fixed}${unit}`;
}

function formatSignedBasisPoints(value) {
  return formatSignedNumber(value, 'bp');
}

function formatMacroYield(value) {
  return typeof value === 'number' ? `${value.toFixed(2)}%` : '--';
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

function formatReportQuarter(value) {
  if (!value) {
    return '报告期未知';
  }
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return '报告期未知';
  }
  const date = new Date(timestamp);
  const quarter = Math.floor(date.getUTCMonth() / 3) + 1;
  return `${date.getUTCFullYear()}Q${quarter}`;
}

function formatFreshnessDays(value) {
  if (typeof value !== 'number') {
    return '滞后未知';
  }
  return `距今 ${value} 天`;
}

function getRiskSignalTone(signal) {
  const severity = signal?.severity;
  if (severity === 'high') {
    return 'negative';
  }
  if (severity === 'warning') {
    return 'warning';
  }
  return 'muted';
}

function getDragonTigerComparableValue(item, key) {
  if (!item || !key) {
    return null;
  }

  switch (key) {
    case 'billboardTimes':
      return typeof item.billboardTimes === 'number' ? item.billboardTimes : typeof item.count === 'number' ? item.count : null;
    case 'orgNetBuyAmount':
      return typeof item.orgNetBuyAmount === 'number' ? item.orgNetBuyAmount : null;
    case 'latestTradeDate':
    case 'tradeDate': {
      const rawValue = key === 'latestTradeDate' ? (item.latestTradeDate || item.latestDate) : item.tradeDate;
      const timestamp = rawValue ? new Date(rawValue).getTime() : Number.NaN;
      return Number.isNaN(timestamp) ? null : timestamp;
    }
    default:
      return typeof item[key] === 'number' ? item[key] : typeof item[key] === 'string' ? item[key] : null;
  }
}

function sortDragonTigerItems(items, sortKey, direction = 'desc') {
  if (!Array.isArray(items)) {
    return [];
  }

  const multiplier = direction === 'asc' ? 1 : -1;
  return [...items].sort((left, right) => {
    const leftValue = getDragonTigerComparableValue(left, sortKey);
    const rightValue = getDragonTigerComparableValue(right, sortKey);

    if (leftValue === null && rightValue === null) {
      return 0;
    }
    if (leftValue === null) {
      return 1;
    }
    if (rightValue === null) {
      return -1;
    }
    if (typeof leftValue === 'string' && typeof rightValue === 'string') {
      return leftValue.localeCompare(rightValue, 'zh-CN') * multiplier;
    }
    return ((leftValue > rightValue) - (leftValue < rightValue)) * multiplier;
  });
}

function getDragonTigerDailyTab(item) {
  const text = [item?.reason, item?.explain].filter(Boolean).join(' ');

  if (/连续十个交易日/.test(text)) {
    return 'ten';
  }
  if (/连续三个交易日/.test(text)) {
    return 'three';
  }
  if (/[月]|近\s*30\s*个?交易日/.test(text)) {
    return 'month';
  }
  return 'single';
}

function getDragonTigerRepresentativeScore(item) {
  const netBuyAmount = typeof item?.netBuyAmount === 'number' ? Math.abs(item.netBuyAmount) : 0;
  const dealAmount = typeof item?.dealAmount === 'number' ? Math.abs(item.dealAmount) : 0;
  return Math.max(netBuyAmount, dealAmount);
}

function updateDragonTigerRepresentativeItem(current, item) {
  const fields = [
    'symbol',
    'code',
    'secuCode',
    'name',
    'tradeDate',
    'latestTradeDate',
    'latestDate',
    'closePrice',
    'changePercent',
    'netBuyAmount',
    'buyAmount',
    'sellAmount',
    'dealAmount',
    'totalAmount',
    'netBuyRatio',
    'dealAmountRatio',
    'turnoverRate',
    'freeMarketCap',
  ];

  fields.forEach((field) => {
    if (typeof item?.[field] !== 'undefined') {
      current[field] = item[field];
    }
  });
}

function aggregateDragonTigerDailyItems(items) {
  if (!Array.isArray(items)) {
    return [];
  }

  const groupedItems = new Map();

  items.forEach((item, index) => {
    const tradeDate = item?.tradeDate || item?.latestDate || '';
    const symbol = item?.symbol || item?.code || item?.name || `unknown-${index}`;
    const key = `${tradeDate}-${symbol}`;
    const detail = {
      reason: item?.reason || item?.explain || '上榜',
      tab: getDragonTigerDailyTab(item),
    };

    if (!groupedItems.has(key)) {
      groupedItems.set(key, {
        ...item,
        tradeDate,
        dailyTab: detail.tab,
        dailyDetails: [detail],
        dailyReasonCount: 1,
        _dragonTigerRepresentativeScore: getDragonTigerRepresentativeScore(item),
      });
      return;
    }

    const current = groupedItems.get(key);
    current.dailyDetails.push(detail);
    current.dailyReasonCount = current.dailyDetails.length;
    current.dailyTab = current.dailyDetails.reduce((selectedTab, currentDetail) => {
      return dragonTigerDailyTabPriority[currentDetail.tab] > dragonTigerDailyTabPriority[selectedTab] ? currentDetail.tab : selectedTab;
    }, current.dailyTab);
    current.reason = current.dailyDetails.map((currentDetail) => currentDetail.reason).join('；');
    current.explain = current.reason;
    const nextScore = getDragonTigerRepresentativeScore(item);
    if (nextScore > (current._dragonTigerRepresentativeScore || 0)) {
      updateDragonTigerRepresentativeItem(current, item);
      current._dragonTigerRepresentativeScore = nextScore;
    }
  });

  return Array.from(groupedItems.values()).map(({ _dragonTigerRepresentativeScore, ...entry }) => entry);
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

function formatGoldPrice(value, currency = 'CNY') {
  if (typeof value !== 'number') {
    return '--';
  }

  if (currency === 'USD') {
    return `$${new Intl.NumberFormat('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value)}`;
  }

  if (currency === 'CNY') {
    return `¥${formatPrice(value)}`;
  }

  return `${formatPrice(value)} ${currency}`;
}

function formatNav(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
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

function formatSignedTurnoverAmount(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return `${value > 0 ? '+' : ''}${formatCompactNumber(value)}元`;
}

function formatRatioMultiple(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return `${value.toFixed(value >= 10 ? 1 : 2)}×均量`;
}

function formatImpactPercent(value) {
  if (typeof value !== 'number') {
    return '影响待估';
  }
  return `估算影响 ${formatSignedPercent(value)}`;
}

function formatPercentValue(value) {
  if (typeof value !== 'number') {
    return '--';
  }

  return `${value.toFixed(2)}%`;
}

function getJumpSeverityClass(severity) {
  if (severity === 'critical') {
    return 'event-card-critical';
  }
  if (severity === 'high') {
    return 'event-card-high';
  }
  return '';
}

const anomalySeverityPriority = {
  critical: 3,
  high: 2,
  medium: 1,
  normal: 0,
};

function getDailyAnomalyMagnitude(item) {
  return Math.max(
    typeof item?.changePct === 'number' ? Math.abs(item.changePct) : 0,
    typeof item?.latestPriceJumpPct === 'number' ? Math.abs(item.latestPriceJumpPct) : 0,
    typeof item?.volumeRatio === 'number' ? item.volumeRatio : 0,
  );
}

function getDailyAnomalyRank(item) {
  const triggerTime = item?.triggerTime ? Date.parse(item.triggerTime) : 0;
  return [
    anomalySeverityPriority[item?.severity] || 0,
    getDailyAnomalyMagnitude(item),
    Number.isNaN(triggerTime) ? 0 : triggerTime,
  ];
}

function isHigherDailyAnomalyRank(candidate, current) {
  const candidateRank = getDailyAnomalyRank(candidate);
  const currentRank = getDailyAnomalyRank(current);

  return candidateRank.some((value, index) => value > currentRank[index] && candidateRank
    .slice(0, index)
    .every((previousValue, previousIndex) => previousValue === currentRank[previousIndex]));
}

function mergeDailyAnomalyFunds(items) {
  const seen = new Set();
  return items.flatMap((item) => (Array.isArray(item?.relatedFunds) ? item.relatedFunds : [])).filter((fund) => {
    const fundKey = `${fund?.fundCode || ''}:${fund?.reportDate || ''}`;
    if (seen.has(fundKey)) {
      return false;
    }
    seen.add(fundKey);
    return true;
  });
}

function dedupeDailyAnomalyItems(items) {
  const groupedItems = new Map();
  const unkeyedItems = [];

  items.forEach((item) => {
    if (!item?.symbol) {
      unkeyedItems.push(item);
      return;
    }
    const symbolItems = groupedItems.get(item.symbol) || [];
    symbolItems.push(item);
    groupedItems.set(item.symbol, symbolItems);
  });

  const dedupedItems = [...unkeyedItems];
  groupedItems.forEach((symbolItems) => {
    const representative = symbolItems.reduce(
      (current, candidate) => (isHigherDailyAnomalyRank(candidate, current) ? candidate : current),
      symbolItems[0],
    );
    const eventCountToday = symbolItems.reduce(
      (total, item) => total + (typeof item?.eventCountToday === 'number' ? item.eventCountToday : 0),
      0,
    );
    dedupedItems.push({
      ...representative,
      eventCountToday,
      relatedFunds: mergeDailyAnomalyFunds(symbolItems),
    });
  });

  return dedupedItems;
}

function getAnomalySeverityLabel(severity) {
  if (severity === 'critical') {
    return '重点';
  }
  if (severity === 'high') {
    return '较高';
  }
  if (severity === 'medium') {
    return '观察';
  }
  return '普通';
}

function getAnomalyTypeLabel(type) {
  if (type === 'volume_spike') {
    return '量能突增';
  }
  if (type === 'price_jump') {
    return '价格异动';
  }
  return '显著变化';
}

function getAiReasonStatusLabel(status) {
  if (status === 'completed') {
    return 'AI归因已生成';
  }
  if (status === 'failed') {
    return 'AI归因失败';
  }
  if (status === 'skipped') {
    return 'AI归因已跳过';
  }
  return 'AI归因待生成';
}

function getVolumeToneClass(ratio) {
  if (typeof ratio !== 'number') {
    return '';
  }
  if (ratio >= 1.5) {
    return 'positive';
  }
  if (ratio <= 0.7) {
    return 'negative';
  }
  return '';
}

function getConnectionStatusLabel(value) {
  return connectionStateLabels[value] || '状态未知';
}

function getMarketStatusLabel(value) {
  return marketStatusLabels[value] || '状态未知';
}

function getMarketStatusTone(value) {
  if (value === 'trading') {
    return 'success';
  }
  if (value === 'break') {
    return 'pending';
  }
  if (value === 'disconnected') {
    return 'error';
  }
  return 'neutral';
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
        sideLabel: tick?.sideLabel,
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

function findNearestPoint(points, targetX) {
  if (!Array.isArray(points) || !points.length || typeof targetX !== 'number') {
    return null;
  }

  return points.reduce((best, point) => {
    if (!best) {
      return point;
    }

    return Math.abs(point.x - targetX) < Math.abs(best.x - targetX) ? point : best;
  }, null);
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

const INTRADAY_RIGHT_AXIS_WIDTH = 168;

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

function buildLineChartGeometry(points, width = 860, height = 320, referencePrice = null, rightAxisWidth = INTRADAY_RIGHT_AXIS_WIDTH) {
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
  const plotWidth = Math.max(width - rightAxisWidth, 1);
  const timeScale = buildIntradayTimeScale(plotWidth, points);

  const scaleY = (value) => topPadding + ((maxValue - value) / range) * usableHeight;
  const fallbackStep = points.length > 1 ? plotWidth / (points.length - 1) : 0;
  const minimumStep = fallbackStep > 0 ? Math.min(fallbackStep * 0.12, 0.9) : 0.6;
  let previousX = null;

  const chartPoints = points.map((point, index) => {
    const mappedX = timeScale.positionToX(getChinaSessionPosition(point.bucketTs));
    const fallbackX = points.length > 1 ? fallbackStep * index : plotWidth / 2;
    const rawX = mappedX ?? fallbackX;
    const x = previousX !== null && rawX <= previousX
      ? Math.min(previousX + minimumStep, plotWidth)
      : rawX;

    previousX = x;

    return {
      x,
      y: scaleY(point.close),
      bucketTs: point.bucketTs,
      label: formatTime(point.bucketTs),
      close: point.close,
      volume: point.volume,
      amount: point.amount,
    };
  });

  const polyline = chartPoints.map((point) => `${point.x},${point.y}`).join(' ');
  const area = `0,${height - bottomPadding} ${polyline} ${plotWidth},${height - bottomPadding}`;
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
    plotWidth,
    rightAxisWidth,
    rightAxisLabelX: {
      price: width - 8,
      delta: width - 60,
      percent: width - 112,
    },
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
  const plotWidth = Math.max(width - INTRADAY_RIGHT_AXIS_WIDTH, 1);
  const timeScale = buildIntradayTimeScale(plotWidth);

  const scaleY = (value) => topPadding + ((maxValue - value) / (maxValue - minValue || 1)) * usableHeight;

  return {
    width,
    height,
    plotWidth,
    rightAxisWidth: INTRADAY_RIGHT_AXIS_WIDTH,
    rightAxisLabelX: {
      price: width - 8,
      percent: width - 112,
    },
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
  const [marketStatus, setMarketStatus] = useState('closed');
  const [marketStatusUpdatedAt, setMarketStatusUpdatedAt] = useState(null);
  const [marketOverview, setMarketOverview] = useState({ indexes: [], generatedAt: null });
  const [goldDashboard, setGoldDashboard] = useState({ generatedAt: null, isTradingSession: false, quotes: [], sources: {}, degraded: false, funds: [], news: [] });
  const [goldRequestState, setGoldRequestState] = useState('idle');
  const [activeView, setActiveView] = useState('overview');
  const [controlView, setControlView] = useState('activate');
  const [selectedSnapshotSymbol, setSelectedSnapshotSymbol] = useState(null);
  const [snapshotDetails, setSnapshotDetails] = useState({});
  const [detailRequestState, setDetailRequestState] = useState('idle');
  const [detailChartView, setDetailChartView] = useState('intraday');
  const [intradayHoverPoint, setIntradayHoverPoint] = useState(null);
  const [overviewSearchQuery, setOverviewSearchQuery] = useState('');
  const [overviewSortKey, setOverviewSortKey] = useState('marketCap');
  const [overviewSortDirection, setOverviewSortDirection] = useState('desc');
  const [contentType, setContentType] = useState('all');
  const [contentTimeRange, setContentTimeRange] = useState('today');
  const [contentSymbolFilter, setContentSymbolFilter] = useState('');
  const [contentFeed, setContentFeed] = useState([]);
  const [contentStatus, setContentStatus] = useState({ jobs: [], latestIngestedAt: null, summary: null });
  const [contentRequestState, setContentRequestState] = useState('idle');
  const [dailyAnomalyReport, setDailyAnomalyReport] = useState(null);
  const [dailyAnomalyRequestState, setDailyAnomalyRequestState] = useState('idle');
  const [dailyAnomalyPortfolioOnly, setDailyAnomalyPortfolioOnly] = useState(false);
  const [dailyAnomalySortBy, setDailyAnomalySortBy] = useState('relevance');
  const [dailyAnomalyChangeThreshold, setDailyAnomalyChangeThreshold] = useState('0');
  const [dailyAnomalyVolumeThreshold, setDailyAnomalyVolumeThreshold] = useState('0');
  const [dragonTigerRange, setDragonTigerRange] = useState('1month');
  const [dragonTigerDate, setDragonTigerDate] = useState('');
  const [dragonTigerDateManuallySet, setDragonTigerDateManuallySet] = useState(false);
  const [dragonTigerDaily, setDragonTigerDaily] = useState([]);
  const [dragonTigerStocks, setDragonTigerStocks] = useState([]);
  const [dragonTigerInstitution, setDragonTigerInstitution] = useState([]);
  const [dragonTigerBranchRank, setDragonTigerBranchRank] = useState([]);
  const [dragonTigerSeatSymbol, setDragonTigerSeatSymbol] = useState('');
  const [dragonTigerSeatDates, setDragonTigerSeatDates] = useState([]);
  const [dragonTigerSeatDate, setDragonTigerSeatDate] = useState('');
  const [dragonTigerSeatSide, setDragonTigerSeatSide] = useState('buy');
  const [dragonTigerSeatDetail, setDragonTigerSeatDetail] = useState([]);
  const [dragonTigerRequestState, setDragonTigerRequestState] = useState('idle');
  const [dragonTigerErrorMessage, setDragonTigerErrorMessage] = useState('');
  const [dragonTigerStatsExpanded, setDragonTigerStatsExpanded] = useState(false);
  const [dragonTigerStatsRequestState, setDragonTigerStatsRequestState] = useState('idle');
  const [dragonTigerStatsErrorMessage, setDragonTigerStatsErrorMessage] = useState('');
  const [dragonTigerStocksExpanded, setDragonTigerStocksExpanded] = useState(false);
  const [dragonTigerSearchQuery, setDragonTigerSearchQuery] = useState('');
  const [dragonTigerDailyTab, setDragonTigerDailyTab] = useState('single');
  const [dragonTigerDailySortKey, setDragonTigerDailySortKey] = useState('netBuyAmount');
  const [dragonTigerDailySortDirection, setDragonTigerDailySortDirection] = useState('desc');
  const [dragonTigerStocksSortKey, setDragonTigerStocksSortKey] = useState('billboardTimes');
  const [dragonTigerStocksSortDirection, setDragonTigerStocksSortDirection] = useState('desc');
  const [dragonTigerStocksPage, setDragonTigerStocksPage] = useState(1);
  const [dragonTigerReloadNonce, setDragonTigerReloadNonce] = useState(0);
  const [dragonTigerStatsReloadNonce, setDragonTigerStatsReloadNonce] = useState(0);
  const [fundCode, setFundCode] = useState('');
  const [fundRequestState, setFundRequestState] = useState('idle');
  const [activeFunds, setActiveFunds] = useState([]);
  const [fundSnapshots, setFundSnapshots] = useState({});
  const [selectedFundCode, setSelectedFundCode] = useState(null);
  const [fundDetails, setFundDetails] = useState({});
  const [fundDetailRequestState, setFundDetailRequestState] = useState('idle');
  const [removingFund, setRemovingFund] = useState('');
  const [autoLinkStocks, setAutoLinkStocks] = useState(true);
  const [fundsSubView, setFundsSubView] = useState('list');
  const [fundPortfolioView, setFundPortfolioView] = useState(null);
  const [fundPortfolioRequestState, setFundPortfolioRequestState] = useState('idle');
  const [fundPortfolioAnalysis, setFundPortfolioAnalysis] = useState(null);
  const [fundPortfolioAnalysisRequestState, setFundPortfolioAnalysisRequestState] = useState('idle');
  const [macroCapabilities, setMacroCapabilities] = useState({ enabled: false, loading: true });
  const [macroSnapshot, setMacroSnapshot] = useState(null);
  const [macroCollectorStatus, setMacroCollectorStatus] = useState(null);
  const [macroAnalysis, setMacroAnalysis] = useState(null);
  const [macroRequestState, setMacroRequestState] = useState('idle');
  const [macroAnalysisRequestState, setMacroAnalysisRequestState] = useState('idle');
  const [llmAuditCapabilities, setLlmAuditCapabilities] = useState({ enabled: false, loading: true, features: {} });
  const [llmAuditSummary, setLlmAuditSummary] = useState(null);
  const [llmAuditRequestState, setLlmAuditRequestState] = useState('idle');
  const [llmAuditReloadNonce, setLlmAuditReloadNonce] = useState(0);
  const activeSymbolsKey = useMemo(() => activeSymbols.join(','), [activeSymbols]);
  const activeFundsKey = useMemo(() => [...activeFunds].sort().join(','), [activeFunds]);

  const wsUrl = useMemo(() => `${wsBaseUrl}/ws/market`, []);
  const dailyAnomalyItems = useMemo(() => {
    const changeThreshold = Number(dailyAnomalyChangeThreshold) || 0;
    const volumeThreshold = Number(dailyAnomalyVolumeThreshold) || 0;
    const matchesThresholds = (item) => {
      const changeMagnitude = Math.max(
        typeof item?.changePct === 'number' ? Math.abs(item.changePct) : 0,
        typeof item?.latestPriceJumpPct === 'number' ? Math.abs(item.latestPriceJumpPct) : 0,
      );
      const volumeRatio = typeof item?.volumeRatio === 'number' ? item.volumeRatio : 0;
      return changeMagnitude >= changeThreshold && volumeRatio >= volumeThreshold;
    };
    const portfolioItems = Array.isArray(dailyAnomalyReport?.portfolioAnomalies)
      ? dedupeDailyAnomalyItems(dailyAnomalyReport.portfolioAnomalies).filter(matchesThresholds)
      : [];
    const otherItems = Array.isArray(dailyAnomalyReport?.otherAnomalies)
      ? dedupeDailyAnomalyItems(dailyAnomalyReport.otherAnomalies).filter(matchesThresholds)
      : [];
    return { portfolioItems, otherItems };
  }, [dailyAnomalyChangeThreshold, dailyAnomalyReport, dailyAnomalyVolumeThreshold]);

  const dragonTigerRangeLabels = {
    '1month': '近一月',
    '3month': '近三月',
    '6month': '近六月',
    '1year': '近一年',
  };

  useEffect(() => {
    if (activeView !== 'dragonTiger' || dragonTigerDateManuallySet) {
      return undefined;
    }

    let cancelled = false;

    async function loadDragonTigerHistorySummary() {
      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/dragon-tiger/history/summary`);
        const payload = await parseJsonOrThrow(response, 'dragon tiger history summary fetch failed');
        if (cancelled) {
          return;
        }

        const latestTradeDate = payload?.daily?.latestTradeDate || payload?.institution?.latestTradeDate || '';
        if (latestTradeDate) {
          setDragonTigerDate((current) => (current && current <= latestTradeDate ? current : latestTradeDate));
        } else if (!dragonTigerDate) {
          setDragonTigerDate(new Date().toISOString().slice(0, 10));
        }
      } catch {
        if (!cancelled && !dragonTigerDate) {
          setDragonTigerDate(new Date().toISOString().slice(0, 10));
        }
      }
    }

    loadDragonTigerHistorySummary();

    return () => {
      cancelled = true;
    };
  }, [activeView, dragonTigerDate, dragonTigerDateManuallySet]);

  useEffect(() => {
    let cancelled = false;

    async function loadMacroCapabilities() {
      try {
        const response = await fetch(buildMacroCapabilitiesUrl());
        const payload = await parseJsonOrThrow(response, 'macro capabilities fetch failed');
        if (cancelled) {
          return;
        }
        setMacroCapabilities({
          enabled: Boolean(payload?.enabled),
          loading: false,
          reason: payload?.reason || null,
          hasSnapshot: Boolean(payload?.hasSnapshot),
          analysisEnabled: Boolean(payload?.analysisEnabled),
          analysisEngine: payload?.analysisEngine || 'rules',
        });
      } catch {
        if (!cancelled) {
          setMacroCapabilities({ enabled: false, loading: false, reason: 'capability_unavailable' });
        }
      }
    }

    loadMacroCapabilities();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timeoutId = null;

    async function loadLlmAuditCapabilities() {
      try {
        const response = await fetch(buildLlmAuditCapabilitiesUrl());
        const payload = await parseJsonOrThrow(response, 'llm audit capabilities fetch failed');
        if (cancelled) {
          return;
        }
        setLlmAuditCapabilities({
          enabled: Boolean(payload?.enabled),
          loading: false,
          reason: payload?.reason || null,
          features: payload?.features && typeof payload.features === 'object' ? payload.features : {},
        });
      } catch {
        if (!cancelled) {
          setLlmAuditCapabilities({ enabled: false, loading: false, reason: 'capability_unavailable', features: {} });
        }
      } finally {
        if (!cancelled) {
          timeoutId = window.setTimeout(loadLlmAuditCapabilities, 30000);
        }
      }
    }

    loadLlmAuditCapabilities();
    return () => {
      cancelled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, []);

  useEffect(() => {
    if (activeView === 'macro' && !macroCapabilities.loading && !macroCapabilities.enabled) {
      setActiveView('overview');
    }
  }, [activeView, macroCapabilities.enabled, macroCapabilities.loading]);

  useEffect(() => {
    if (activeView === 'llmAudit' && !llmAuditCapabilities.loading && !llmAuditCapabilities.enabled) {
      setActiveView('overview');
    }
  }, [activeView, llmAuditCapabilities.enabled, llmAuditCapabilities.loading]);

  useEffect(() => {
    setFundPortfolioAnalysis(null);
    setFundPortfolioAnalysisRequestState('idle');
  }, [activeFundsKey]);

  useEffect(() => {
    if (activeView !== 'llmAudit' || !llmAuditCapabilities.enabled) {
      return undefined;
    }

    let cancelled = false;

    async function loadLlmAuditSummary() {
      setLlmAuditRequestState('loading');
      try {
        const response = await fetch(buildLlmAuditDailyUrl());
        const payload = await parseJsonOrThrow(response, 'llm audit daily fetch failed');
        if (cancelled) {
          return;
        }
        setLlmAuditSummary(payload || null);
        setLlmAuditRequestState('ready');
      } catch {
        if (!cancelled) {
          setLlmAuditSummary(null);
          setLlmAuditRequestState('error');
        }
      }
    }

    loadLlmAuditSummary();
    return () => {
      cancelled = true;
    };
  }, [activeView, llmAuditCapabilities.enabled, llmAuditReloadNonce]);

  const dragonTigerDailyAggregated = useMemo(() => aggregateDragonTigerDailyItems(dragonTigerDaily), [dragonTigerDaily]);

  const dragonTigerDailyTabbed = useMemo(
    () => dragonTigerDailyAggregated.filter((item) => item.dailyTab === dragonTigerDailyTab),
    [dragonTigerDailyAggregated, dragonTigerDailyTab],
  );

  const dragonTigerDailyTabCounts = useMemo(() => {
    return dragonTigerDailyAggregated.reduce((counts, item) => {
      const tab = item?.dailyTab || 'single';
      return { ...counts, [tab]: (counts[tab] || 0) + 1 };
    }, {});
  }, [dragonTigerDailyAggregated]);

  const dragonTigerDailyFiltered = useMemo(() => {
    const query = dragonTigerSearchQuery.trim().toLowerCase();
    if (!query) {
      return dragonTigerDailyTabbed;
    }

    return dragonTigerDailyTabbed.filter((item) => {
      const detailReasons = Array.isArray(item?.dailyDetails)
        ? item.dailyDetails.map((detail) => detail.reason)
        : [];
      const candidates = [item?.symbol, item?.code, item?.name, item?.reason, item?.explain, ...detailReasons]
        .filter(Boolean)
        .map((value) => `${value}`.toLowerCase());
      return candidates.some((value) => value.includes(query));
    });
  }, [dragonTigerDailyTabbed, dragonTigerSearchQuery]);

  const dragonTigerDailySorted = useMemo(() => {
    const sortKey = dragonTigerDailySortKey;
    return sortDragonTigerItems(
      dragonTigerDailyFiltered,
      sortKey,
      dragonTigerDailySortDirection,
    );
  }, [dragonTigerDailyFiltered, dragonTigerDailySortDirection, dragonTigerDailySortKey]);

  const dragonTigerDailyVisible = useMemo(() => dragonTigerDailySorted, [dragonTigerDailySorted]);

  const dragonTigerStocksSorted = useMemo(() => {
    return sortDragonTigerItems(
      dragonTigerStocks,
      dragonTigerStocksSortKey,
      dragonTigerStocksSortDirection,
    );
  }, [dragonTigerStocks, dragonTigerStocksSortDirection, dragonTigerStocksSortKey]);

  const dragonTigerStocksVisible = useMemo(() => {
    if (dragonTigerStocksExpanded) {
      return dragonTigerStocksSorted;
    }
    return dragonTigerStocksSorted.slice(0, dragonTigerDefaultStockDisplayLimit);
  }, [dragonTigerStocksExpanded, dragonTigerStocksSorted]);

  const dragonTigerInstitutionSorted = useMemo(() => {
    return [...dragonTigerInstitution].sort((left, right) => {
      const leftValue = typeof left?.orgNetAmount === 'number' ? left.orgNetAmount : Number.NEGATIVE_INFINITY;
      const rightValue = typeof right?.orgNetAmount === 'number' ? right.orgNetAmount : Number.NEGATIVE_INFINITY;
      return rightValue - leftValue;
    });
  }, [dragonTigerInstitution]);

  const dragonTigerBranchRankSorted = useMemo(() => {
    return [...dragonTigerBranchRank].sort((left, right) => {
      const leftValue = typeof left?.buyTimes1d === 'number' ? left.buyTimes1d : Number.NEGATIVE_INFINITY;
      const rightValue = typeof right?.buyTimes1d === 'number' ? right.buyTimes1d : Number.NEGATIVE_INFINITY;
      return rightValue - leftValue;
    });
  }, [dragonTigerBranchRank]);

  const dragonTigerSeatDatesSorted = useMemo(() => {
    return [...dragonTigerSeatDates].sort((left, right) => {
      const leftTime = left?.tradeDate ? new Date(left.tradeDate).getTime() : 0;
      const rightTime = right?.tradeDate ? new Date(right.tradeDate).getTime() : 0;
      return rightTime - leftTime;
    });
  }, [dragonTigerSeatDates]);

  const dragonTigerSeatDetailSorted = useMemo(() => {
    return [...dragonTigerSeatDetail].sort((left, right) => {
      const leftValue = typeof left?.netAmount === 'number' ? left.netAmount : Number.NEGATIVE_INFINITY;
      const rightValue = typeof right?.netAmount === 'number' ? right.netAmount : Number.NEGATIVE_INFINITY;
      return rightValue - leftValue;
    });
  }, [dragonTigerSeatDetail]);

  useEffect(() => {
    setDragonTigerStocksPage(1);
  }, [dragonTigerRange, dragonTigerStocksSortDirection, dragonTigerStocksSortKey]);

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
        if (typeof payload.marketStatus === 'string') {
          setMarketStatus(payload.marketStatus);
        }
        if (typeof payload.serverGeneratedAt === 'string' && payload.serverGeneratedAt) {
          setMarketStatusUpdatedAt(payload.serverGeneratedAt);
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
    let cancelled = false;

    async function loadMarketOverview() {
      try {
        const response = await fetch(buildMarketOverviewUrl());
        const payload = await parseJsonOrThrow(response, 'market overview fetch failed');
        if (cancelled) {
          return;
        }

        setMarketOverview({
          indexes: Array.isArray(payload?.indexes) ? payload.indexes : [],
          generatedAt: typeof payload?.generatedAt === 'string' ? payload.generatedAt : null,
          breadth: payload?.breadth && typeof payload.breadth === 'object' ? payload.breadth : null,
        });

        if (typeof payload?.marketStatus === 'string') {
          setMarketStatus(payload.marketStatus);
        }
        if (typeof payload?.serverGeneratedAt === 'string' && payload.serverGeneratedAt) {
          setMarketStatusUpdatedAt(payload.serverGeneratedAt);
        }
      } catch {
        if (!cancelled) {
          setMarketOverview((current) => current || { indexes: [], generatedAt: null, breadth: null });
        }
      }
    }

    loadMarketOverview();
    const intervalId = window.setInterval(loadMarketOverview, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

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
    if (activeView !== 'gold') {
      return undefined;
    }

    let cancelled = false;

    async function loadGoldDashboard({ keepReadyState = false } = {}) {
      if (!keepReadyState) {
        setGoldRequestState('loading');
      }

      try {
        const response = await fetch(buildGoldDashboardUrl());
        const payload = await parseJsonOrThrow(response, 'gold dashboard fetch failed');
        if (cancelled) {
          return;
        }

        setGoldDashboard({
          generatedAt: typeof payload?.generatedAt === 'string' ? payload.generatedAt : null,
          isTradingSession: Boolean(payload?.isTradingSession),
          quotes: Array.isArray(payload?.quotes) ? payload.quotes : [],
          sources: payload?.sources && typeof payload.sources === 'object' ? payload.sources : {},
          degraded: Boolean(payload?.degraded),
          funds: Array.isArray(payload?.funds) ? payload.funds : [],
          news: Array.isArray(payload?.news) ? payload.news : [],
        });
        setGoldRequestState('ready');
      } catch {
        if (!cancelled) {
          setGoldRequestState('error');
        }
      }
    }

    loadGoldDashboard();
    const intervalId = window.setInterval(() => loadGoldDashboard({ keepReadyState: true }), 15000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeView]);

  useEffect(() => {
    if (activeView !== 'funds') {
      return undefined;
    }

    let cancelled = false;

    async function loadFunds() {
      setFundRequestState('loading');
      try {
        const [activeResponse, snapshotResponse] = await Promise.all([
          fetch(`${apiBaseUrl}/api/v1/funds/active`),
          fetch(`${apiBaseUrl}/api/v1/funds/snapshots`),
        ]);
        const [activePayload, snapshotPayload] = await Promise.all([
          parseJsonOrThrow(activeResponse, 'fund active fetch failed'),
          parseJsonOrThrow(snapshotResponse, 'fund snapshots fetch failed'),
        ]);
        if (cancelled) {
          return;
        }
        setActiveFunds(Array.isArray(activePayload?.funds) ? activePayload.funds : []);
        setFundSnapshots(snapshotPayload?.snapshots && typeof snapshotPayload.snapshots === 'object' ? snapshotPayload.snapshots : {});
        setFundRequestState('ready');
      } catch {
        if (!cancelled) {
          setFundRequestState('error');
        }
      }
    }

    loadFunds();
    const intervalId = window.setInterval(loadFunds, 60000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeView]);

  useEffect(() => {
    setFundPortfolioAnalysis(null);
    setFundPortfolioAnalysisRequestState('idle');
  }, [activeFundsKey]);

  useEffect(() => {
    if (activeView !== 'funds' || fundsSubView !== 'portfolio' || selectedFundCode) {
      return undefined;
    }

    let cancelled = false;

    async function loadFundPortfolioView({ keepReadyState = false } = {}) {
      if (!keepReadyState) {
        setFundPortfolioRequestState('loading');
      }
      try {
        const response = await fetch(buildFundPortfolioUrl());
        const payload = await parseJsonOrThrow(response, 'fund portfolio fetch failed');
        if (cancelled) {
          return;
        }
        setFundPortfolioView(payload && typeof payload === 'object' ? payload : null);
        setFundPortfolioRequestState('ready');
      } catch {
        if (!cancelled) {
          setFundPortfolioRequestState('error');
        }
      }
    }

    loadFundPortfolioView();
    const intervalId = window.setInterval(() => loadFundPortfolioView({ keepReadyState: true }), 60000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeFundsKey, activeView, fundsSubView, selectedFundCode]);

  useEffect(() => {
    if (activeView !== 'macro' || !macroCapabilities.enabled) {
      return undefined;
    }

    let cancelled = false;

    async function loadMacroData({ keepReadyState = false } = {}) {
      if (!keepReadyState) {
        setMacroRequestState('loading');
      }
      try {
        const [snapshotResponse, analysisResponse] = await Promise.all([
          fetch(buildMacroSnapshotUrl()),
          fetch(buildMacroAnalysisLatestUrl()),
        ]);
        const [snapshotPayload, analysisPayload] = await Promise.all([
          parseJsonOrThrow(snapshotResponse, 'macro snapshot fetch failed'),
          analysisResponse.ok ? analysisResponse.json() : Promise.resolve({ analysis: null }),
        ]);
        if (cancelled) {
          return;
        }
        setMacroSnapshot(snapshotPayload?.snapshot && typeof snapshotPayload.snapshot === 'object' ? snapshotPayload.snapshot : null);
        setMacroCollectorStatus(snapshotPayload?.collectorStatus && typeof snapshotPayload.collectorStatus === 'object' ? snapshotPayload.collectorStatus : null);
        setMacroAnalysis(analysisPayload?.analysis && typeof analysisPayload.analysis === 'object' ? analysisPayload.analysis : null);
        setMacroRequestState('ready');
      } catch {
        if (!cancelled) {
          setMacroRequestState('error');
        }
      }
    }

    loadMacroData();
    const intervalId = window.setInterval(() => loadMacroData({ keepReadyState: true }), 60000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeView, macroCapabilities.enabled]);

  useEffect(() => {
    if (!selectedFundCode) {
      return undefined;
    }

    let cancelled = false;

    async function loadFundDetail() {
      setFundDetailRequestState('loading');
      try {
        const response = await fetch(`${apiBaseUrl}/api/v1/funds/${encodeURIComponent(selectedFundCode)}/detail`);
        const payload = await parseJsonOrThrow(response, 'fund detail fetch failed');
        if (cancelled) {
          return;
        }
        setFundDetails((current) => ({
          ...current,
          [selectedFundCode]: payload,
        }));
        setFundDetailRequestState('ready');
      } catch {
        if (!cancelled) {
          setFundDetailRequestState('error');
        }
      }
    }

    loadFundDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedFundCode]);

  useEffect(() => {
    if (activeView !== 'content') {
      return undefined;
    }

    let cancelled = false;
    const pageSize = 100;

    async function loadContent() {
      setContentRequestState('loading');
      try {
        const [firstFeedResponse, statusResponse] = await Promise.all([
          fetch(buildContentFeedUrl({ symbol: contentSymbolFilter, type: contentType, timeRange: contentTimeRange, limit: pageSize })),
          fetch(buildContentStatusUrl(contentSymbolFilter)),
        ]);
        const [firstFeedPayload, statusPayload] = await Promise.all([
          parseJsonOrThrow(firstFeedResponse, 'content feed fetch failed'),
          parseJsonOrThrow(statusResponse, 'content status fetch failed'),
        ]);
        if (cancelled) {
          return;
        }

        let nextFeed = Array.isArray(firstFeedPayload.items) ? firstFeedPayload.items : [];
        if (contentTimeRange === 'today') {
          const seenBefore = new Set();
          let cursor = nextFeed.length ? (nextFeed[nextFeed.length - 1]?.publishedAt || nextFeed[nextFeed.length - 1]?.firstSeenAt || '') : '';

          while (!cancelled && cursor && nextFeed.length % pageSize === 0 && !seenBefore.has(cursor)) {
            seenBefore.add(cursor);
            const pageResponse = await fetch(
              buildContentFeedUrl({
                symbol: contentSymbolFilter,
                type: contentType,
                timeRange: contentTimeRange,
                limit: pageSize,
                before: cursor,
              }),
            );
            const pagePayload = await parseJsonOrThrow(pageResponse, 'content feed fetch failed');
            const pageItems = Array.isArray(pagePayload.items) ? pagePayload.items : [];
            if (!pageItems.length) {
              break;
            }
            nextFeed = dedupeContentItems([...nextFeed, ...pageItems]);
            if (pageItems.length < pageSize) {
              break;
            }
            cursor = pageItems[pageItems.length - 1]?.publishedAt || pageItems[pageItems.length - 1]?.firstSeenAt || '';
          }
        }

        setContentFeed(nextFeed);
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

  useEffect(() => {
    if (activeView !== 'events') {
      return undefined;
    }

    let cancelled = false;

    async function loadDailyAnomalyReport({ keepReadyState = false } = {}) {
      if (!keepReadyState) {
        setDailyAnomalyRequestState('loading');
      }

      try {
        const response = await fetch(buildDailyAnomalyReportUrl({
          portfolioOnly: dailyAnomalyPortfolioOnly,
          sortBy: dailyAnomalySortBy,
        }));
        const payload = await parseJsonOrThrow(response, 'daily anomaly report fetch failed');
        if (cancelled) {
          return;
        }
        setDailyAnomalyReport(payload && typeof payload === 'object' ? payload : null);
        setDailyAnomalyRequestState('ready');
      } catch {
        if (!cancelled) {
          setDailyAnomalyRequestState('error');
        }
      }
    }

    loadDailyAnomalyReport();
    const intervalId = window.setInterval(() => {
      loadDailyAnomalyReport({ keepReadyState: true });
    }, 60000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeSymbolsKey, activeView, dailyAnomalyPortfolioOnly, dailyAnomalySortBy]);

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

  const overviewItems = useMemo(() => {
    const normalizedQuery = overviewSearchQuery.trim().toLowerCase();

    return activeSymbols
      .filter((currentSymbol) => {
        if (!normalizedQuery) {
          return true;
        }

        const snapshot = snapshots[currentSymbol];
        const companyName = typeof snapshot?.companyName === 'string' ? snapshot.companyName.toLowerCase() : '';
        return currentSymbol.toLowerCase().includes(normalizedQuery) || companyName.includes(normalizedQuery);
      })
      .sort((leftSymbol, rightSymbol) => {
        const leftSnapshot = snapshots[leftSymbol];
        const rightSnapshot = snapshots[rightSymbol];
        const leftValue = leftSnapshot?.[overviewSortKey];
        const rightValue = rightSnapshot?.[overviewSortKey];

        const leftComparable = typeof leftValue === 'number' ? leftValue : Number.NEGATIVE_INFINITY;
        const rightComparable = typeof rightValue === 'number' ? rightValue : Number.NEGATIVE_INFINITY;

        if (leftComparable === rightComparable) {
          return leftSymbol.localeCompare(rightSymbol, 'zh-Hans-CN');
        }

        return overviewSortDirection === 'asc'
          ? leftComparable - rightComparable
          : rightComparable - leftComparable;
      });
  }, [activeSymbols, overviewSearchQuery, overviewSortDirection, overviewSortKey, snapshots]);

  const overviewLastUpdatedAt = useMemo(() => {
    const timestamps = activeSymbols
      .map((currentSymbol) => snapshots[currentSymbol]?.updatedAt)
      .filter((value) => typeof value === 'string' && value);

    if (!timestamps.length) {
      return null;
    }

    return timestamps.sort().at(-1) || null;
  }, [activeSymbols, snapshots]);

  useEffect(() => {
    if (contentSymbolFilter && !activeSymbols.includes(contentSymbolFilter)) {
      setContentSymbolFilter('');
    }
  }, [activeSymbols, contentSymbolFilter]);

  useEffect(() => {
    if (activeView !== 'dragonTiger') {
      return undefined;
    }

    setDragonTigerStatsExpanded(false);
    setDragonTigerStocksExpanded(false);

    let cancelled = false;

    async function loadDragonTiger() {
      setDragonTigerRequestState('loading');
      setDragonTigerErrorMessage('');
      try {
        const dailyResponse = await fetch(buildDragonTigerDailyUrl(dragonTigerDate));
        const dailyPayload = await parseJsonOrThrow(dailyResponse, 'dragon tiger daily fetch failed');

        if (cancelled) {
          return;
        }

        setDragonTigerDaily(Array.isArray(dailyPayload?.items) ? dailyPayload.items : []);
        setDragonTigerErrorMessage(
          dailyPayload?.stale
            ? `当前展示最近一次可用缓存：${dailyPayload?.staleReason || '上游接口暂不可用'}`
            : '',
        );
        setDragonTigerRequestState('ready');
      } catch (error) {
        if (!cancelled) {
          setDragonTigerErrorMessage(error instanceof Error ? error.message : 'dragon tiger daily fetch failed');
          setDragonTigerRequestState('error');
        }
      }
    }

    loadDragonTiger();

    return () => {
      cancelled = true;
    };
  }, [activeView, dragonTigerDate, dragonTigerReloadNonce]);

  useEffect(() => {
    if (activeView !== 'dragonTiger' || !dragonTigerStatsExpanded) {
      return undefined;
    }

    setDragonTigerStocksExpanded(false);

    let cancelled = false;

    async function loadDragonTigerStats() {
      setDragonTigerStatsRequestState('loading');
      setDragonTigerStatsErrorMessage('');
      try {
        const [stocksResponse, institutionResponse, branchRankResponse] = await Promise.all([
          fetch(buildDragonTigerStocksUrl(dragonTigerRange)),
          fetch(buildDragonTigerInstitutionUrl(dragonTigerDate, dragonTigerDate)),
          fetch(buildDragonTigerBranchRankUrl(dragonTigerRange)),
        ]);

        const [stocksPayload, institutionPayload, branchRankPayload] = await Promise.all([
          parseJsonOrThrow(stocksResponse, 'dragon tiger stocks fetch failed'),
          parseJsonOrThrow(institutionResponse, 'dragon tiger institution fetch failed'),
          parseJsonOrThrow(branchRankResponse, 'dragon tiger branch rank fetch failed'),
        ]);

        if (cancelled) {
          return;
        }

        setDragonTigerStocks(Array.isArray(stocksPayload?.items) ? stocksPayload.items : []);
        setDragonTigerInstitution(Array.isArray(institutionPayload?.items) ? institutionPayload.items : []);
        setDragonTigerBranchRank(Array.isArray(branchRankPayload?.items) ? branchRankPayload.items : []);
        const staleMessages = [stocksPayload, institutionPayload, branchRankPayload]
          .filter((payload) => payload?.stale)
          .map((payload) => payload?.staleReason || '上游接口暂不可用');
        setDragonTigerStatsErrorMessage(staleMessages.length ? `当前统计区使用缓存数据：${staleMessages[0]}` : '');
        setDragonTigerStatsRequestState('ready');
      } catch (error) {
        if (!cancelled) {
          setDragonTigerStatsErrorMessage(error instanceof Error ? error.message : 'dragon tiger stats fetch failed');
          setDragonTigerStatsRequestState('error');
        }
      }
    }

    loadDragonTigerStats();

    return () => {
      cancelled = true;
    };
  }, [activeView, dragonTigerDate, dragonTigerRange, dragonTigerStatsExpanded, dragonTigerStatsReloadNonce]);

  useEffect(() => {
    if (activeView !== 'dragonTiger' || !dragonTigerStatsExpanded || !dragonTigerSeatSymbol) {
      return undefined;
    }

    let cancelled = false;

    async function loadDragonTigerSeatDates() {
      try {
        const response = await fetch(buildDragonTigerSeatDatesUrl(dragonTigerSeatSymbol));
        const payload = await parseJsonOrThrow(response, 'dragon tiger seat dates fetch failed');
        if (cancelled) {
          return;
        }
        const nextSeatDates = Array.isArray(payload?.items) ? payload.items : [];
        setDragonTigerSeatDates(nextSeatDates);
        if (!dragonTigerSeatDate && nextSeatDates.length) {
          setDragonTigerSeatDate(nextSeatDates[0]?.tradeDate || '');
        }
      } catch {
        if (!cancelled) {
          setDragonTigerSeatDates([]);
          setDragonTigerSeatDate('');
        }
      }
    }

    loadDragonTigerSeatDates();

    return () => {
      cancelled = true;
    };
  }, [activeView, dragonTigerStatsExpanded, dragonTigerSeatSymbol]);

  useEffect(() => {
    if (activeView !== 'dragonTiger' || !dragonTigerStatsExpanded || !dragonTigerSeatSymbol || !dragonTigerSeatDate) {
      return undefined;
    }

    let cancelled = false;

    async function loadDragonTigerSeatDetail() {
      try {
        const response = await fetch(buildDragonTigerSeatDetailUrl(dragonTigerSeatSymbol, dragonTigerSeatDate, dragonTigerSeatSide));
        const payload = await parseJsonOrThrow(response, 'dragon tiger seat detail fetch failed');
        if (cancelled) {
          return;
        }
        setDragonTigerSeatDetail(Array.isArray(payload?.items) ? payload.items : []);
      } catch {
        if (!cancelled) {
          setDragonTigerSeatDetail([]);
        }
      }
    }

    loadDragonTigerSeatDetail();

    return () => {
      cancelled = true;
    };
  }, [activeView, dragonTigerSeatDate, dragonTigerSeatSide, dragonTigerSeatSymbol, dragonTigerStatsExpanded]);

  useEffect(() => {
    if (dragonTigerStatsExpanded) {
      return;
    }

    setDragonTigerSeatSymbol('');
    setDragonTigerSeatDates([]);
    setDragonTigerSeatDate('');
    setDragonTigerSeatDetail([]);
    setDragonTigerStocksExpanded(false);
  }, [dragonTigerStatsExpanded]);

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

  useEffect(() => {
    setIntradayHoverPoint(null);
  }, [selectedSnapshotSymbol, detailChartView]);

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

  function handleOpenFundDetail(fundCodeValue) {
    setSelectedFundCode(fundCodeValue);
    setFundDetailRequestState(fundDetails[fundCodeValue] ? 'ready' : 'loading');
  }

  function handleCloseFundDetail() {
    setSelectedFundCode(null);
    setFundDetailRequestState('idle');
  }

  function renderRiskSignalList(signals, emptyMessage = '当前暂无额外风险提示。') {
    const items = Array.isArray(signals) ? signals : [];
    if (!items.length) {
      return <p className="panel-tip compact">{emptyMessage}</p>;
    }
    return (
      <div className="fund-risk-list">
        {items.map((signal, index) => (
          <article className="fund-risk-card" key={`${signal?.kind || 'risk'}-${index}`}>
            <span className={`market-breadth-chip ${getRiskSignalTone(signal)}`}>{signal?.title || '风险提示'}</span>
            <p>{signal?.message || '--'}</p>
          </article>
        ))}
      </div>
    );
  }

  function renderFundPortfolioView() {
    const portfolio = fundPortfolioView && typeof fundPortfolioView === 'object' ? fundPortfolioView : {};
    const summary = portfolio?.summary && typeof portfolio.summary === 'object' ? portfolio.summary : {};
    const assumptions = portfolio?.assumptions && typeof portfolio.assumptions === 'object' ? portfolio.assumptions : {};
    const stockExposure = Array.isArray(portfolio?.stockExposure) ? portfolio.stockExposure : [];
    const repeatedHoldings = Array.isArray(portfolio?.repeatedHoldings) ? portfolio.repeatedHoldings : [];
    const riskSignals = Array.isArray(portfolio?.riskSignals) ? portfolio.riskSignals : [];
    const analysisPayload = fundPortfolioAnalysis?.analysis || fundPortfolioAnalysis;
    const features = llmAuditCapabilities?.features && typeof llmAuditCapabilities.features === 'object'
      ? llmAuditCapabilities.features
      : {};
    const aiEnabled = Boolean(features.fundPortfolioRiskAnalysis);

    return (
      <div className="fund-portfolio-view">
        <section className="fund-portfolio-hero">
          <div>
            <h3>监控组合视角</h3>
            <p className="panel-tip compact">{portfolio?.statusMessage || assumptions?.note || '当前按已激活基金观察池估算持仓暴露。'}</p>
          </div>
          <div className="fund-card-signal-row">
            {assumptions?.weightingLabel ? <span className="market-breadth-chip muted">{assumptions.weightingLabel}</span> : null}
            {assumptions?.disclosureBasis ? <span className="market-breadth-chip muted">{assumptions.disclosureBasis}</span> : null}
            {portfolio?.status === 'partial_holdings' ? <span className="market-breadth-chip warning">存在待同步基金</span> : null}
          </div>
        </section>
        {fundPortfolioRequestState === 'loading' ? <p className="status-line pending">监控组合视角加载中...</p> : null}
        {fundPortfolioRequestState === 'error' ? <p className="status-line error">监控组合视角加载失败，请稍后重试。</p> : null}
        {fundPortfolioRequestState === 'ready' && (portfolio?.status === 'no_active_funds' || portfolio?.status === 'waiting_for_holdings') ? (
          <p className="panel-tip compact fund-portfolio-empty">{portfolio?.statusMessage || '当前没有可用的监控组合数据。'}</p>
        ) : null}
        {fundPortfolioRequestState === 'ready' && portfolio?.status !== 'no_active_funds' && portfolio?.status !== 'waiting_for_holdings' ? (
          <>
            <div className="market-summary-grid fund-portfolio-summary-grid">
              <article className="summary-metric summary-metric-strong">
                <span>激活基金</span>
                <strong>{summary?.activeFundCount ?? 0}</strong>
                <em>纳入估算 {summary?.participatingFundCount ?? 0}</em>
              </article>
              <article className="summary-metric">
                <span>重复持仓</span>
                <strong>{summary?.repeatedHoldingCount ?? 0}</strong>
                <em>{typeof summary?.top1ExposurePercent === 'number' ? `Top1 ${formatPercentValue(summary.top1ExposurePercent)}` : 'Top1 --'}</em>
              </article>
              <article className="summary-metric">
                <span>前 3 暴露</span>
                <strong>{typeof summary?.top3ExposurePercent === 'number' ? formatPercentValue(summary.top3ExposurePercent) : '--'}</strong>
                <em>{summary?.latestReportDate ? `${formatReportQuarter(summary.latestReportDate)} · ${formatFreshnessDays(summary.maxFreshnessDays)}` : '报告期待同步'}</em>
              </article>
              <article className="summary-metric">
                <span>QDII 占比</span>
                <strong>{typeof summary?.qdiiFundRatio === 'number' ? `${Math.round(summary.qdiiFundRatio * 100)}%` : '--'}</strong>
                <em>{summary?.qdiiFundCount ?? 0} 只基金</em>
              </article>
            </div>
            {repeatedHoldings.length ? (
              <section className="detail-card wide-card">
                <div className="detail-card-header compact-card-header">
                  <div>
                    <h3>重复持仓提示</h3>
                    <p className="panel-tip compact">优先展示被多只激活基金共同持有的股票，便于快速识别隐性集中暴露。</p>
                  </div>
                  <span className="table-meta-badge">重复股 {repeatedHoldings.length}</span>
                </div>
                <div className="portfolio-overlap-grid">
                  {repeatedHoldings.slice(0, 4).map((item) => (
                    <article className="portfolio-overlap-card" key={`${item.stockSymbol}-${item.latestReportDate || 'latest'}`}>
                      <strong>{item.stockName || item.stockSymbol}</strong>
                      <p className="panel-tip compact">{item.stockSymbol} · {item.latestReportDate || '报告期待同步'}</p>
                      <div className="fund-card-signal-row">
                        <span className="market-breadth-chip warning">关联 {item.contributingFundCount || 0} 只基金</span>
                        <span className="market-breadth-chip muted">暴露 {formatPercentValue(item.estimatedBasketExposurePercent)}</span>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            ) : null}
            <section className="detail-card wide-card">
              <div className="detail-card-header compact-card-header">
                <div>
                  <h3>结构风险提示</h3>
                  <p className="panel-tip compact">规则层先给出确定性结构信号，再决定是否调用 AI 做补充解释。</p>
                </div>
              </div>
              {renderRiskSignalList(riskSignals, '当前暂无明显结构风险提示。')}
            </section>
            <section className="detail-card wide-card">
              <div className="detail-card-header compact-card-header">
                <div>
                  <h3>合并持仓明细</h3>
                  <p className="panel-tip compact">组合估算权重按已同步基金等权计算，目的是看暴露结构，不是还原真实组合净值。</p>
                </div>
                <span className="table-meta-badge">股票 {summary?.stockExposureCount ?? 0}</span>
              </div>
              <div className="detail-table-wrap detail-table-wrap-scrollable">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>股票</th>
                      <th>估算组合权重</th>
                      <th>关联基金</th>
                      <th>最近报告期</th>
                      <th>最新涨跌</th>
                      <th>单股跌 1% 影响</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stockExposure.length ? stockExposure.map((item) => (
                      <tr key={`${item.stockSymbol}-${item.latestReportDate || 'latest'}`}>
                        <td>
                          {item.stockName || item.stockSymbol}
                          <span className="table-subtext">{item.stockSymbol}{item.stockMarket ? ` · ${item.stockMarket}` : ''}</span>
                        </td>
                        <td>{formatPercentValue(item.estimatedBasketExposurePercent)}</td>
                        <td>
                          {Array.isArray(item.contributingFunds) && item.contributingFunds.length
                            ? item.contributingFunds.map((fund) => fund.fundName || fund.fundCode).join(' / ')
                            : '--'}
                        </td>
                        <td>{item.latestReportDate ? `${formatReportQuarter(item.latestReportDate)} · ${item.latestReportDate}` : '--'}</td>
                        <td className={item.changePct > 0 ? 'positive' : item.changePct < 0 ? 'negative' : ''}>{formatSignedPercent(item.changePct)}</td>
                        <td className={item.stressImpactDown1Pct < 0 ? 'negative' : ''}>{formatSignedPercent(item.stressImpactDown1Pct)}</td>
                      </tr>
                    )) : <tr><td colSpan="6" className="panel-tip compact">当前暂无合并持仓数据。</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="macro-analysis-card fund-portfolio-analysis-card">
              <div className="panel-heading compact-heading">
                <div>
                  <h3>AI 风险解读</h3>
                  <p className="panel-tip compact">
                    {aiEnabled
                      ? '在规则提示基础上，复用共享 LLM 配置生成保守解释；输出只解释观察池结构，不提供交易建议。'
                      : '当前未启用 AI 风险解读，页面仍完整显示规则型风险提示。'}
                  </p>
                </div>
                {aiEnabled ? (
                  <button type="button" className="view-tab" onClick={handleGenerateFundPortfolioAnalysis} disabled={fundPortfolioAnalysisRequestState === 'loading' || fundPortfolioRequestState !== 'ready'}>
                    {fundPortfolioAnalysisRequestState === 'loading' ? '生成中…' : '生成 AI 风险解读'}
                  </button>
                ) : <span className="table-meta-badge">规则模式</span>}
              </div>
              {analysisPayload ? (
                <>
                  <p className="macro-analysis-summary">{analysisPayload.summary || '--'}</p>
                  <div className="macro-analysis-meta">
                    <span className="market-breadth-chip">级别 {analysisPayload.riskLevel || '--'}</span>
                    <span className="market-breadth-chip">引擎 {analysisPayload.engine || 'rules'}</span>
                    <span className="market-breadth-chip">置信度 {typeof analysisPayload.confidence === 'number' ? `${Math.round(analysisPayload.confidence * 100)}%` : '--'}</span>
                  </div>
                  <div className="fund-analysis-grid">
                    <article className="summary-metric">
                      <span>主要驱动</span>
                      <ul className="fund-analysis-list">
                        {(Array.isArray(analysisPayload.drivers) ? analysisPayload.drivers : []).map((item) => <li key={`driver-${item}`}>{item}</li>)}
                      </ul>
                    </article>
                    <article className="summary-metric">
                      <span>继续观察</span>
                      <ul className="fund-analysis-list">
                        {(Array.isArray(analysisPayload.watchItems) ? analysisPayload.watchItems : []).map((item) => <li key={`watch-${item}`}>{item}</li>)}
                      </ul>
                    </article>
                  </div>
                  <p className="panel-tip compact">{Array.isArray(analysisPayload.limitations) && analysisPayload.limitations.length ? analysisPayload.limitations.join(' · ') : '仅作结构风险观察，不构成投资建议。'}</p>
                </>
              ) : <p className="panel-tip compact">{aiEnabled ? '尚未生成 AI 风险解读。' : '当前仅展示规则型风险提示。'}</p>}
              {fundPortfolioAnalysisRequestState === 'error' ? <p className="status-line error">AI 风险解读生成失败，请稍后重试。</p> : null}
            </section>
          </>
        ) : null}
      </div>
    );
  }

  function renderOverviewCard(item, snapshot) {
    const cardTone = getSnapshotCardTone(snapshot?.changePct);

    return (
      <section
        className={`snapshot-card clickable trend-${cardTone}`}
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
          <div className="snapshot-header-side">
            <span>{snapshot?.source || '等待采集'}</span>
            <span className="freshness-chip">{formatAgeLabel(snapshot?.updatedAt)}</span>
          </div>
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
          <div>
            <dt>更新时间</dt>
            <dd>{formatTime(snapshot?.updatedAt)}</dd>
          </div>
        </dl>
      </section>
    );
  }

  async function handleSubmitFund(event) {
    event.preventDefault();
    const normalizedFundCode = fundCode.trim();

    if (!/^\d{6}$/.test(normalizedFundCode)) {
      setFundRequestState('请输入6位基金代码');
      return;
    }

    setFundRequestState('submitting');
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/funds/activate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ fundCode: normalizedFundCode, autoLinkStocks }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || `基金激活失败（HTTP ${response.status}）`);
      }
      setFundRequestState(typeof payload.status === 'string' ? payload.status : 'accepted');
      setFundCode('');
    } catch (error) {
      setFundRequestState(error.message);
    }
  }

  async function handleRemoveFund(fundCodeValue) {
    if (removingFund) {
      return;
    }

    setRemovingFund(fundCodeValue);
    setFundRequestState(`正在移除基金 ${fundCodeValue}...`);
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/funds/${encodeURIComponent(fundCodeValue)}`, {
        method: 'DELETE',
      });
      if (!response.ok) {
        throw new Error(`移除失败（HTTP ${response.status}）`);
      }
      setActiveFunds((current) => current.filter((item) => item !== fundCodeValue));
      setFundRequestState(`已停止监控基金 ${fundCodeValue}`);
    } catch (error) {
      setFundRequestState(error.message);
    } finally {
      setRemovingFund('');
    }
  }

  async function handleGenerateFundPortfolioAnalysis() {
    if (fundPortfolioAnalysisRequestState === 'loading' || !fundPortfolioView || fundPortfolioRequestState !== 'ready') {
      return;
    }
    setFundPortfolioAnalysisRequestState('loading');
    try {
      const response = await fetch(buildFundPortfolioAnalysisUrl(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ focus: 'general', depth: 'brief' }),
      });
      const payload = await parseJsonOrThrow(response, 'fund portfolio analysis generation failed');
      setFundPortfolioAnalysis(payload?.analysis && typeof payload.analysis === 'object' ? payload.analysis : null);
      setFundPortfolioAnalysisRequestState('ready');
    } catch {
      setFundPortfolioAnalysisRequestState('error');
    }
  }

  async function handleGenerateMacroAnalysis() {
    if (macroAnalysisRequestState === 'loading' || !macroSnapshot) {
      return;
    }
    setMacroAnalysisRequestState('loading');
    try {
      const response = await fetch(buildMacroAnalysisGenerateUrl(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ focus: 'qdii_impact', depth: 'brief' }),
      });
      const payload = await parseJsonOrThrow(response, 'macro analysis generation failed');
      setMacroAnalysis(payload?.analysis || null);
      setMacroAnalysisRequestState('ready');
    } catch {
      setMacroAnalysisRequestState('error');
    }
  }

  function renderMacroYieldCard(label, metric) {
    const change = metric?.changeD5Bp;
    return (
      <article className="macro-metric-card" key={label}>
        <div>
          <strong>{label}</strong>
          <p className="snapshot-subtitle">数据日 {metric?.date || '--'}</p>
        </div>
        <div className="snapshot-metric">{formatMacroYield(metric?.value)}</div>
        <dl className="watchlist-meta-grid">
          <div><dt>1日</dt><dd className={metric?.changeD1Bp > 0 ? 'negative' : metric?.changeD1Bp < 0 ? 'positive' : ''}>{formatSignedBasisPoints(metric?.changeD1Bp)}</dd></div>
          <div><dt>5日</dt><dd className={change > 0 ? 'negative' : change < 0 ? 'positive' : ''}>{formatSignedBasisPoints(change)}</dd></div>
          <div><dt>20日</dt><dd className={metric?.changeD20Bp > 0 ? 'negative' : metric?.changeD20Bp < 0 ? 'positive' : ''}>{formatSignedBasisPoints(metric?.changeD20Bp)}</dd></div>
        </dl>
      </article>
    );
  }

  function renderMacroContextCard(label, metric, changeKey = 'changeD1') {
    const change = metric?.[changeKey];
    return (
      <article className="market-index-card" key={label}>
        <strong>{label}</strong>
        <div className="market-index-metric">{typeof metric?.value === 'number' ? metric.value.toFixed(2) : '--'}</div>
        <span className={change > 0 ? 'positive' : change < 0 ? 'negative' : ''}>{changeKey === 'changeD1Pct' ? formatSignedPercent(change) : formatSignedBasisPoints(change).replace('bp', '')}</span>
      </article>
    );
  }

  function renderMacroView() {
    const yields = macroSnapshot?.yields || {};
    const context = macroSnapshot?.context || {};
    const alerts = Array.isArray(macroSnapshot?.alerts) ? macroSnapshot.alerts : [];
    const analysisPayload = macroAnalysis?.analysis || macroAnalysis;
    return (
      <article className="panel wide macro-panel">
        <div className="panel-heading">
          <div>
            <h2>美债宏观</h2>
            <p className="panel-tip compact">FRED 日度数据驱动，后端检测到 FRED_API_KEY 后才展示此页面。</p>
          </div>
          <div className="management-meta">
            <span className="symbol-count-badge">数据日 {macroSnapshot?.date || '--'}</span>
            <span className="symbol-count-badge">状态 {macroCollectorStatus?.status || macroSnapshot?.status || '--'}</span>
          </div>
        </div>
        {macroRequestState === 'loading' ? <p className="status-line pending">宏观数据加载中...</p> : null}
        {macroRequestState === 'error' ? <p className="status-line error">宏观数据加载失败，请稍后重试。</p> : null}
        <div className="macro-grid">
          {renderMacroYieldCard('2Y 美债', yields.y2)}
          {renderMacroYieldCard('10Y 美债', yields.y10)}
          {renderMacroYieldCard('30Y 美债', yields.y30)}
          <article className="macro-metric-card">
            <strong>10Y-2Y 利差</strong>
            <div className="snapshot-metric small">{formatSignedBasisPoints(yields.spread10Y2YBp)}</div>
            <p className="panel-tip compact">收益率曲线倒挂/修复观察项。</p>
          </article>
        </div>
        <div className="market-index-grid macro-context-grid">
          {renderMacroContextCard('VIX', context.vix)}
          {renderMacroContextCard('美元指数', context.dxy)}
          {renderMacroContextCard('S&P 500', context.sp500, 'changeD1Pct')}
        </div>
        {alerts.length ? (
          <div className="macro-alert-list">
            {alerts.map((alert) => <span className="market-breadth-chip warning" key={`${alert.series}-${alert.message}`}>{alert.message}</span>)}
          </div>
        ) : <p className="panel-tip compact">暂无阈值预警。</p>}
        <section className="macro-analysis-card">
          <div className="panel-heading compact-heading">
            <div>
              <h3>宏观解读</h3>
              <p className="panel-tip compact">
                {macroCapabilities.analysisEngine === 'llm' ? '当前复用容器 LLM 配置生成解读；输出仅作宏观观察，不提供交易操作建议。' : 'LLM 不可用时使用规则兜底解读；输出仅作宏观观察，不提供交易操作建议。'}
              </p>
            </div>
            <button type="button" className="view-tab" onClick={handleGenerateMacroAnalysis} disabled={macroAnalysisRequestState === 'loading' || !macroSnapshot}>
              {macroAnalysisRequestState === 'loading' ? '生成中…' : '生成解读'}
            </button>
          </div>
          {analysisPayload ? (
            <>
              <p className="macro-analysis-summary">{analysisPayload.summary || '--'}</p>
              <div className="macro-analysis-meta">
                <span className="market-breadth-chip">影响 {analysisPayload.impactDirection || '--'}</span>
                <span className="market-breadth-chip">强度 {analysisPayload.impactLevel || '--'}</span>
                <span className="market-breadth-chip">置信度 {typeof analysisPayload.confidence === 'number' ? `${Math.round(analysisPayload.confidence * 100)}%` : '--'}</span>
              </div>
              <p className="panel-tip compact">{analysisPayload.watch?.specific || analysisPayload.reasoning?.qdiiImpact || '仅提供宏观观察，不构成投资建议。'}</p>
            </>
          ) : <p className="panel-tip compact">尚未生成解读。</p>}
          {macroAnalysisRequestState === 'error' ? <p className="status-line error">解读生成失败，请稍后重试。</p> : null}
        </section>
      </article>
    );
  }

  function renderLlmAuditBarList(items, labelKey, valueKey) {
    const maxCount = items.reduce((currentMax, item) => Math.max(currentMax, Number(item?.count) || 0), 0);
    if (!items.length) {
      return <p className="panel-tip compact">当日暂无审计记录。</p>;
    }
    return (
      <div className="llm-audit-bar-list">
        {items.map((item) => {
          const count = Number(item?.count) || 0;
          const width = maxCount > 0 ? `${Math.max((count / maxCount) * 100, 6)}%` : '0%';
          return (
            <div className="llm-audit-bar-row" key={`${item?.[valueKey] || item?.[labelKey] || 'unknown'}-${count}`}>
              <div className="llm-audit-bar-header">
                <span className="llm-audit-bar-label">{item?.[labelKey] || '--'}</span>
                <span className="llm-audit-bar-value">{count}</span>
              </div>
              <div className="llm-audit-bar-track" aria-hidden="true">
                <div className="llm-audit-bar-fill" style={{ width }} />
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  function renderLlmAuditView() {
    const summary = llmAuditSummary && typeof llmAuditSummary === 'object' ? llmAuditSummary : {};
    const features = llmAuditCapabilities?.features && typeof llmAuditCapabilities.features === 'object'
      ? llmAuditCapabilities.features
      : {};
    const availableFeatureCount = Object.values(features).filter(Boolean).length;
    const byModule = Array.isArray(summary.byModule)
      ? summary.byModule.map((item) => ({
          ...item,
          label: llmAuditModuleLabels[item?.menuModule] || item?.menuModule || '--',
        }))
      : [];
    const byCategory = Array.isArray(summary.byCategory)
      ? summary.byCategory.map((item) => ({
          ...item,
          label: llmAuditCategoryLabels[item?.callCategory] || item?.callCategory || '--',
        }))
      : [];
    const byStatus = Array.isArray(summary.byStatus)
      ? summary.byStatus.map((item) => ({
          ...item,
          label: llmAuditStatusLabels[item?.status] || item?.status || '--',
        }))
      : [];

    return (
      <article className="panel wide llm-audit-panel">
        <div className="panel-heading">
          <div>
            <h2>LLM审计</h2>
            <p className="panel-tip compact">当前只审计当日调用量，按菜单模块和调用类别聚合展示。</p>
          </div>
          <div className="management-meta">
            <span className="symbol-count-badge">日期 {summary?.date || '--'}</span>
            <button type="button" className="view-tab" onClick={() => setLlmAuditReloadNonce((current) => current + 1)}>
              刷新
            </button>
          </div>
        </div>
        {llmAuditRequestState === 'loading' ? <p className="status-line pending">审计数据加载中...</p> : null}
        {llmAuditRequestState === 'error' ? <p className="status-line error">审计数据加载失败，请稍后重试。</p> : null}
        <div className="llm-audit-summary-grid">
          <article className="llm-audit-summary-card">
            <strong>当日总量</strong>
            <div className="market-index-metric">{Number(summary?.totalCount) || 0}</div>
            <p className="panel-tip compact">当前返回的是当日审计记录总数。</p>
          </article>
          <article className="llm-audit-summary-card">
            <strong>可用能力</strong>
            <div className="market-index-metric">{availableFeatureCount}</div>
            <p className="panel-tip compact">已启用 {Object.keys(features).length} 个 LLM 相关能力中的 {availableFeatureCount} 个。</p>
          </article>
          <article className="llm-audit-summary-card">
            <strong>最新调用</strong>
            <div className="market-index-metric small">{formatRelativeDateTime(summary?.latestInvokedAt)}</div>
            <p className="panel-tip compact">{formatDateTime(summary?.latestInvokedAt)}</p>
          </article>
        </div>
        <div className="llm-audit-chart-grid">
          <section className="llm-audit-chart-card">
            <div className="section-heading compact-heading">
              <div>
                <h3>按菜单模块</h3>
                <p className="panel-tip compact">归属到用户可见的一级菜单模块。</p>
              </div>
            </div>
            {renderLlmAuditBarList(byModule, 'label', 'menuModule')}
          </section>
          <section className="llm-audit-chart-card">
            <div className="section-heading compact-heading">
              <div>
                <h3>按调用类别</h3>
                <p className="panel-tip compact">区分 AI摘要、异动归因和宏观解读。</p>
              </div>
            </div>
            {renderLlmAuditBarList(byCategory, 'label', 'callCategory')}
          </section>
        </div>
        <section className="llm-audit-chart-card">
          <div className="section-heading compact-heading">
            <div>
              <h3>状态分布</h3>
              <p className="panel-tip compact">`completed` 表示产出可用结果，`failed` 表示尝试调用但没有可用输出，`skipped` 表示进入了保护或跳过逻辑。</p>
            </div>
          </div>
          <div className="llm-audit-status-row">
            {byStatus.length ? byStatus.map((item) => (
              <span className="market-breadth-chip" key={`${item.status}-${item.count}`}>
                {item.label} {item.count}
              </span>
            )) : <span className="market-breadth-chip muted">当日暂无状态分布数据</span>}
          </div>
        </section>
      </article>
    );
  }

  function handleOpenGoldFundDetail(fundCodeValue) {
    handleOpenFundDetail(fundCodeValue);
    setActiveView('funds');
  }

  function renderGoldQuoteCard(item) {
    const tone = getSnapshotCardTone(item?.changePct);
    const sourceState = goldDashboard?.sources?.[item?.id] || {};
    const statusLabel = sourceState?.status === 'ok'
      ? '正常'
      : sourceState?.status === 'stale'
        ? '降级'
        : sourceState?.status === 'disabled'
          ? '停用'
          : '异常';

    return (
      <section className={`snapshot-card trend-${tone} gold-quote-card`} key={item?.id || item?.code || item?.name}>
        <header>
          <div>
            <strong>{item?.name || '黄金行情'}</strong>
            <p className="snapshot-subtitle">{item?.code || '--'} · {item?.market || '--'}</p>
          </div>
          <div className="snapshot-header-side">
            <span>{item?.source || '--'}</span>
            <span className={`market-breadth-chip ${sourceState?.status === 'ok' ? 'positive' : sourceState?.status === 'stale' ? 'warning' : sourceState?.status === 'disabled' ? 'muted' : 'negative'}`}>{statusLabel}</span>
          </div>
        </header>
        <div className="snapshot-metric">{formatGoldPrice(item?.price, item?.currency)}</div>
        <dl>
          <div>
            <dt>涨跌幅</dt>
            <dd className={item?.changePct > 0 ? 'positive' : item?.changePct < 0 ? 'negative' : ''}>{formatSignedPercent(item?.changePct)}</dd>
          </div>
          <div>
            <dt>最高/最低</dt>
            <dd>{formatGoldPrice(item?.high, item?.currency)} / {formatGoldPrice(item?.low, item?.currency)}</dd>
          </div>
          <div>
            <dt>开盘</dt>
            <dd>{formatGoldPrice(item?.open, item?.currency)}</dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{formatTime(item?.updatedAt)}</dd>
          </div>
        </dl>
        {sourceState?.error ? <p className="panel-tip compact">来源异常：{sourceState.error}</p> : null}
      </section>
    );
  }

  function renderGoldFundCard(item) {
    return (
      <section className="snapshot-card fund-card gold-fund-card" key={item?.fundCode || item?.fundName}>
        <header>
          <div>
            <strong>{item?.fundName || '黄金基金'}</strong>
            <p className="snapshot-subtitle">{item?.fundCode || '--'} · {item?.fundType || '基金'}</p>
          </div>
          <div className="snapshot-header-side">
            <span>{item?.source || '--'}</span>
            <span className="freshness-chip">净值日 {item?.navDate || '--'}</span>
          </div>
        </header>
        <div className="snapshot-metric">{formatNav(item?.nav)}</div>
        <dl>
          <div>
            <dt>日涨跌</dt>
            <dd className={item?.dailyReturn > 0 ? 'positive' : item?.dailyReturn < 0 ? 'negative' : ''}>{formatSignedPercent(item?.dailyReturn)}</dd>
          </div>
          <div>
            <dt>估算联动</dt>
            <dd className={item?.estimatedIntradayReturn > 0 ? 'positive' : item?.estimatedIntradayReturn < 0 ? 'negative' : ''}>{formatSignedPercent(item?.estimatedIntradayReturn)}</dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{formatRelativeDateTime(item?.updatedAt)}</dd>
          </div>
          <div>
            <dt>基金公司</dt>
            <dd>{item?.fundCompany || '--'}</dd>
          </div>
        </dl>
        {item?.fundCode ? (
          <div className="gold-fund-action-row">
            <button type="button" className="view-tab" onClick={() => handleOpenGoldFundDetail(item.fundCode)}>
              查看基金详情
            </button>
          </div>
        ) : null}
      </section>
    );
  }

  function renderGoldView() {
    const goldQuotes = Array.isArray(goldDashboard?.quotes)
      ? [...goldDashboard.quotes].sort((left, right) => {
        const leftOrder = typeof left?.sortOrder === 'number' ? left.sortOrder : Number.POSITIVE_INFINITY;
        const rightOrder = typeof right?.sortOrder === 'number' ? right.sortOrder : Number.POSITIVE_INFINITY;
        return leftOrder - rightOrder;
      })
      : [];
    const goldSources = Object.values(goldDashboard?.sources || {});
    const degradedSources = goldSources.filter((item) => item?.status && item.status !== 'ok');

    return (
      <article className="panel wide gold-panel">
        <div className="panel-heading">
          <div>
            <h2>黄金</h2>
            <p className="panel-tip compact">已验证数据源驱动的跨市场黄金看板，当前先聚焦实时价格、黄金基金和相关新闻。</p>
          </div>
          <div className="content-status-summary">
            <span className="symbol-count-badge">最近更新 {formatRelativeDateTime(goldDashboard.generatedAt)}</span>
            <span className="symbol-count-badge">刷新模式 {goldDashboard.isTradingSession ? '交易时段' : '非交易时段'}</span>
            {goldDashboard.degraded ? <span className="symbol-count-badge warning">来源降级 {degradedSources.length}</span> : null}
          </div>
        </div>

        {goldRequestState === 'loading' ? <p className="status-line pending">黄金看板加载中...</p> : null}
        {goldRequestState === 'error' ? <p className="status-line error">黄金看板加载失败，请稍后重试。</p> : null}

        <div className="gold-source-row">
          {goldSources.map((item) => (
            <span className={`market-breadth-chip ${item?.status === 'ok' ? 'positive' : item?.status === 'stale' ? 'warning' : item?.status === 'disabled' ? 'muted' : 'negative'}`} key={`${item?.id || 'gold-source'}-${item?.status || 'unknown'}`}>
              {(item?.id || '').toUpperCase() || 'SOURCE'} · {item?.status === 'ok' ? '正常' : item?.status === 'stale' ? '降级' : item?.status === 'disabled' ? '停用' : '异常'}
            </span>
          ))}
        </div>

        <div className="snapshot-grid gold-quote-grid">
          {goldQuotes.map((item) => renderGoldQuoteCard(item))}
        </div>

        <section className="gold-note-card">
          <div className="section-heading compact-heading">
            <div>
              <h3>走势对比</h3>
              <p className="panel-tip compact">历史叠加图会在黄金历史落库后补齐；当前版本先保证实时价格、黄金基金和资讯链路稳定可用。</p>
            </div>
          </div>
        </section>

        <section className="gold-section">
          <div className="section-heading">
            <div>
              <h3>我的黄金基金</h3>
              <p className="panel-tip compact">复用现有基金监控快照，自动筛出已激活的黄金相关基金。</p>
            </div>
            <span className="table-meta-badge">共 {Array.isArray(goldDashboard.funds) ? goldDashboard.funds.length : 0} 只</span>
          </div>
          <div className="snapshot-grid fund-grid gold-fund-grid">
            {Array.isArray(goldDashboard.funds) && goldDashboard.funds.length
              ? goldDashboard.funds.map((item) => renderGoldFundCard(item))
              : <p className="panel-tip compact">当前没有已激活的黄金相关基金。</p>}
          </div>
        </section>

        <section className="gold-section">
          <div className="section-heading">
            <div>
              <h3>关联资讯</h3>
              <p className="panel-tip compact">复用现有市场快讯链路，按黄金关键词过滤标题。</p>
            </div>
            <span className="table-meta-badge">共 {Array.isArray(goldDashboard.news) ? goldDashboard.news.length : 0} 条</span>
          </div>
          <div className="content-grid gold-news-grid">
            {Array.isArray(goldDashboard.news) && goldDashboard.news.length
              ? goldDashboard.news.map((item) => renderContentCard(item))
              : <p className="content-empty-state">当前没有匹配的黄金资讯。</p>}
          </div>
        </section>
      </article>
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

  function renderMarketOverviewBar() {
    if (!Array.isArray(marketOverview.indexes) || !marketOverview.indexes.length) {
      return null;
    }

    const breadth = marketOverview?.breadth && typeof marketOverview.breadth === 'object' ? marketOverview.breadth : null;
    const breadthSource = typeof breadth?.source === 'string' ? breadth.source : 'unknown';
    const breadthIsDegraded = Boolean(breadth?.degraded);
    const advanceCount = typeof breadth?.advanceCount === 'number' ? breadth.advanceCount : null;
    const declineCount = typeof breadth?.declineCount === 'number' ? breadth.declineCount : null;
    const totalWidth = typeof advanceCount === 'number' && typeof declineCount === 'number' ? advanceCount + declineCount : 0;
    const advanceRatio = totalWidth > 0 ? (advanceCount / totalWidth) * 100 : 0;
    const declineRatio = totalWidth > 0 ? (declineCount / totalWidth) * 100 : 0;

    return (
      <div className="market-overview-bar">
        <div className="market-overview-header">
          <div>
            <p className="eyebrow">市场概览</p>
            <p className="panel-tip compact">独立指数链路提供大盘状态，不占用个股监控采集循环。</p>
          </div>
          <div className="market-overview-meta">
            <span className={`status-line ${getMarketStatusTone(connectionState === 'connected' ? marketStatus : 'disconnected')}`}>
              市场状态：{getMarketStatusLabel(connectionState === 'connected' ? marketStatus : 'disconnected')}
            </span>
            <span className="symbol-count-badge">状态时间 {marketStatusUpdatedAt ? formatTime(marketStatusUpdatedAt) : '--:--:--'}</span>
          </div>
        </div>
        <div className="market-index-grid">
          {marketOverview.indexes.map((item) => (
            <article className="market-index-card" key={item.code || item.symbol || item.name}>
              <div>
                <strong>{item.name || item.symbol || '--'}</strong>
                <p className="snapshot-subtitle">{item.code || '--'}</p>
              </div>
              <div className="market-index-metric">{formatPrice(item.lastPrice)}</div>
              <div className="market-index-meta-row">
                <span className={item.changePct > 0 ? 'positive' : item.changePct < 0 ? 'negative' : ''}>
                  {formatSignedPercent(item.changePct)}
                </span>
                <span className={item.changeAmount > 0 ? 'positive' : item.changeAmount < 0 ? 'negative' : ''}>
                  {typeof item.changeAmount === 'number' ? `${item.changeAmount > 0 ? '+' : ''}${item.changeAmount.toFixed(2)}` : '--'}
                </span>
              </div>
            </article>
          ))}
        </div>
        <div className="market-breadth-panel">
          {breadth ? (
            <>
              <div className="market-breadth-summary" aria-hidden="true">
                <div className="market-breadth-side fall">
                  <span className="market-breadth-label">下跌</span>
                  <strong>{breadth.declineCount ?? '--'}</strong>
                </div>
                <div className="market-breadth-side rise">
                  <span className="market-breadth-label">上涨</span>
                  <strong>{breadth.advanceCount ?? '--'}</strong>
                </div>
              </div>
              <div className="market-breadth-track" aria-label="下跌上涨对抗条">
                <div className="market-breadth-segment fall" style={{ width: `${declineRatio}%` }} />
                <div className="market-breadth-segment rise" style={{ width: `${advanceRatio}%` }} />
              </div>
              <div className="market-breadth-footnote">
                <span className="market-breadth-chip flat">平盘 {breadth.flatCount ?? '--'}</span>
                <span className="market-breadth-chip">涨停 {breadth.limitUpCount ?? '--'}</span>
                <span className="market-breadth-chip">跌停 {breadth.limitDownCount ?? '--'}</span>
                <span className="market-breadth-chip muted">样本 {breadth.sampleSize ?? '--'}</span>
                <span className={`market-breadth-chip ${breadthIsDegraded ? 'warning' : 'muted'}`}>
                  {breadthIsDegraded ? '降级样本' : `来源 ${breadthSource}`}
                </span>
              </div>
            </>
          ) : (
            <span className="market-breadth-chip muted">暂无市场广度数据</span>
          )}
        </div>
      </div>
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
              {item.aiSummary ? <span className="content-ai-badge">AI摘要</span> : null}
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
        <p className="content-card-summary">{item.aiSummary || item.summary || '当前仅同步到标题与基础元信息。'}</p>
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

  function renderDragonTigerCard(item, index) {
    const dealAmountLabel = typeof item.dealAmountRatio === 'number' ? `龙虎榜成交额 · ${formatPercentValue(item.dealAmountRatio)}` : '龙虎榜成交额';
    const details = Array.isArray(item.dailyDetails) && item.dailyDetails.length
      ? item.dailyDetails
      : [{ reason: item.reason || item.explain || '上榜', tab: item.dailyTab || 'single' }];
    const reasonCount = item.dailyReasonCount || details.length;
    return (
      <article className="dragon-tiger-card" key={`${item.code || item.symbol || item.name || 'dragon-tiger'}-${item.tradeDate || item.latestDate || 'unknown-date'}-${index}`}>
        <header className="dragon-tiger-card-header">
          <div>
            <div className="dragon-tiger-card-title-row">
              <strong>{item.name || '--'}</strong>
              <span className="dragon-tiger-source-chip">{dragonTigerDailyTabLabels[item.dailyTab] || '单日榜'}</span>
              {reasonCount > 1 ? <span className="dragon-tiger-reason-count">同日 {reasonCount} 个原因</span> : null}
            </div>
            <p className="snapshot-subtitle">
              {item.code || item.symbol || '--'} · {item.tradeDate || item.latestDate || '--'}
            </p>
          </div>
          <div className="dragon-tiger-card-right">
            <span className="dragon-tiger-metric">{formatPrice(item.closePrice)}</span>
            <span className="dragon-tiger-source-chip">东方财富</span>
          </div>
        </header>
        <div className="dragon-tiger-reason-list">
          {details.map((detail, detailIndex) => (
            <span className="dragon-tiger-reason-item" key={`${detail.reason}-${detailIndex}`}>
              <span className="dragon-tiger-reason-tab">{dragonTigerDailyTabLabels[detail.tab] || '单日榜'}</span>
              {detail.reason || '上榜'}
            </span>
          ))}
        </div>
        <div className="dragon-tiger-metrics-row">
          <span className={`dragon-tiger-pill ${item.netBuyAmount > 0 ? 'positive' : item.netBuyAmount < 0 ? 'negative' : ''}`}>
            净买额 {formatTurnoverAmount(item.netBuyAmount)}
          </span>
          <span className="dragon-tiger-pill">买/卖 {formatTurnoverAmount(item.buyAmount)} / {formatTurnoverAmount(item.sellAmount)}</span>
          <span className="dragon-tiger-pill">{dealAmountLabel} {formatTurnoverAmount(item.dealAmount)}</span>
          <span className="dragon-tiger-pill">总成交额 {formatTurnoverAmount(item.totalAmount)}</span>
        </div>
      </article>
    );
  }

  function renderDragonTigerView() {
    const visibleStocksPageCount = Math.max(1, Math.ceil(Math.min(dragonTigerStocksSorted.length, dragonTigerDefaultStockDisplayLimit) / dragonTigerDefaultPageSize));
    const dragonTigerStocksPaged = dragonTigerStocksExpanded
      ? dragonTigerStocksVisible
      : dragonTigerStocksVisible.slice((dragonTigerStocksPage - 1) * dragonTigerDefaultPageSize, dragonTigerStocksPage * dragonTigerDefaultPageSize);

    return (
      <article className="panel wide dragon-tiger-panel">
        <div className="panel-heading">
          <div>
            <h2>龙虎榜</h2>
              <p className="panel-tip compact">默认先看当日上榜个股与上榜原因，近月统计和席位明细按需展开。</p>
          </div>
          <div className="content-status-summary">
            <span className="symbol-count-badge">日榜日期 {dragonTigerDate}</span>
            {dragonTigerStatsExpanded ? <span className="symbol-count-badge">统计区间 {dragonTigerRangeLabels[dragonTigerRange] || dragonTigerRange}</span> : null}
          </div>
        </div>

        <div className="submenu-row content-toolbar-row dragon-tiger-toolbar-row">
          <div className="content-toolbar-groups dragon-tiger-toolbar-groups">
            <div className="content-filter-row dragon-tiger-filter-row">
              <label className="content-symbol-select-label" htmlFor="dragon-tiger-date">日期</label>
              <input
                id="dragon-tiger-date"
                className="overview-search-input dragon-tiger-date-input"
                type="date"
                value={dragonTigerDate}
                onChange={(event) => {
                  setDragonTigerDate(event.target.value);
                  setDragonTigerDateManuallySet(true);
                }}
              />
            </div>
            <div className="content-filter-row dragon-tiger-filter-row">
              <label className="content-symbol-select-label" htmlFor="dragon-tiger-search">搜索</label>
              <input
                id="dragon-tiger-search"
                className="overview-search-input dragon-tiger-date-input"
                value={dragonTigerSearchQuery}
                onChange={(event) => setDragonTigerSearchQuery(event.target.value)}
                placeholder="按代码、名称、上榜原因筛选"
              />
            </div>
            <div className="content-filter-row dragon-tiger-filter-row">
              <label className="content-symbol-select-label" htmlFor="dragon-tiger-daily-sort">日榜排序</label>
              <div className="overview-sort-controls">
                <select
                  id="dragon-tiger-daily-sort"
                  className="content-symbol-select"
                  value={dragonTigerDailySortKey}
                  onChange={(event) => setDragonTigerDailySortKey(event.target.value)}
                >
                  {dragonTigerSortOptions.daily.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="content-switch-option overview-sort-toggle"
                  onClick={() => setDragonTigerDailySortDirection((current) => (current === 'desc' ? 'asc' : 'desc'))}
                >
                  {dragonTigerDailySortDirection === 'desc' ? '降序' : '升序'}
                </button>
              </div>
            </div>
            {dragonTigerStatsExpanded ? (
              <div className="content-filter-row dragon-tiger-filter-row">
                <label className="content-symbol-select-label" htmlFor="dragon-tiger-range">区间</label>
                <select
                  id="dragon-tiger-range"
                  className="content-symbol-select"
                  value={dragonTigerRange}
                  onChange={(event) => setDragonTigerRange(event.target.value)}
                >
                  {Object.entries(dragonTigerRangeLabels).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </div>
            ) : null}
          </div>
          <div className="dragon-tiger-toolbar-meta">
            {dragonTigerRequestState === 'loading' ? <span className="status-line pending">龙虎榜数据加载中...</span> : null}
            {dragonTigerRequestState === 'error' ? <span className="status-line error">龙虎榜数据加载失败，请稍后重试。</span> : null}
            <button type="button" className="content-switch-option" onClick={() => setDragonTigerReloadNonce((current) => current + 1)}>
              重新加载
            </button>
          </div>
        </div>

        {dragonTigerRequestState === 'ready' && dragonTigerErrorMessage ? <p className="status-line pending">{dragonTigerErrorMessage}</p> : null}
        {dragonTigerRequestState === 'error' && dragonTigerErrorMessage ? <p className="panel-tip compact">{dragonTigerErrorMessage}</p> : null}

        <div className="dragon-tiger-section">
          <div className="section-heading">
            <div>
              <h3>日榜总览</h3>
              <p className="panel-tip compact">支持按代码、名称和同日原因筛选，切换 Tab 后会重置分页；四个 Tab 都是同一天出榜数据。
              </p>
            </div>
            <div className="content-status-summary">
              <span className="table-meta-badge">共 {dragonTigerDailySorted.length} 条</span>
            </div>
          </div>
          <div className="dragon-tiger-tab-row" role="tablist" aria-label="龙虎榜日榜分类切换">
            {dragonTigerDailyTabs.map((tab) => (
              <button
                key={tab.value}
                type="button"
                className={dragonTigerDailyTab === tab.value ? 'view-tab active' : 'view-tab'}
                onClick={() => setDragonTigerDailyTab(tab.value)}
                aria-pressed={dragonTigerDailyTab === tab.value}
              >
                {tab.label}
                <span className="dragon-tiger-tab-count">{dragonTigerDailyTabCounts[tab.value] || 0}</span>
              </button>
            ))}
          </div>
          <div className="panel-scroll-area">
            <div className="dragon-tiger-grid">
              {dragonTigerDailyVisible.length ? dragonTigerDailyVisible.map((item, index) => renderDragonTigerCard(item, index)) : <p className="panel-tip compact">当前筛选条件下暂无龙虎榜日榜数据。</p>}
            </div>
          </div>
        </div>

        <div className="dragon-tiger-section">
          <div className="section-heading">
            <div>
              <h3>近月统计与席位明细</h3>
              <p className="panel-tip compact">按需展开近一月到近一年的个股统计、机构净买额、营业部排行和席位明细。</p>
            </div>
            <button
              type="button"
              className="content-switch-option"
              onClick={() => setDragonTigerStatsExpanded((current) => !current)}
            >
              {dragonTigerStatsExpanded ? '收起统计' : '展开统计'}
            </button>
          </div>
          {!dragonTigerStatsExpanded ? <p className="panel-tip compact">默认收起，避免首屏直接加载近月长表。</p> : null}
          {dragonTigerStatsExpanded ? (
            <>
              {dragonTigerStatsRequestState === 'loading' ? <p className="status-line pending">统计数据加载中...</p> : null}
              {dragonTigerStatsRequestState === 'error' ? <p className="status-line error">统计数据加载失败，请稍后重试。</p> : null}
              {dragonTigerStatsRequestState === 'ready' && dragonTigerStatsErrorMessage ? <p className="status-line pending">{dragonTigerStatsErrorMessage}</p> : null}
              {dragonTigerStatsRequestState === 'error' && dragonTigerStatsErrorMessage ? <p className="panel-tip compact">{dragonTigerStatsErrorMessage}</p> : null}

               <div className="section-heading">
                 <div>
                   <h3>个股上榜统计</h3>
                   <p className="panel-tip compact">支持切换排序字段；默认仍限制在前 100 只内以避免首屏过长。</p>
                 </div>
                 <span className="table-meta-badge">共 {dragonTigerStocksSorted.length} 只</span>
               </div>
              <div className="dragon-tiger-pagination-row">
                <div className="content-filter-row dragon-tiger-filter-row">
                  <label className="content-symbol-select-label" htmlFor="dragon-tiger-stocks-sort">个股统计排序</label>
                  <div className="overview-sort-controls">
                    <select
                      id="dragon-tiger-stocks-sort"
                      className="content-symbol-select"
                      value={dragonTigerStocksSortKey}
                      onChange={(event) => setDragonTigerStocksSortKey(event.target.value)}
                    >
                      {dragonTigerSortOptions.stocks.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="content-switch-option overview-sort-toggle"
                      onClick={() => setDragonTigerStocksSortDirection((current) => (current === 'desc' ? 'asc' : 'desc'))}
                    >
                      {dragonTigerStocksSortDirection === 'desc' ? '降序' : '升序'}
                    </button>
                  </div>
                </div>
                <button type="button" className="content-switch-option" onClick={() => setDragonTigerStatsReloadNonce((current) => current + 1)}>
                  重新加载统计
                </button>
              </div>
              {dragonTigerStocksSorted.length > dragonTigerDefaultStockDisplayLimit ? (
                <div className="content-toolbar-groups">
                  <button
                    type="button"
                    className="content-switch-option"
                    onClick={() => setDragonTigerStocksExpanded((current) => !current)}
                  >
                    {dragonTigerStocksExpanded ? '仅看前100' : `显示全部 ${dragonTigerStocksSorted.length} 只`}
                  </button>
                </div>
              ) : null}
          <div className="detail-table-wrap detail-table-wrap-scrollable dragon-tiger-table-wrap">
            <table className="detail-table dragon-tiger-table">
              <thead>
                <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>最近上榜日</th>
                    <th>上榜次数</th>
                    <th>净买额</th>
                    <th>买入额</th>
                    <th>卖出额</th>
                    <th>机构买入净额</th>
                </tr>
              </thead>
              <tbody>
                {dragonTigerStocksPaged.length ? dragonTigerStocksPaged.map((item, index) => (
                  <tr key={`${item.code || item.symbol || item.name || 'stock'}-${item.latestTradeDate || item.latestDate || 'unknown-date'}-${index}`}>
                      <td>{item.symbol || item.code || '--'}</td>
                      <td>{item.name || '--'}</td>
                      <td>{item.latestTradeDate ? formatDate(item.latestTradeDate) : '--'}</td>
                      <td>{item.billboardTimes ?? '--'}</td>
                      <td className={item.netBuyAmount > 0 ? 'positive' : item.netBuyAmount < 0 ? 'negative' : ''}>{formatTurnoverAmount(item.netBuyAmount)}</td>
                      <td>{formatTurnoverAmount(item.buyAmount)}</td>
                      <td>{formatTurnoverAmount(item.sellAmount)}</td>
                      <td className={item.orgNetBuyAmount > 0 ? 'positive' : item.orgNetBuyAmount < 0 ? 'negative' : ''}>{formatTurnoverAmount(item.orgNetBuyAmount)}</td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan="8" className="panel-tip compact">暂无个股上榜统计数据。</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {dragonTigerStocksSorted.length > dragonTigerDefaultStockDisplayLimit && !dragonTigerStocksExpanded ? (
            <p className="panel-tip compact">默认展示上榜次数最高的前 {dragonTigerDefaultStockDisplayLimit} 只个股，避免近月统计列表过长。</p>
          ) : null}
          {!dragonTigerStocksExpanded && visibleStocksPageCount > 1 ? (
            <div className="dragon-tiger-pagination-row">
              <span className="panel-tip compact">第 {dragonTigerStocksPage} / {visibleStocksPageCount} 页</span>
              <div className="dragon-tiger-pagination-actions">
                <button type="button" className="content-switch-option" disabled={dragonTigerStocksPage <= 1} onClick={() => setDragonTigerStocksPage((current) => Math.max(current - 1, 1))}>上一页</button>
                <button type="button" className="content-switch-option" disabled={dragonTigerStocksPage >= visibleStocksPageCount} onClick={() => setDragonTigerStocksPage((current) => Math.min(current + 1, visibleStocksPageCount))}>下一页</button>
              </div>
            </div>
          ) : null}
            </>
          ) : null}
        </div>

        {dragonTigerStatsExpanded ? (
          <>
            <div className="dragon-tiger-section">
              <div className="section-heading">
                <div>
                  <h3>机构买卖统计</h3>
                  <p className="panel-tip compact">按日期区间展示机构净买额与买卖额。</p>
                </div>
                <span className="table-meta-badge">共 {dragonTigerInstitutionSorted.length} 条</span>
              </div>
              <div className="detail-table-wrap detail-table-wrap-scrollable dragon-tiger-table-wrap">
                <table className="detail-table dragon-tiger-table">
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>名称</th>
                      <th>上榜日</th>
                      <th>机构净买额</th>
                      <th>机构买入额</th>
                      <th>机构卖出额</th>
                      <th>买方机构数</th>
                      <th>卖方机构数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dragonTigerInstitutionSorted.length ? dragonTigerInstitutionSorted.map((item, index) => (
                      <tr key={`${item.symbol || item.code || item.name || 'institution'}-${item.tradeDate || 'unknown-date'}-${index}`}>
                        <td>{item.symbol || item.code || '--'}</td>
                        <td>{item.name || '--'}</td>
                        <td>{item.tradeDate ? formatDate(item.tradeDate) : '--'}</td>
                        <td className={item.orgNetAmount > 0 ? 'positive' : item.orgNetAmount < 0 ? 'negative' : ''}>{formatTurnoverAmount(item.orgNetAmount)}</td>
                        <td>{formatTurnoverAmount(item.orgBuyAmount)}</td>
                        <td>{formatTurnoverAmount(item.orgSellAmount)}</td>
                        <td>{item.buyOrgCount ?? '--'}</td>
                        <td>{item.sellOrgCount ?? '--'}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan="8" className="panel-tip compact">暂无机构买卖统计数据。</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="dragon-tiger-section">
              <div className="section-heading">
                <div>
                  <h3>营业部排行</h3>
                  <p className="panel-tip compact">展示营业部近阶段回报表现。</p>
                </div>
                <span className="table-meta-badge">共 {dragonTigerBranchRankSorted.length} 条</span>
              </div>
              <div className="detail-table-wrap detail-table-wrap-scrollable dragon-tiger-table-wrap">
                <table className="detail-table dragon-tiger-table">
                  <thead>
                    <tr>
                      <th>营业部</th>
                      <th>1日平均涨幅</th>
                      <th>1日上涨概率</th>
                      <th>2日平均涨幅</th>
                      <th>2日上涨概率</th>
                      <th>3日平均涨幅</th>
                      <th>3日上涨概率</th>
                      <th>5日平均涨幅</th>
                      <th>5日上涨概率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dragonTigerBranchRankSorted.length ? dragonTigerBranchRankSorted.map((item) => (
                      <tr key={item.branchName || ''}>
                        <td>{item.branchName || '--'}</td>
                        <td>{typeof item.avgIncrease1d === 'number' ? formatSignedPercent(item.avgIncrease1d) : '--'}</td>
                        <td>{typeof item.riseProbability1d === 'number' ? formatPercentValue(item.riseProbability1d) : '--'}</td>
                        <td>{typeof item.avgIncrease2d === 'number' ? formatSignedPercent(item.avgIncrease2d) : '--'}</td>
                        <td>{typeof item.riseProbability2d === 'number' ? formatPercentValue(item.riseProbability2d) : '--'}</td>
                        <td>{typeof item.avgIncrease3d === 'number' ? formatSignedPercent(item.avgIncrease3d) : '--'}</td>
                        <td>{typeof item.riseProbability3d === 'number' ? formatPercentValue(item.riseProbability3d) : '--'}</td>
                        <td>{typeof item.avgIncrease5d === 'number' ? formatSignedPercent(item.avgIncrease5d) : '--'}</td>
                        <td>{typeof item.riseProbability5d === 'number' ? formatPercentValue(item.riseProbability5d) : '--'}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan="9" className="panel-tip compact">暂无营业部排行数据。</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="dragon-tiger-section">
              <div className="section-heading">
                <div>
                  <h3>席位明细</h3>
                  <p className="panel-tip compact">先选择个股，再选上榜日与方向查看买卖席位。</p>
                </div>
                <span className="table-meta-badge">共 {dragonTigerSeatDetailSorted.length} 条</span>
              </div>
              <div className="dragon-tiger-toolbar">
                <div className="content-filter-row dragon-tiger-filter-row">
                  <label className="content-symbol-select-label" htmlFor="dragon-tiger-seat-symbol">个股</label>
                  <select
                    id="dragon-tiger-seat-symbol"
                    className="content-symbol-select"
                    value={dragonTigerSeatSymbol}
                    onChange={(event) => setDragonTigerSeatSymbol(event.target.value)}
                  >
                    <option value="">请选择个股</option>
                    {dragonTigerStocksSorted.slice(0, 50).map((item, index) => (
                      <option key={`${item.symbol || item.code || item.name || 'seat-symbol'}-${item.latestTradeDate || item.latestDate || 'unknown-date'}-${index}`} value={item.symbol || item.code || ''}>
                        {item.name || item.symbol || item.code || '--'}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="content-filter-row dragon-tiger-filter-row">
                  <label className="content-symbol-select-label" htmlFor="dragon-tiger-seat-date">上榜日</label>
                  <select
                    id="dragon-tiger-seat-date"
                    className="content-symbol-select"
                    value={dragonTigerSeatDate}
                    onChange={(event) => setDragonTigerSeatDate(event.target.value)}
                  >
                    <option value="">请选择日期</option>
                    {dragonTigerSeatDatesSorted.map((item) => (
                      <option key={`${item.symbol || ''}-${item.tradeDate || ''}`} value={item.tradeDate || ''}>
                        {item.tradeDate ? formatDate(item.tradeDate) : '--'}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="content-filter-row dragon-tiger-filter-row">
                  <label className="content-symbol-select-label" htmlFor="dragon-tiger-seat-side">方向</label>
                  <select
                    id="dragon-tiger-seat-side"
                    className="content-symbol-select"
                    value={dragonTigerSeatSide}
                    onChange={(event) => setDragonTigerSeatSide(event.target.value)}
                  >
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                  </select>
                </div>
              </div>
              <div className="detail-table-wrap detail-table-wrap-scrollable dragon-tiger-table-wrap">
                <table className="detail-table dragon-tiger-table">
                  <thead>
                    <tr>
                      <th>营业部</th>
                      <th>席位类型</th>
                      <th>买入额</th>
                      <th>卖出额</th>
                      <th>净额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dragonTigerSeatDetailSorted.length ? dragonTigerSeatDetailSorted.map((item, index) => (
                      <tr key={`${item.branchName || 'branch'}-${index}`}>
                        <td>{item.branchName || '--'}</td>
                        <td>{item.seatType || '--'}</td>
                        <td>{formatTurnoverAmount(item.buyAmount)}</td>
                        <td>{formatTurnoverAmount(item.sellAmount)}</td>
                        <td className={item.netAmount > 0 ? 'positive' : item.netAmount < 0 ? 'negative' : ''}>{formatTurnoverAmount(item.netAmount)}</td>
                      </tr>
                    )) : (
                      <tr><td colSpan="5" className="panel-tip compact">暂无席位明细数据。</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        ) : null}
      </article>
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

  function renderFundCard(item) {
    const snapshot = fundSnapshots[item] || {};
    const holdings = Array.isArray(snapshot.topHoldingsPreview) ? snapshot.topHoldingsPreview : [];
    const transparency = snapshot?.transparency && typeof snapshot.transparency === 'object' ? snapshot.transparency : {};
    return (
      <section className="snapshot-card fund-card clickable" key={item} role="button" tabIndex={0} onClick={() => handleOpenFundDetail(item)} onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleOpenFundDetail(item);
        }
      }}>
        <header>
          <div>
            <strong>{snapshot.fundName || '待识别基金'}</strong>
            <p className="snapshot-subtitle">{item} · {snapshot.fundType || '基金'}</p>
          </div>
          <div className="snapshot-header-side">
            <span>{snapshot.source || '等待采集'}</span>
            <span className="freshness-chip">净值日 {snapshot.navDate || '--'}</span>
          </div>
        </header>
        <div className="snapshot-metric">{formatNav(snapshot.nav)}</div>
        <dl>
          <div>
            <dt>日涨跌</dt>
            <dd className={snapshot.dailyReturn > 0 ? 'positive' : snapshot.dailyReturn < 0 ? 'negative' : ''}>{formatSignedPercent(snapshot.dailyReturn)}</dd>
          </div>
          <div>
            <dt>估算联动</dt>
            <dd className={snapshot.estimatedIntradayReturn > 0 ? 'positive' : snapshot.estimatedIntradayReturn < 0 ? 'negative' : ''}>{formatSignedPercent(snapshot.estimatedIntradayReturn)}</dd>
          </div>
          <div>
            <dt>披露覆盖</dt>
            <dd>{typeof transparency.disclosedWeightPercent === 'number' ? formatPercentValue(transparency.disclosedWeightPercent) : '--'}</dd>
          </div>
          <div>
            <dt>报告期</dt>
            <dd>{transparency.latestReportDate ? `${formatReportQuarter(transparency.latestReportDate)} · ${formatFreshnessDays(transparency.freshnessDays)}` : '--'}</dd>
          </div>
          <div>
            <dt>重仓摘要</dt>
            <dd>{holdings.length ? holdings.slice(0, 3).map((holding) => holding.stockName || holding.stockSymbol).join(' / ') : '--'}</dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{formatRelativeDateTime(snapshot.updatedAt)}</dd>
          </div>
        </dl>
        <div className="fund-card-signal-row">
          {transparency.latestReportDate ? <span className="market-breadth-chip muted">{formatReportQuarter(transparency.latestReportDate)}</span> : null}
          {typeof transparency.freshnessDays === 'number' ? <span className={`market-breadth-chip ${transparency.freshnessDays >= 45 ? 'warning' : 'muted'}`}>{formatFreshnessDays(transparency.freshnessDays)}</span> : null}
          {typeof transparency.disclosedWeightPercent === 'number' ? <span className="market-breadth-chip muted">披露约 {Math.round(transparency.disclosedWeightPercent)}%</span> : null}
        </div>
        <p className="panel-tip compact fund-card-note">估算联动仅基于已披露重仓与实时股价。</p>
      </section>
    );
  }

  function renderFundDetailView() {
    const detail = selectedFundCode ? fundDetails[selectedFundCode] : null;
    const profile = detail?.profile || {};
    const snapshot = detail?.snapshot || fundSnapshots[selectedFundCode] || {};
    const transparency = detail?.transparency || snapshot?.transparency || {};
    const navHistory = Array.isArray(detail?.navHistory) ? detail.navHistory : [];
    const topHoldings = Array.isArray(detail?.topHoldings) ? detail.topHoldings : [];
    const performance = Array.isArray(detail?.holdingStocksPerformance) ? detail.holdingStocksPerformance : [];
    const riskSignals = Array.isArray(detail?.riskSignals) ? detail.riskSignals : Array.isArray(snapshot?.riskSignals) ? snapshot.riskSignals : [];

    return (
      <div className="detail-view fund-detail-view">
        <button className="back-button" type="button" onClick={handleCloseFundDetail}>← 返回基金总览</button>
        <div className="detail-header">
          <div>
            <p className="eyebrow">基金详情</p>
            <h2>{profile.fundName || snapshot.fundName || selectedFundCode}</h2>
            <p className="lede">{selectedFundCode} · {profile.fundType || snapshot.fundType || '基金'} · 净值日 {snapshot.navDate || '--'}</p>
          </div>
          <div className="detail-header-metrics">
            <span className="snapshot-metric small">{formatNav(snapshot.nav)}</span>
            <span className={snapshot.dailyReturn > 0 ? 'positive' : snapshot.dailyReturn < 0 ? 'negative' : ''}>{formatSignedPercent(snapshot.dailyReturn)}</span>
            <span className={snapshot.estimatedIntradayReturn > 0 ? 'positive' : snapshot.estimatedIntradayReturn < 0 ? 'negative' : ''}>估算联动 {formatSignedPercent(snapshot.estimatedIntradayReturn)}</span>
          </div>
        </div>
        {fundDetailRequestState === 'loading' ? <p className="status-line pending">基金详情加载中...</p> : null}
        {fundDetailRequestState === 'error' ? <p className="status-line error">基金详情加载失败，请稍后重试。</p> : null}
        <div className="detail-grid">
          <section className="detail-card">
            <h3>基础资料</h3>
            <dl>
              <div><dt>基金公司</dt><dd>{profile.fundCompany || '--'}</dd></div>
              <div><dt>经理</dt><dd>{profile.managerName || '--'}</dd></div>
              <div><dt>成立日期</dt><dd>{profile.establishedDate || '--'}</dd></div>
              <div><dt>跟踪标的</dt><dd>{profile.benchmarkIndex || '--'}</dd></div>
              <div><dt>风险等级</dt><dd>{profile.riskLevel || '--'}</dd></div>
              <div><dt>管理费率</dt><dd>{formatPercentValue(profile.managementFee)}</dd></div>
              <div><dt>托管费率</dt><dd>{formatPercentValue(profile.custodyFee)}</dd></div>
            </dl>
          </section>
          <section className="detail-card">
            <h3>净值历史</h3>
            <div className="mini-nav-list">
              {navHistory.slice(0, 8).map((item) => (
                <div className="mini-nav-row" key={`${item.fundCode}-${item.navDate}`}>
                  <span>{item.navDate}</span>
                  <strong>{formatNav(item.nav)}</strong>
                  <em className={item.dailyReturn > 0 ? 'positive' : item.dailyReturn < 0 ? 'negative' : ''}>{formatSignedPercent(item.dailyReturn)}</em>
                </div>
              ))}
              {!navHistory.length ? <p className="panel-tip compact">暂无净值历史。</p> : null}
            </div>
          </section>
          <section className="detail-card">
            <h3>披露与估算边界</h3>
            <dl>
              <div><dt>最新报告期</dt><dd>{transparency.latestReportDate ? `${formatReportQuarter(transparency.latestReportDate)} · ${transparency.latestReportDate}` : '--'}</dd></div>
              <div><dt>披露覆盖</dt><dd>{typeof transparency.disclosedWeightPercent === 'number' ? formatPercentValue(transparency.disclosedWeightPercent) : '--'}</dd></div>
              <div><dt>未披露部分</dt><dd>{typeof transparency.undisclosedWeightPercent === 'number' ? formatPercentValue(transparency.undisclosedWeightPercent) : '--'}</dd></div>
              <div><dt>距今时效</dt><dd>{typeof transparency.freshnessDays === 'number' ? formatFreshnessDays(transparency.freshnessDays) : '--'}</dd></div>
            </dl>
            <p className="panel-tip compact">估算联动 = 已披露重仓权重 x 实时股价变化，不代表官方实时净值。</p>
          </section>
          <section className="detail-card">
            <h3>风险提示</h3>
            {renderRiskSignalList(riskSignals, '当前暂无足够披露持仓用于生成更多风险提示。')}
          </section>
          <section className="detail-card wide-card">
            <h3>十大重仓与实时联动估算</h3>
            <div className="detail-table-wrap detail-table-wrap-scrollable">
              <table className="detail-table">
                <thead><tr><th>排名</th><th>股票</th><th>权重</th><th>最新价</th><th>涨跌幅</th><th>估算贡献</th></tr></thead>
                <tbody>
                  {topHoldings.length ? topHoldings.map((holding) => {
                    const linked = performance.find((item) => item.stockSymbol === holding.stockSymbol) || {};
                    return (
                      <tr key={`${holding.stockSymbol}-${holding.reportDate}`}>
                        <td>{holding.rank ?? '--'}</td>
                        <td>
                          {holding.stockName || holding.stockSymbol}
                          <span className="table-subtext">{holding.stockSymbol}{holding.stockMarket ? ` · ${holding.stockMarket}` : ''}</span>
                        </td>
                        <td>{formatSignedPercent(holding.weightPercent).replace('+', '')}</td>
                        <td>{formatPrice(linked.lastPrice)}</td>
                        <td className={linked.changePct > 0 ? 'positive' : linked.changePct < 0 ? 'negative' : ''}>{formatSignedPercent(linked.changePct)}</td>
                        <td className={linked.estimatedContribution > 0 ? 'positive' : linked.estimatedContribution < 0 ? 'negative' : ''}>{formatSignedPercent(linked.estimatedContribution)}</td>
                      </tr>
                    );
                  }) : <tr><td colSpan="6" className="panel-tip compact">暂无重仓数据。</td></tr>}
                </tbody>
              </table>
            </div>
            <p className="panel-tip compact">估算贡献只基于重仓股实时快照和持仓权重，不代表官方实时净值。</p>
          </section>
        </div>
      </div>
    );
  }

  function renderFundsView() {
    return (
      <article className="panel wide fund-panel">
        {selectedFundCode ? (
          renderFundDetailView()
        ) : (
          <>
            <div className="panel-heading">
              <div>
                <h2>基金监控</h2>
                <p className="panel-tip compact">激活基金后按日刷新净值、季度同步重仓，并自动把重仓股纳入股票监控集合。</p>
              </div>
              <div className="management-meta"><span className="symbol-count-badge">监控中 {activeFunds.length}</span></div>
            </div>
            <div className="submenu-row">
              <div className="submenu-tabs">
                <button type="button" className={fundsSubView === 'list' ? 'view-tab active' : 'view-tab'} onClick={() => setFundsSubView('list')}>基金列表</button>
                <button type="button" className={fundsSubView === 'portfolio' ? 'view-tab active' : 'view-tab'} onClick={() => setFundsSubView('portfolio')}>监控组合视角</button>
              </div>
            </div>
            {fundsSubView === 'list' ? (
              <>
                <form className="symbol-form compact-form fund-form" onSubmit={handleSubmitFund}>
                  <label htmlFor="fund-code">基金代码</label>
                  <div className="inline-form-row">
                    <input id="fund-code" value={fundCode} onChange={(event) => setFundCode(event.target.value)} placeholder="例如 007329" />
                    <button type="submit" disabled={fundRequestState === 'submitting'}>{fundRequestState === 'submitting' ? '提交中…' : '激活基金'}</button>
                  </div>
                  <label className="inline-checkbox">
                    <input type="checkbox" checked={autoLinkStocks} onChange={(event) => setAutoLinkStocks(event.target.checked)} />
                    自动关联重仓股到股票监控
                  </label>
                  <p className="panel-tip compact">支持 6 位基金代码；collector 会按基金节奏异步补全净值与持仓。</p>
                </form>
                <p className={`status-line ${getRequestStatusTone(fundRequestState)}`}>请求状态：{getRequestStatusLabel(fundRequestState)}</p>
                {fundRequestState === 'loading' ? <p className="status-line pending">基金列表加载中...</p> : null}
                {fundRequestState === 'error' ? <p className="status-line error">基金列表加载失败，请稍后重试。</p> : null}
                <div className="snapshot-grid fund-grid">
                  {activeFunds.length ? activeFunds.map((item) => renderFundCard(item)) : <p className="panel-tip compact">尚无激活基金。</p>}
                </div>
                <div className="fund-watchlist-row">
                  {activeFunds.map((item) => (
                    <button key={item} type="button" className="remove-symbol-button" onClick={() => handleRemoveFund(item)} disabled={Boolean(removingFund)}>
                      {removingFund === item ? '移除中…' : `移除 ${item}`}
                    </button>
                  ))}
                </div>
              </>
            ) : renderFundPortfolioView()}
          </>
        )}
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
    const intradayCompleteness = detailPayload?.intradayCompleteness || null;
    const orderBook = detailPayload?.orderBook || {};
    const capabilities = detailPayload?.capabilities || {};
    const stockFundHoldings = Array.isArray(detailPayload?.fundHoldingSummary?.items) ? detailPayload.fundHoldingSummary.items : [];
    const capitalFlow = detailPayload?.capitalFlow || null;
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
    const intradayCompletenessStatus = typeof intradayCompleteness?.status === 'string' ? intradayCompleteness.status : 'unavailable';
    const intradayCompletenessLabel = intradayCompletenessStatus === 'complete'
      ? '分时完整'
      : intradayCompletenessStatus === 'pending'
        ? '分时补齐中'
        : intradayCompletenessStatus === 'incomplete'
          ? '分时未完整'
          : '分时不可用';
    const intradayCompletenessTone = intradayCompletenessStatus === 'complete'
      ? 'muted'
      : intradayCompletenessStatus === 'pending'
        ? 'warning'
        : intradayCompletenessStatus === 'incomplete'
          ? 'warning'
          : 'muted';
    const shouldShowIntradayCompletenessNotice = showingIntraday && intradayCompletenessStatus !== 'complete';
    const intradayCompletenessMessage = intradayCompletenessStatus === 'pending'
      ? `分时数据仍在补齐中，当前仅展示已入库的真实点位${intradayCompleteness?.lastBucketTs ? `；最新分钟 ${formatTime(intradayCompleteness.lastBucketTs)}` : ''}。`
      : intradayCompletenessStatus === 'incomplete'
        ? `尾盘分时数据仍不完整，图表只展示已接收到的真实分钟线${intradayCompleteness?.missingBucketCount > 0 ? `；当前缺少 ${intradayCompleteness.missingBucketCount} 个分钟桶。` : '。'}`
        : '当前没有可用于判定完整性的 1 分钟分时数据。';
    const intradayHoverSummary = showingIntraday && intradayLineChart && intradayHoverPoint
      ? {
          time: formatTime(intradayHoverPoint.bucketTs),
          price: formatPrice(intradayHoverPoint.close),
          delta: typeof intradayLineChart.previousClose === 'number' ? intradayHoverPoint.close - intradayLineChart.previousClose : null,
          percent: typeof intradayLineChart.previousClose === 'number' && intradayLineChart.previousClose !== 0
            ? ((intradayHoverPoint.close - intradayLineChart.previousClose) / intradayLineChart.previousClose) * 100
            : null,
        }
      : null;
    const handleIntradayHoverMove = (event) => {
      if (!intradayLineChart) {
        return;
      }

      const rect = event.currentTarget.getBoundingClientRect();
      if (!rect.width || !rect.height) {
        return;
      }

      const localX = event.clientX - rect.left;
      const clampedPlotPx = Math.max(0, Math.min(localX, rect.width));
      const svgX = (clampedPlotPx / rect.width) * intradayLineChart.plotWidth;
      setIntradayHoverPoint(findNearestPoint(intradayLineChart.chartPoints, svgX));
    };
    const handleIntradayHoverLeave = () => setIntradayHoverPoint(null);

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
                {showingIntraday ? (
                  intradayHoverSummary ? (
                    <div className="chart-summary-hover-state">
                      <span className="chart-summary-label chart-summary-time">{intradayHoverSummary.time}</span>
                      <strong className="chart-summary-price">{intradayHoverSummary.price}</strong>
                      <div className="chart-summary-hover-metrics">
                        <span className={`chart-summary-change ${intradayHoverSummary.delta > 0 ? 'positive' : intradayHoverSummary.delta < 0 ? 'negative' : ''}`}>
                          {formatSignedPriceDelta(intradayHoverSummary.delta)}
                        </span>
                        <span className={`chart-summary-change ${intradayHoverSummary.percent > 0 ? 'positive' : intradayHoverSummary.percent < 0 ? 'negative' : ''}`}>
                          {formatSignedPercent(intradayHoverSummary.percent)}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <>
                      <span className="chart-summary-label">最新价</span>
                      <strong className="chart-summary-price">{formatPrice(snapshot?.lastPrice)}</strong>
                      <span className={`chart-summary-change ${snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}`}>
                        {formatSignedPercent(snapshot?.changePct)}
                      </span>
                    </>
                  )
                ) : (
                  <>
                    <span className="chart-summary-label">最新价</span>
                    <strong className="chart-summary-price">{formatPrice(snapshot?.lastPrice)}</strong>
                    <span className={`chart-summary-change ${snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}`}>
                      {formatSignedPercent(snapshot?.changePct)}
                    </span>
                  </>
                )}
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
                        {shouldShowIntradayCompletenessNotice ? <span className={`market-breadth-chip ${intradayCompletenessTone}`}>{intradayCompletenessLabel}</span> : null}
                      </h4>
                      <span>{intradayDateLabel ? `${intradayDateLabel} · ` : ''}{intradayChartPoints.length} 个点位 · {intradayDataModeLabel} · 白线价格 / 黄线均价</span>
                    </div>
                    {shouldShowIntradayCompletenessNotice ? <p className="panel-tip compact intraday-completeness-tip">{intradayCompletenessMessage}</p> : null}
                    <div className="chart-interaction-layer">
                    <svg
                      className={`kline-chart line-chart-surface ${intradayTone}-tone`}
                      viewBox={`0 0 ${intradayLineChart.width} ${intradayLineChart.height}`}
                      role="img"
                      aria-label="分时采样走势"
                    >
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
                          <line x1="0" x2={intradayLineChart.plotWidth} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                          <text x={intradayLineChart.rightAxisLabelX.price} y={tick.y - 4} textAnchor="end" className="chart-axis-label chart-axis-price-label">
                            {tick.value.toFixed(2)}
                          </text>
                          <text x={intradayLineChart.rightAxisLabelX.delta} y={tick.y - 4} textAnchor="end" className={`chart-axis-label ${tick.delta > 0 ? 'axis-positive' : tick.delta < 0 ? 'axis-negative' : ''}`}>
                            {formatSignedPriceDelta(tick.delta)}
                          </text>
                          <text x={intradayLineChart.rightAxisLabelX.percent} y={tick.y - 4} textAnchor="end" className={`chart-axis-label ${tick.percent > 0 ? 'axis-positive' : tick.percent < 0 ? 'axis-negative' : ''}`}>
                            {formatSignedAxisPercent(tick.percent)}
                          </text>
                        </g>
                      ))}
                      <line x1={intradayLineChart.plotWidth} x2={intradayLineChart.plotWidth} y1="0" y2={intradayLineChart.height} className="chart-axis-divider" />
                      {intradayLineChart.baselineY !== null ? (
                        <g>
                          <line x1="0" x2={intradayLineChart.plotWidth} y1={intradayLineChart.baselineY} y2={intradayLineChart.baselineY} className="empty-chart-guide" />
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
                        <text x={Math.min(intradayLineChart.highPoint.x + 8, intradayLineChart.plotWidth - 70)} y={Math.max(intradayLineChart.highPoint.y - 10, 18)} className="chart-extrema-label">
                          高 {formatPrice(intradayLineChart.highPoint.close)}
                        </text>
                      ) : null}
                      {intradayLineChart.lowPoint ? (
                        <text x={Math.min(intradayLineChart.lowPoint.x + 8, intradayLineChart.plotWidth - 70)} y={Math.min(intradayLineChart.lowPoint.y + 18, intradayLineChart.height - 10)} className="chart-extrema-label">
                          低 {formatPrice(intradayLineChart.lowPoint.close)}
                        </text>
                      ) : null}
                      {intradayLineChart.latestPoint ? (
                        <g>
                          <line
                            x1="0"
                            x2={intradayLineChart.plotWidth}
                            y1={intradayLineChart.latestPoint.y}
                            y2={intradayLineChart.latestPoint.y}
                            className="current-price-guide"
                          />
                        </g>
                      ) : null}
                      {intradayHoverSummary ? (
                        <g>
                          <line x1={intradayHoverPoint.x} x2={intradayHoverPoint.x} y1="0" y2={intradayLineChart.height} className="chart-hover-crosshair" />
                          <circle cx={intradayHoverPoint.x} cy={intradayHoverPoint.y} r="4.6" className="chart-hover-point" />
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
                      <div
                        className="chart-hover-overlay"
                        style={{ width: `${(intradayLineChart.plotWidth / intradayLineChart.width) * 100}%` }}
                        onMouseMove={handleIntradayHoverMove}
                        onMouseLeave={handleIntradayHoverLeave}
                        onPointerMove={handleIntradayHoverMove}
                        onPointerLeave={handleIntradayHoverLeave}
                        aria-hidden="true"
                      />
                    </div>
                    <div className="chart-axis-row intraday-axis-row" style={{ width: `${(intradayLineChart.plotWidth / intradayLineChart.width) * 100}%` }}>
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
                          <line x1="0" x2={emptyIntradayChart.plotWidth} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                          <text x={emptyIntradayChart.rightAxisLabelX.price} y={tick.y - 4} textAnchor="end" className="chart-axis-label">
                            {tick.label}
                          </text>
                          <text x={emptyIntradayChart.rightAxisLabelX.percent} y={tick.y - 4} textAnchor="end" className={`chart-axis-label ${tick.percent > 0 ? 'axis-positive' : tick.percent < 0 ? 'axis-negative' : ''}`}>
                            {formatSignedAxisPercent(tick.percent)}
                          </text>
                        </g>
                      ))}
                      <line x1={emptyIntradayChart.plotWidth} x2={emptyIntradayChart.plotWidth} y1="0" y2={emptyIntradayChart.height} className="chart-axis-divider" />
                      <rect
                        x={emptyIntradayChart.lunchBreakStartX}
                        y="0"
                        width={Math.max(emptyIntradayChart.lunchBreakEndX - emptyIntradayChart.lunchBreakStartX, 0)}
                        height={emptyIntradayChart.height}
                        className="lunch-break-mask"
                      />
                      <line x1="0" x2={emptyIntradayChart.plotWidth} y1={emptyIntradayChart.guideY} y2={emptyIntradayChart.guideY} className="empty-chart-guide" />
                    </svg>
                    <div className="chart-axis-row intraday-axis-row" style={{ width: `${(emptyIntradayChart.plotWidth / emptyIntradayChart.width) * 100}%` }}>
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
                <h3>主力资金流向</h3>
                <p className="panel-tip compact">日级盘后资金结构，用来辅助区分主力吸筹与散户追涨；不会随实时快照逐笔跳动。</p>
              </div>
              <div className="capital-flow-header-meta">
                <span className="table-meta-badge">交易日 {capitalFlow?.tradeDate ? formatDate(capitalFlow.tradeDate) : '--'}</span>
                <span className={`market-breadth-chip ${capitalFlow?.stale ? 'warning' : 'muted'}`}>{capitalFlow?.stale ? '降级缓存' : '日级已更新'}</span>
              </div>
            </div>
            {capitalFlow ? (
              <>
                <div className="market-summary-grid capital-flow-summary-grid">
                  <article className="summary-metric summary-metric-strong">
                    <span>主力净流入</span>
                    <strong className={capitalFlow?.mainNetInflow > 0 ? 'positive' : capitalFlow?.mainNetInflow < 0 ? 'negative' : ''}>{formatSignedTurnoverAmount(capitalFlow?.mainNetInflow)}</strong>
                    <em className={capitalFlow?.mainNetRatio > 0 ? 'positive' : capitalFlow?.mainNetRatio < 0 ? 'negative' : ''}>{formatSignedPercent(capitalFlow?.mainNetRatio)}</em>
                  </article>
                  <article className="summary-metric">
                    <span>超大单</span>
                    <strong className={capitalFlow?.superLargeNetInflow > 0 ? 'positive' : capitalFlow?.superLargeNetInflow < 0 ? 'negative' : ''}>{formatSignedTurnoverAmount(capitalFlow?.superLargeNetInflow)}</strong>
                    <em className={capitalFlow?.superLargeNetRatio > 0 ? 'positive' : capitalFlow?.superLargeNetRatio < 0 ? 'negative' : ''}>{formatSignedPercent(capitalFlow?.superLargeNetRatio)}</em>
                  </article>
                  <article className="summary-metric">
                    <span>大单</span>
                    <strong className={capitalFlow?.largeNetInflow > 0 ? 'positive' : capitalFlow?.largeNetInflow < 0 ? 'negative' : ''}>{formatSignedTurnoverAmount(capitalFlow?.largeNetInflow)}</strong>
                    <em className={capitalFlow?.largeNetRatio > 0 ? 'positive' : capitalFlow?.largeNetRatio < 0 ? 'negative' : ''}>{formatSignedPercent(capitalFlow?.largeNetRatio)}</em>
                  </article>
                  <article className="summary-metric">
                    <span>中单</span>
                    <strong className={capitalFlow?.mediumNetInflow > 0 ? 'positive' : capitalFlow?.mediumNetInflow < 0 ? 'negative' : ''}>{formatSignedTurnoverAmount(capitalFlow?.mediumNetInflow)}</strong>
                    <em className={capitalFlow?.mediumNetRatio > 0 ? 'positive' : capitalFlow?.mediumNetRatio < 0 ? 'negative' : ''}>{formatSignedPercent(capitalFlow?.mediumNetRatio)}</em>
                  </article>
                  <article className="summary-metric">
                    <span>小单</span>
                    <strong className={capitalFlow?.smallNetInflow > 0 ? 'positive' : capitalFlow?.smallNetInflow < 0 ? 'negative' : ''}>{formatSignedTurnoverAmount(capitalFlow?.smallNetInflow)}</strong>
                    <em className={capitalFlow?.smallNetRatio > 0 ? 'positive' : capitalFlow?.smallNetRatio < 0 ? 'negative' : ''}>{formatSignedPercent(capitalFlow?.smallNetRatio)}</em>
                  </article>
                  <article className="summary-metric">
                    <span>对应收盘 / 涨跌幅</span>
                    <strong>{`${formatPrice(capitalFlow?.closePrice)} / ${formatSignedPercent(capitalFlow?.changePct)}`}</strong>
                    <em>{capitalFlow?.companyName || snapshot?.companyName || selectedSnapshotSymbol}</em>
                  </article>
                </div>
                <div className="capital-flow-footnote-row">
                  <span className="panel-tip compact">来源 {capitalFlow?.source || '--'}</span>
                  <span className="panel-tip compact">采集于 {formatDateTime(capitalFlow?.collectedAt)}</span>
                  <span className="panel-tip compact">最近尝试 {formatDateTime(capitalFlow?.lastAttemptAt || capitalFlow?.collectedAt)}</span>
                </div>
                {capitalFlow?.staleReason ? <p className="panel-tip compact capital-flow-stale-reason">当前使用最近一次成功采集的数据：{capitalFlow.staleReason}</p> : null}
              </>
            ) : (
              <p className="panel-tip compact">暂无主力资金流向数据。</p>
            )}
          </section>

          <section className="detail-card wide-card">
            <div className="detail-card-header compact-card-header">
              <div>
                <h3>基金持仓</h3>
                <p className="panel-tip compact">按最新报告期展示持有该股票的基金，来自基金反向持仓明细。</p>
              </div>
              {detailPayload?.fundHoldingSummary?.latestReportDate ? <span className="table-meta-badge">报告期 {detailPayload.fundHoldingSummary.latestReportDate}</span> : null}
            </div>
            {stockFundHoldings.length ? (
              <div className="detail-table-wrap detail-table-wrap-scrollable">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>基金</th>
                      <th>类型</th>
                      <th>报告期</th>
                      <th>净值占比</th>
                      <th>持股市值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stockFundHoldings.slice(0, 12).map((item) => (
                      <tr key={`${item.fundCode}-${item.reportDate}`}>
                        <td>{item.fundName || item.fundCode}<span className="table-subtext">{item.fundCode}</span></td>
                        <td>{item.fundType || '--'}</td>
                        <td>{item.reportDate || '--'}</td>
                        <td>{formatSignedPercent(item.weightPercent).replace('+', '')}</td>
                        <td>{formatTurnoverAmount(item.holdMarketValue)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="panel-tip compact">暂无基金持仓反查数据。</p>
            )}
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
                      <th>相对昨收</th>
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
                        <td>{tick.sideLabel || (tick.side === 'buy' ? '高于/持平昨收' : tick.side === 'sell' ? '低于昨收' : '--')}</td>
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
                      <span>{event.sideLabel || (event.side === 'buy' ? '高于/持平昨收' : event.side === 'sell' ? '低于昨收' : '方向 --')}</span>
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

  function renderAnomalyCard(item) {
    const funds = Array.isArray(item?.relatedFunds) ? item.relatedFunds : [];
    const changeTone = getSnapshotCardTone(
      typeof item?.changePct === 'number' ? item.changePct : item?.latestPriceJumpPct,
    );
    return (
      <section className={`event-card anomaly-card trend-${changeTone} ${getJumpSeverityClass(item?.severity)}`.trim()} key={item?.symbol}>
        <header>
          <div>
            <strong>{item?.stockName || '待识别公司'}</strong>
            <p className="snapshot-subtitle">
              {item?.symbol || '--'} · {getAnomalyTypeLabel(item?.anomalyType)}
            </p>
          </div>
          <span>{formatTime(item?.triggerTime)}</span>
        </header>
        <div className="event-card-topline">
          <span className="event-summary-chip">{getAnomalySeverityLabel(item?.severity)}异动</span>
          <span className={`event-jump-badge trend-${changeTone} ${getJumpSeverityClass(item?.severity)}`.trim()}>
            {typeof item?.changePct === 'number'
              ? `日内 ${formatSignedPercent(item.changePct)}`
              : `最新一步 ${formatSignedPercent(item?.latestPriceJumpPct)}`}
          </span>
        </div>
        <dl>
          <div>
            <dt>触发价</dt>
            <dd>{formatPrice(item?.triggerPrice)}</dd>
          </div>
          <div>
            <dt>最新一步</dt>
            <dd>{formatSignedPercent(item?.latestPriceJumpPct)}</dd>
          </div>
          <div>
            <dt>量能相对20日</dt>
            <dd className={getVolumeToneClass(item?.volumeRatio)}>{formatRatioMultiple(item?.volumeRatio)}</dd>
          </div>
          <div>
            <dt>今日事件</dt>
            <dd>{formatPlainNumber(item?.eventCountToday ?? 0)} 条</dd>
          </div>
        </dl>
        {funds.length ? (
          <div className="anomaly-fund-list">
            <h4>关联持仓基金</h4>
            {funds.slice(0, 3).map((fund) => (
              <div className="anomaly-fund-row" key={`${item?.symbol}-${fund.fundCode}-${fund.reportDate}`}>
                <span>{fund.fundName || fund.fundCode}</span>
                <small>
                  {fund.fundCode} · {fund.reportDate || '报告期未知'} · 仓位 {formatSignedPercent(fund.stockWeightInFund).replace('+', '')} · {formatImpactPercent(fund.estimatedImpact)}
                </small>
              </div>
            ))}
          </div>
        ) : null}
        <div className="anomaly-ai-reason">
          <span>{getAiReasonStatusLabel(item?.aiReasonStatus)}</span>
          {item?.aiReason ? <p>{item.aiReason}</p> : null}
        </div>
        <p className="panel-tip compact">{item?.impactEstimate || '仅用于复盘观察，不构成投资建议。'}</p>
      </section>
    );
  }

  function renderAnomalySection(title, items, emptyText) {
    return (
      <section className="anomaly-section">
        <div className="detail-card-header compact-card-header">
          <div>
            <h3>{title}</h3>
            <p className="panel-tip compact">基于今日监控数据中的显著变化，持仓信息来自最近披露报告期。</p>
          </div>
          <span className="table-meta-badge">{items.length} 条</span>
        </div>
        {items.length ? (
          <div className="event-grid anomaly-grid">
            {items.map((item) => renderAnomalyCard(item))}
          </div>
        ) : (
          <p className="panel-tip compact">{emptyText}</p>
        )}
      </section>
    );
  }

  function renderDailyAnomalyView() {
    const summary = dailyAnomalyReport?.summary || {};
    const { portfolioItems, otherItems } = dailyAnomalyItems;
    return (
      <article className="panel wide anomaly-report-panel">
        <div className="section-heading">
          <div>
            <h2>持仓异动日报</h2>
            <p className="panel-tip compact">从“盯最新一步”改为复盘今日显著变化；量能为相对 20 日日均量的粗略参考。</p>
          </div>
          <div className="event-status-slot" aria-live="polite">
            <span
              className={[
                'symbol-count-badge',
                'event-status-badge',
                'warning',
                dailyAnomalyRequestState === 'error' ? 'visible' : 'hidden',
              ].filter(Boolean).join(' ')}
              aria-hidden={dailyAnomalyRequestState !== 'error'}
            >
              日报加载失败
            </span>
          </div>
        </div>
        <div className="anomaly-report-summary">
          <span className="symbol-count-badge">日期 {dailyAnomalyReport?.date || '--'}</span>
          <span className="symbol-count-badge">生成 {dailyAnomalyReport?.generatedAt ? formatTime(dailyAnomalyReport.generatedAt) : '--:--:--'}</span>
          <span className="symbol-count-badge">重点 {summary.criticalCount ?? 0}</span>
          <span className="symbol-count-badge">较高 {summary.highCount ?? 0}</span>
          <span className="symbol-count-badge">持仓相关 {portfolioItems.length}</span>
          <span className="symbol-count-badge">监控标的 {summary.activeSymbolCount ?? activeSymbols.length}</span>
        </div>
        <div className="content-filter-row anomaly-filter-row">
          <label className="content-symbol-select-label" htmlFor="anomaly-scope-select">筛选</label>
          <select
            id="anomaly-scope-select"
            className="content-symbol-select"
            value={dailyAnomalyPortfolioOnly ? 'portfolio' : 'all'}
            onChange={(event) => setDailyAnomalyPortfolioOnly(event.target.value === 'portfolio')}
          >
            <option value="all">全部异动</option>
            <option value="portfolio">仅持仓相关</option>
          </select>
          <label className="content-symbol-select-label" htmlFor="anomaly-sort-select">排序</label>
          <select
            id="anomaly-sort-select"
            className="content-symbol-select"
            value={dailyAnomalySortBy}
            onChange={(event) => setDailyAnomalySortBy(event.target.value)}
          >
            <option value="relevance">按关联度</option>
            <option value="magnitude">按幅度</option>
            <option value="time">按时间</option>
          </select>
          <label className="content-symbol-select-label" htmlFor="anomaly-change-threshold-select">涨跌阈值</label>
          <select
            id="anomaly-change-threshold-select"
            className="content-symbol-select"
            value={dailyAnomalyChangeThreshold}
            onChange={(event) => setDailyAnomalyChangeThreshold(event.target.value)}
          >
            <option value="0">不限</option>
            <option value="2">≥ 2%</option>
            <option value="3">≥ 3%</option>
            <option value="5">≥ 5%</option>
          </select>
          <label className="content-symbol-select-label" htmlFor="anomaly-volume-threshold-select">量比阈值</label>
          <select
            id="anomaly-volume-threshold-select"
            className="content-symbol-select"
            value={dailyAnomalyVolumeThreshold}
            onChange={(event) => setDailyAnomalyVolumeThreshold(event.target.value)}
          >
            <option value="0">不限</option>
            <option value="1.5">≥ 1.5×</option>
            <option value="3">≥ 3×</option>
            <option value="5">≥ 5×</option>
          </select>
        </div>
        <div className="panel-scroll-area anomaly-report-body">
          {dailyAnomalyRequestState === 'loading' && !dailyAnomalyReport ? (
            <p className="panel-tip compact">正在生成今日异动日报…</p>
          ) : (
            <>
              {renderAnomalySection('重点监控：持仓相关异动', portfolioItems, '暂无满足阈值的持仓相关显著异动。')}
              {renderAnomalySection('其他监控标的异动', otherItems, '暂无满足阈值的其他监控标的异动。')}
              {!portfolioItems.length && !otherItems.length ? (
                <p className="panel-tip compact anomaly-disclaimer">当前仅表示没有满足阈值的显著异动，不代表所有持仓均无风险。</p>
              ) : (
                <p className="panel-tip compact anomaly-disclaimer">异动日报仅用于盘中/盘后复盘，不构成投资建议。</p>
              )}
            </>
          )}
        </div>
      </article>
    );
  }

  return (
    <main className="layout">
      <section className="panel hero">
        <p className="eyebrow">项目 · MoneyRush</p>
        <h1>实时行情看板</h1>
        <p className="lede">
          用总览看全局，用事件看异动，用龙虎榜看盘后资金，用管理页维护监控标的。
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
            异动日报
          </button>
          <button
            className={activeView === 'content' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('content')}
          >
            资讯
          </button>
          <button
            className={activeView === 'dragonTiger' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('dragonTiger')}
          >
            龙虎榜
          </button>
          <button
            className={activeView === 'funds' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('funds')}
          >
            基金
          </button>
          <button
            className={activeView === 'gold' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('gold')}
          >
            黄金
          </button>
          {macroCapabilities.enabled ? (
            <button
              className={activeView === 'macro' ? 'view-tab active' : 'view-tab'}
              type="button"
              onClick={() => setActiveView('macro')}
            >
              美债宏观
            </button>
          ) : null}
          {llmAuditCapabilities.enabled ? (
            <button
              className={activeView === 'llmAudit' ? 'view-tab active' : 'view-tab'}
              type="button"
              onClick={() => setActiveView('llmAudit')}
            >
              LLM审计
            </button>
          ) : null}
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
                {renderMarketOverviewBar()}
                <div className="section-heading overview-heading">
                  <div>
                    <h2>快照总览</h2>
                    <p className="panel-tip compact">按标的汇总最新价格、涨跌幅和关键估值指标，点击卡片可查看详情。</p>
                  </div>
                  <div className="overview-heading-meta">
                    <span className="symbol-count-badge">监控中 {activeSymbols.length}</span>
                    <span className="symbol-count-badge">最近更新 {overviewLastUpdatedAt ? formatRelativeDateTime(overviewLastUpdatedAt) : '--'}</span>
                    <span className={`status-line ${getMarketStatusTone(connectionState === 'connected' ? marketStatus : 'disconnected')}`}>
                      市场状态：{getMarketStatusLabel(connectionState === 'connected' ? marketStatus : 'disconnected')}
                    </span>
                    <span className="symbol-count-badge">状态时间 {marketStatusUpdatedAt ? formatTime(marketStatusUpdatedAt) : '--:--:--'}</span>
                  </div>
                </div>
                <div className="overview-toolbar">
                  <div className="content-filter-row overview-search-row">
                    <label className="content-symbol-select-label" htmlFor="overview-search-input">
                      搜索标的
                    </label>
                    <input
                      id="overview-search-input"
                      className="overview-search-input"
                      value={overviewSearchQuery}
                      onChange={(event) => setOverviewSearchQuery(event.target.value)}
                      placeholder="搜索股票代码/名称"
                    />
                  </div>
                  <div className="content-filter-row overview-sort-row">
                    <label className="content-symbol-select-label" htmlFor="overview-sort-select">
                      排序方式
                    </label>
                    <div className="overview-sort-controls">
                      <select
                        id="overview-sort-select"
                        className="content-symbol-select"
                        value={overviewSortKey}
                        onChange={(event) => setOverviewSortKey(event.target.value)}
                      >
                        <option value="marketCap">总市值</option>
                        <option value="changePct">涨跌幅</option>
                        <option value="turnoverRate">换手率</option>
                        <option value="lastPrice">最新价</option>
                        <option value="capitalFlowMainNetInflow">主力净流入</option>
                      </select>
                      <button
                        type="button"
                        className="view-tab overview-sort-toggle"
                        onClick={() => setOverviewSortDirection((current) => (current === 'desc' ? 'asc' : 'desc'))}
                      >
                        {overviewSortDirection === 'desc' ? '降序' : '升序'}
                      </button>
                    </div>
                  </div>
                </div>
                <div className="panel-scroll-area">
                  <div className="snapshot-grid">
                    {overviewItems.length ? (
                      overviewItems.map((item) => {
                        const snapshot = snapshots[item];
                        return renderOverviewCard(item, snapshot);
                      })
                    ) : (
                      <p>{activeSymbols.length ? '当前筛选条件下没有匹配标的。' : '尚未生成快照数据。'}</p>
                    )}
                  </div>
                </div>
              </>
            )}
          </article>
        ) : activeView === 'content' ? (
          renderContentView()
        ) : activeView === 'gold' ? (
          renderGoldView()
        ) : activeView === 'funds' ? (
          renderFundsView()
        ) : activeView === 'dragonTiger' ? (
          renderDragonTigerView()
        ) : activeView === 'macro' && macroCapabilities.enabled ? (
          renderMacroView()
        ) : activeView === 'llmAudit' && llmAuditCapabilities.enabled ? (
          renderLlmAuditView()
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
          renderDailyAnomalyView()
        )}
      </section>
    </main>
  );
}

export default App;
