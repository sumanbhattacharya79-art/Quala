## Metrics Reference

### Backtest Metrics
- `cagr`: Time-weighted return (TWR) of the portfolio over the backtest period.
- `annualized_volatility`: Annualized standard deviation of portfolio returns (risk/variability).
- `sharpe_ratio`: Risk-adjusted return vs risk-free rate (excess return per unit of volatility).
- `sortino_ratio`: Risk-adjusted return using only downside volatility (penalizes negative returns).
- `max_drawdown`: Worst peak-to-trough decline during the backtest.
- `cumulative_return`: Total return over the backtest period.

### Monte Carlo Metrics
- `prob_underperform_benchmark`: Probability the simulated portfolio return is below the benchmark.
- `drawdown_p5`: 5th percentile of drawdowns (a bad but not worst-case drawdown).
- `drawdown_p1`: 1st percentile of drawdowns (more extreme tail drawdown).
- `prob_blowup`: Probability the portfolio hits the blowup threshold set in the config.
- `prob_strategy_a_better`: Probability strategy A outperforms strategy B in simulation.



