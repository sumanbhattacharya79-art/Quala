# Portfolio Visualizer App - Technical Design

## Overview
This document describes the system architecture, data flow, core services, and implementation details for the portfolio visualizer app. It is aligned with the requirements in `PRD.md`.

## Architecture

### High-Level Components
- **Web Client**: Chat intake, portfolio views, leaderboard, charts.
- **API Server**: Auth, portfolio CRUD, backtest orchestration, leaderboard queries.
- **Backtest Engine**: Deterministic computation service for time series and metrics.
- **Data Store**: Relational database for users, portfolios, backtests, and results.
- **Market Data Pipeline**: Ingests price series and benchmark data.

### Deployment Topology
- **API Server** and **Backtest Engine** deployed as separate services.
- Backtest Engine runs as synchronous worker for small jobs or async job queue for long jobs.
- Database runs as managed Postgres (or equivalent).
- Object storage for exports (CSV/PDF) and large time series if needed.

## Technology Choices (Suggested)
- **Frontend**: React + TypeScript, charting with ECharts or Recharts.
- **Backend**: Node.js (Fastify/NestJS) or Python (FastAPI).
- **Backtesting**: Python service using pandas/numpy.
- **Database**: PostgreSQL.
- **Cache**: Redis for leaderboard and time series caching.

## Data Flow

### Chat Intake
1) User sends a message via Web Client.
2) API calls LLM to extract intent and constraints.
3) Parsed parameters stored in DB and used to create or update portfolio/backtest.

### Backtest Execution
1) API receives backtest request.
2) Backtest job created in DB with status.
3) Backtest Engine loads portfolio, price series, benchmark data.
4) Engine computes time series and metrics.
5) Results written to `backtest_timeseries` and `backtest_results`.
6) API returns results to client, updates leaderboard cache.

## Core Services

### API Server
- Auth and user management.
- Portfolio CRUD.
- Backtest orchestration.
- Leaderboard queries and caching.

### Backtest Engine
- Deterministic, idempotent processing.
- Supports daily or monthly bars.
- Rebalancing rules:
  - Fixed interval (monthly/quarterly).
  - Threshold-based (weights drift beyond X%).
  - Custom schedule.
- Transaction cost model:
  - bps per trade on notional.
  - optional slippage factor.

### Market Data Pipeline
- Daily batch ingestion of price series.
- Data validation:
  - Missing data handling, forward fills where appropriate.
  - Corporate actions applied via adjusted close.

## Backtest Algorithm Details

### Inputs
- Portfolio target weights.
- Price series for each asset.
- Start/end dates.
- Rebalancing rule.
- Transaction cost assumptions.
- Benchmark symbol.

### Steps
1) Align all asset price series to a common calendar.
2) Compute returns: daily or monthly.
3) Initialize portfolio with target weights.
4) For each period:
   - Apply asset returns to current weights.
   - Check rebalancing rule; if triggered:
     - Compute trades, apply transaction costs.
     - Reset to target weights.
   - Update portfolio value and weights.
5) Compute benchmark returns and value.
6) Store time series and summary metrics.

### Metrics Computation
- CAGR: `(end_value / start_value)^(1/years) - 1`.
- Volatility: annualized std of period returns.
- Sharpe/Sortino: use risk-free rate config.
- Max drawdown: peak-to-trough from cumulative series.
- Beta: cov(portfolio, benchmark) / var(benchmark).
- Tracking error: std of return difference.

## API Design Details

### Endpoints
- `POST /api/chat/intake`
  - Input: `{ message }`
  - Output: `{ intent, constraints, session_id }`
- `POST /api/portfolios`
  - Input: `{ name, assets, weights, constraints }`
- `GET /api/portfolios`
  - Output: list of portfolios.
- `GET /api/portfolios/:id`
  - Output: portfolio detail with summary metrics.
- `POST /api/backtests`
  - Input: `{ portfolio_id, benchmark_id, start_date, end_date, frequency, rebalancing_rule }`
  - Output: `{ backtest_id, status }`
- `GET /api/backtests/:id`
  - Output: `{ metrics, timeseries }`
- `GET /api/leaderboard`
  - Output: ordered list with CAGR and risk stats.

## Caching Strategy
- Leaderboard cached in Redis with short TTL (5-15 min).
- Backtest time series cached per backtest id.
- Cache invalidated when a new backtest completes.

## Data Schema Notes
- Consider partitioning `price_series` and `backtest_timeseries` by date.
- Use numeric(18,8) for weights and returns to avoid float drift.
- Index for fast leaderboard queries:
  - `backtest_results(metric_name='cagr')` + `portfolio_id`.

## Observability
- Structured logs for all API requests.
- Trace backtest execution time and failures.
- Metrics:
  - Backtest duration.
  - Job queue depth.
  - Error rates by endpoint.

## Security
- JWT-based auth or session cookies.
- Encrypt PII at rest (email).
- Rate limit chat intake and backtest endpoints.

## Failure Modes & Recovery
- Missing price data: skip asset or backfill with last known price.
- LLM parsing errors: fallback to structured form prompts.
- Backtest timeout: return partial result or retry.

## Testing Strategy
- Unit tests for backtest algorithm and metrics.
- Integration tests for API endpoints.
- Snapshot tests for chart data formatting.
- Data validation tests for market data ingestion.

## Open Questions
- Final tech stack selection.
- Data vendor and licensing.
- Target backtest scale (max assets, max years).

