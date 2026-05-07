import argparse
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


def fetch_sp500_companies(verify_ssl: bool = True) -> pd.DataFrame:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "portfolio-optimizer/1.0"}
    response = requests.get(url, timeout=30, verify=verify_ssl, headers=headers)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    if not tables:
        raise ValueError("No tables found on the S&P 500 Wikipedia page.")
    table = tables[0]
    if "Symbol" not in table.columns:
        raise ValueError("S&P 500 table does not include a Symbol column.")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch S&P 500 company data and save to a CSV file."
    )
    parser.add_argument(
        "--output",
        default="sp500_companies.csv",
        help="Output CSV filename inside data_input/",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification if your environment requires it.",
    )
    args = parser.parse_args()

    companies = fetch_sp500_companies(verify_ssl=not args.insecure)
    output_dir = Path(__file__).resolve().parent
    output_path = output_dir / args.output
    companies.to_csv(output_path, index=False)
    print(f"Saved {len(companies)} companies to {output_path}")


if __name__ == "__main__":
    main()

