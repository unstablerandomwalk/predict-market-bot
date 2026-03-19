"""
core/database.py — SQLite persistence layer
Stores all trades, predictions, and performance history.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/trades.db")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id       TEXT NOT NULL,
            platform        TEXT NOT NULL,
            question        TEXT,
            signal          TEXT,           -- BUY_YES / BUY_NO
            p_model         REAL,
            p_market        REAL,
            edge            REAL,
            position_size   REAL,
            entry_price     REAL,
            exit_price      REAL,
            contracts       INTEGER,
            pnl             REAL,
            outcome         TEXT,           -- WIN / LOSS / PENDING / CANCELLED
            failure_type    TEXT,           -- BAD_PREDICTION etc if loss
            entry_time      TEXT,
            exit_time       TEXT,
            notes           TEXT,
            raw_json        TEXT
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id       TEXT NOT NULL,
            platform        TEXT,
            question        TEXT,
            p_claude        REAL,
            p_gpt4o         REAL,
            p_gemini        REAL,
            p_model         REAL,
            p_market        REAL,
            edge            REAL,
            signal          TEXT,
            actual_outcome  REAL,           -- filled in after resolution
            brier_score     REAL,           -- filled in after resolution
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS performance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT UNIQUE,
            trades_total    INTEGER DEFAULT 0,
            trades_won      INTEGER DEFAULT 0,
            pnl_day         REAL DEFAULT 0,
            pnl_cumulative  REAL DEFAULT 0,
            win_rate        REAL DEFAULT 0,
            sharpe          REAL DEFAULT 0,
            max_drawdown    REAL DEFAULT 0,
            brier_score     REAL DEFAULT 0,
            api_spend       REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at      TEXT,
            platform        TEXT,
            markets_found   INTEGER,
            markets_flagged INTEGER,
            top_markets     TEXT            -- JSON array
        );
        """)
    logger.info("Database initialized at %s", DB_PATH)


def log_trade(trade: dict) -> int:
    """Insert a new trade record. Returns row id."""
    with get_conn() as conn:
        c = conn.execute("""
            INSERT INTO trades
            (market_id, platform, question, signal, p_model, p_market, edge,
             position_size, entry_price, contracts, outcome, entry_time, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.get("market_id"), trade.get("platform"), trade.get("question"),
            trade.get("signal"), trade.get("p_model"), trade.get("p_market"),
            trade.get("edge"), trade.get("position_size"), trade.get("entry_price"),
            trade.get("contracts"), "PENDING",
            datetime.utcnow().isoformat(),
            json.dumps(trade)
        ))
        return c.lastrowid


def close_trade(trade_id: int, exit_price: float, pnl: float, outcome: str, failure_type: str = None):
    """Update a trade with exit details."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades SET exit_price=?, pnl=?, outcome=?, failure_type=?, exit_time=?
            WHERE id=?
        """, (exit_price, pnl, outcome, failure_type, datetime.utcnow().isoformat(), trade_id))


def log_prediction(pred: dict) -> int:
    with get_conn() as conn:
        c = conn.execute("""
            INSERT INTO predictions
            (market_id, platform, question, p_claude, p_gpt4o, p_gemini,
             p_model, p_market, edge, signal, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pred.get("market_id"), pred.get("platform"), pred.get("question"),
            pred.get("p_claude"), pred.get("p_gpt4o"), pred.get("p_gemini"),
            pred.get("p_model"), pred.get("p_market"), pred.get("edge"),
            pred.get("signal"), datetime.utcnow().isoformat()
        ))
        return c.lastrowid


def get_open_trades() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE outcome='PENDING'").fetchall()
        return [dict(r) for r in rows]


def get_daily_pnl() -> float:
    today = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(pnl), 0) as total FROM trades
            WHERE DATE(entry_time) = ? AND outcome != 'PENDING'
        """, (today,)).fetchone()
        return row["total"]


def get_all_predictions_with_outcomes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM predictions WHERE actual_outcome IS NOT NULL
        """).fetchall()
        return [dict(r) for r in rows]


def update_prediction_outcome(pred_id: int, actual: float, brier: float):
    with get_conn() as conn:
        conn.execute("""
            UPDATE predictions SET actual_outcome=?, brier_score=? WHERE id=?
        """, (actual, brier, pred_id))


def get_performance_summary() -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(MIN(pnl), 0) as worst_trade,
                COALESCE(MAX(pnl), 0) as best_trade
            FROM trades WHERE outcome != 'PENDING'
        """).fetchone()
        if not row or row["total"] == 0:
            return {"total": 0, "win_rate": 0, "total_pnl": 0}
        return {
            "total": row["total"],
            "wins": row["wins"],
            "win_rate": round(row["wins"] / row["total"], 4) if row["total"] > 0 else 0,
            "total_pnl": round(row["total_pnl"], 2),
            "worst_trade": round(row["worst_trade"], 2),
            "best_trade": round(row["best_trade"], 2),
        }
