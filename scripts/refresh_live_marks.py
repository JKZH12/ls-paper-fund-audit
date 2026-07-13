#!/usr/bin/env python3
"""Refresh every open paper-portfolio mark from FMP without placing trades.

The ledger is USD-based.  Exchange-local quotes are converted before the
existing ``paper_portfolio mark`` command records their simulated price marks
and audit events.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB = Path("data/portfolio.sqlite")
FMP_API = "/Users/jack/.local/bin/fmp-api"
FX_BY_SUFFIX = {".T": "JPYUSD", ".HK": "HKDUSD", ".DE": "EURUSD", ".PA": "EURUSD"}


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    timestamp: int | None


@dataclass(frozen=True)
class MarkPlan:
    symbol: str
    usd_price: float
    source: str


def fetch_quote(symbol: str) -> Quote:
    result = subprocess.run(
        [FMP_API, "quote", f"symbol={symbol}", "--compact"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError(f"{symbol}: expected exactly one FMP quote, got {payload!r}")
    row = payload[0]
    price = float(row["price"])
    if price <= 0:
        raise ValueError(f"{symbol}: invalid FMP price {price}")
    return Quote(symbol=symbol, price=price, timestamp=row.get("timestamp"))


def holdings(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT symbol FROM holdings ORDER BY symbol").fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError("no open holdings in the ledger")
    return [str(row[0]) for row in rows]


def fx_symbol(symbol: str) -> str | None:
    return next((fx for suffix, fx in FX_BY_SUFFIX.items() if symbol.endswith(suffix)), None)


def run(args: argparse.Namespace) -> int:
    symbols = holdings(args.db)
    needed_fx = sorted({fx for symbol in symbols if (fx := fx_symbol(symbol))})
    requested = symbols + needed_fx
    quotes: dict[str, Quote] = {}
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {executor.submit(fetch_quote, symbol): symbol for symbol in requested}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                quotes[symbol] = future.result()
            except Exception as exc:  # quote coverage must be complete before applying marks
                failures.append(f"{symbol}: {exc}")

    if failures:
        print("Live-mark refresh aborted; no marks were written:", file=sys.stderr)
        print("\n".join(sorted(failures)), file=sys.stderr)
        return 1

    plans: list[MarkPlan] = []
    for symbol in symbols:
        quote = quotes[symbol]
        fx = fx_symbol(symbol)
        if fx is None:
            usd_price = quote.price
            source = f"fmp-live quote={symbol} ts={quote.timestamp}"
        else:
            fx_quote = quotes[fx]
            usd_price = quote.price * fx_quote.price
            source = (
                f"fmp-live quote={symbol} ts={quote.timestamp};"
                f"fx={fx} px={fx_quote.price} ts={fx_quote.timestamp}"
            )
        plans.append(MarkPlan(symbol=symbol, usd_price=usd_price, source=source))

    print(f"Validated {len(plans)} marks with {len(needed_fx)} FX conversions.")
    for plan in plans:
        print(f"{plan.symbol:8} {plan.usd_price:12.6f}  {plan.source}")

    if not args.apply:
        print("Dry run only. Re-run with --apply to append simulated price marks.")
        return 0

    for plan in plans:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "paper_portfolio",
                "mark",
                plan.symbol,
                f"{plan.usd_price:.10f}",
                "--source",
                plan.source,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
    print(f"Applied {len(plans)} simulated FMP marks.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="append validated simulated marks to the audit trail")
    parser.add_argument("--max-workers", type=int, default=4)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
