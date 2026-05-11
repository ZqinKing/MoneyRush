# MoneyRush

MoneyRush is a greenfield market-monitoring base focused on a fast Phase 1 loop: activate a symbol, collect market data, persist time-series records, cache hot snapshots, and stream live state back to a dashboard.

## Current Status

This repository now contains the Milestone 1 bootstrap scaffold plus the first Milestone 2 vertical slice:

- FastAPI API service with health and symbol-activation endpoints
- collector process wired to Redis Streams, simulated market collection, and Timescale persistence
- React + Vite frontend shell with symbol activation, snapshot board, and live WebSocket market-state view
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
   docker compose -f infra/compose/docker-compose.yml up --build
   ```

3. Open the frontend at `http://localhost:5173`.
4. Use the symbol form to enqueue an activation command such as `000001`.
5. Watch the collector generate snapshots, persist market rows, and stream market-state updates.

Only the user-facing services are published to the host by default:

- frontend: `5173`
- API: `8000`

PostgreSQL/TimescaleDB and Redis stay on the internal Docker network only, which avoids local port conflicts and reduces accidental exposure.

## Useful Endpoints

- API root: `http://localhost:8000/`
- Live health: `http://localhost:8000/api/v1/health/live`
- Ready health: `http://localhost:8000/api/v1/health/ready`
- Active symbols: `http://localhost:8000/api/v1/symbols/active`
- Active snapshots: `http://localhost:8000/api/v1/symbols/snapshots`
- WebSocket stream: `ws://localhost:8000/ws/market`

## License

This repository uses the `MoneyRush Personal Use License 1.0`.

- Personal and non-commercial use is free.
- Commercial use requires prior written authorization.

See `LICENSE` for the full terms.
