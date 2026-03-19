"""
config.py — Central configuration for predict-market-bot
All tunable parameters live here. Edit this file, not the individual scripts.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── API Keys ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")       # optional
GOOGLE_API_KEY      = os.getenv("GOOGLE_API_KEY", "")       # optional
NEWS_API_KEY        = os.getenv("NEWS_API_KEY", "")
REDDIT_CLIENT_ID    = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_SECRET       = os.getenv("REDDIT_CLIENT_SECRET", "")

# ─── Kalshi Credentials ───────────────────────────────────────────────────────
KALSHI_API_KEY      = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_SECRET   = os.getenv("KALSHI_API_SECRET", "")
KALSHI_DEMO         = os.getenv("KALSHI_DEMO", "true").lower() == "true"
KALSHI_BASE_URL     = (
    "https://demo-api.kalshi.co/trade-api/v2"
    if KALSHI_DEMO else
    "https://trading-api.kalshi.com/trade-api/v2"
)

# ─── Polymarket Credentials ───────────────────────────────────────────────────
POLY_PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY", "")     # EIP-712 wallet key
POLY_CLOB_URL       = "https://clob.polymarket.com"
POLY_GAMMA_URL      = "https://gamma-api.polymarket.com"

# ─── Risk Parameters (hard limits) ───────────────────────────────────────────
MAX_POSITION_PCT    = 0.05      # max 5% of bankroll per trade
MAX_OPEN_POSITIONS  = 25        # max concurrent open positions
MAX_DAILY_LOSS_PCT  = 0.15      # stop trading if daily loss > 15%
MAX_DRAWDOWN_PCT    = 0.08      # pause if drawdown > 8%
KELLY_FRACTION      = 0.25      # always quarter-Kelly
MIN_EDGE            = 0.04      # minimum 4% edge to trade
MAX_API_SPEND_DAY   = 50.00     # $50/day AI API cost cap
STOP_FILE           = "STOP"    # create this file to halt all trading

# ─── Scan Parameters ─────────────────────────────────────────────────────────
MIN_VOLUME_24H      = 200       # contracts
MAX_DAYS_TO_EXPIRY  = 30
MAX_SPREAD          = 0.05      # 5 cents
MIN_PRICE           = 0.05      # don't trade near-zero contracts
MAX_PRICE           = 0.95      # don't trade near-certain contracts
PRICE_MOVE_FLAG     = 0.10      # flag if price moved >10% in 2h
VOLUME_SPIKE_MULT   = 2.0       # flag if volume > 2x 7-day avg
SCAN_INTERVAL_MIN   = 20        # scan every 20 minutes

# ─── Prediction Parameters ────────────────────────────────────────────────────
STD_DEV_ASSUMPTION  = 0.08      # assumed std dev for Z-score calc
CONFIDENCE_PENALTY  = 0.20      # penalty when only one model available
BRIER_THRESHOLD     = 0.30      # if Brier Score > this, raise edge threshold

# ─── Execution Parameters ────────────────────────────────────────────────────
MAX_SLIPPAGE        = 0.02      # abort if price moves >2% before fill
ORDER_EXPIRY_MIN    = 30        # GTC orders expire after 30 minutes
HEDGE_TRIGGER_PCT   = 0.03      # reduce position if moves 3% against us

# ─── Bankroll ─────────────────────────────────────────────────────────────────
# Set your starting bankroll here. Update after deposits/withdrawals.
STARTING_BANKROLL   = float(os.getenv("BANKROLL", "500.0"))

# ─── Platforms ───────────────────────────────────────────────────────────────
ACTIVE_PLATFORMS    = os.getenv("PLATFORMS", "polymarket").split(",")  # "kalshi,polymarket"

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL           = "INFO"
LOG_FILE            = "logs/trades.log"
DATA_DIR            = "data"
REFS_DIR            = "references"

# ─── Scheduling ──────────────────────────────────────────────────────────────
ACTIVE_HOURS_START  = 6     # 6am local
ACTIVE_HOURS_END    = 23    # 11pm local
COMPOUND_RUN_HOUR   = 0     # midnight nightly job

# ─── Model Weights (must sum to 1.0) ─────────────────────────────────────────
MODEL_WEIGHTS = {
    "claude":  0.50,    # primary — always available
    "gpt4o":   0.30,    # optional — needs OPENAI_API_KEY
    "gemini":  0.20,    # optional — needs GOOGLE_API_KEY
}
