# Prediction Market Bot — Formula Reference

## Core Trading Formulas

### Edge (Most Important)
```
edge = p_model - p_market

Only trade when |edge| > 0.04 (4%)
```

### Expected Value
```
EV = p_win × b - (1 - p_win)

where:
  p_win = your estimated probability of winning
  b = net odds = (payout - 1)

For YES contracts:
  b = (1 - p_market) / p_market
  EV = p_model × (1/p_market - 1) - (1 - p_model)

Target: EV > 0 before entering any trade
```

### Mispricing Z-Score
```
delta = (p_model - p_market) / σ

where σ = assumed std deviation (default: 0.08)

Higher delta = stronger signal
delta > 0.5 = solid opportunity
delta > 1.0 = very strong opportunity
```

### Brier Score (Calibration)
```
BS = (1/n) × Σ(p_predicted - outcome)²

where outcome ∈ {0, 1}

Interpretation:
  BS < 0.10 = excellent calibration
  BS < 0.25 = good (target)
  BS > 0.25 = needs review
  BS > 0.33 = no better than always predicting 50%
  BS = 0.50 = random (worst)
```

---

## Kelly Criterion — Position Sizing

### Full Kelly
```
f* = (p × b - q) / b

where:
  p = probability of winning
  q = 1 - p = probability of losing
  b = net odds (dollars won per dollar bet)
  f* = optimal fraction of bankroll to bet

For YES contracts on prediction markets:
  b = (1 - p_market) / p_market
  f* = (p_model × (1 - p_market)/p_market - (1 - p_model)) / ((1 - p_market)/p_market)

Simplified:
  f* = (p_model - p_market) / (1 - p_market)
```

### Fractional Kelly (Always Use This)
```
f_actual = f* × fraction

Recommended fractions:
  Quarter-Kelly: fraction = 0.25 (safest — use by default)
  Half-Kelly:    fraction = 0.50 (for high confidence trades)
  Full-Kelly:    fraction = 1.00 (theoretically optimal, practically ruinous)

Why fractional? Full Kelly maximizes long-run growth but the ride is brutal.
Quarter-Kelly reduces variance by ~75% with only ~10-15% reduction in growth rate.
```

### Example Calculation
```
Scenario: 70% confidence, market at 55%

p_model = 0.70
p_market = 0.55
b = (1 - 0.55) / 0.55 = 0.818

f* = (0.70 × 0.818 - 0.30) / 0.818 = (0.573 - 0.300) / 0.818 = 0.334

Quarter-Kelly: f = 0.334 × 0.25 = 0.083 → bet 8.3% of bankroll

On $1,000 bankroll:
  Position = $83
  Contracts (at $0.55 each) = 150
  Max profit = 150 × $0.45 = $67.50
  Max loss = $83
  Expected value = 0.70 × $67.50 - 0.30 × $83 = $47.25 - $24.90 = $22.35
```

---

## Risk Metrics

### Value at Risk (95% confidence)
```
VaR_95 = position_size × (1 - p_model) × 1.65

Simplified for prediction markets: VaR ≈ position_size × (1 - p_model)
Keep daily VaR below 5% of bankroll
```

### Sharpe Ratio
```
Sharpe = (mean daily P&L) / (std dev daily P&L) × √252

Target: > 2.0
```

### Max Drawdown
```
MaxDD = (peak_value - trough_value) / peak_value

Hard limit: 8% — pause trading if breached
```

### Profit Factor
```
PF = Gross Profit / Gross Loss

Target: > 1.5
PF = 1.0 means breaking even
PF < 1.0 means losing money overall
```

---

## Prediction Market Mechanics

### Contract Payoff
```
YES contract at price P:
  Pay: P
  Win: $1.00 (profit = $1 - P)
  Lose: $0.00 (loss = P)

NO contract at price P_no = (1 - P):
  Pay: 1 - P
  Win: $1.00 (profit = P)
  Lose: $0.00 (loss = 1 - P)
```

### Implied Probability
```
Market price P directly = implied probability of YES

If YES trades at 0.65, market assigns 65% probability to YES outcome
```

### Spread
```
Spread = Ask_YES - Bid_YES

Or equivalently:
Spread = 1 - Best_YES_bid - Best_NO_bid

Target: spread < 0.05 ($0.05) for clean entry/exit
```

---

## Kalshi API — Key Endpoints

```
Base URL (demo): https://demo-api.kalshi.co/trade-api/v2
Base URL (live): https://trading-api.kalshi.com/trade-api/v2

GET  /markets                    → list active markets
GET  /markets/{ticker}           → single market
GET  /markets/{ticker}/orderbook → current orderbook
GET  /portfolio/balance          → account balance (cents)
GET  /portfolio/positions        → open positions
POST /portfolio/orders           → place order
DEL  /portfolio/orders/{id}      → cancel order
```

## Polymarket API — Key Endpoints

```
CLOB: https://clob.polymarket.com
Gamma: https://gamma-api.polymarket.com

GET  /markets          → list markets (Gamma)
GET  /book             → orderbook (CLOB, requires token_id)
GET  /last-trade-price → latest price (CLOB, requires token_id)
POST /order            → place order (requires auth)
```
