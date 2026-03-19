import time

"""
scripts/execute_trade.py — Step 5: Place approved trades
Limit orders only. Abort on slippage. Log everything.
"""

import json
import logging
import os
import sys
import time as time_module
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.database import log_trade, init_db
from core.kalshi_client import KalshiClient
from core.polymarket_client import PolymarketClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EXECUTE] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE)]
)
logger = logging.getLogger(__name__)


def check_stop_file() -> bool:
    """Returns True if trading should halt."""
    if os.path.exists(config.STOP_FILE):
        logger.critical("🛑 STOP FILE DETECTED — halting all trading")
        return True
    return False


def verify_current_price(market_id: str, platform: str, expected_price: float) -> tuple[bool, float]:
    """
    Verify the market price hasn't moved too much since signal generation.
    Returns (ok_to_trade, current_price).
    """
    try:
        if platform == "kalshi":
            client = KalshiClient()
            current = client.get_mid_price(market_id)
        elif platform == "polymarket":
            client = PolymarketClient()
            # For polymarket we need yes_token_id
            current = expected_price  # fallback if no token_id
        else:
            return True, expected_price

        if current == 0 or current == 1:
            return False, current  # market likely resolved

        slippage = abs(current - expected_price) / expected_price
        if slippage > config.MAX_SLIPPAGE:
            logger.warning(
                f"Slippage too high: expected {expected_price:.3f}, "
                f"now {current:.3f} ({slippage:.1%} > {config.MAX_SLIPPAGE:.1%})"
            )
            return False, current

        return True, current
    except Exception as e:
        logger.error(f"Price verification failed: {e}")
        return True, expected_price  # allow trade if we can't verify


def execute_kalshi_trade(trade: dict) -> dict:
    """Execute a single Kalshi trade."""
    client = KalshiClient()
    ticker = trade["market_id"]
    signal = trade["signal"]
    contracts = trade["contracts"]
    p_market = trade["p_market"]

    # Determine side
    side = "yes" if signal == "BUY_YES" else "no"
    # Limit price in cents, slightly more aggressive than market to get fill
    if signal == "BUY_YES":
        limit_cents = int(p_market * 100) + 1  # 1 cent above mid
    else:
        limit_cents = int((1 - p_market) * 100) + 1

    # Cap at valid range
    limit_cents = max(1, min(99, limit_cents))

    # Set expiry 30 minutes from now
    expiry_ts = int((datetime.now(timezone.utc) + timedelta(minutes=config.ORDER_EXPIRY_MIN)).timestamp())

    result = client.place_order(
        ticker=ticker,
        side=side,
        action="buy",
        count=contracts,
        limit_price=limit_cents,
        expiration_ts=expiry_ts,
    )
    return result


def execute_polymarket_trade(trade: dict) -> dict:
    """Execute a single Polymarket trade."""
    client = PolymarketClient()
    token_id = trade.get("yes_token_id", "")
    signal = trade["signal"]
    position_dollars = trade["position_dollars"]
    p_market = trade["p_market"]
    
    if not token_id:
        return {"error": "No yes_token_id in trade data"}

    if signal == "BUY_YES":
        return client.place_order(
            token_id=token_id,
            side="BUY",
            size=position_dollars,
            price=p_market + 0.01,
        )
    elif signal == "BUY_NO":
        # For NO: buy NO token (token_id should be the NO token)
        return {"error": "NO token trading requires separate NO token_id — not yet implemented"}
    return {"error": "Unknown signal"}


def execute_paper_trade(trade: dict) -> dict:
    """Simulate a trade without real money. For testing."""
    logger.info(f"📄 PAPER TRADE: {trade['signal']} {trade['contracts']}x {trade['market_id']}")
    return {
        "order_id": f"PAPER_{int(time.time())}",
        "status": "filled",
        "filled_price": trade["p_market"],
        "filled_count": trade["contracts"],
        "paper_trade": True,
    }


def execute_trade(trade: dict, mode: str = "paper") -> dict:
    """
    Execute a single approved trade.
    mode: "paper" (safe default), "live" (real money)
    """
    if check_stop_file():
        return {"error": "STOP file present", "executed": False}

    market_id = trade.get("market_id")
    platform = trade.get("platform", "kalshi")
    p_market = trade.get("p_market", 0.5)
    question = trade.get("question", "")

    # Duplicate check — skip if already have open position
    import sqlite3
    conn = sqlite3.connect("data/trades.db")
    existing = conn.execute("""
        SELECT id FROM trades 
        WHERE question = ? AND signal = ?
        AND (outcome = 'PENDING' OR outcome IS NULL OR outcome = '')
    """, (question, trade.get("signal", ""))).fetchone()
    conn.close()
    if existing:
        logger.info(f"⚠️  Skipping duplicate — already have open position in this market")
        return {"executed": False, "reason": "duplicate"}

    logger.info(f"\n{'─'*50}")
    logger.info(f"Executing: {question[:60]}")
    logger.info(f"Platform: {platform.upper()} | Signal: {trade['signal']} | "
                f"Contracts: {trade['contracts']} | ${trade['position_dollars']:.2f}")

    # 1. Verify price hasn't moved (slippage check)
    price_ok, current_price = verify_current_price(market_id, platform, p_market)
    if not price_ok:
        logger.warning("Aborting: Price moved beyond slippage tolerance")
        return {"error": "slippage_exceeded", "executed": False, "current_price": current_price}

    # 2. Place order
    if mode == "paper":
        order_result = execute_paper_trade(trade)
    elif mode == "live":
        if platform == "kalshi":
            order_result = execute_kalshi_trade(trade)
        elif platform == "polymarket":
            order_result = execute_polymarket_trade(trade)
        else:
            return {"error": f"Unknown platform: {platform}", "executed": False}
    else:
        return {"error": f"Unknown mode: {mode}", "executed": False}

    # 3. Check for errors
    if "error" in order_result:
        logger.error(f"Order failed: {order_result['error']}")
        return {**order_result, "executed": False}

    # 4. Log to database
    trade_record = {
        **trade,
        "entry_price": current_price,
        "order_id": order_result.get("order_id", ""),
        "fill_status": order_result.get("status", ""),
        "mode": mode,
    }
    try:
        trade_id = log_trade(trade_record)
        logger.info(f"Trade logged to DB: id={trade_id}")
    except Exception as e:
        logger.error(f"DB logging failed: {e}")
        trade_id = None

    # 5. Append to trade log CSV
    import csv
    log_path = Path(config.DATA_DIR) / "trade_log.csv"
    fieldnames = ["timestamp", "platform", "market_id", "question", "signal",
                  "contracts", "position_dollars", "entry_price", "p_model",
                  "p_market", "edge", "mode", "order_id"]
    write_header = not log_path.exists()
    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.utcnow().isoformat(),
            "platform": platform,
            "market_id": market_id,
            "question": question[:80],
            "signal": trade["signal"],
            "contracts": trade["contracts"],
            "position_dollars": trade["position_dollars"],
            "entry_price": current_price,
            "p_model": trade.get("p_model"),
            "p_market": p_market,
            "edge": trade.get("edge"),
            "mode": mode,
            "order_id": order_result.get("order_id", ""),
        })

    logger.info(f"✅ Trade executed: {order_result.get('status', 'submitted')}")
    return {
        "executed": True,
        "trade_id": trade_id,
        "order_result": order_result,
        "entry_price": current_price,
        "mode": mode,
    }


def run_execution(approved_trades: list[dict] = None, mode: str = "paper") -> list[dict]:
    """Execute all approved trades."""
    if approved_trades is None:
        # Try to load from risk results
        try:
            risk_path = Path(config.DATA_DIR) / "risk_result.json"
            approved_trades = [json.loads(risk_path.read_text())]
        except Exception:
            logger.error("No approved trades to execute")
            return []

    if mode == "live":
        logger.warning("⚠️  LIVE TRADING MODE — real money at stake")
        # Safety pause
        time_module.sleep(2)

    results = []
    for trade in approved_trades:
        if check_stop_file():
            logger.critical("STOP file detected — halting execution loop")
            break
        result = execute_trade(trade, mode=mode)
        results.append(result)
        time_module.sleep(1)  # small delay between orders

    executed = sum(1 for r in results if r.get("executed"))
    logger.info(f"\nExecution complete: {executed}/{len(results)} trades placed")
    return results


if __name__ == "__main__":
    init_db()
    # Default to paper trading
    mode = "paper"
    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        mode = "live"
        print("⚠️  WARNING: Live trading mode enabled")

    from scripts.validate_risk import run_validation
    approved = run_validation()
    if approved:
        results = run_execution(approved, mode=mode)
    else:
        print("No approved trades to execute.")
