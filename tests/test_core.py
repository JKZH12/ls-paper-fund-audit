import unittest
import tempfile
from pathlib import Path

from paper_portfolio.audit import ensure_genesis_event, record_audit_event, verify_audit_chain, write_manifest
from paper_portfolio.core import Holding, PortfolioState, apply_trade, holding_unrealized_pnl, portfolio_metrics
from paper_portfolio.db import connect, create_portfolio


def empty_state(cash=1_000_000):
    return PortfolioState(initial_cash=cash, cash=cash, holdings={})


class CoreTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
