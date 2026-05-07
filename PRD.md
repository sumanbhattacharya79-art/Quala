# Portfolio Visualizer App - Product Requirements Document

## Overview
Build a portfolio visualizer app that lets users describe investment goals in a chat-first interface, generates or evaluates portfolios, and presents analytics including growth, volatility, and leaderboard rankings by CAGR.

## Goals
- Provide an LLM-powered intake that converts user intent into portfolio requirements.
- Display user portfolios with composition and key performance metrics.
- Rank portfolios globally by CAGR with transparent calculation methods.
- Support backtesting and rebalancing analysis for decision support.

## Non-Goals
- Real-time trading execution.
- Tax optimization or personal financial advice compliance workflows.
- Regulatory filings or broker integrations.

## Target Users
- Retail investors exploring portfolio construction.
- Analysts testing strategy rules and rebalancing schemes.
- Educators demonstrating portfolio risk/return tradeoffs.

## User Flow
1) Landing Page: User describes goals in a chat box.
   - Example intents:
     - "Build me a portfolio where volatility is within 20% of S&P 500."
     - "Run backtesting on the portfolio."
     - "What rebalancing is needed?"
2) Portfolio Page: For a given user, show list of portfolios, each portfolio's composition, growth, and volatility.
3) Leaderboard: Rank all portfolios by CAGR.

## Functional Requirements

### Landing Page (Chat-First Intake)
- Chat interface accepts natural language requests.
- System extracts intent and constraints:
  - Benchmark constraints (e.g., volatility vs. S&P 500).
  - Backtest horizon and frequency.
  - Rebalancing rules (e.g., monthly, threshold-based).
- Provide clarification prompts if input is incomplete.
- Persist user request and derived parameters.

### Portfolio Page
- List portfolios owned by the user.
- For each portfolio:
  - Composition (assets, weights).
  - Growth (cumulative return, NAV series chart).
  - Volatility (annualized standard deviation).
  - Backtest summary (period, frequency, rebalancing rule).
- Detail view includes:
  - Performance chart vs. benchmark.
  - Drawdown chart.
  - Risk/return scatter vs. benchmark universe.

### Leaderboard
- Rank portfolios by CAGR (descending).
- Filters:
  - Date range / backtest window.
  - Rebalancing frequency.
  - Portfolio size or asset class.
- Display risk metrics to contextualize rank:
  - Volatility, max drawdown, Sharpe.

### Backtesting & Analytics
- Support backtests on historical data:
  - Daily or monthly bars.
  - Rebalancing rules.
  - Transaction cost assumptions.
- Produce time series of returns and weights over time.
- Allow comparison to benchmark (e.g., S&P 500).

## Non-Functional Requirements
- Performance: Backtests for a portfolio < 5 seconds for 10 years of daily data.
- Accuracy: Metrics match standard finance definitions.
- Explainability: Surface calculation details and assumptions.
- Security: Encrypt sensitive user data at rest.
- Auditability: Store calculation inputs and outputs for reproducibility.

## Metrics & Success Criteria
- % of users who complete a portfolio after chat intake.
- Median time to first portfolio chart.
- Backtest completion rate and average runtime.
- Leaderboard engagement (views per user).

## Data Model (Relational Database)

### Tables

**users**
- id (PK)
- email
- created_at

**sessions**
- id (PK)
- user_id (FK -> users.id)
- created_at
- last_active_at

**chat_messages**
- id (PK)
- session_id (FK -> sessions.id)
- role (user/system/assistant)
- content
- created_at

**portfolios**
- id (PK)
- user_id (FK -> users.id)
- name
- description
- created_at
- updated_at

**portfolio_assets**
- id (PK)
- portfolio_id (FK -> portfolios.id)
- asset_id (FK -> assets.id)
- target_weight
- created_at

**assets**
- id (PK)
- symbol
- name
- asset_class
- currency

**price_series**
- id (PK)
- asset_id (FK -> assets.id)
- date
- open
- high
- low
- close
- adjusted_close
- volume

**benchmarks**
- id (PK)
- symbol
- name
- description

**backtests**
- id (PK)
- portfolio_id (FK -> portfolios.id)
- benchmark_id (FK -> benchmarks.id)
- start_date
- end_date
- frequency (daily/monthly)
- rebalancing_rule (monthly/threshold/custom)
- transaction_cost_bps
- created_at

**backtest_results**
- id (PK)
- backtest_id (FK -> backtests.id)
- metric_name
- metric_value

**backtest_timeseries**
- id (PK)
- backtest_id (FK -> backtests.id)
- date
- portfolio_value
- benchmark_value
- portfolio_return
- benchmark_return
- portfolio_drawdown
- benchmark_drawdown

**rebalancing_events**
- id (PK)
- backtest_id (FK -> backtests.id)
- date
- asset_id (FK -> assets.id)
- trade_weight_delta
- trade_notional

### Indexing
- price_series (asset_id, date) composite index.
- backtest_timeseries (backtest_id, date) composite index.
- portfolio_assets (portfolio_id).

## Backtesting Engine Requirements
- Input: portfolio weights, rebalancing rule, price series, benchmark.
- Process:
  1) Normalize weights at each rebalance.
  2) Apply transaction costs per rebalance.
  3) Compute daily/monthly returns.
  4) Track portfolio value and weights through time.
  5) Compute benchmark series.
- Output: time series and summary metrics.

## Technical Indicators & Metrics

### Core Performance
- CAGR
- Cumulative return
- Annualized volatility
- Sharpe ratio (risk-free rate configurable)
- Sortino ratio
- Max drawdown
- Calmar ratio

### Risk & Correlation
- Beta vs benchmark
- Tracking error
- Information ratio
- Correlation to benchmark

### Rebalancing & Turnover
- Turnover (sum of absolute trades per period)
- Transaction cost impact
- Drift vs target weights

### Benchmark Comparisons
- Relative return (portfolio - benchmark)
- Rolling volatility (30/60/90 day)
- Rolling Sharpe

## API Endpoints (Draft)
- `POST /api/chat/intake` -> parse intent, store session + message
- `POST /api/portfolios` -> create portfolio from intake
- `GET /api/portfolios` -> list user portfolios
- `GET /api/portfolios/:id` -> portfolio detail
- `POST /api/backtests` -> run backtest
- `GET /api/backtests/:id` -> backtest summary + time series
- `GET /api/leaderboard` -> ranked portfolios by CAGR

## UX Notes
- Provide a "What does this mean?" tooltip for each metric.
- Show assumptions: risk-free rate, transaction costs, data range.
- Allow export of backtest report as CSV or PDF.

## Open Questions
- Data source for price series and benchmark data.
- Supported asset universe at launch (ETFs only vs. equities).
- Default risk-free rate and transaction cost assumptions.
- Authentication: email-only vs. OAuth providers.


Build a chat based investment app:
landing page: chat window: users can chat to 1) create portfolio, 2) upload any existing portfolio via csv or manual entry 3) run backtesting and montecarlo analysis 4) seek rebalancing help

Agent split: keep Intake and Rebalancing as separate agents; combine Backtesting + Monte Carlo into one agent.

## Agentic System Build
- Orchestrator: lightweight router classifies intent (intake/import/backtest+MC/rebalance) and delegates to the right agent.
- Shared context: session state with user goals, constraints, holdings, and data range; persisted in DB and passed to agents.
- Tooling layer: agents call deterministic services (price data fetch, optimizer, backtest engine, MC simulator, metrics) via well-defined APIs.
- Safety/guardrails: validate tickers, weights sum to 100%, risk constraints, and show assumptions (fees, risk-free rate).
- Response assembly: agents return structured outputs; UI renders charts/tables; narrative explanation generated from metrics.
- Observability: log prompts, tool calls, and outputs for debugging; add evaluation tests for common intents.

### Chat Backend Agentic Framework
- Agent interface: `Agent.run(context, message) -> {intent, actions[], artifacts[], reply}` with strict JSON schema.
- Router: intent classifier + confidence; low confidence falls back to clarifying questions.
- Planner: decomposes into steps (e.g., validate holdings → fetch prices → backtest → MC → summarize).
- Tool registry: typed tool contracts with input validation and deterministic outputs.
- Memory: short-term session state + long-term profile (risk tolerance, constraints).
- Safety: refusal rules for advice boundaries; sanitize inputs; rate limits and timeouts.
- Output contracts: structured payloads for UI (timeseries, MC quantiles, trades) + natural language explanation.
- Eval harness: golden prompts + expected JSON outputs; regression tests for key flows.

intake ask:
current amount today
monthly expense today
when to retire
any upcoming big expense
risk appetite


change the app flow:
1. user land on the page, see 2 options to choose from- create portfolio , analyze portfolio.
2. user choose one
3. if create portfolio: form appears, user answers in plain English. Backend packages questions + answers as context and prompts LLM to propose a portfolio.
4. chat window appears with the LLM response and proposed allocation.
5. form is saved, any further chat to modify the portfolio gets appended to the form. 
6. user can accept; if accepted, portfolio is saved. Otherwise iterate via chat.
7. if analyze is chosen: use the saved portfolio, or upload a new portfolio via JSON or CSV, then run analysis.

free: 1 portfolio save, no health update or rebalance 
2/mo: 2 portfolio (now, retirement), health update and rebalance, 3 acc only investment portfolio plaid conn
5/mo: what if scenario: 6 portfolios, health update and rebalance + savings, 9 acc plaid conn, get a 360 view of the portoflio


user choose create portfoio, update portfolio, stock screening

