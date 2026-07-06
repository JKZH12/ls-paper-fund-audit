from __future__ import annotations

import sqlite3
from pathlib import Path

from .core import Holding, PortfolioState


DEFAULT_DB_PATH = Path("data/portfolio.sqlite")


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            initial_cash REAL NOT NULL CHECK (initial_cash > 0),
            cash REAL NOT NULL CHECK (cash >= 0),
            base_currency TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS holdings (
            portfolio_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL,
            average_cost REAL NOT NULL,
            realized_pnl REAL NOT NULL DEFAULT 0,
            last_price REAL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (portfolio_id, symbol),
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell', 'short', 'cover')),
            quantity REAL NOT NULL CHECK (quantity > 0),
            price REAL NOT NULL CHECK (price > 0),
            fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
            realized_pnl REAL NOT NULL DEFAULT 0,
            trade_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL CHECK (price > 0),
            price_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL DEFAULT 'manual',
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def create_portfolio(
    conn: sqlite3.Connection,
    *,
    name: str,
    initial_cash: float,
    base_currency: str,
    strategy_type: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO portfolios (name, strategy_type, initial_cash, cash, base_currency)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, strategy_type, initial_cash, initial_cash, base_currency.upper()),
    )
    conn.commit()
    return int(cur.lastrowid)


def default_portfolio_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM portfolios ORDER BY id LIMIT 1").fetchone()
    if not row:
        raise ValueError("no portfolio exists; run init first")
    return int(row["id"])


def get_portfolio(conn: sqlite3.Connection, portfolio_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
    if not row:
        raise ValueError(f"portfolio not found: {portfolio_id}")
    return row


def load_state(conn: sqlite3.Connection, portfolio_id: int) -> PortfolioState:
    portfolio = get_portfolio(conn, portfolio_id)
    holdings: dict[str, Holding] = {}
    for row in conn.execute(
        "SELECT symbol, quantity, average_cost, realized_pnl, last_price FROM holdings WHERE portfolio_id = ?",
        (portfolio_id,),
    ):
        holdings[row["symbol"]] = Holding(
            symbol=row["symbol"],
            quantity=float(row["quantity"]),
            average_cost=float(row["average_cost"]),
            realized_pnl=float(row["realized_pnl"]),
            last_price=None if row["last_price"] is None else float(row["last_price"]),
        )
    return PortfolioState(initial_cash=float(portfolio["initial_cash"]), cash=float(portfolio["cash"]), holdings=holdings)


def save_state(conn: sqlite3.Connection, portfolio_id: int, state: PortfolioState) -> None:
    conn.execute("UPDATE portfolios SET cash = ? WHERE id = ?", (state.cash, portfolio_id))
    existing = {
        row["symbol"]
        for row in conn.execute("SELECT symbol FROM holdings WHERE portfolio_id = ?", (portfolio_id,))
    }
    current = set(state.holdings)
    for symbol in existing - current:
        conn.execute("DELETE FROM holdings WHERE portfolio_id = ? AND symbol = ?", (portfolio_id, symbol))
    for holding in state.holdings.values():
        conn.execute(
            """
            INSERT INTO holdings (portfolio_id, symbol, quantity, average_cost, realized_pnl, last_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(portfolio_id, symbol) DO UPDATE SET
                quantity = excluded.quantity,
                average_cost = excluded.average_cost,
                realized_pnl = excluded.realized_pnl,
                last_price = excluded.last_price,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                portfolio_id,
                holding.symbol,
                holding.quantity,
                holding.average_cost,
                holding.realized_pnl,
                holding.last_price,
            ),
        )


def record_transaction(
    conn: sqlite3.Connection,
    *,
    portfolio_id: int,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    fee: float,
    realized_pnl: float,
    notes: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO transactions (portfolio_id, symbol, side, quantity, price, fee, realized_pnl, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (portfolio_id, symbol.upper(), side, quantity, price, fee, realized_pnl, notes),
    )
    return int(cur.lastrowid)


def update_price(
    conn: sqlite3.Connection,
    *,
    portfolio_id: int,
    symbol: str,
    price: float,
    source: str = "manual",
) -> None:
    symbol = symbol.upper()
    conn.execute(
        """
        INSERT INTO price_snapshots (portfolio_id, symbol, price, source)
        VALUES (?, ?, ?, ?)
        """,
        (portfolio_id, symbol, price, source),
    )
    conn.execute(
        """
        UPDATE holdings
        SET last_price = ?, updated_at = CURRENT_TIMESTAMP
        WHERE portfolio_id = ? AND symbol = ?
        """,
        (price, portfolio_id, symbol),
    )
    conn.commit()


def list_transactions(conn: sqlite3.Connection, portfolio_id: int, limit: int = 50) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM transactions
            WHERE portfolio_id = ?
            ORDER BY trade_time DESC, id DESC
            LIMIT ?
            """,
            (portfolio_id, limit),
        )
    )
