import { useEffect, useMemo, useState } from 'react';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const wsBaseUrl = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000';

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

function App() {
  const [symbol, setSymbol] = useState('000001');
  const [connectionState, setConnectionState] = useState('connecting');
  const [requestState, setRequestState] = useState('idle');
  const [activeSymbols, setActiveSymbols] = useState([]);
  const [snapshots, setSnapshots] = useState({});
  const [messages, setMessages] = useState([]);
  const [activeView, setActiveView] = useState('overview');
  const [controlView, setControlView] = useState('activate');

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

  function removeSymbolFromState(symbolToRemove) {
    setActiveSymbols((current) => current.filter((item) => item !== symbolToRemove));
    setSnapshots((current) => {
      const next = { ...current };
      delete next[symbolToRemove];
      return next;
    });
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

  return (
    <main className="layout">
      <section className="panel hero">
        <p className="eyebrow">项目 · MoneyRush</p>
        <h1>实时盯盘控制台</h1>
        <p className="lede">
          默认中文展示，首页优先看结构化快照。实时事件日志收进单独菜单，避免首页被原始流式信息占满。
        </p>
        <div className="hero-toolbar">
          <button
            className={activeView === 'overview' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('overview')}
          >
            首页总览
          </button>
          <button
            className={activeView === 'events' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('events')}
          >
            实时事件
          </button>
          <button
            className={activeView === 'management' ? 'view-tab active' : 'view-tab'}
            type="button"
            onClick={() => setActiveView('management')}
          >
            监控管理
          </button>
        </div>
      </section>

      <section className="grid single-column">
        {activeView === 'overview' ? (
          <article className="panel wide">
            <h2>快照看板</h2>
            <div className="snapshot-grid">
              {activeSymbols.length ? (
                activeSymbols.map((item) => {
                  const snapshot = snapshots[item];
                  return (
                    <section className="snapshot-card" key={item}>
                      <header>
                        <div>
                          <strong>{snapshot?.companyName || '待识别公司'}</strong>
                          <p className="snapshot-subtitle">
                            {item} · {snapshot?.exchange || '--'}
                          </p>
                        </div>
                        <span>{snapshot?.source || '等待采集'}</span>
                      </header>
                      <div className="snapshot-metric">{snapshot?.lastPrice ?? '--'}</div>
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
                })
              ) : (
                <p>尚未生成快照数据。</p>
              )}
            </div>
          </article>
        ) : activeView === 'management' ? (
          <article className="panel wide management-panel">
            <div className="panel-heading">
              <div>
                <h2>监控管理</h2>
                <p className="panel-tip compact">激活、查看、移除都收敛在这里，避免首页出现重复操作入口。</p>
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
                  标的激活
                </button>
                <button
                  className={controlView === 'watchlist' ? 'view-tab active' : 'view-tab'}
                  type="button"
                  onClick={() => setControlView('watchlist')}
                >
                  当前监控
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
              <p className="status-line">请求状态：{requestState}</p>
              <p className="status-line">实时连接：{connectionState}</p>
            </div>
          </article>
        ) : (
          <article className="panel wide">
            <h2>实时事件</h2>
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
