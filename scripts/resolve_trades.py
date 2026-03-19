"""
scripts/resolve_trades.py — Auto-resolver
Checks Polymarket for settled markets, updates trade outcomes and P&L in DB.
Run daily: python run.py --resolve  (or directly: python scripts/resolve_trades.py)
"""

import sqlite3
import requests
import json
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [resolver] %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "data/trades.db"
GAMMA_API = "https://gamma-api.polymarket.com"


def get_open_trades(conn):
    """Fetch all trades that haven't been resolved yet."""
    rows = conn.execute("""
        SELECT id, market_id, question, signal, contracts, entry_price
        FROM trades
        WHERE outcome IS NULL OR outcome = '' OR outcome = 'PENDING'
        ORDER BY entry_time ASC
    """).fetchall()
    return rows


def check_market_resolution(market_id: str) -> dict:
    """
    Query Polymarket Gamma API for a market's resolution status.
    Returns dict with resolved, outcome, winning_price.
    """
    try:
        # Try by condition_id / token_id format
        url = f"{GAMMA_API}/markets/{market_id}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            resolved = data.get("resolved", False) or data.get("closed", False)
            if resolved:
                # Determine winning outcome from resolution data
                resolution = data.get("resolution", "")
                winning_price = None
                if resolution == "yes" or resolution == "1":
                    winning_price = 1.0
                elif resolution == "no" or resolution == "0":
                    winning_price = 0.0
                return {
                    "resolved": True,
                    "resolution": resolution,
                    "winning_price": winning_price,
                    "question": data.get("question", ""),
                }
            return {"resolved": False}

        # Fallback: search by question via events endpoint
        return {"resolved": False}

    except Exception as e:
        log.warning(f"API error for {market_id}: {e}")
        return {"resolved": False}


def check_market_by_question(question: str) -> dict:
    """Search for a market by question text and check resolution."""
    try:
        params = {"q": question[:80], "limit": 5}
        r = requests.get(f"{GAMMA_API}/markets", params=params, timeout=10)
        if r.status_code != 200:
            return {"resolved": False}

        markets = r.json()
        if not markets:
            return {"resolved": False}

        for m in markets:
            # Match on question similarity
            if any(word in m.get("question", "").lower()
                   for word in question.lower().split()[:4]):
                resolved = m.get("resolved", False) or m.get("closed", False)
                if resolved:
                    resolution = m.get("resolution", "")
                    winning_price = None
                    if resolution in ("yes", "1", "YES"):
                        winning_price = 1.0
                    elif resolution in ("no", "0", "NO"):
                        winning_price = 0.0
                    return {
                        "resolved": True,
                        "resolution": resolution,
                        "winning_price": winning_price,
                        "question": m.get("question", ""),
                    }
        return {"resolved": False}

    except Exception as e:
        log.warning(f"Search error for '{question[:40]}': {e}")
        return {"resolved": False}


def calculate_pnl(signal: str, contracts: int, entry_price: float,
                  winning_price: float) -> float:
    """
    Calculate P&L for a resolved trade.
    BUY_YES: profit if winning_price=1.0, loss if 0.0
    BUY_NO:  profit if winning_price=0.0, loss if 1.0
    Each contract costs entry_price, pays out winning_price.
    """
    if entry_price is None:
        entry_price = 0.50  # default assumption

    if signal == "BUY_YES":
        # Paid entry_price per contract, received winning_price
        pnl_per_contract = winning_price - entry_price
    elif signal == "BUY_NO":
        # Bought NO = bought (1 - YES), so profit if YES = 0
        pnl_per_contract = (1.0 - winning_price) - (1.0 - entry_price)
    else:
        pnl_per_contract = 0.0

    return round(pnl_per_contract * contracts, 4)


def resolve_trades():
    """Main resolution loop."""
    conn = sqlite3.connect(DB_PATH)
    trades = get_open_trades(conn)

    if not trades:
        log.info("No open trades to resolve.")
        conn.close()
        return {"resolved": 0, "still_open": 0, "errors": 0}

    log.info(f"Checking {len(trades)} open trades for resolution...")

    resolved_count = 0
    still_open = 0
    errors = 0

    for trade_id, market_id, question, signal, contracts, entry_price in trades:
        log.info(f"  Checking #{trade_id}: {question[:55]}...")

        # Try by market_id first, then by question search
        result = check_market_resolution(market_id)
        if not result["resolved"]:
            result = check_market_by_question(question)

        if result["resolved"] and result.get("winning_price") is not None:
            winning_price = result["winning_price"]
            pnl = calculate_pnl(signal, contracts or 1,
                                 entry_price or 0.50, winning_price)
            outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

            conn.execute("""
                UPDATE trades
                SET outcome = ?, pnl = ?, exit_price = ?, exit_time = ?
                WHERE id = ?
            """, (outcome, pnl, winning_price,
                  datetime.now(timezone.utc).isoformat(), trade_id))
            conn.commit()

            emoji = "✅" if outcome == "win" else "❌"
            log.info(f"    {emoji} Resolved: {outcome.upper()} | P&L: ${pnl:+.2f}")
            resolved_count += 1

        elif result["resolved"] and result.get("winning_price") is None:
            log.info(f"    ⚠️  Resolved but outcome unclear — skipping")
            errors += 1
        else:
            log.info(f"    ⏳ Still open")
            still_open += 1

    # Print summary
    total_pnl = sum(
        r[0] for r in conn.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL"
        ).fetchall()
    )

    log.info("")
    log.info("=" * 50)
    log.info(f"RESOLUTION SUMMARY")
    log.info(f"  Newly resolved : {resolved_count}")
    log.info(f"  Still open     : {still_open}")
    log.info(f"  Errors         : {errors}")
    log.info(f"  Total P&L so far: ${total_pnl:+.2f}")
    log.info("=" * 50)

    conn.close()
    return {
        "resolved": resolved_count,
        "still_open": still_open,
        "errors": errors,
        "total_pnl": total_pnl,
    }


if __name__ == "__main__":
    resolve_trades()
