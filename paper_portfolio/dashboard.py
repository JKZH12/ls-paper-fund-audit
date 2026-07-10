"""Regenerate the static model-book dashboard from the local ledger."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .audit import audit_status
from .core import portfolio_metrics
from .db import DEFAULT_DB_PATH, connect, default_portfolio_id, list_transactions, load_state
from .report import realized_pnl_from_transactions


DEFAULT_DASHBOARD_PATH = Path("reports/dashboard/index.html")
_BOOK_RE = re.compile(r"const book = (?P<book>\{.*?\n\};)", re.DOTALL)
_HKT = ZoneInfo("Asia/Hong_Kong")


def _hkt(value: str | None = None) -> str:
    if value is None:
        moment = datetime.now(timezone.utc)
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _read_book(path: Path) -> tuple[str, dict[str, object]]:
    content = path.read_text(encoding="utf-8")
    match = _BOOK_RE.search(content)
    if not match:
        raise ValueError(f"could not find embedded book JSON in {path}")
    return content, json.loads(match.group("book")[:-1])


def refresh_dashboard(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    dashboard_path: Path = DEFAULT_DASHBOARD_PATH,
) -> Path:
    """Replace the dashboard snapshot while retaining its display taxonomy."""
    content, book = _read_book(dashboard_path)
    positions_meta = {item["symbol"]: item for item in book["positions"]}

    conn = connect(db_path)
    portfolio_id = default_portfolio_id(conn)
    state = load_state(conn, portfolio_id)
    metrics = portfolio_metrics(state)
    realized_pnl = realized_pnl_from_transactions(conn, portfolio_id)
    event_count, head_hash = audit_status(conn, portfolio_id)
    updated_at = _hkt()

    positions = []
    for holding in sorted(state.holdings.values(), key=lambda item: item.symbol):
        metadata = dict(positions_meta.get(holding.symbol, {}))
        if not metadata:
            metadata = {
                "symbol": holding.symbol,
                "name": holding.symbol,
                "theme": "Unclassified",
                "pair": "Unclassified",
            }
        metadata.update(
            {
                "quantity": holding.quantity,
                "averageCost": holding.average_cost,
                "lastPrice": holding.last_price if holding.last_price is not None else holding.average_cost,
                "realizedPnl": holding.realized_pnl,
                "updatedAt": updated_at,
            }
        )
        positions.append(metadata)

    trades = [
        {
            "time": _hkt(str(row["trade_time"])),
            "symbol": row["symbol"],
            "side": row["side"],
            "quantity": float(row["quantity"]),
            "price": float(row["price"]),
            "notes": row["notes"],
        }
        for row in list_transactions(conn, portfolio_id, limit=12)
    ]
    conn.close()

    book.update(
        {
            "asOf": updated_at,
            "summary": {
                **metrics,
                "realizedPnl": realized_pnl,
                "grossExposurePct": metrics["gross_exposure_pct"],
                "netExposurePct": metrics["net_exposure_pct"],
                "totalEquity": metrics["total_equity"],
                "longMarketValue": metrics["long_market_value"],
                "shortMarketValue": metrics["short_market_value"],
                "netMarketValue": metrics["net_market_value"],
                "grossExposure": metrics["gross_exposure"],
                "totalPnl": metrics["total_pnl"],
                "returnPct": metrics["return_pct"],
                "unrealizedPnl": metrics["unrealized_pnl"],
            },
            "positions": positions,
            "trades": trades,
        }
    )
    # Keep the static document's initial load status in sync with its embedded data.
    content = _BOOK_RE.sub(f"const book = {json.dumps(book, indent=2, ensure_ascii=False)};", content, count=1)
    content = re.sub(r"(<span class=\"pill\"><span class=\"dot\"></span>)\d+ positions</span>", rf"\g<1>{len(positions)} positions</span>", content)
    content = re.sub(r"(<span class=\"pill\"><span class=\"dot warn\"></span>Last mark ).*?</span>", rf"\g<1>{updated_at}</span>", content)
    content = re.sub(r"<span class=\"pill mono\">Audit \d+ events</span>", f'<span class="pill mono">Audit {event_count} events</span>', content)
    content = re.sub(r"(<div class=\"panel-title\">Exposure Stack</div>\s*<span class=\"pill\">Gross )[^<]+", rf"\g<1>{metrics['gross_exposure_pct'] * 100:.2f}%", content)
    content = re.sub(r"<span class=\"pill mono\">[0-9a-f]+\.\.\.[0-9a-f]+</span>", f'<span class="pill mono">{head_hash[:4]}...{head_hash[-4:]}</span>', content)
    content = re.sub(r"(<strong>Head hash</strong>\s*<span class=\"mono\">)[0-9a-f]+", rf"\g<1>{head_hash}", content)
    report_path = f"reports/daily/{date.today().isoformat()}.md"
    content = re.sub(r"(<strong>Daily report</strong>\s*<span class=\"mono\">)reports/daily/\d{4}-\d{2}-\d{2}\.md", rf"\g<1>{report_path}", content)
    content = re.sub(
        r"(<strong>FX-linked marks</strong>\s*<span>).*?(</span>)",
        r"\g<1>Live FMP marks use JPYUSD, HKDUSD, and EURUSD; each converted mark retains its quote and FX timestamps in the audit source.\g<2>",
        content,
    )
    content = re.sub(
        r"(<strong>Quote freshness</strong>\s*<span>).*?(</span>)",
        rf"\g<1>{len(positions)} FMP-sourced marks refreshed {updated_at}; regional exchange quotes can reflect the latest market close.\g<2>",
        content,
    )
    dashboard_path.write_text(content, encoding="utf-8")
    return dashboard_path


if __name__ == "__main__":
    path = refresh_dashboard()
    print(f"Wrote dashboard: {path}")
