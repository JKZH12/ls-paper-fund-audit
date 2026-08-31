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
_REPORT_METRIC_RE = re.compile(r"^\| (?P<label>Total equity|Total PnL|Return) \| (?P<value>[^|]+) \|$", re.MULTILINE)
_HKT = ZoneInfo("Asia/Hong_Kong")
_DEFAULT_POSITION_METADATA: dict[str, dict[str, object]] = {
    "TSEM": {
        "symbol": "TSEM",
        "name": "Tower Semiconductor",
        "theme": "Specialty foundry / networking",
        "pair": "TSEM / CSCO",
    },
    "CSCO": {
        "symbol": "CSCO",
        "name": "Cisco Systems",
        "theme": "Specialty foundry / networking",
        "pair": "TSEM / CSCO",
    },
    "MRVL": {
        "symbol": "MRVL",
        "name": "Marvell Technology",
        "theme": "AI compute systems",
        "pair": "CBRS / MRVL",
    },
    "UCTT": {
        "symbol": "UCTT",
        "name": "Ultra Clean Holdings",
        "theme": "Semi capex / materials",
        "pair": "AMAT / UCTT",
    },
    "SNDK": {
        "symbol": "SNDK",
        "name": "SanDisk",
        "theme": "Storage hierarchy",
        "pair": "SNDK / MU",
    },
    "AEHR": {
        "symbol": "AEHR",
        "name": "Aehr Test Systems",
        "theme": "SiC / power semicap",
        "pair": "AEHR standalone",
    },
    "WOLF": {
        "symbol": "WOLF",
        "name": "Wolfspeed",
        "theme": "AI compute systems",
        "pair": "SMTC / WOLF",
    },
}
_POSITION_METADATA_OVERRIDES: dict[str, dict[str, object]] = {
    "TSEM": {
        "name": "Tower Semiconductor",
        "theme": "Specialty foundry / semiconductor hedge",
        "pair": "TSEM / AVGO",
    },
    "AVGO": {
        "name": "Broadcom",
        "theme": "Specialty foundry / semiconductor hedge",
        "pair": "TSEM / AVGO",
    },
    "0992.HK": {
        "name": "Lenovo Group",
        "theme": "AI hardware / consumer and enterprise devices",
        "pair": "Zhongji / Xiaomi + Lenovo",
    },
    "SNDK": {
        "pair": "SNDK / MU",
    },
    "NVDA": {
        "name": "NVIDIA",
        "theme": "AI compute / accelerators",
        "pair": "NVDA / AAPL",
    },
    "QCOM": {
        "name": "Qualcomm",
        "theme": "Compute semis",
        "pair": "QCOM standalone short",
    },
    "AAPL": {
        "name": "Apple",
        "theme": "AI compute / mega-cap technology hedge",
        "pair": "NVDA / AAPL",
    },
    "285A.T": {
        "name": "Kioxia Holdings",
        "theme": "NAND / storage hierarchy",
        "pair": "Kioxia / Sony",
    },
    "6525.T": {
        "name": "Kokusai Electric",
        "theme": "Japan semicap",
        "pair": "Kokusai / Advantest",
    },
    "6857.T": {
        "name": "Advantest",
        "theme": "Semiconductor test",
        "pair": "Advantest / Nikon",
    },
    "6981.T": {
        "name": "Murata Manufacturing",
        "theme": "Passive components / MLCC",
        "pair": "Murata / Taiyo Yuden",
    },
    "6976.T": {
        "name": "Taiyo Yuden",
        "theme": "Passive components / MLCC",
        "pair": "Murata / Taiyo Yuden",
    },
    "7011.T": {
        "name": "Mitsubishi Heavy Industries",
        "theme": "Japan industrials / defense",
        "pair": "Mitsubishi Heavy Industries standalone short",
    },
    "5016.T": {
        "name": "JX Advanced Metals",
        "theme": "Semiconductor materials / metals",
        "pair": "JX Advanced Metals standalone short",
    },
    "3436.T": {
        "name": "SUMCO",
        "theme": "Silicon wafers",
        "pair": "SUMCO standalone short",
    },
    "7731.T": {
        "name": "Nikon",
        "theme": "Japan precision / lithography",
        "pair": "Advantest / Nikon",
    },
    "9434.T": {
        "name": "SoftBank Corp.",
        "theme": "Japan telecom / defensive funding short",
        "pair": "SoftBank Corp. standalone short",
    },
    "5801.T": {
        "name": "Furukawa Electric",
        "theme": "Japan optical / cables",
        "pair": "Furukawa / Sumitomo",
    },
    "5802.T": {
        "name": "Sumitomo Electric",
        "theme": "Japan optical / cables",
        "pair": "Furukawa / Sumitomo",
    },
    "5803.T": {
        "name": "Fujikura",
        "theme": "Japan optical / cables",
        "pair": "Fujikura standalone long",
    },
    "6327.T": {
        "name": "Kitagawa Seiki",
        "theme": "AI substrates / PCB equipment",
        "pair": "Kitagawa Seiki standalone long",
    },
    "6758.T": {
        "name": "Sony Group",
        "theme": "Japan electronics / imaging",
        "pair": "Kioxia / Sony",
    },
    "CBRS": {
        "theme": "AI compute systems",
        "pair": "CBRS standalone long",
    },
    "AEHR": {
        "pair": "AEHR standalone",
    },
    "BE": {
        "name": "Bloom Energy",
        "theme": "AI power / onsite generation",
        "pair": "BE standalone long",
    },
    "NBIS": {
        "name": "Nebius",
        "theme": "AI cloud demand",
        "pair": "NBIS / CRWV",
    },
    "CRWV": {
        "name": "CoreWeave",
        "theme": "AI cloud demand",
        "pair": "NBIS / CRWV",
        "pairAllocations": [],
    },
    "CRDO": {
        "name": "Credo Technology Group",
        "theme": "AI connectivity / interconnect",
        "pair": "CRDO / ALAB",
    },
    "ALAB": {
        "name": "Astera Labs",
        "theme": "AI connectivity / interconnect",
        "pair": "CRDO / ALAB",
    },
    "GEV": {
        "pair": "GEV / ENR.DE",
    },
    "ENR.DE": {
        "pair": "GEV / ENR.DE",
    },
    "WDC": {
        "pair": "WDC / STX",
    },
    "MU": {
        "pair": "SNDK / MU",
    },
    "STX": {
        "name": "Seagate Technology",
        "theme": "Storage hierarchy",
        "pair": "WDC / STX",
    },
    "SKHY": {
        "pair": "MU+SKHY storage shorts",
    },
    "ASML": {
        "pair": "ASML / KLAC",
    },
    "AMAT": {
        "pair": "AMAT / UCTT",
    },
    "KLAC": {
        "pair": "ASML / KLAC",
    },
    "AMD": {
        "name": "Advanced Micro Devices",
        "theme": "AI compute / x86",
        "pair": "AMD / INTC",
    },
    "INTC": {
        "name": "Intel",
        "theme": "AI compute / x86",
        "pair": "AMD / INTC",
    },
    "1888.HK": {
        "name": "Kingboard Laminates",
        "theme": "PCB materials / copper-clad laminates",
        "pair": "Kingboard Laminates standalone short",
    },
    "3277.HK": {
        "name": "Gpixel Changchun Microelectronics",
        "theme": "Image sensors / semiconductors",
        "pair": "Gpixel standalone long",
    },
    "3308.HK": {
        "name": "Zhongji Innolight",
        "theme": "AI optical transceivers / device demand hedge",
        "pair": "Zhongji / Xiaomi + Lenovo",
    },
    "1810.HK": {
        "name": "Xiaomi",
        "theme": "AI hardware / consumer and enterprise devices",
        "pair": "Zhongji / Xiaomi + Lenovo",
    },
    "2513.HK": {
        "name": "Z.AI",
        "theme": "Foundation models / AI applications",
        "pair": "Z.AI / MiniMax",
    },
    "0100.HK": {
        "name": "MiniMax",
        "theme": "Foundation models / AI applications",
        "pair": "Z.AI / MiniMax",
    },
    "6869.HK": {
        "name": "YOFC",
        "theme": "Optical fiber / telecom infrastructure",
        "pair": "YOFC standalone long",
    },
    "000636.SZ": {
        "name": "Fenghua Advanced Technology",
        "theme": "Passive components / MLCC",
        "pair": "Fenghua Advanced Technology standalone short",
    },
    "300661.SZ": {
        "name": "SG Micro",
        "theme": "Analog semiconductors",
        "pair": "SG Micro standalone short",
    },
    "AAOI": {
        "name": "Applied Optoelectronics",
        "theme": "AI optical transceivers",
        "pair": "AAOI standalone long",
    },
    "COHR": {
        "name": "Coherent",
        "theme": "Optical networking",
        "pair": "COHR standalone long",
    },
    "ORCL": {
        "name": "Oracle",
        "theme": "Cloud infrastructure / enterprise software",
        "pair": "Oracle standalone short",
    },
    "TER": {
        "name": "Teradyne",
        "theme": "Semiconductor test",
        "pair": "Teradyne standalone long",
    },
    "SMTC": {
        "name": "Semtech",
        "theme": "Analog / mixed-signal semiconductors",
        "pair": "SMTC / WOLF",
    },
    "SMCI": {
        "name": "Super Micro Computer",
        "theme": "AI compute / server systems",
        "pair": "SMCI standalone short",
    },
}


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


def _load_performance_history(report_dir: Path) -> list[dict[str, object]]:
    """Load each dated report's final local snapshot for the performance curve."""
    history: list[dict[str, object]] = []
    for path in sorted(report_dir.glob("????-??-??.md")):
        metrics = {
            match.group("label"): match.group("value").strip()
            for match in _REPORT_METRIC_RE.finditer(path.read_text(encoding="utf-8"))
        }
        if not {"Total equity", "Total PnL", "Return"}.issubset(metrics):
            continue
        history.append(
            {
                "date": path.stem,
                "totalEquity": float(metrics["Total equity"].replace(",", "")),
                "totalPnl": float(metrics["Total PnL"].replace(",", "")),
                "returnPct": round(float(metrics["Return"].removesuffix("%")) / 100, 6),
            }
        )
    return history


def _drawdown_metrics(
    history: list[dict[str, object]],
    *,
    current_equity: float,
    previous_peak_equity: float = 0.0,
    previous_peak_date: str | None = None,
) -> dict[str, object]:
    """Return drawdown from the persisted daily/intraday high-water mark."""
    snapshots = [
        (str(item["date"]), float(item["totalEquity"]))
        for item in history
        if float(item["totalEquity"]) > 0
    ]
    if previous_peak_equity > 0:
        snapshots.append(
            (previous_peak_date or date.today().isoformat(), previous_peak_equity)
        )
    if snapshots:
        peak_date, peak_equity = max(snapshots, key=lambda item: item[1])
    else:
        peak_date, peak_equity = date.today().isoformat(), current_equity

    if current_equity > peak_equity:
        peak_date, peak_equity = date.today().isoformat(), current_equity

    drawdown_value = current_equity - peak_equity
    drawdown_pct = drawdown_value / peak_equity if peak_equity else 0.0
    return {
        "peakEquity": peak_equity,
        "peakDate": peak_date,
        "drawdownFromPeakValue": drawdown_value,
        "drawdownFromPeakPct": drawdown_pct,
    }


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
    performance_history = _load_performance_history(dashboard_path.parent.parent / "daily")
    previous_summary = book.get("summary", {})
    drawdown = _drawdown_metrics(
        performance_history,
        current_equity=metrics["total_equity"],
        previous_peak_equity=float(
            previous_summary.get("peakEquity", previous_summary.get("totalEquity", 0.0))
        ),
        previous_peak_date=str(
            previous_summary.get("peakDate", str(book.get("asOf", ""))[:10])
        ),
    )

    positions = []
    for holding in sorted(state.holdings.values(), key=lambda item: item.symbol):
        metadata = dict(positions_meta.get(holding.symbol, {}))
        if not metadata:
            metadata = dict(
                _DEFAULT_POSITION_METADATA.get(
                    holding.symbol,
                    {
                        "symbol": holding.symbol,
                        "name": holding.symbol,
                        "theme": "Unclassified",
                        "pair": "Unclassified",
                    },
                )
            )
        metadata.update(_POSITION_METADATA_OVERRIDES.get(holding.symbol, {}))
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
            "performanceHistory": performance_history,
            "summary": {
                **metrics,
                **drawdown,
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
