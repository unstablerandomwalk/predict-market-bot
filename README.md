# Market Prediction and Trading Bot

An autonomous AI trading bot for [Polymarket](https://polymarket.com) prediction markets. Uses a 3-model AI ensemble (Claude, GPT-4o, Gemini) to identify mispricings, size positions using Kelly Criterion, and execute trades automatically.

> **Status:** Paper trading — live deployment in progress.

---

## How It Works

```
SCAN → RESEARCH → PREDICT → RISK → EXECUTE → COMPOUND
```

1. **Scan** — Fetches 100 live Polymarket markets, filters to the 25 most liquid and tradeable
2. **Research** — Scrapes NewsAPI + Reddit for sentiment signals on each market
3. **Predict** — 3-model AI ensemble estimates true probability vs market price
4. **Risk** — Kelly Criterion sizing, 8 hard-coded risk checks, position limits
5. **Execute** — Places paper/live orders, logs everything to SQLite
6. **Compound** — Nightly learning job classifies losses, adjusts thresholds

Runs 3× daily via cron (9am, 2pm, 8pm IST). Daily P&L reports auto-generated at 8am.

---

## Architecture

```
predict-market-bot/
├── run.py                    # Main orchestrator
├── config.py                 # All settings
├── scripts/
│   ├── scan_markets.py       # Step 1: Filter markets
│   ├── research.py           # Step 2: News + Reddit sentiment
│   ├── predict.py            # Step 3: AI ensemble predictions
│   ├── validate_risk.py      # Step 4: Risk checks + Kelly sizing
│   ├── execute_trade.py      # Step 5: Order placement
│   ├── compound.py           # Step 6: Nightly learning
│   ├── daily_report.py       # Auto P&L reports
│   └── trade_notifier.py     # Mac push notifications for deadlines
├── core/
│   ├── polymarket_client.py  # Polymarket CLOB API wrapper
│   ├── database.py           # SQLite persistence
│   └── kalshi_client.py      # Kalshi REST wrapper
└── data/
    └── trades.db             # Trade log (gitignored)
```

---

## AI Ensemble

| Model | Role | Cost |
|-------|------|------|
| Claude Haiku | Primary — reasoning + geopolitical knowledge | ~$0.001/run |
| GPT-4o-mini | Secondary — consensus check | ~$0.001/run |
| Gemini Flash | Tertiary — tiebreaker | Free |

Each model independently estimates the true probability. The ensemble averages predictions weighted by confidence. A trade signal is generated only when the consensus edge exceeds **4%** vs market price.

---

## Risk Management

- **Max position size:** 5% of bankroll per trade
- **Max open positions:** 35 concurrent
- **Max daily loss:** 15% of bankroll
- **Max drawdown:** 8% before halt
- **Kelly fraction:** 0.25× (quarter-Kelly)
- **Stop file:** `touch STOP` halts all trading immediately
- **Slippage check:** Aborts if price moves >3% since signal

---

## Setup

```bash
git clone https://github.com/unstablerandomwalk/predict-market-bot
cd predict-market-bot
bash setup_mac.sh
```

Copy `.env.example` to `.env` and fill in:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...
GOOGLE_API_KEY=AIzaSy...
NEWS_API_KEY=...
POLY_PRIVATE_KEY=0x...     # MetaMask wallet (for live trading only)
```

---

## Usage

```bash
source .venv/bin/activate

# Paper trade (no real money)
python run.py --mode paper --once

# Scan markets only
python run.py --mode scan --once

# View daily report
python scripts/daily_report.py && cat logs/daily_report.md

# Update resolved trades interactively
python scripts/trade_notifier.py --update

# Emergency stop
touch STOP
```

---

## Paper Trading Results

> Updated as trades resolve. Started March 13, 2026.

| Metric | Value |
|--------|-------|
| Total Resolved Trades | 34 |
| Win Rate | 76.5% |
| Total P&L | +$89.00 |
| Avg Edge (wins) | $4.52 |
| Avg Edge (losses) | $3.56 |

*Live stats after a 2 week trading period.*

---

## Tech Stack

`Python 3.12` `SQLite` `Anthropic Claude API` `OpenAI API` `Google Gemini API` `Polymarket CLOB API` `NewsAPI` `PRAW (Reddit)` `Web3/Ethereum` `Kelly Criterion` `cron`

---

## Disclaimer

This bot is for educational and research purposes. Prediction market trading involves financial risk. Past paper trading performance does not guarantee future live trading results. Use at your own risk.

---

*Built by [@unstablerandomwalk](https://github.com/unstablerandomwalk)*
