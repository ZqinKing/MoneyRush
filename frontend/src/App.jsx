import { useEffect, useMemo, useState } from 'react';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const wsBaseUrl = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000';

function App() {
  const [symbol, setSymbol] = useState('000001');
  const [connectionState, setConnectionState] = useState('connecting');
  const [requestState, setRequestState] = useState('idle');
  const [activeSymbols, setActiveSymbols] = useState([]);
  const [snapshots, setSnapshots] = useState({});
  const [messages, setMessages] = useState([]);

  const wsUrl = useMemo(() => `${wsBaseUrl}/ws/market`, []);

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

  return (
    <main className="layout">
      <section className="panel hero">
        <p className="eyebrow">Project MoneyRush</p>
        <h1>Milestone 1 bootstrap dashboard</h1>
        <p className="lede">
          This shell proves the stack wiring: activate a symbol, push the command into Redis, and watch the API stream
          active-symbol heartbeats back over WebSocket.
        </p>
      </section>

      <section className="grid">
        <article className="panel">
          <h2>Symbol activation</h2>
          <form className="symbol-form" onSubmit={handleSubmit}>
            <label htmlFor="symbol">A-share code</label>
            <input id="symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="000001" />
            <button type="submit">Activate</button>
          </form>
          <p className="status-line">Request state: {requestState}</p>
          <p className="status-line">WebSocket: {connectionState}</p>
        </article>

        <article className="panel">
          <h2>Active symbol set</h2>
          <ul className="pill-list">
            {activeSymbols.length ? activeSymbols.map((item) => <li key={item}>{item}</li>) : <li>No active symbols yet.</li>}
          </ul>
        </article>

        <article className="panel wide">
          <h2>Snapshot board</h2>
          <div className="snapshot-grid">
            {activeSymbols.length ? (
              activeSymbols.map((item) => {
                const snapshot = snapshots[item];
                return (
                  <section className="snapshot-card" key={item}>
                    <header>
                      <strong>{item}</strong>
                      <span>{snapshot?.source || 'pending collector'}</span>
                    </header>
                    <div className="snapshot-metric">{snapshot?.lastPrice ?? '--'}</div>
                    <dl>
                      <div>
                        <dt>Change</dt>
                        <dd>{snapshot?.changePct ?? '--'}%</dd>
                      </div>
                      <div>
                        <dt>Turnover</dt>
                        <dd>{snapshot?.turnoverRate ?? '--'}</dd>
                      </div>
                      <div>
                        <dt>PE / PB</dt>
                        <dd>{snapshot ? `${snapshot.pe} / ${snapshot.pb}` : '--'}</dd>
                      </div>
                    </dl>
                  </section>
                );
              })
            ) : (
              <p>No collector snapshots yet.</p>
            )}
          </div>
        </article>

        <article className="panel wide">
          <h2>Realtime event log</h2>
          <div className="log-list">
            {messages.length ? (
              messages.map((message, index) => (
                <pre key={`${message.generatedAt || message.raw || message.type}-${index}`}>{JSON.stringify(message, null, 2)}</pre>
              ))
            ) : (
              <p>No events received yet.</p>
            )}
          </div>
        </article>
      </section>
    </main>
  );
}

export default App;
