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

function normalizeLoopbackUrl(configuredUrl, fallbackUrl) {
  const browserHostname = getBrowserHostname();
  const rawUrl = configuredUrl || fallbackUrl;

  try {
    const parsedUrl = new URL(rawUrl);
    if (browserHostname && isLoopbackHostname(browserHostname) && isLoopbackHostname(parsedUrl.hostname)) {
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
};

const connectionStateLabels = {
  connecting: '连接中',
  connected: '已连接',
  disconnected: '连接已断开',
  error: '连接异常',
};

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

  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false });
}

function formatDateTime(value) {
  if (!value) {
    return '--';
  }

  return new Date(value).toLocaleString('zh-CN', { hour12: false });
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

  return requestStateLabels[value] || `请求失败：${value}`;
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
  const stepX = width / bars.length;
  const candleWidth = Math.max(stepX * 0.58, 4);

  const scaleY = (price) => topPadding + ((maxHigh - price) / priceRange) * usableHeight;

  const candles = bars.map((bar, index) => {
    const x = index * stepX + stepX / 2;
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

  return { width, height, candles, ticks, minLow, maxHigh };
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
  const stepX = width / bars.length;
  const barWidth = Math.max(stepX * 0.58, 3);

  const items = bars.map((bar, index) => {
    const volume = typeof bar.volume === 'number' ? bar.volume : 0;
    const barHeight = (volume / maxVolume) * usableHeight;
    const x = index * stepX + stepX / 2 - barWidth / 2;
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

function buildLineChartGeometry(points, width = 860, height = 320) {
  if (!Array.isArray(points) || points.length === 0) {
    return null;
  }

  const closes = points.map((point) => point.close).filter((value) => typeof value === 'number');
  if (!closes.length) {
    return null;
  }

  const maxValue = Math.max(...closes);
  const minValue = Math.min(...closes);
  const range = maxValue - minValue || Math.max(maxValue * 0.02, 1);
  const topPadding = 18;
  const bottomPadding = 26;
  const usableHeight = height - topPadding - bottomPadding;
  const stepX = points.length > 1 ? width / (points.length - 1) : width;

  const scaleY = (value) => topPadding + ((maxValue - value) / range) * usableHeight;

  const chartPoints = points.map((point, index) => ({
    x: points.length > 1 ? index * stepX : width / 2,
    y: scaleY(point.close),
    label: formatTime(point.bucketTs),
    close: point.close,
  }));

  const polyline = chartPoints.map((point) => `${point.x},${point.y}`).join(' ');
  const area = `0,${height - bottomPadding} ${polyline} ${width},${height - bottomPadding}`;
  const ticks = [maxValue, maxValue - range / 2, minValue].map((value) => ({ value, y: scaleY(value) }));

  return { width, height, chartPoints, polyline, area, ticks, maxValue, minValue };
}

function App() {
  const [symbol, setSymbol] = useState('000001');
  const [connectionState, setConnectionState] = useState('connecting');
  const [requestState, setRequestState] = useState('idle');
  const [activeSymbols, setActiveSymbols] = useState([]);
  const [snapshots, setSnapshots] = useState({});
  const [messages, setMessages] = useState([]);
  const [activeView, setActiveView] = useState('overview');
  const [controlView, setControlView] = useState('activate');
  const [selectedSnapshotSymbol, setSelectedSnapshotSymbol] = useState(null);
  const [snapshotDetails, setSnapshotDetails] = useState({});
  const [detailRequestState, setDetailRequestState] = useState('idle');

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

  const selectedSnapshot = selectedSnapshotSymbol ? snapshots[selectedSnapshotSymbol] : null;
  const selectedDetail = selectedSnapshotSymbol ? snapshotDetails[selectedSnapshotSymbol] : null;

  useEffect(() => {
    if (!selectedSnapshotSymbol) {
      return undefined;
    }

    let cancelled = false;

    async function loadDetails() {
      setDetailRequestState('loading');

      try {
        const [detailResponse, ticksResponse, eventsResponse, klineResponse] = await Promise.all([
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/detail`),
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/ticks?limit=20`),
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/events?limit=10`),
          fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(selectedSnapshotSymbol)}/kline?period=1d&limit=1`),
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

    return () => {
      cancelled = true;
    };
  }, [selectedSnapshotSymbol]);

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
    setRequestState('submitting');

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/symbols/activate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ symbol }),
      });

      if (!response.ok) {
        throw new Error(`activation failed with status ${response.status}`);
      }

      setRequestState('accepted');
    } catch (error) {
      setRequestState(error.message);
    }
  }

  async function handleRemoveSymbol(symbolToRemove) {
    setRequestState(`正在移除 ${symbolToRemove}...`);

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/symbols/${encodeURIComponent(symbolToRemove)}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error(`remove failed with status ${response.status}`);
      }

      removeSymbolFromState(symbolToRemove);
      setRequestState(`已停止监控 ${symbolToRemove}`);
    } catch (error) {
      setRequestState(error.message);
    }
  }

  function handleOpenSnapshotDetail(symbolToOpen) {
    setSelectedSnapshotSymbol(symbolToOpen);
    setDetailRequestState(snapshotDetails[symbolToOpen] ? 'ready' : 'loading');
  }

  function handleCloseSnapshotDetail() {
    setSelectedSnapshotSymbol(null);
    setDetailRequestState('idle');
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

  function renderDetailView() {
    const detailPayload = selectedDetail?.detail || null;
    const dailyBars =
      detailPayload?.dailyBarsPreview && Array.isArray(detailPayload.dailyBarsPreview) && detailPayload.dailyBarsPreview.length
        ? detailPayload.dailyBarsPreview
        : selectedDetail?.klines || [];
    const intradaySampledBars =
      detailPayload?.intradaySampledBars && Array.isArray(detailPayload.intradaySampledBars)
        ? detailPayload.intradaySampledBars
        : [];
    const chartBars = dailyBars.length > 1 ? dailyBars : intradaySampledBars;
    const usingIntradayBars = dailyBars.length <= 1 && intradaySampledBars.length > 1;
    const latestKline = chartBars.length ? chartBars[chartBars.length - 1] : detailPayload?.latestKline || null;
    const latestEvent = detailPayload?.latestEvent || null;
    const orderBook = detailPayload?.orderBook || {};
    const capabilities = detailPayload?.capabilities || {};
    const ticks = dedupeTicks(selectedDetail?.ticks || []);
    const events = dedupeEvents(selectedDetail?.events || []);
    const snapshot = selectedSnapshot || detailPayload?.snapshot || null;
    const candleChart = dailyBars.length > 1 ? buildCandlestickChartGeometry(dailyBars) : null;
    const volumeChart = buildVolumeChartGeometry(chartBars);
    const intradayLineChart = !candleChart ? buildLineChartGeometry(intradaySampledBars) : null;

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
            <span className="detail-price">{snapshot?.lastPrice ?? '--'}</span>
            <span className={snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}>
              {formatSignedPercent(snapshot?.changePct)}
            </span>
          </div>
        </div>

        {detailRequestState === 'loading' ? <p className="status-line">详情数据加载中...</p> : null}
        {detailRequestState === 'error' ? <p className="status-line">详情数据加载失败，请稍后重试。</p> : null}

        <div className="detail-hero-grid">
          <section className="detail-card chart-card wide-card">
            <div className="chart-card-header">
              <div>
                <h3>{usingIntradayBars ? '盘中走势概览' : '日 K 走势概览'}</h3>
                <p className="panel-tip compact">
                  {usingIntradayBars
                    ? '历史日 K 不足时，自动回退为基于持续采样 tick 聚合的盘中走势。'
                    : '展示日 K 与成交量，暂不提供分钟级 K 线。'}
                </p>
              </div>
              <div className="chart-summary-badge">
                <span>最新价 {formatPrice(snapshot?.lastPrice)}</span>
                <span>{formatSignedPercent(snapshot?.changePct)}</span>
              </div>
            </div>

            {candleChart || intradayLineChart ? (
              <div className="chart-stack">
                {candleChart ? (
                  <svg className="kline-chart" viewBox={`0 0 ${candleChart.width} ${candleChart.height}`} role="img" aria-label="日K蜡烛图">
                    {candleChart.ticks.map((tick) => (
                      <g key={`${tick.value}`}>
                        <line x1="0" x2={candleChart.width} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                        <text x={candleChart.width - 6} y={tick.y - 4} textAnchor="end" className="chart-axis-label">
                          {tick.value.toFixed(2)}
                        </text>
                      </g>
                    ))}
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
                ) : (
                  <svg className="kline-chart" viewBox={`0 0 ${intradayLineChart.width} ${intradayLineChart.height}`} role="img" aria-label="盘中采样走势">
                    {intradayLineChart.ticks.map((tick) => (
                      <g key={`${tick.value}`}>
                        <line x1="0" x2={intradayLineChart.width} y1={tick.y} y2={tick.y} className="chart-grid-line" />
                        <text x={intradayLineChart.width - 6} y={tick.y - 4} textAnchor="end" className="chart-axis-label">
                          {tick.value.toFixed(2)}
                        </text>
                      </g>
                    ))}
                    <polygon points={intradayLineChart.area} className="line-chart-area" />
                    <polyline points={intradayLineChart.polyline} className="line-chart-path" />
                    {intradayLineChart.chartPoints.map((point, index) => (
                      <circle key={`point-${index}`} cx={point.x} cy={point.y} r="1.8" className="line-chart-point" />
                    ))}
                  </svg>
                )}

                {volumeChart ? (
                  <svg className="volume-chart" viewBox={`0 0 ${volumeChart.width} ${volumeChart.height}`} role="img" aria-label="成交量柱图">
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
                  <p className="panel-tip compact">当前图表缺少可用成交量数据。</p>
                )}

                <div className="chart-axis-row">
                  {(candleChart ? candleChart.candles : intradayLineChart.chartPoints)
                    .filter((_, index) => index % Math.max(Math.floor((candleChart ? candleChart.candles.length : intradayLineChart.chartPoints.length) / 6), 1) === 0)
                    .map((item, index) => <span key={`label-${index}`}>{item.label}</span>)}
                </div>
              </div>
            ) : (
              <p className="panel-tip compact">历史日K与盘中采样走势都不足，当前仅能展示最新行情摘要。</p>
            )}
          </section>

          <section className="detail-card market-summary-card">
            <h3>行情摘要</h3>
            <dl>
              <div>
                <dt>最新价</dt>
                <dd>{snapshot?.lastPrice ?? '--'}</dd>
              </div>
              <div>
                <dt>涨跌幅</dt>
                <dd className={snapshot?.changePct > 0 ? 'positive' : snapshot?.changePct < 0 ? 'negative' : ''}>
                  {formatSignedPercent(snapshot?.changePct)}
                </dd>
              </div>
              <div>
                <dt>开 / 高 / 低 / 收</dt>
                <dd>
                  {latestKline
                    ? `${latestKline.open ?? '--'} / ${latestKline.high ?? '--'} / ${latestKline.low ?? '--'} / ${latestKline.close ?? '--'}`
                    : '--'}
                </dd>
              </div>
              <div>
                <dt>成交量</dt>
                <dd>{formatCompactNumber(latestKline?.volume)}</dd>
              </div>
              <div>
                <dt>成交额</dt>
                <dd>{formatCompactNumber(latestKline?.amount)}</dd>
              </div>
              <div>
                <dt>更新时间</dt>
                <dd>{formatDateTime(snapshot?.updatedAt)}</dd>
              </div>
            </dl>
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
                    ? `${formatPrice(orderBook?.bid1)} / ${formatPlainNumber(orderBook?.bidVolume1)}`
                    : '--'}
                </dd>
              </div>
              <div>
                <dt>卖一</dt>
                <dd>
                  {orderBook?.ask1 != null || orderBook?.askVolume1 != null
                    ? `${formatPrice(orderBook?.ask1)} / ${formatPlainNumber(orderBook?.askVolume1)}`
                    : '--'}
                </dd>
              </div>
            </dl>
            <p className="panel-tip compact">
              {!capabilities?.supportsBestBidAsk ? '当前数据源未稳定提供买一卖一；' : ''} 暂不提供五档盘口。
            </p>
          </section>

          <section className="detail-card wide-card">
            <h3>最近 Tick</h3>
            {ticks.length ? (
              <div className="detail-table-wrap">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>价格</th>
                      <th>成交量</th>
                      <th>成交额</th>
                      <th>方向</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ticks.map((tick, index) => (
                      <tr key={`${tick.ts}-${tick.price}-${index}`}>
                        <td>{formatTime(tick.ts)}</td>
                        <td>{tick.price ?? '--'}</td>
                        <td>{formatCompactNumber(tick.volume)}</td>
                        <td>{formatCompactNumber(tick.amount)}</td>
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
            {events.length ? (
              <ul className="detail-event-list">
                {events.map((event, index) => (
                  <li key={`${event.ts}-${event.eventType}-${index}`}>
                    <strong>{event.eventType || '--'}</strong>
                    <span>{formatDateTime(event.ts)}</span>
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
                  <button type="submit">激活监控</button>
                </div>
              </form>
            ) : (
              <div className="watchlist-panel">
                <ul className="pill-list">
                  {activeSymbols.length ? (
                    activeSymbols.map((item) => (
                      <li className="symbol-pill" key={item}>
                        <span>{item}</span>
                        <button type="button" className="remove-symbol-button" onClick={() => handleRemoveSymbol(item)}>
                          移除
                        </button>
                      </li>
                    ))
                  ) : (
                    <li>尚无激活标的。</li>
                  )}
                </ul>
              </div>
            )}

            <div className="management-status-row">
               <p className="status-line">请求状态：{getRequestStatusLabel(requestState)}</p>
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
                        <dd>{event?.tick?.price ?? snapshot?.lastPrice ?? '--'}</dd>
                      </div>
                      <div>
                        <dt>成交量</dt>
                        <dd>{formatCompactNumber(event?.tick?.volume)}</dd>
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
                        <dd>{event?.kline ? `${event.kline.low} ~ ${event.kline.high}` : '--'}</dd>
                      </div>
                      <div>
                        <dt>收盘价</dt>
                        <dd>{event?.kline?.close ?? '--'}</dd>
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
