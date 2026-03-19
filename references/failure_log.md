# Failure Log — Predict Market Bot

Every loss is analyzed here. The scan and research agents read this file
before processing new markets to avoid repeating known mistakes.

## Failure Types
- **BAD_PREDICTION**: Model probability estimate was significantly wrong
- **BAD_TIMING**: Right direction, wrong timing (market moved before settlement)
- **BAD_EXECUTION**: Slippage, API issues, or fill problems
- **EXTERNAL_SHOCK**: Unpredictable external event changed the outcome
- **CALIBRATION_DRIFT**: Model is systematically overconfident or underconfident
- **LOW_LIQUIDITY**: Couldn't enter or exit at expected prices

## Key Lessons (Updated by compound.py)

_No losses yet — start paper trading to populate this log._

---

## How to Use This File

The research agent reads this log before analyzing any market.
Pattern matches are flagged automatically. Key patterns to watch:

1. **Same market category losing repeatedly** → Reduce position size in that category
2. **Losses clustered around specific news events** → Increase edge threshold pre-event
3. **Losses on low-volume markets** → Raise minimum volume filter
4. **Late timing losses** → Only trade markets with > 5 days to expiry
