"""
scripts/compound.py — Step 6: Learn from every trade
Runs nightly. Classifies losses, updates failure log, tracks performance.
"""

import json
import logging
import os
import sys
import csv
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.database import (
    get_conn, get_performance_summary, get_all_predictions_with_outcomes,
    update_prediction_outcome
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [COMPOUND] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE)]
)
logger = logging.getLogger(__name__)


# ── Failure Classification ────────────────────────────────────────────────────

FAILURE_TYPES = {
    "BAD_PREDICTION":   "Model estimated probability was significantly wrong",
    "BAD_TIMING":       "Right direction but market moved before settlement",
    "BAD_EXECUTION":    "Slippage, fill issues, or API problems",
    "EXTERNAL_SHOCK":   "Unpredictable external event changed outcome",
    "CALIBRATION_DRIFT":"Model systematically overconfident or underconfident",
    "LOW_LIQUIDITY":    "Couldn't exit at expected price",
}

def classify_failure(trade: dict) -> str:
    """
    Classify why a trade was lost.
    Heuristic rules — can be overridden manually.
    """
    edge = abs(trade.get("edge", 0))
    pnl = trade.get("pnl", 0)
    p_model = trade.get("p_model", 0.5)
    p_market = trade.get("p_market", 0.5)

    # Large model error
    actual = trade.get("actual_outcome", 0.5)
    if actual is not None:
        model_error = abs(p_model - actual)
        if model_error > 0.25:
            return "BAD_PREDICTION"

    # Small edge = marginal trade
    if edge < 0.06:
        return "BAD_TIMING"

    return "EXTERNAL_SHOCK"


# ── Brier Score ───────────────────────────────────────────────────────────────

def compute_brier_score_full() -> float | None:
    """Compute Brier Score across all resolved predictions in DB."""
    rows = get_all_predictions_with_outcomes()
    if len(rows) < 10:
        return None
    bs = sum((r["p_model"] - r["actual_outcome"]) ** 2 for r in rows) / len(rows)
    return round(bs, 4)


# ── Performance Metrics ───────────────────────────────────────────────────────

def compute_sharpe(pnl_list: list[float]) -> float:
    """
    Simplified Sharpe ratio: mean(pnl) / std(pnl) * sqrt(252).
    Uses daily P&L. Returns 0.0 if insufficient data.
    """
    if len(pnl_list) < 5:
        return 0.0
    import statistics
    mean_pnl = statistics.mean(pnl_list)
    if len(pnl_list) < 2:
        return 0.0
    std_pnl = statistics.stdev(pnl_list)
    if std_pnl == 0:
        return 0.0
    # Annualize: prediction markets don't have standard trading days,
    # but we scale by sqrt(252) for consistency
    return round((mean_pnl / std_pnl) * (252 ** 0.5), 2)


def compute_max_drawdown(pnl_list: list[float]) -> float:
    """Compute max peak-to-trough drawdown from cumulative P&L list."""
    if not pnl_list:
        return 0.0
    cumulative = []
    running = 0
    for p in pnl_list:
        running += p
        cumulative.append(running)

    max_dd = 0.0
    peak = cumulative[0]
    for val in cumulative:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return round(max_dd, 4)


def get_daily_pnl_series() -> list[float]:
    """Get list of daily P&L values from trade log."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT DATE(entry_time) as date, SUM(pnl) as daily_pnl
                FROM trades
                WHERE outcome IN ('WIN', 'LOSS') AND pnl IS NOT NULL
                GROUP BY DATE(entry_time)
                ORDER BY DATE(entry_time)
            """).fetchall()
            return [r["daily_pnl"] for r in rows]
    except Exception:
        return []


# ── Failure Log ───────────────────────────────────────────────────────────────

def append_to_failure_log(trade: dict, failure_type: str, lesson: str):
    """Append a loss analysis to references/failure_log.md"""
    log_path = Path(config.REFS_DIR) / "failure_log.md"
    log_path.parent.mkdir(exist_ok=True)

    entry = f"""
## Loss: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}

**Market:** {trade.get('question', 'Unknown')[:80]}
**Platform:** {trade.get('platform', '?')} | **Market ID:** {trade.get('market_id', '?')}
**Signal:** {trade.get('signal', '?')} | **P&L:** ${trade.get('pnl', 0):.2f}
**Model prob:** {trade.get('p_model', '?')} | **Market prob:** {trade.get('p_market', '?')}
**Edge at entry:** {trade.get('edge', '?')}

**Failure Type:** `{failure_type}` — {FAILURE_TYPES.get(failure_type, '')}

**Lesson:** {lesson}

---
"""
    with open(log_path, "a") as f:
        f.write(entry)

    logger.info(f"Failure logged: {failure_type} — {lesson[:60]}")


def generate_lesson(trade: dict, failure_type: str) -> str:
    """Use Claude to generate a lesson from a losing trade."""
    if not config.ANTHROPIC_API_KEY:
        return f"Failure type: {failure_type}. Review trade manually."

    prompt = f"""A prediction market trade resulted in a loss. Analyze it and provide ONE concrete lesson
    for future trades (max 2 sentences).

Trade details:
- Question: {trade.get('question', '')}
- Signal: {trade.get('signal', '')}
- Model probability: {trade.get('p_model', '')}
- Market probability at entry: {trade.get('p_market', '')}
- Edge: {trade.get('edge', '')}
- P&L: ${trade.get('pnl', 0):.2f}
- Failure type: {failure_type}

Respond with ONLY the lesson. No preamble."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",  # cheap model for this
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Lesson generation failed: {e}")
        return f"Review {failure_type} pattern in similar markets."


# ── Main Compound Function ────────────────────────────────────────────────────

def run_compound() -> dict:
    """
    Nightly consolidation job.
    1. Classify all unclassified losses
    2. Update Brier scores
    3. Compute performance metrics
    4. Save performance summary
    5. Return performance dict
    """
    logger.info("Running nightly compound/learning job...")

    # 1. Get all losing trades without failure classification
    with get_conn() as conn:
        losses = conn.execute("""
            SELECT * FROM trades
            WHERE outcome='LOSS' AND (failure_type IS NULL OR failure_type='')
        """).fetchall()
        losses = [dict(r) for r in losses]

    logger.info(f"  Found {len(losses)} unclassified losses to analyze")

    for trade in losses:
        failure_type = classify_failure(trade)
        lesson = generate_lesson(trade, failure_type)
        append_to_failure_log(trade, failure_type, lesson)
        with get_conn() as conn:
            conn.execute(
                "UPDATE trades SET failure_type=? WHERE id=?",
                (failure_type, trade["id"])
            )

    # 2. Performance metrics
    summary = get_performance_summary()
    daily_pnl_series = get_daily_pnl_series()
    sharpe = compute_sharpe(daily_pnl_series)
    max_drawdown = compute_max_drawdown(daily_pnl_series)
    brier = compute_brier_score_full()

    total = summary.get("total", 0)
    wins = summary.get("wins", 0)
    win_rate = summary.get("win_rate", 0)
    profit_factor = 0.0

    # Profit factor: gross profit / gross loss
    with get_conn() as conn:
        pf_row = conn.execute("""
            SELECT
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as gross_loss
            FROM trades WHERE outcome != 'PENDING'
        """).fetchone()
        if pf_row and pf_row["gross_loss"] and pf_row["gross_loss"] > 0:
            profit_factor = round(pf_row["gross_profit"] / pf_row["gross_loss"], 2)

    performance = {
        "date": datetime.utcnow().date().isoformat(),
        "total_trades": total,
        "win_rate": round(win_rate, 4),
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "profit_factor": profit_factor,
        "brier_score": brier,
        "total_pnl": summary.get("total_pnl", 0),
        "best_trade": summary.get("best_trade", 0),
        "worst_trade": summary.get("worst_trade", 0),
        "computed_at": datetime.utcnow().isoformat(),
    }

    # 3. Health checks
    alerts = []
    if total >= 30 and win_rate < 0.60:
        alerts.append(f"⚠️  Win rate {win_rate:.1%} < 60% target — consider raising edge threshold")
    if sharpe < 2.0 and total >= 20:
        alerts.append(f"⚠️  Sharpe {sharpe:.2f} < 2.0 — review position sizing")
    if max_drawdown > config.MAX_DRAWDOWN_PCT:
        alerts.append(f"🔴 Drawdown {max_drawdown:.1%} > {config.MAX_DRAWDOWN_PCT:.1%} limit — PAUSE TRADING")
    if brier and brier > config.BRIER_THRESHOLD:
        alerts.append(f"⚠️  Brier Score {brier:.3f} > {config.BRIER_THRESHOLD} — model miscalibrated")
    if profit_factor < 1.5 and total >= 30:
        alerts.append(f"⚠️  Profit factor {profit_factor:.2f} < 1.5 — review strategy")

    performance["alerts"] = alerts

    # 4. Save
    out_path = Path(config.DATA_DIR) / "performance.json"
    out_path.write_text(json.dumps(performance, indent=2))

    logger.info(f"\n{'='*50}")
    logger.info("PERFORMANCE SUMMARY")
    logger.info(f"{'='*50}")
    logger.info(f"Trades: {total} | Win Rate: {win_rate:.1%} | Sharpe: {sharpe:.2f}")
    logger.info(f"Max DD: {max_drawdown:.1%} | Profit Factor: {profit_factor:.2f}")
    if brier:
        logger.info(f"Brier Score: {brier:.4f} ({'✓ calibrated' if brier < 0.25 else '⚠ miscalibrated'})")
    logger.info(f"Total P&L: ${performance['total_pnl']:+.2f}")
    for alert in alerts:
        logger.warning(alert)

    return performance


if __name__ == "__main__":
    perf = run_compound()
    print("\nPerformance summary saved to data/performance.json")
    print(json.dumps(perf, indent=2))
