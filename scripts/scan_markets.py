"""
scripts/scan_markets.py — Step 1: Find markets worth trading
Filters Kalshi and Polymarket for liquid, mispriced opportunities.
"""

import json
import logging
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.kalshi_client import KalshiClient
from core.polymarket_client import PolymarketClient
from core.database import init_db, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCAN] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE)]
)
logger = logging.getLogger(__name__)

Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
Path("logs").mkdir(exist_ok=True)


def days_until_expiry(expiry_str: str) -> float:
    """Parse ISO date string and return days to expiry."""
    if not expiry_str:
        return 999
    try:
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (expiry - now).total_seconds() / 86400
    except Exception:
        return 999


def flag_anomalies(market: dict) -> list[str]:
    """
    Check for anomalies that suggest potential mispricings.
    Returns list of flag strings.
    """
    flags = []
    price = market.get("current_price", 0.5)
    spread = market.get("spread", 0.1)

    # Wide spread = low liquidity = harder to trade
    if spread > config.MAX_SPREAD:
        flags.append(f"WIDE_SPREAD:{spread:.3f}")

    # Price near extremes = near-certain = less opportunity
    if price < 0.07:
        flags.append("NEAR_ZERO")
    if price > 0.93:
        flags.append("NEAR_CERTAIN")

    # Low volume
    if market.get("volume_24h", 0) < config.MIN_VOLUME_24H:
        flags.append(f"LOW_VOLUME:{market.get('volume_24h', 0)}")

    return flags


def scan_kalshi(client: KalshiClient) -> list[dict]:
    """Scan Kalshi for tradeable markets."""
    logger.info("Scanning Kalshi...")
    raw_markets = client.get_markets(limit=200)
    logger.info(f"  Found {len(raw_markets)} raw Kalshi markets")

    tradeable = []
    for raw in raw_markets:
        try:
            m = client.normalize_market(raw)
        except Exception as e:
            logger.debug(f"  Normalization error: {e}")
            continue

        days = days_until_expiry(m.get("expiry_date", ""))
        volume = m.get("volume_24h", 0)
        price = m.get("current_price", 0.5)
        spread = m.get("spread", 1.0)

        # Hard filters
        if volume < config.MIN_VOLUME_24H:
            continue
        if days > config.MAX_DAYS_TO_EXPIRY or days < 0.1:
            continue
        if spread > config.MAX_SPREAD:
            continue
        if price < config.MIN_PRICE or price > config.MAX_PRICE:
            continue

        m["days_to_expiry"] = round(days, 2)
        m["anomaly_flags"] = flag_anomalies(m)

        # Score: higher = more interesting
        # Favor: high volume, narrow spread, mid-range price, near expiry
        volume_score = min(volume / 5000, 1.0)
        spread_score = 1.0 - (spread / 0.05)
        price_score = 1.0 - abs(price - 0.5) * 2
        urgency_score = max(0, 1.0 - days / 30)
        m["opportunity_score"] = round(
            0.35 * volume_score + 0.25 * spread_score +
            0.25 * price_score + 0.15 * urgency_score, 4
        )
        tradeable.append(m)

    tradeable.sort(key=lambda x: x["opportunity_score"], reverse=True)
    logger.info(f"  {len(tradeable)} Kalshi markets passed filters")
    return tradeable


def scan_polymarket(client: PolymarketClient) -> list[dict]:
    """Scan Polymarket for tradeable markets."""
    logger.info("Scanning Polymarket...")
    raw_markets = client.get_markets(limit=100)
    logger.info(f"  Found {len(raw_markets)} raw Polymarket markets")

    tradeable = []
    for raw in raw_markets:
        try:
            m = client.normalize_market(raw)
        except Exception as e:
            logger.debug(f"  Normalization error: {e}")
            continue

        days = days_until_expiry(m.get("expiry_date", ""))
        volume = m.get("volume_24h", 0)
        price = m.get("current_price", 0.5)
        spread = m.get("spread", 1.0)

        if volume < config.MIN_VOLUME_24H:
            continue
        if days > config.MAX_DAYS_TO_EXPIRY or days < 0.1:
            continue
        if price < config.MIN_PRICE or price > config.MAX_PRICE:
            continue

        m["days_to_expiry"] = round(days, 2)
        m["anomaly_flags"] = flag_anomalies(m)

        volume_score = min(volume / 10000, 1.0)
        spread_score = max(0, 1.0 - (spread / 0.05))
        price_score = 1.0 - abs(price - 0.5) * 2
        urgency_score = max(0, 1.0 - days / 30)
        m["opportunity_score"] = round(
            0.35 * volume_score + 0.25 * spread_score +
            0.25 * price_score + 0.15 * urgency_score, 4
        )
        tradeable.append(m)

    tradeable.sort(key=lambda x: x["opportunity_score"], reverse=True)
    logger.info(f"  {len(tradeable)} Polymarket markets passed filters")
    return tradeable


def run_scan(platforms: list[str] = None) -> list[dict]:
    """Main scan function. Returns ranked list of tradeable markets."""
    if platforms is None:
        platforms = config.ACTIVE_PLATFORMS

    all_markets = []

    if "kalshi" in platforms:
        try:
            k = KalshiClient()
            all_markets += scan_kalshi(k)
        except Exception as e:
            logger.error(f"Kalshi scan failed: {e}")

    if "polymarket" in platforms:
        try:
            p = PolymarketClient()
            all_markets += scan_polymarket(p)
        except Exception as e:
            logger.error(f"Polymarket scan failed: {e}")

    # Merge and re-rank across platforms
    all_markets.sort(key=lambda x: x["opportunity_score"], reverse=True)
    top = all_markets[:30]  # keep top 30

    # Save results
    output = {
        "scanned_at": datetime.utcnow().isoformat(),
        "total_found": len(all_markets),
        "top_markets": top,
    }
    out_path = Path(config.DATA_DIR) / "scan_results.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"Scan complete: {len(all_markets)} total, {len(top)} shortlisted → {out_path}")

    # Log to DB
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO scan_log (scanned_at, platform, markets_found, markets_flagged, top_markets)
                VALUES (?,?,?,?,?)
            """, (
                output["scanned_at"],
                ",".join(platforms),
                len(all_markets),
                len(top),
                json.dumps([m["market_id"] for m in top])
            ))
    except Exception:
        pass

    return top


if __name__ == "__main__":
    init_db()
    markets = run_scan()
    print(f"\n{'='*60}")
    print(f"TOP MARKETS ({len(markets)} found)")
    print(f"{'='*60}")
    for i, m in enumerate(markets[:10], 1):
        print(f"{i:2d}. [{m['platform'].upper():10s}] {m['question'][:55]}")
        print(f"     Price: {m['current_price']:.2f} | Vol: {m['volume_24h']:>6.0f} | "
              f"Spread: {m['spread']:.3f} | Expires: {m['days_to_expiry']:.1f}d | "
              f"Score: {m['opportunity_score']:.3f}")
        if m["anomaly_flags"]:
            print(f"     ⚑  Flags: {', '.join(m['anomaly_flags'])}")
        print()
