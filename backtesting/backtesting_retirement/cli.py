"""
CLI for retirement backtest: input portfolio (e.g. JSON or CSV), run and print metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.backtesting_retirement.runner import run_retirement_backtest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run retirement portfolio Monte Carlo (how long portfolio will last)."
    )
    parser.add_argument(
        "--portfolio",
        required=True,
        help="Portfolio weights as JSON object, e.g. '{\"VTI\": 0.6, \"BND\": 0.4}' or path to JSON file.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data_output",
        help="Directory containing {ticker}_monthly.csv and {ticker}_dividend.csv.",
    )
    parser.add_argument(
        "--initial-value",
        type=float,
        default=1_000_000,
        help="Portfolio value at retirement start (dollars).",
    )
    parser.add_argument(
        "--monthly-withdrawal",
        type=float,
        default=5000,
        help="Target monthly withdrawal at start (dollars).",
    )
    parser.add_argument(
        "--inflation",
        type=float,
        default=0.03,
        help="Annual inflation rate for withdrawal escalation.",
    )
    parser.add_argument(
        "--max-years",
        type=int,
        default=None,
        help="Maximum simulation years. Overridden if --dob and --retirement-year are set.",
    )
    parser.add_argument(
        "--dob",
        type=str,
        default=None,
        help="Date of birth (YYYY-MM-DD or YYYY) to compute retirement horizon to --max-age.",
    )
    parser.add_argument(
        "--retirement-year",
        type=int,
        default=None,
        help="Year of retirement. With --dob, sets max_years = max_age - (retirement_year - birth_year).",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=100,
        help="Assumed maximum age (default 100). With --dob and --retirement-year, horizon = max_age - retirement_age.",
    )
    parser.add_argument(
        "--n-sims",
        type=int,
        default=5000,
        help="Number of Monte Carlo simulations.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed.",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not fetch missing price/dividend data.",
    )
    args = parser.parse_args()

    # Parse portfolio
    portfolio_arg = args.portfolio.strip()
    if Path(portfolio_arg).exists():
        portfolio = json.loads(Path(portfolio_arg).read_text())
        if "portfolio" in portfolio:
            portfolio = portfolio["portfolio"]
    else:
        portfolio = json.loads(portfolio_arg)
    if not isinstance(portfolio, dict):
        print("Error: portfolio must be a JSON object of ticker -> weight.", file=sys.stderr)
        return 1

    # Compute max_years from DOB + retirement year, or use --max-years
    retirement_age_arg = None
    if args.dob is not None and args.retirement_year is not None:
        try:
            if len(args.dob) == 4:
                birth_year = int(args.dob)
            else:
                birth_year = datetime.strptime(args.dob, "%Y-%m-%d").year
        except ValueError:
            print("Error: --dob must be YYYY or YYYY-MM-DD.", file=sys.stderr)
            return 1
        retirement_age_arg = args.retirement_year - birth_year
        max_years = args.max_age - retirement_age_arg
        if max_years <= 0:
            print("Error: retirement_year - birth_year must be less than max_age.", file=sys.stderr)
            return 1
        print(f"Retirement age: {retirement_age_arg}, horizon to age {args.max_age}: {max_years} years")
    else:
        max_years = args.max_years if args.max_years is not None else 50

    try:
        out = run_retirement_backtest(
            portfolio_weights=portfolio,
            data_output_dir=args.data_dir,
            initial_value=args.initial_value,
            monthly_withdrawal=args.monthly_withdrawal,
            inflation_rate=args.inflation,
            max_years=max_years,
            n_sims=args.n_sims,
            seed=args.seed,
            fetch_if_missing=not args.no_fetch,
            retirement_age=retirement_age_arg,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    metrics = out["metrics"]
    print("Retirement Monte Carlo results")
    print("------------------------------")
    print("1. Probability of success (confidence score):", f"{metrics.get('probability_of_success', 1 - metrics['depleted_fraction']):.1%}")
    print("2. Probability of failure:", f"{metrics['depleted_fraction']:.1%}")
    mag_p50 = metrics.get("magnitude_of_failure_p50")
    mag_p90 = metrics.get("magnitude_of_failure_p90")
    if mag_p50 is not None:
        print(f"   Magnitude of failure (P50): ${mag_p50:,.0f}")
    if mag_p90 is not None:
        print(f"   Magnitude of failure (P90): ${mag_p90:,.0f}")
    print()
    print("3. Percentiles of outcomes (P10 / P50 / P90):")
    if metrics.get("twr_p50") is not None:
        print(f"   TWR: {metrics.get('twr_p10', 0):.2%} / {metrics['twr_p50']:.2%} / {metrics.get('twr_p90', 0):.2%}")
    summary_paths = out.get("summary_paths", {})
    if summary_paths:
        last_idx = min(max_years * 12, len(summary_paths.get("p50", [])) - 1)
        p10 = summary_paths.get("p10")
        p50 = summary_paths.get("p50")
        p90 = summary_paths.get("p90")
        if p10 is not None and p50 is not None and p90 is not None and last_idx >= 0:
            horizon_label = (
                f"age {args.max_age}"
                if args.dob is not None and args.retirement_year is not None
                else f"end of year {max_years}"
            )
            print(f"   Portfolio value at {horizon_label}: ${p10.iloc[last_idx]:,.0f} / ${p50.iloc[last_idx]:,.0f} / ${p90.iloc[last_idx]:,.0f}")
    print()
    wr0 = metrics.get("withdrawal_rate_year0")
    if wr0 is not None:
        print(f"4. Sustainable withdrawal rate (year 0): {wr0:.2%}")
    gc_p10, gc_p50, gc_p90 = metrics.get("goal_completion_p10"), metrics.get("goal_completion_p50"), metrics.get("goal_completion_p90")
    if gc_p10 is not None or gc_p50 is not None or gc_p90 is not None:
        gc_str = " / ".join(f"{g:.1%}" if g is not None else "N/A" for g in (gc_p10, gc_p50, gc_p90))
        print(f"5. Goal completion (P10 / P50 / P90): {gc_str}")
    age_p10 = metrics.get("age_at_depletion_p10")
    age_p50 = metrics.get("age_at_depletion_p50")
    age_p90 = metrics.get("age_at_depletion_p90")
    if age_p10 is not None and age_p50 is not None and age_p90 is not None:
        print(f"6. Age of plan failure (P10 / P50 / P90): {age_p10:.0f} / {age_p50:.0f} / {age_p90:.0f}")
    else:
        print("6. Age of plan failure: N/A (use --dob and --retirement-year to enable)")
    print()
    print(f"Annualized average yield: {metrics['portfolio_yield_mean_annual']:.2%}")
    print(f"Annualized capital growth rate: {metrics['portfolio_log_return_mean_annual']:.2%}")
    if out.get("data_start") and out.get("data_end"):
        print(f"Input data range (for return/yield stats): {out['data_start']} to {out['data_end']}")
    # log_returns = out.get("log_return_series")
    # if log_returns is not None and len(log_returns) > 0:
    #     vals = [round(float(x), 6) for x in log_returns.tolist()]
    #     print(f"Log returns (monthly): {vals}")

    # YoY portfolio value, yield/price breakdown, and yearly outflow
    if summary_paths:
        monthly_inflation = (1 + args.inflation) ** (1 / 12) - 1
        year1_outflow = (
            args.monthly_withdrawal
            * (((1 + monthly_inflation) ** 12 - 1) / monthly_inflation)
            if monthly_inflation > 0
            else args.monthly_withdrawal * 12
        )
        yearly_outflows = [
            year1_outflow * (1 + args.inflation) ** (y - 1)
            for y in range(1, max_years + 1)
        ]
        yearly_price = out.get("summary_yearly_price", {})
        yearly_yield = out.get("summary_yearly_yield", {})
        print()
        print("Year | Portfolio(P50) | Yield $(P50) | Price $(P50) | Outflow   | Net")
        print("-" * 75)
        prev_p50 = None
        for year in range(max_years + 1):
            month_idx = year * 12
            p50_val = summary_paths.get("p50")
            if p50_val is None or month_idx >= len(p50_val):
                break
            curr_p50 = float(p50_val.iloc[month_idx])
            outflow = yearly_outflows[year - 1] if year >= 1 else 0
            portfolio_str = f"${curr_p50:,.0f}"
            if year >= 1 and year - 1 < len(yearly_yield.get("p50", [])) and year - 1 < len(yearly_price.get("p50", [])):
                yld = yearly_yield["p50"].iloc[year - 1]
                prc = yearly_price["p50"].iloc[year - 1]
                yield_str = f"${yld:+,.0f}"
                price_str = f"${prc:+,.0f}"
            else:
                yield_str = "         -"
                price_str = "         -"
            outflow_str = f"${outflow:,.0f}" if year >= 1 else "-"
            net = curr_p50 - prev_p50 if prev_p50 is not None else None
            net_str = f"${net:+,.0f}" if net is not None else "-"
            print(f"{year:>4} | {portfolio_str:>14} | {yield_str:>11} | {price_str:>11} | {outflow_str:>9} | {net_str}")
            prev_p50 = curr_p50
    return 0


if __name__ == "__main__":
    sys.exit(main())
