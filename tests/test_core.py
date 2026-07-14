import unittest
import tempfile
import json
from pathlib import Path

from paper_portfolio.audit import (
    ensure_genesis_event,
    record_audit_event,
    restore_database_from_event_log,
    verify_audit_chain,
    write_manifest,
)
from paper_portfolio.core import Holding, PortfolioState, apply_trade, holding_unrealized_pnl, portfolio_metrics
from paper_portfolio.dashboard import _load_performance_history
from paper_portfolio.db import connect, create_portfolio, load_state, record_transaction, save_state, update_price


def empty_state(cash=1_000_000):
    return PortfolioState(initial_cash=cash, cash=cash, holdings={})


class CoreTest(unittest.TestCase):
    def test_load_performance_history_uses_final_dated_report_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            (report_dir / "2026-07-06.md").write_text(
                "| Total equity | 49,931,244.06 |\n"
                "| Total PnL | -68,755.94 |\n"
                "| Return | -0.14% |\n",
                encoding="utf-8",
            )
            (report_dir / "notes.md").write_text("not a dated report", encoding="utf-8")

            self.assertEqual(
                _load_performance_history(report_dir),
                [
                    {
                        "date": "2026-07-06",
                        "totalEquity": 49_931_244.06,
                        "totalPnl": -68_755.94,
                        "returnPct": -0.0014,
                    }
                ],
            )

    def test_buy_success(self):
        state = apply_trade(empty_state(), symbol="NVDA", side="buy", quantity=10, price=100, fee=1)
        self.assertAlmostEqual(state.cash, 998_999)
        self.assertAlmostEqual(state.holdings["NVDA"].quantity, 10)
        self.assertAlmostEqual(state.holdings["NVDA"].average_cost, 100)

    def test_buy_fails_when_cash_is_insufficient(self):
        with self.assertRaisesRegex(ValueError, "insufficient cash"):
            apply_trade(empty_state(cash=100), symbol="NVDA", side="buy", quantity=2, price=100, fee=1)

    def test_sell_success_and_realized_pnl(self):
        state = apply_trade(empty_state(), symbol="NVDA", side="buy", quantity=10, price=100)
        state = apply_trade(state, symbol="NVDA", side="sell", quantity=4, price=120, fee=1)
        holding = state.holdings["NVDA"]
        self.assertAlmostEqual(state.cash, 999_479)
        self.assertAlmostEqual(holding.quantity, 6)
        self.assertAlmostEqual(holding.realized_pnl, 79)

    def test_sell_fails_when_position_is_insufficient(self):
        state = apply_trade(empty_state(), symbol="NVDA", side="buy", quantity=1, price=100)
        with self.assertRaisesRegex(ValueError, "insufficient long position"):
            apply_trade(state, symbol="NVDA", side="sell", quantity=2, price=100)

    def test_multiple_buys_weight_average_cost(self):
        state = apply_trade(empty_state(), symbol="NVDA", side="buy", quantity=10, price=100)
        state = apply_trade(state, symbol="NVDA", side="buy", quantity=10, price=120)
        self.assertAlmostEqual(state.holdings["NVDA"].average_cost, 110)

    def test_short_and_cover_realized_pnl(self):
        state = apply_trade(empty_state(), symbol="TSLA", side="short", quantity=10, price=200, fee=1)
        self.assertAlmostEqual(state.cash, 1_001_999)
        state = apply_trade(state, symbol="TSLA", side="cover", quantity=4, price=180, fee=1)
        holding = state.holdings["TSLA"]
        self.assertAlmostEqual(holding.quantity, -6)
        self.assertAlmostEqual(holding.realized_pnl, 79)

    def test_unrealized_pnl_and_total_equity_for_long(self):
        state = PortfolioState(
            initial_cash=1_000_000,
            cash=999_000,
            holdings={"NVDA": Holding(symbol="NVDA", quantity=10, average_cost=100, last_price=110)},
        )
        self.assertAlmostEqual(holding_unrealized_pnl(state.holdings["NVDA"]), 100)
        metrics = portfolio_metrics(state)
        self.assertAlmostEqual(metrics["total_equity"], 1_000_100)
        self.assertAlmostEqual(metrics["total_pnl"], 100)

    def test_unrealized_pnl_and_total_equity_for_short(self):
        state = PortfolioState(
            initial_cash=1_000_000,
            cash=1_002_000,
            holdings={"TSLA": Holding(symbol="TSLA", quantity=-10, average_cost=200, last_price=180)},
        )
        self.assertAlmostEqual(holding_unrealized_pnl(state.holdings["TSLA"]), 200)
        metrics = portfolio_metrics(state)
        self.assertAlmostEqual(metrics["total_equity"], 1_000_200)
        self.assertAlmostEqual(metrics["total_pnl"], 200)

    def test_audit_chain_verifies_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "portfolio.sqlite"
            conn = connect(db_path)
            portfolio_id = create_portfolio(
                conn,
                name="LS Paper Fund",
                initial_cash=50_000_000,
                base_currency="USD",
                strategy_type="long_short_hedge_fund",
            )
            with conn:
                ensure_genesis_event(conn, portfolio_id)
                record_audit_event(conn, portfolio_id=portfolio_id, event_type="test_event", payload={"ok": True})
            result = verify_audit_chain(conn, portfolio_id)
            self.assertTrue(result.ok)
            self.assertEqual(result.event_count, 2)
            manifest = write_manifest(conn, portfolio_id=portfolio_id, workspace=workspace, db_path=db_path)
            self.assertTrue(manifest.exists())
            self.assertTrue((workspace / "audit/events.jsonl").exists())

    def test_manifest_realized_pnl_uses_transactions_after_closed_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "portfolio.sqlite"
            conn = connect(db_path)
            portfolio_id = create_portfolio(
                conn,
                name="LS Paper Fund",
                initial_cash=1_000_000,
                base_currency="USD",
                strategy_type="long_short_hedge_fund",
            )
            with conn:
                ensure_genesis_event(conn, portfolio_id)

            state = load_state(conn, portfolio_id)
            state = apply_trade(state, symbol="NVDA", side="buy", quantity=10, price=100)
            state = apply_trade(state, symbol="NVDA", side="sell", quantity=10, price=120)
            with conn:
                save_state(conn, portfolio_id, state)
                record_transaction(
                    conn,
                    portfolio_id=portfolio_id,
                    symbol="NVDA",
                    side="sell",
                    quantity=10,
                    price=120,
                    fee=0,
                    realized_pnl=200,
                    notes="closed profitable long",
                )

            manifest = write_manifest(conn, portfolio_id=portfolio_id, workspace=workspace, db_path=db_path)
            data = json.loads(manifest.read_text())
            self.assertAlmostEqual(data["metrics"]["total_pnl"], 200)
            self.assertAlmostEqual(data["metrics"]["realized_pnl"], 200)
            self.assertAlmostEqual(data["metrics"]["unrealized_pnl"], 0)

    def test_rebuild_database_from_event_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source_db = workspace / "source.sqlite"
            restored_db = workspace / "restored.sqlite"
            conn = connect(source_db)
            portfolio_id = create_portfolio(
                conn,
                name="LS Paper Fund",
                initial_cash=50_000_000,
                base_currency="USD",
                strategy_type="long_short_hedge_fund",
            )
            with conn:
                ensure_genesis_event(conn, portfolio_id)

            before = load_state(conn, portfolio_id)
            after = apply_trade(before, symbol="NVDA", side="buy", quantity=10, price=100, fee=1)
            realized_pnl = 0.0
            with conn:
                save_state(conn, portfolio_id, after)
                trade_id = record_transaction(
                    conn,
                    portfolio_id=portfolio_id,
                    symbol="NVDA",
                    side="buy",
                    quantity=10,
                    price=100,
                    fee=1,
                    realized_pnl=realized_pnl,
                    notes="test rebuild",
                )
                record_audit_event(
                    conn,
                    portfolio_id=portfolio_id,
                    event_type="trade_recorded",
                    payload={
                        "trade_id": trade_id,
                        "symbol": "NVDA",
                        "side": "buy",
                        "quantity": 10,
                        "price": 100,
                        "fee": 1,
                        "realized_pnl": realized_pnl,
                        "notes": "test rebuild",
                        "post_trade_snapshot": {
                            "cash": after.cash,
                            "holding": after.holdings["NVDA"].__dict__,
                        },
                    },
                )
            update_price(conn, portfolio_id=portfolio_id, symbol="NVDA", price=110, source="test mark")
            with conn:
                record_audit_event(
                    conn,
                    portfolio_id=portfolio_id,
                    event_type="price_mark_updated",
                    payload={"symbol": "NVDA", "price": 110, "source": "test mark"},
                )
            write_manifest(conn, portfolio_id=portfolio_id, workspace=workspace, db_path=source_db)

            result = restore_database_from_event_log(
                event_log_path=workspace / "audit/events.jsonl",
                db_path=restored_db,
                write_restored_manifest=False,
            )
            self.assertTrue(result.ok)
            restored_conn = connect(restored_db)
            restored_state = load_state(restored_conn, portfolio_id)
            self.assertAlmostEqual(restored_state.cash, after.cash)
            self.assertAlmostEqual(restored_state.holdings["NVDA"].quantity, 10)
            self.assertAlmostEqual(restored_state.holdings["NVDA"].last_price, 110)
            restored_result = verify_audit_chain(restored_conn, portfolio_id)
            self.assertEqual(restored_result.head_hash, result.head_hash)


if __name__ == "__main__":
    unittest.main()
