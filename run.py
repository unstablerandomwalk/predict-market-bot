"""
run.py — Main entry point for predict-market-bot
Orchestrates the full pipeline: Scan → Research → Predict → Risk → Execute → Compound

Usage:
  python3 run.py                          # paper trade, all platforms
  python3 run.py --mode paper             # explicit paper trade
  python3 run.py --mode live              # REAL MONEY — be careful
  python3 run.py --mode scan              # scan only, no trades
  python3 run.py --platforms kalshi       # single platform
  python3 run.py --mode paper --once      # run once and exit
  python3 run.py --compound               # run nightly learning job only
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from core.database import init_db

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE),
    ]
)
logger = logging.getLogger("main")

Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
Path("logs").mkdir(exist_ok=True)
Path(config.REFS_DIR).mkdir(exist_ok=True)


BANNER = """
╔══════════════════════════════════════════════════════════╗
║          PREDICT MARKET BOT — by Claude                  ║
║          Kalshi + Polymarket | AI-powered trading        ║
╚══════════════════════════════════════════════════════════╝
"""


def is_active_hours() -> bool:
    """Only trade during configured active hours."""
    hour = datetime.now().hour
    return config.ACTIVE_HOURS_START <= hour <= config.ACTIVE_HOURS_END


def run_pipeline(mode: str = "paper", platforms: list[str] = None) -> dict:
    """
    Run the full 5-step trading pipeline once.
    Returns summary of what happened.
    """
    if os.path.exists(config.STOP_FILE):
        logger.critical("🛑 STOP FILE EXISTS — pipeline halted")
        return {"status": "halted", "reason": "STOP file present"}

    if not is_active_hours() and mode == "live":
        logger.info("Outside active trading hours — skipping")
        return {"status": "skipped", "reason": "outside active hours"}

    if platforms:
        os.environ["PLATFORMS"] = ",".join(platforms)

    start_time = datetime.utcnow()
    logger.info(f"\n{'━'*60}")
    logger.info(f"Pipeline run starting | Mode: {mode.upper()} | {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"{'━'*60}")

    result = {
        "started_at": start_time.isoformat(),
        "mode": mode,
        "steps_completed": [],
    }

    # ── STEP 1: SCAN ─────────────────────────────────────────────────────────
    try:
        logger.info("\n📡 STEP 1: SCANNING MARKETS...")
        from scripts.scan_markets import run_scan
        markets = run_scan(platforms or config.ACTIVE_PLATFORMS)
        result["markets_found"] = len(markets)
        result["steps_completed"].append("scan")
        logger.info(f"Scan: {len(markets)} markets shortlisted")
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        result["error"] = f"scan: {e}"
        return result

    if not markets:
        logger.info("No markets found — pipeline ending early")
        return {**result, "status": "no_markets"}

    if mode == "scan":
        logger.info("Scan-only mode — stopping here")
        return {**result, "status": "scan_complete"}

    # ── STEP 2: RESEARCH ─────────────────────────────────────────────────────
    try:
        logger.info("\n🔍 STEP 2: RESEARCHING MARKETS...")
        from scripts.research import run_research
        research = run_research()
        result["markets_researched"] = len(research)
        result["steps_completed"].append("research")
    except Exception as e:
        logger.error(f"Research failed: {e}")
        result["error"] = f"research: {e}"
        return result

    # ── STEP 3: PREDICT ───────────────────────────────────────────────────────
    try:
        logger.info("\n🧠 STEP 3: PREDICTING PROBABILITIES...")
        from scripts.predict import run_predictions
        predictions = run_predictions()
        signals = [p for p in predictions if p.get("signal") != "NO_TRADE"]
        result["predictions_made"] = len(predictions)
        result["trade_signals"] = len(signals)
        result["steps_completed"].append("predict")
        logger.info(f"Prediction: {len(signals)} trade signals from {len(predictions)} markets")
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        result["error"] = f"predict: {e}"
        return result

    if not signals:
        logger.info("No trade signals generated — pipeline ending")
        return {**result, "status": "no_signals"}

    # ── STEP 4: RISK VALIDATION ───────────────────────────────────────────────
    try:
        logger.info("\n🛡️  STEP 4: RISK VALIDATION...")
        from scripts.validate_risk import run_validation
        approved = run_validation()
        result["approved_trades"] = len(approved)
        result["steps_completed"].append("risk")
        logger.info(f"Risk: {len(approved)} trades approved")
    except Exception as e:
        logger.error(f"Risk validation failed: {e}")
        result["error"] = f"risk: {e}"
        return result

    if not approved:
        logger.info("No trades approved by risk validator — pipeline ending")
        return {**result, "status": "none_approved"}

    # ── STEP 5: EXECUTE ───────────────────────────────────────────────────────
    try:
        logger.info(f"\n💰 STEP 5: EXECUTING TRADES (mode={mode.upper()})...")
        from scripts.execute_trade import run_execution
        execution_results = run_execution(approved, mode=mode)
        executed = sum(1 for r in execution_results if r.get("executed"))
        result["trades_executed"] = executed
        result["steps_completed"].append("execute")
        logger.info(f"Execution: {executed}/{len(approved)} trades placed")
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        result["error"] = f"execute: {e}"
        return result

    elapsed = (datetime.utcnow() - start_time).total_seconds()
    result["status"] = "complete"
    result["elapsed_seconds"] = round(elapsed, 1)
    result["completed_at"] = datetime.utcnow().isoformat()

    logger.info(f"\n✅ Pipeline complete in {elapsed:.1f}s")
    logger.info(f"Summary: {result['markets_found']} markets → "
                f"{result.get('trade_signals', 0)} signals → "
                f"{result.get('approved_trades', 0)} approved → "
                f"{result.get('trades_executed', 0)} executed")

    return result


def run_forever(mode: str = "paper", platforms: list[str] = None, interval_minutes: int = None):
    """Run the pipeline on a schedule."""
    if interval_minutes is None:
        interval_minutes = config.SCAN_INTERVAL_MIN

    print(BANNER)
    print(f"  Mode: {mode.upper()}")
    print(f"  Platforms: {platforms or config.ACTIVE_PLATFORMS}")
    print(f"  Interval: every {interval_minutes} minutes")
    print(f"  Active hours: {config.ACTIVE_HOURS_START}:00 — {config.ACTIVE_HOURS_END}:00")
    print(f"  Emergency stop: touch {config.STOP_FILE}")
    print()

    init_db()

    def job():
        run_pipeline(mode=mode, platforms=platforms)

    def compound_job():
        from scripts.compound import run_compound
        run_compound()

    # Schedule pipeline
    schedule.every(interval_minutes).minutes.do(job)

    # Schedule nightly compound job at midnight
    schedule.every().day.at("00:00").do(compound_job)

    logger.info(f"Scheduler started. Pipeline will run every {interval_minutes} minutes.")

    # Run immediately on start
    job()

    while True:
        if os.path.exists(config.STOP_FILE):
            logger.critical("🛑 STOP FILE DETECTED — exiting")
            break
        schedule.run_pending()
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description="Predict Market Bot")
    parser.add_argument("--mode", choices=["paper", "live", "scan"], default="paper")
    parser.add_argument("--platforms", type=str, default=None,
                        help="Comma-separated: kalshi,polymarket")
    parser.add_argument("--once", action="store_true", help="Run pipeline once and exit")
    parser.add_argument("--compound", action="store_true", help="Run learning job and exit")
    parser.add_argument("--resolve", action="store_true", help="Resolve open trades and update P&L")
    parser.add_argument("--interval", type=int, default=None, help="Minutes between scans")
    parser.add_argument("--test", action="store_true", help="Run component tests")
    args = parser.parse_args()

    init_db()

    platforms = args.platforms.split(",") if args.platforms else None

    if args.test:
        run_tests()
        return

    if args.compound:
        from scripts.compound import run_compound
        perf = run_compound()
        print(json.dumps(perf, indent=2))
        return
    if args.resolve:
        from scripts.resolve_trades import resolve_trades
        resolve_trades()
        return
    if args.once:
        result = run_pipeline(mode=args.mode, platforms=platforms)
        print(json.dumps(result, indent=2, default=str))
        return

    # Run forever
    if args.mode == "live":
        print("\n⚠️  WARNING: LIVE TRADING MODE")
        print("Real money will be used. Make sure you have:")
        print("  1. Tested in paper mode for at least 2 weeks")
        print("  2. Verified your API keys work")
        print("  3. Set BANKROLL correctly in .env")
        print("  4. Set KALSHI_DEMO=false in .env")
        confirm = input("\nType 'YES I UNDERSTAND' to continue: ")
        if confirm != "YES I UNDERSTAND":
            print("Aborted.")
            return

    run_forever(mode=args.mode, platforms=platforms, interval_minutes=args.interval)


def run_tests():
    """Quick sanity checks for each component."""
    print("Running component tests...\n")
    errors = []

    # Test Kelly sizing
    print("1. Kelly sizing...")
    from scripts.kelly_size import calculate_position_size
    result = calculate_position_size(0.70, 0.55, 1000, "BUY_YES")
    assert result["position_dollars"] > 0, "Kelly should produce positive position"
    assert result["position_pct_bankroll"] <= 0.05, "Should not exceed 5% cap"
    print(f"   ✓ Kelly: ${result['position_dollars']} ({result['contracts']} contracts)")

    # Test risk validator instantiation
    print("2. Risk validator...")
    from scripts.validate_risk import RiskValidator
    v = RiskValidator()
    ok, msg = v.check_stop_file()
    print(f"   ✓ Stop file check: {msg}")

    # Test DB
    print("3. Database...")
    from core.database import get_performance_summary
    summary = get_performance_summary()
    print(f"   ✓ DB: {summary.get('total', 0)} trades on record")

    # Test Kalshi client (read-only)
    print("4. Kalshi client...")
    try:
        from core.kalshi_client import KalshiClient
        k = KalshiClient()
        markets = k.get_markets(limit=5)
        print(f"   ✓ Kalshi: {len(markets)} markets fetched")
    except Exception as e:
        print(f"   ⚠ Kalshi: {e} (check API keys)")

    print("\nAll tests complete.")


if __name__ == "__main__":
    main()
