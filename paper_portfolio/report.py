from __future__ import annotations

from datetime import date
from pathlib import Path

import sqlite3

from .audit import audit_status
from .core import holding_market_value, holding_unrealized_pnl, portfolio_metrics
from .db import get_portfolio, list_transactions, load_state


def realized_pnl_from_transactions(conn: sqlite3.Connection, portfolio_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0) AS realized_pnl FROM transactions WHERE portfolio_id = ?",
        (portfolio_id,),
    ).fetchone()
    return float(row["realized_pnl"])


def money(value: float) -> str:
    return f"{value:,.2f}"


def pct(value: float) -> str:
    return f"{value * 100:,.2f}%"


def render_report(conn: sqlite3.Connection, portfolio_id: int, report_date: date | None = None) -> str:
    report_date = report_date or date.today()
    portfolio = get_portfolio(conn, portfolio_id)
    state = load_state(conn, portfolio_id)
    metrics = portfolio_metrics(state)
    realized_pnl = realized_pnl_from_transactions(conn, portfolio_id)
    audit_event_count, audit_head_hash = audit_status(conn, portfolio_id)

    lines = [
        f"# Daily Portfolio Report - {report_date.isoformat()}",
        "",
        "## Portfolio",
        "",
        f"- Name: {portfolio['name']}",
        f"- Type: {portfolio['strategy_type']}",
        f"- Base currency: {portfolio['base_currency']}",
        f"- Initial cash: {money(float(portfolio['initial_cash']))}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total equity | {money(metrics['total_equity'])} |",
        f"| Cash | {money(metrics['cash'])} |",
        f"| Long market value | {money(metrics['long_market_value'])} |",
        f"| Short market value | {money(metrics['short_market_value'])} |",
        f"| Net market value | {money(metrics['net_market_value'])} |",
        f"| Gross exposure | {money(metrics['gross_exposure'])} |",
        f"| Net exposure | {pct(metrics['net_exposure_pct'])} |",
        f"| Gross exposure | {pct(metrics['gross_exposure_pct'])} |",
        f"| Total PnL | {money(metrics['total_pnl'])} |",
        f"| Return | {pct(metrics['return_pct'])} |",
        f"| Realized PnL | {money(realized_pnl)} |",
        f"| Unrealized PnL | {money(metrics['unrealized_pnl'])} |",
        "",
        "## Holdings",
        "",
        "| Symbol | Qty | Avg cost | Last price | Market value | Unrealized PnL | Unrealized % | Realized PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    if state.holdings:
        for holding in sorted(state.holdings.values(), key=lambda item: item.symbol):
            last_price = holding.last_price if holding.last_price is not None else holding.average_cost
            unrealized = holding_unrealized_pnl(holding)
            basis = abs(holding.quantity) * holding.average_cost
            unrealized_pct = unrealized / basis if basis else 0.0
            lines.append(
                "| {symbol} | {qty:,.4f} | {avg} | {last} | {mv} | {upnl} | {upnl_pct} | {rpnl} |".format(
                    symbol=holding.symbol,
                    qty=holding.quantity,
                    avg=money(holding.average_cost),
                    last=money(last_price),
                    mv=money(holding_market_value(holding)),
                    upnl=money(unrealized),
                    upnl_pct=pct(unrealized_pct),
                    rpnl=money(holding.realized_pnl),
                )
            )
    else:
        lines.append("| No holdings |  |  |  |  |  |  |  |")

    lines.extend(
        [
            "",
            "## Recent Transactions",
            "",
            "| Time | Symbol | Side | Qty | Price | Fee | Realized PnL | Notes |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    transactions = list_transactions(conn, portfolio_id, limit=20)
    if transactions:
        for row in transactions:
            lines.append(
                f"| {row['trade_time']} | {row['symbol']} | {row['side']} | {row['quantity']:,.4f} | "
                f"{money(float(row['price']))} | {money(float(row['fee']))} | "
                f"{money(float(row['realized_pnl']))} | {row['notes']} |"
            )
    else:
        lines.append("| No transactions |  |  |  |  |  |  |  |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Simulated trading only; no real order was placed.",
            "- Prices are manual marks unless a future data source is added.",
            "",
            "## Audit",
            "",
            f"- Audit events: {audit_event_count}",
            f"- Ledger head hash: `{audit_head_hash}`",
            "- Verify locally with `python3 -m paper_portfolio audit verify`.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_daily_report(conn: sqlite3.Connection, portfolio_id: int, output_dir: Path = Path("reports/daily")) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_date = date.today()
    path = output_dir / f"{report_date.isoformat()}.md"
    path.write_text(render_report(conn, portfolio_id, report_date), encoding="utf-8")
    return path
