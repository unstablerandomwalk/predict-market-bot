"""
scripts/daily_report.py — Fully automated daily summary
Resolves open trades, computes P&L, and writes a clean report to logs/daily_report.md
Run automatically at 8am via cron, or manually: python scripts/daily_report.py
"""

import sqlite3
import requests
import json
import logging
from datetime import datetime, timezone, date

DB_PATH = "data/trades.db"
REPORT_PATH = "logs/daily_report.md"
GAMMA_API = "https://gamma-api.polymarket.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [daily] %(message)s")
log = logging.getLogger(__name__)


# ── 1. RESOLVE OPEN TRADES ────────────────────────────────────────────────────

def check_market(question: str, market_id: str = None) -> dict:
    """Check resolution using last-trade-price on the token ID."""
    try:
        if not market_id:
            return {"resolved": False}

        r = requests.get(
            "https://clob.polymarket.com/last-trade-price",
            params={"token_id": market_id},
            timeout=10
        )
        if r.status_code != 200:
            return {"resolved": False}

        price = float(r.json().get("price", 0.5))

        if price >= 0.99:
            return {"resolved": True, "winning_price": 1.0}
        elif price <= 0.01:
            return {"resolved": True, "winning_price": 0.0}
        else:
            return {"resolved": False}

    except Exception as e:
        return {"resolved": False}


def resolve_open_trades(conn):
    rows = conn.execute("""
        SELECT id, market_id, question, signal, contracts, entry_price
        FROM trades
        WHERE outcome IS NULL OR outcome = '' OR outcome = 'PENDING'
    """).fetchall()

    resolved = 0
    for trade_id, market_id, question, signal, contracts, entry_price in rows:
        result = check_market(question, market_id)
        if not result["resolved"] or result.get("winning_price") is None:
            continue

        wp = result["winning_price"]
        ep = entry_price or 0.50
        contracts = contracts or 1

        if signal == "BUY_YES":
            pnl = round((wp - ep) * contracts, 4)
        elif signal == "BUY_NO":
            pnl = round(((1.0 - wp) - (1.0 - ep)) * contracts, 4)
        else:
            pnl = 0.0

        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
        conn.execute("""
            UPDATE trades SET outcome=?, pnl=?, exit_price=?, exit_time=?
            WHERE id=?
        """, (outcome, pnl, wp, datetime.now(timezone.utc).isoformat(), trade_id))
        conn.commit()
        log.info(f"  Resolved #{trade_id}: {outcome} | P&L ${pnl:+.2f} | {question[:50]}")
        resolved += 1

    return resolved


# ── 2. COMPUTE STATS ──────────────────────────────────────────────────────────

def compute_stats(conn):
    rows = conn.execute("""
        SELECT signal, contracts, entry_price, exit_price, pnl, outcome, question, entry_time
        FROM trades
        WHERE outcome IS NOT NULL AND outcome != 'PENDING'
        ORDER BY entry_time DESC
    """).fetchall()

    if not rows:
        return None

    total = len(rows)
    wins = sum(1 for r in rows if r[5] == "win")
    losses = sum(1 for r in rows if r[5] == "loss")
    total_pnl = sum(r[4] or 0 for r in rows)
    win_pnl = sum(r[4] for r in rows if r[5] == "win" and r[4])
    loss_pnl = sum(abs(r[4]) for r in rows if r[5] == "loss" and r[4])
    profit_factor = round(win_pnl / loss_pnl, 2) if loss_pnl > 0 else float("inf")
    win_rate = round(wins / total * 100, 1) if total > 0 else 0

    open_rows = conn.execute("""
        SELECT question, signal, contracts, entry_price, entry_time
        FROM trades
        WHERE outcome IS NULL OR outcome = '' OR outcome = 'PENDING'
        ORDER BY entry_time DESC
    """).fetchall()

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "profit_factor": profit_factor,
        "resolved_trades": rows,
        "open_trades": open_rows,
    }


# ── 3. WRITE REPORT ───────────────────────────────────────────────────────────

def write_report(stats, newly_resolved):
    today = date.today().strftime("%B %d, %Y")
    now = datetime.now().strftime("%H:%M")
    lines = []

    lines.append(f"# 📊 Bot Daily Report — {today}")
    lines.append(f"*Generated at {now} IST*\n")

    if stats is None:
        lines.append("No resolved trades yet. Still paper trading — check back soon!\n")
    else:
        # Summary
        pnl_emoji = "🟢" if stats["total_pnl"] >= 0 else "🔴"
        lines.append("## Summary")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total Trades | {stats['total']} |")
        lines.append(f"| Win Rate | {stats['win_rate']}% |")
        lines.append(f"| Wins / Losses | {stats['wins']} / {stats['losses']} |")
        lines.append(f"| Total P&L | {pnl_emoji} ${stats['total_pnl']:+.2f} (paper) |")
        lines.append(f"| Profit Factor | {stats['profit_factor']} |")
        lines.append(f"| Newly Resolved Today | {newly_resolved} |\n")

        # Open positions
        lines.append("## ⏳ Open Positions")
        if stats["open_trades"]:
            for q, sig, contracts, ep, et in stats["open_trades"]:
                ep_str = f"${ep:.2f}" if ep else "~$0.50"
                lines.append(f"- **{sig}** {contracts}x @ {ep_str} — {q[:60]}")
        else:
            lines.append("- No open positions")
        lines.append("")

        # Recent resolved trades
        lines.append("## 📋 Resolved Trades (most recent first)")
        for sig, contracts, ep, xp, pnl, outcome, question, et in stats["resolved_trades"][:20]:
            emoji = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➖"
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "$?"
            lines.append(f"{emoji} **{outcome.upper()}** {pnl_str} | {sig} | {question[:55]}")
        lines.append("")

    lines.append("---")
    lines.append("*This report is auto-generated. Paper trading mode — no real money at risk.*")

    import os
    os.makedirs("logs", exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))

    log.info(f"Report saved to {REPORT_PATH}")


# ── 4. MAIN ───────────────────────────────────────────────────────────────────

def run_daily_report():
    log.info("Starting daily report...")
    conn = sqlite3.connect(DB_PATH)

    log.info("Step 1: Resolving open trades...")
    newly_resolved = resolve_open_trades(conn)
    log.info(f"  {newly_resolved} trades resolved")

    log.info("Step 2: Computing stats...")
    stats = compute_stats(conn)

    log.info("Step 3: Writing report...")
    write_report(stats, newly_resolved)

    conn.close()

    if stats:
        log.info(f"\n{'='*50}")
        log.info(f"  Trades: {stats['total']} | Win Rate: {stats['win_rate']}%")
        log.info(f"  P&L: ${stats['total_pnl']:+.2f} | PF: {stats['profit_factor']}")
        log.info(f"  Open positions: {len(stats['open_trades'])}")
        log.info(f"{'='*50}")
    else:
        log.info("No resolved trades yet.")

    log.info(f"Done. View report: cat {REPORT_PATH}")


if __name__ == "__main__":
    run_daily_report()
