"""
SQLite Persistent Storage with Write-Ahead Logging (WAL) Mode.
Asynchronous non-blocking storage for Orders, Execution Fills, and Portfolio Snapshots.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import aiosqlite

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderStatus,
    PortfolioState,
)

logger = logging.getLogger("multiquant.state.sqlite")


class SQLiteStore:
    """
    High-performance async SQLite persistence engine configured with WAL mode.
    Guarantees ACID compliance without blocking the main trading event loop.
    """

    def __init__(self, db_path: str = "multiquant_state.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Establish connection and apply WAL optimizations."""
        self._db = await aiosqlite.connect(self.db_path)
        # WAL mode permits concurrent readers without waiting for writers
        await self._db.execute("PRAGMA journal_mode = WAL;")
        await self._db.execute("PRAGMA synchronous = NORMAL;")
        await self._db.execute("PRAGMA foreign_keys = ON;")
        await self._init_schema()
        logger.info("Connected to SQLite DB: %s (WAL mode active)", self.db_path)

    async def disconnect(self) -> None:
        """Close database connection cleanly."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("Closed SQLite connection.")

    async def _init_schema(self) -> None:
        """Create tables if they don't already exist."""
        if self._db is None:
            raise RuntimeError("Database connection is not initialized.")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL,
                stop_price REAL,
                status TEXT NOT NULL,
                client_tag TEXT,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
        """)

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                filled_quantity REAL NOT NULL,
                filled_price REAL NOT NULL,
                fee REAL NOT NULL,
                slippage REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                message TEXT,
                FOREIGN KEY (order_id) REFERENCES orders(order_id)
            );
        """)

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_equity REAL NOT NULL,
                cash REAL NOT NULL,
                peak_equity REAL NOT NULL,
                drawdown_pct REAL NOT NULL,
                positions_json TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            );
        """)
        await self._db.commit()

    async def save_order(self, order: OrderRequest, status: OrderStatus) -> None:
        """Persist or update an order."""
        if self._db is None:
            raise RuntimeError("Database connection is not initialized.")

        now = datetime.now(UTC).isoformat()
        query = """
            INSERT INTO orders (
                order_id, symbol, side, order_type, quantity, price, stop_price,
                status, client_tag, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at;
        """
        await self._db.execute(
            query,
            (
                order.order_id,
                order.symbol,
                order.side.value,
                order.order_type.value,
                order.quantity,
                order.price,
                order.stop_price,
                status.value,
                order.client_tag,
                order.timestamp.isoformat(),
                now,
            ),
        )
        await self._db.commit()

    async def save_fill(self, fill: FillEvent) -> None:
        """Persist execution fill record safely ensuring foreign key integrity."""
        if self._db is None:
            raise RuntimeError("Database connection is not initialized.")

        # Ensure parent order exists to satisfy Foreign Key constraint
        await self._db.execute(
            """
            INSERT OR IGNORE INTO orders (
                order_id, symbol, side, order_type, quantity, price, stop_price,
                status, client_tag, created_at, updated_at
            ) VALUES (?, ?, ?, 'MARKET', ?, ?, NULL, 'FILLED', 'direct_fill', ?, ?);
            """,
            (
                fill.order_id,
                fill.symbol,
                fill.side.value,
                fill.filled_quantity,
                fill.filled_price,
                fill.timestamp.isoformat(),
                fill.timestamp.isoformat(),
            ),
        )

        query = """
            INSERT INTO fills (
                order_id, symbol, side, status, filled_quantity,
                filled_price, fee, slippage, timestamp, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        await self._db.execute(
            query,
            (
                fill.order_id,
                fill.symbol,
                fill.side.value,
                fill.status.value,
                fill.filled_quantity,
                fill.filled_price,
                fill.fee,
                fill.slippage,
                fill.timestamp.isoformat(),
                fill.message,
            ),
        )
        await self._db.commit()

    async def save_portfolio_snapshot(self, portfolio: PortfolioState) -> None:
        """Save a historical portfolio snapshot."""
        if self._db is None:
            raise RuntimeError("Database connection is not initialized.")

        positions_dict = {
            sym: {
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "realized_pnl": pos.realized_pnl,
            }
            for sym, pos in portfolio.positions.items()
        }

        query = """
            INSERT INTO portfolio_snapshots (
                total_equity, cash, peak_equity, drawdown_pct, positions_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?);
        """
        await self._db.execute(
            query,
            (
                portfolio.total_equity,
                portfolio.cash,
                portfolio.peak_equity,
                portfolio.drawdown_pct,
                json.dumps(positions_dict),
                portfolio.timestamp.isoformat(),
            ),
        )
        await self._db.commit()

    async def get_order(self, order_id: str) -> dict[str, object] | None:
        """Retrieve order by order_id."""
        if self._db is None:
            raise RuntimeError("Database connection is not initialized.")

        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM orders WHERE order_id = ?;", (order_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def get_fills_by_order(self, order_id: str) -> list[dict[str, object]]:
        """Retrieve all fills associated with an order."""
        if self._db is None:
            raise RuntimeError("Database connection is not initialized.")

        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM fills WHERE order_id = ? ORDER BY timestamp ASC;", (order_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
