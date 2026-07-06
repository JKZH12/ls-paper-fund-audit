from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal


Side = Literal["buy", "sell", "short", "cover"]


@dataclass(frozen=True)
class Holding:
    symbol: str
    quantity: float = 0.0
    average_cost: float = 0.0
    realized_pnl: float = 0.0
    last_price: float | None = None


@dataclass(frozen=True)
class PortfolioState:
    initial_cash: float
    cash: float
    holdings: dict[str, Holding]


def apply_trade(
    state: PortfolioState,
    *,
    symbol: str,
    side: Side,
    quantity: float,
    price: float,
    fee: float = 0.0,
) -> PortfolioState:
    """Apply one simulated trade and return the updated portfolio state."""
    validate_trade_inputs(symbol=symbol, side=side, quantity=quantity, price=price, fee=fee)
    symbol = symbol.upper()
    holding = state.holdings.get(symbol, Holding(symbol=symbol))

    if side == "buy":
        updated_cash, updated_holding = _apply_buy(state.cash, holding, quantity, price, fee)
    elif side == "sell":
        updated_cash, updated_holding = _apply_sell(state.cash, holding, quantity, price, fee)
    elif side == "short":
        updated_cash, updated_holding = _apply_short(state.cash, holding, quantity, price, fee)
    elif side == "cover":
        updated_cash, updated_holding = _apply_cover(state.cash, holding, quantity, price, fee)
    else:
        raise ValueError(f"Unsupported side: {side}")

    holdings = dict(state.holdings)
    if abs(updated_holding.quantity) < 1e-9:
        holdings.pop(symbol, None)
    else:
        holdings[symbol] = updated_holding
    return replace(state, cash=updated_cash, holdings=holdings)


def validate_trade_inputs(*, symbol: str, side: Side, quantity: float, price: float, fee: float) -> None:
    if not symbol or not symbol.strip():
        raise ValueError("symbol is required")
    if side not in {"buy", "sell", "short", "cover"}:
        raise ValueError("side must be one of buy, sell, short, cover")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price <= 0:
        raise ValueError("price must be positive")
    if fee < 0:
        raise ValueError("fee cannot be negative")


def _apply_buy(cash: float, holding: Holding, quantity: float, price: float, fee: float) -> tuple[float, Holding]:
    if holding.quantity < -1e-9:
        raise ValueError("use cover to reduce a short position")
    total_cost = quantity * price + fee
    if cash + 1e-9 < total_cost:
        raise ValueError("insufficient cash")
    new_quantity = holding.quantity + quantity
    new_average = ((holding.quantity * holding.average_cost) + (quantity * price)) / new_quantity
    return cash - total_cost, replace(holding, quantity=new_quantity, average_cost=new_average)


def _apply_sell(cash: float, holding: Holding, quantity: float, price: float, fee: float) -> tuple[float, Holding]:
    if holding.quantity + 1e-9 < quantity:
        raise ValueError("insufficient long position")
    proceeds = quantity * price - fee
    realized_pnl = (price - holding.average_cost) * quantity - fee
    new_quantity = holding.quantity - quantity
    new_average = 0.0 if abs(new_quantity) < 1e-9 else holding.average_cost
    return cash + proceeds, replace(
        holding,
        quantity=new_quantity,
        average_cost=new_average,
        realized_pnl=holding.realized_pnl + realized_pnl,
    )


def _apply_short(cash: float, holding: Holding, quantity: float, price: float, fee: float) -> tuple[float, Holding]:
    if holding.quantity > 1e-9:
        raise ValueError("use sell to reduce a long position before shorting")
    proceeds = quantity * price - fee
    current_short = abs(holding.quantity)
    new_short = current_short + quantity
    new_average = ((current_short * holding.average_cost) + (quantity * price)) / new_short
    return cash + proceeds, replace(holding, quantity=-new_short, average_cost=new_average)


def _apply_cover(cash: float, holding: Holding, quantity: float, price: float, fee: float) -> tuple[float, Holding]:
    current_short = abs(min(holding.quantity, 0.0))
    if current_short + 1e-9 < quantity:
        raise ValueError("insufficient short position")
    total_cost = quantity * price + fee
    if cash + 1e-9 < total_cost:
        raise ValueError("insufficient cash")
    realized_pnl = (holding.average_cost - price) * quantity - fee
    new_short = current_short - quantity
    new_quantity = -new_short
    new_average = 0.0 if abs(new_quantity) < 1e-9 else holding.average_cost
    return cash - total_cost, replace(
        holding,
        quantity=new_quantity,
        average_cost=new_average,
        realized_pnl=holding.realized_pnl + realized_pnl,
    )


def holding_market_value(holding: Holding) -> float:
    price = holding.last_price if holding.last_price is not None else holding.average_cost
    return holding.quantity * price


def holding_unrealized_pnl(holding: Holding) -> float:
    price = holding.last_price if holding.last_price is not None else holding.average_cost
    if holding.quantity >= 0:
        return (price - holding.average_cost) * holding.quantity
    return (holding.average_cost - price) * abs(holding.quantity)


def portfolio_metrics(state: PortfolioState) -> dict[str, float]:
    market_value = sum(holding_market_value(holding) for holding in state.holdings.values())
    long_market_value = sum(max(holding_market_value(holding), 0.0) for holding in state.holdings.values())
    short_market_value = sum(abs(min(holding_market_value(holding), 0.0)) for holding in state.holdings.values())
    gross_exposure = long_market_value + short_market_value
    total_equity = state.cash + market_value
    unrealized_pnl = sum(holding_unrealized_pnl(holding) for holding in state.holdings.values())
    realized_pnl = sum(holding.realized_pnl for holding in state.holdings.values())
    total_pnl = total_equity - state.initial_cash
    return {
        "cash": state.cash,
        "long_market_value": long_market_value,
        "short_market_value": short_market_value,
        "net_market_value": market_value,
        "gross_exposure": gross_exposure,
        "total_equity": total_equity,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "return_pct": total_pnl / state.initial_cash if state.initial_cash else 0.0,
        "net_exposure_pct": market_value / total_equity if total_equity else 0.0,
        "gross_exposure_pct": gross_exposure / total_equity if total_equity else 0.0,
    }
