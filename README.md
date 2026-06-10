# MoneyRush

[中文说明 / Chinese README](./README.zh-CN.md)

MoneyRush is a greenfield market-monitoring base focused on a fast Phase 1 loop: activate a symbol, collect market data, persist time-series records, cache hot snapshots, and stream live state back to a dashboard.

## Current Status

This repository now contains the Milestone 1 bootstrap scaffold plus the first Milestone 2 vertical slice:

- FastAPI API service with health and symbol-activation endpoints
- collector process wired to Redis Streams, simulated market collection, and Timescale persistence
- React + Vite frontend shell with symbol activation, snapshot board, and live WebSocket market-state view
- Dashboard `全球股市` tab backed by the latest global market index cache
- Docker Compose stack for TimescaleDB, Redis, API, collector, and frontend
- initial PostgreSQL/TimescaleDB schema bootstrap

## Repository Layout

```text
backend/        FastAPI service and application modules
collector/      Background worker shell for vendor polling
frontend/       React + Vite dashboard shell
infra/          Compose file and Dockerfiles
```

## Quick Start

1. Optionally copy `.env.example` to `.env` and adjust ports or credentials.
2. Start the stack:

   ```bash
   docker compose -p moneyrush -f infra/compose/docker-compose.yml up --build
   ```

   This pins the Compose project name so generated container names stay under the `moneyrush` prefix.

3. Open the frontend at `http://localhost:5173`.
4. Use the symbol form to enqueue an activation command such as `000001`, or open the `全球股市` tab for the global market map.
5. Watch the collectors generate snapshots, persist market rows, cache global indices, and stream market-state updates.

Only the frontend service is published to the host by default:

- frontend: `5173`

The frontend dev server proxies same-origin `/api` and `/ws` traffic to the API service over the internal Docker network. API, PostgreSQL/TimescaleDB, and Redis stay internal by default, which avoids local port conflicts and reduces accidental exposure.

## Useful Endpoints

- Live health: `http://localhost:5173/api/v1/health/live`
- Ready health: `http://localhost:5173/api/v1/health/ready`
- Active symbols: `http://localhost:5173/api/v1/symbols/active`
- Active snapshots: `http://localhost:5173/api/v1/symbols/snapshots`
- Global markets latest: `http://localhost:5173/api/v1/global-markets/latest`
- WebSocket stream: `ws://localhost:5173/ws/market`

If you put an external Nginx reverse proxy in front of the stack, point it at the frontend service and keep `/api` plus `/ws` on the same public origin. Make sure WebSocket upgrade headers are preserved for `/ws`. For direct host access to the API during debugging, publish `api:8000` explicitly with a Compose override rather than exposing it by default.

## License

This repository uses **GNU AGPL v3** for the community edition.

- This is a standard copyleft open-source license.
- If you modify and run the software for users over a network, AGPLv3 may require you to offer the corresponding source of that modified version to those users.
- If you want to use MoneyRush without AGPLv3 obligations, see the commercial licensing path below.

See `LICENSE` for the full terms.

Commercial licensing information is available in [`COMMERCIAL-LICENSE.md`](./COMMERCIAL-LICENSE.md).

For Chinese readers, a non-authoritative explanatory note is also available at [`LICENSE.zh-CN.md`](./LICENSE.zh-CN.md).
