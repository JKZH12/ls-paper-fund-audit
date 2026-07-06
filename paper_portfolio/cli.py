from __future__ import annotations

import argparse
from pathlib import Path

from .audit import ensure_genesis_event, git_anchor, record_audit_event, verify_audit_chain, write_manifest
from .core import apply_trade
from .core import portfolio_metrics
from .db import (
    DEFAULT_DB_PATH,
    connect,
    create_portfolio,
    default_portfolio_id,
    load_state,
    record_transaction,
    save_state,
    update_price,
)
from .report import money, pct, render_report, write_daily_report


TRADE_SIDES = {"buy", "sell", "short", "cover"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local paper portfolio ledger")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--portfolio-id", type=int, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a portfolio")
    init_parser.add_argument("--name", default="LS Paper Fund")
    init_parser.add_argument("--initial-cash", type=float, default=50_000_000)
    init_parser.add_argument("--base-currency", default="USD")
    init_parser.add_argument("--strategy-type", default="long_short_hedge_fund")

    trade_parser = subparsers.add_parser("trade", help="record a simulated trade")
    trade_parser.add_argument("--symbol", required=True)
    trade_parser.add_argument("--side", required=True, choices=["buy", "sell", "short", "cover"])
    trade_parser.add_argument("--quantity", type=float, required=True)
    trade_parser.add_argument("--price", type=float, required=True)
    trade_parser.add_argument("--fee", type=float, default=0.0)
    trade_parser.add_argument("--notes", default="")

    for side in sorted(TRADE_SIDES):
        side_parser = subparsers.add_parser(side, help=f"record a simulated {side} order")
        side_parser.add_argument("symbol")
        side_parser.add_argument("quantity", type=float)
        side_parser.add_argument("price", type=float)
        side_parser.add_argument("--fee", type=float, default=0.0)
        side_parser.add_argument("--notes", default="")
        side_parser.set_defaults(side=side)

    price_parser = subparsers.add_parser("price", help="update a manual mark price")
    price_parser.add_argument("--symbol", required=True)
    price_parser.add_argument("--price", type=float, required=True)
    price_parser.add_argument("--source", default="manual")

    mark_parser = subparsers.add_parser("mark", help="update a manual mark price")
    mark_parser.add_argument("symbol")
    mark_parser.add_argument("price", type=float)
    mark_parser.add_argument("--source", default="manual")

    subparsers.add_parser("summary", help="print current summary")

    report_parser = subparsers.add_parser("report", help="write or print a daily report")
    report_parser.add_argument("--print", action="store_true", dest="print_report")
    report_parser.add_argument("--out-dir", type=Path, default=Path("reports/daily"))

    audit_parser = subparsers.add_parser("audit", help="manage audit trail")
    audit_subparsers = audit_parser.add_subparsers(dest="audit_command", required=True)

    audit_subparsers.add_parser("init", help="create the genesis audit event if missing")
    audit_subparsers.add_parser("verify", help="verify audit hash chain")
    audit_subparsers.add_parser("manifest", help="write today's audit manifest")

    anchor_parser = audit_subparsers.add_parser("anchor", help="commit audit artifacts to git and push if remote exists")
    anchor_parser.add_argument("--include-code", action="store_true")
    anchor_parser.add_argument("--no-push", action="store_true")
    anchor_parser.add_argument("--message", default=None)

    return parser


def resolve_portfolio_id(conn, portfolio_id: int | None) -> int:
    return portfolio_id if portfolio_id is not None else default_portfolio_id(conn)


def realized_delta_for_trade(before, *, symbol: str, side: str, quantity: float, price: float, fee: float) -> float:
    holding = before.holdings.get(symbol.upper())
    if side == "sell" and holding is not None:
        return (price - holding.average_cost) * quantity - fee
    if side == "cover" and holding is not None:
        return (holding.average_cost - price) * quantity - fee
    return 0.0


def handle_trade(conn, args, portfolio_id: int) -> None:
    with conn:
        ensure_genesis_event(conn, portfolio_id)

    before = load_state(conn, portfolio_id)
    realized_delta = realized_delta_for_trade(
        before,
        symbol=args.symbol,
        side=args.side,
        quantity=args.quantity,
        price=args.price,
        fee=args.fee,
    )
    after = apply_trade(
        before,
        symbol=args.symbol,
        side=args.side,
        quantity=args.quantity,
        price=args.price,
        fee=args.fee,
    )
    with conn:
        save_state(conn, portfolio_id, after)
        trade_id = record_transaction(
            conn,
            portfolio_id=portfolio_id,
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            price=args.price,
            fee=args.fee,
            realized_pnl=realized_delta,
            notes=args.notes,
        )
        record_audit_event(
            conn,
            portfolio_id=portfolio_id,
            event_type="trade_recorded",
            payload={
                "trade_id": trade_id,
                "symbol": args.symbol.upper(),
                "side": args.side,
                "quantity": args.quantity,
                "price": args.price,
                "fee": args.fee,
                "realized_pnl": realized_delta,
                "notes": args.notes,
                "post_trade_snapshot": {
                    "cash": after.cash,
                    "holding": None if args.symbol.upper() not in after.holdings else after.holdings[args.symbol.upper()].__dict__,
                },
            },
        )
    manifest_path = write_manifest(conn, portfolio_id=portfolio_id, db_path=args.db)
    print(f"Recorded simulated trade {trade_id}: {args.side} {args.quantity:g} {args.symbol.upper()} @ {args.price:g}")
    print(f"Audit manifest: {manifest_path}")


def handle_price(conn, args, portfolio_id: int) -> None:
    with conn:
        ensure_genesis_event(conn, portfolio_id)
    update_price(conn, portfolio_id=portfolio_id, symbol=args.symbol, price=args.price, source=args.source)
    with conn:
        record_audit_event(
            conn,
            portfolio_id=portfolio_id,
            event_type="price_mark_updated",
            payload={
                "symbol": args.symbol.upper(),
                "price": args.price,
                "source": args.source,
            },
        )
    manifest_path = write_manifest(conn, portfolio_id=portfolio_id, db_path=args.db)
    print(f"Updated price: {args.symbol.upper()} = {args.price:g}")
    print(f"Audit manifest: {manifest_path}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    conn = connect(args.db)

    if args.command == "init":
        portfolio_id = create_portfolio(
            conn,
            name=args.name,
            initial_cash=args.initial_cash,
            base_currency=args.base_currency,
            strategy_type=args.strategy_type,
        )
        with conn:
            ensure_genesis_event(conn, portfolio_id)
        manifest_path = write_manifest(conn, portfolio_id=portfolio_id, db_path=args.db)
        print(f"Created portfolio {portfolio_id}: {args.name}")
        print(f"Audit manifest: {manifest_path}")
        return

    portfolio_id = resolve_portfolio_id(conn, args.portfolio_id)

    if args.command == "trade" or args.command in TRADE_SIDES:
        handle_trade(conn, args, portfolio_id)
        return

    if args.command in {"price", "mark"}:
        handle_price(conn, args, portfolio_id)
        return

    if args.command == "summary":
        state = load_state(conn, portfolio_id)
        metrics = portfolio_metrics(state)
        print(f"Total equity: {money(metrics['total_equity'])}")
        print(f"Cash: {money(metrics['cash'])}")
        print(f"Total PnL: {money(metrics['total_pnl'])} ({pct(metrics['return_pct'])})")
        print(f"Gross exposure: {money(metrics['gross_exposure'])} ({pct(metrics['gross_exposure_pct'])})")
        print(f"Net exposure: {money(metrics['net_market_value'])} ({pct(metrics['net_exposure_pct'])})")
        return

    if args.command == "report":
        with conn:
            ensure_genesis_event(conn, portfolio_id)
        if args.print_report:
            print(render_report(conn, portfolio_id))
        else:
            path = args.out_dir / f"{__import__('datetime').date.today().isoformat()}.md"
            with conn:
                record_audit_event(
                    conn,
                    portfolio_id=portfolio_id,
                    event_type="daily_report_generated",
                    payload={
                        "report_path": str(path),
                        "report_date": path.stem,
                    },
                )
            path = write_daily_report(conn, portfolio_id, args.out_dir)
            manifest_path = write_manifest(conn, portfolio_id=portfolio_id, report_path=path, db_path=args.db)
            print(f"Wrote report: {path}")
            print(f"Audit manifest: {manifest_path}")
        return

    if args.command == "audit":
        if args.audit_command == "init":
            with conn:
                event_hash = ensure_genesis_event(conn, portfolio_id)
            manifest_path = write_manifest(conn, portfolio_id=portfolio_id, db_path=args.db)
            if event_hash:
                print(f"Created genesis audit event: {event_hash}")
            else:
                print("Genesis audit event already exists")
            print(f"Audit manifest: {manifest_path}")
            return
        if args.audit_command == "verify":
            result = verify_audit_chain(conn, portfolio_id)
            if result.ok:
                print(f"Audit OK: {result.event_count} events, head {result.head_hash}")
            else:
                print(f"Audit FAILED: {result.event_count} events, head {result.head_hash}")
                for problem in result.problems:
                    print(f"- {problem}")
                raise SystemExit(1)
            return
        if args.audit_command == "manifest":
            with conn:
                ensure_genesis_event(conn, portfolio_id)
            manifest_path = write_manifest(conn, portfolio_id=portfolio_id, db_path=args.db)
            print(f"Audit manifest: {manifest_path}")
            return
        if args.audit_command == "anchor":
            result = verify_audit_chain(conn, portfolio_id)
            if not result.ok:
                for problem in result.problems:
                    print(f"- {problem}")
                raise SystemExit(1)
            manifest_path = write_manifest(conn, portfolio_id=portfolio_id, db_path=args.db)
            message = args.message or f"Anchor paper portfolio audit {manifest_path.stem}"
            anchor = git_anchor(include_code=args.include_code, message=message, push=not args.no_push)
            print(f"Audit manifest: {manifest_path}")
            print(f"Git anchor: {anchor}")
            return

    parser.error(f"unknown command: {args.command}")
