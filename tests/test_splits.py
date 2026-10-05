import argparse
import json

import pytest

from paper_portfolio.audit import restore_database_from_event_log, verify_audit_chain
from paper_portfolio.cli import handle_split, main
from paper_portfolio.core import Holding, PortfolioState, apply_split, portfolio_metrics
from paper_portfolio.db import connect, create_portfolio, load_state, save_state


@pytest.mark.parametrize("quantity,ratio", [(10.5, 5), (-10.5, 10), (10.5, 0.5)])
def test_split_preserves_economics_with_old_mark(quantity, ratio):
    state = PortfolioState(10000, 10000, {"TEST": Holding("TEST", quantity, 100, 12, 120)})
    after = apply_split(state, symbol="TEST", ratio=ratio, mark_basis="pre-split")
    assert portfolio_metrics(after) == pytest.approx(portfolio_metrics(state))
    assert after.holdings["TEST"].quantity == quantity * ratio
    assert after.holdings["TEST"].average_cost == 100 / ratio
    assert state.holdings["TEST"].quantity == quantity


@pytest.mark.parametrize("ratio", [0, -1, 1, float("nan"), float("inf")])
def test_split_rejects_invalid_ratio(ratio):
    state = PortfolioState(10000, 10000, {"TEST": Holding("TEST", 10, 100)})
    with pytest.raises(ValueError):
        apply_split(state, symbol="TEST", ratio=ratio, mark_basis="pre-split")


@pytest.mark.parametrize("quantity,side", [(10.5, "sell"), (-10.5, "cover")])
def test_split_is_audited_duplicate_safe_and_rebuildable(tmp_path, quantity, side):
    path = tmp_path / "source.sqlite"
    conn = connect(path)
    pid = create_portfolio(conn, name="Test", initial_cash=10000, base_currency="USD", strategy_type="long_short_hedge_fund")
    with conn:
        save_state(conn, pid, PortfolioState(10000, 10000, {"TEST": Holding("TEST", quantity, 100, 12, 24)}))
    args = argparse.Namespace(db=path, symbol="TEST", ratio=5, effective_date="2020-01-01",
                              source="verified test disclosure", mark_basis="post-split")
    handle_split(conn, args, pid)
    state = load_state(conn, pid)
    assert state.cash == 10000
    assert state.holdings["TEST"] == Holding("TEST", quantity * 5, 20, 12, 24)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    with pytest.raises(ValueError, match="already applied"):
        handle_split(conn, args, pid)
    assert load_state(conn, pid) == state
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2
    assert verify_audit_chain(conn, pid).ok
    payload = json.loads(conn.execute("SELECT payload_json FROM audit_events WHERE event_type='stock_split_applied'").fetchone()[0])
    assert payload["source"] == args.source
    conn.close()
    # A subsequent close must use the new share units and cost basis.
    main(["--db", str(path), side, "TEST", str(abs(quantity * 5)), "24"])
    original = connect(path)
    rebuilt_path = tmp_path / "rebuilt.sqlite"
    result = restore_database_from_event_log(event_log_path=tmp_path / "audit/events.jsonl",
                                            db_path=rebuilt_path, write_restored_manifest=False)
    restored = connect(rebuilt_path)
    assert result.ok
    assert load_state(restored, pid) == load_state(original, pid)
    assert load_state(restored, pid).holdings == {}
    pnl = original.execute("SELECT realized_pnl FROM transactions").fetchone()[0]
    assert pnl == pytest.approx(quantity * 5 * 4)
    restored.close()
    original.close()
