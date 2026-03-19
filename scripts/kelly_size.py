"""
scripts/kelly_size.py — Deterministic position sizing using Kelly Criterion
This is pure math — no LLM involved. Deterministic and testable.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def kelly_fraction(p_win: float, b_odds: float) -> float:
    """
    Calculate full Kelly fraction.
    
    Args:
        p_win: Probability of winning (0.0 to 1.0)
        b_odds: Net odds (e.g., for a $1 contract paying $1 on win: b=1.0)
                For prediction markets: b = (1 - p_market) / p_market
    
    Returns:
        Full Kelly fraction (proportion of bankroll to bet)
    """
    q = 1.0 - p_win
    if b_odds <= 0:
        return 0.0
    f = (p_win * b_odds - q) / b_odds
    return max(0.0, f)  # never negative


def fractional_kelly(p_win: float, b_odds: float, fraction: float = None) -> float:
    """
    Fractional Kelly (default: quarter-Kelly = 0.25).
    Returns fraction of bankroll to bet.
    """
    if fraction is None:
        fraction = config.KELLY_FRACTION
    f_full = kelly_fraction(p_win, b_odds)
    return f_full * fraction


def calculate_position_size(
    p_model: float,
    p_market: float,
    bankroll: float,
    signal: str,            # "BUY_YES" or "BUY_NO"
    fraction: float = None,
) -> dict:
    """
    Full position size calculation for a prediction market trade.
    
    In prediction markets:
    - A YES contract pays $1 if yes, $0 if no
    - You pay p_market per contract
    - Net profit per contract if win: (1 - p_market)
    - Net loss per contract if lose: p_market
    - So b = (1 - p_market) / p_market
    
    For BUY_NO:
    - You pay (1 - p_market) for NO
    - Win probability = (1 - p_model)
    - b = p_market / (1 - p_market)
    """
    if fraction is None:
        fraction = config.KELLY_FRACTION

    if signal == "BUY_YES":
        p_win = p_model
        cost_per_contract = p_market
        b_odds = (1 - p_market) / p_market if p_market > 0 else 0
    elif signal == "BUY_NO":
        p_win = 1 - p_model
        cost_per_contract = 1 - p_market
        b_odds = p_market / (1 - p_market) if p_market < 1 else 0
    else:
        return {"position_dollars": 0, "contracts": 0, "reason": "NO_TRADE signal"}

    f_full = kelly_fraction(p_win, b_odds)
    f_frac = f_full * fraction

    # Raw dollar amount
    position_dollars = f_frac * bankroll

    # Cap at MAX_POSITION_PCT
    max_allowed = bankroll * config.MAX_POSITION_PCT
    if position_dollars > max_allowed:
        position_dollars = max_allowed
        cap_applied = True
    else:
        cap_applied = False

    # Calculate number of contracts (each costs cost_per_contract dollars)
    contracts = int(position_dollars / cost_per_contract) if cost_per_contract > 0 else 0
    actual_dollars = contracts * cost_per_contract

    return {
        "signal": signal,
        "p_win": round(p_win, 4),
        "b_odds": round(b_odds, 4),
        "kelly_full": round(f_full, 4),
        "kelly_fraction": fraction,
        "kelly_fractional": round(f_frac, 4),
        "position_dollars": round(actual_dollars, 2),
        "position_pct_bankroll": round(actual_dollars / bankroll, 4) if bankroll > 0 else 0,
        "contracts": contracts,
        "cost_per_contract": round(cost_per_contract, 4),
        "max_profit": round(contracts * (1 - cost_per_contract), 2),
        "max_loss": round(actual_dollars, 2),
        "expected_value": round(p_win * contracts * (1 - cost_per_contract) - (1 - p_win) * actual_dollars, 2),
        "cap_applied": cap_applied,
        "bankroll": bankroll,
    }


def get_current_bankroll(platform: str = None) -> float:
    """
    Get current bankroll from live account balance.
    Falls back to config.STARTING_BANKROLL if API unavailable.
    """
    try:
        if platform == "kalshi" or platform is None:
            from core.kalshi_client import KalshiClient
            k = KalshiClient()
            balance = k.get_balance()
            if balance > 0:
                return balance
    except Exception:
        pass
    return config.STARTING_BANKROLL


if __name__ == "__main__":
    # Self-test with example scenarios
    print("Kelly Criterion Position Sizing — Self Test")
    print("=" * 60)

    scenarios = [
        {"p_model": 0.70, "p_market": 0.55, "signal": "BUY_YES", "bankroll": 1000, "label": "Strong edge YES"},
        {"p_model": 0.30, "p_market": 0.50, "signal": "BUY_NO",  "bankroll": 1000, "label": "Strong edge NO"},
        {"p_model": 0.62, "p_market": 0.55, "signal": "BUY_YES", "bankroll": 500,  "label": "Weak edge YES"},
        {"p_model": 0.80, "p_market": 0.70, "signal": "BUY_YES", "bankroll": 10000,"label": "High prob mkt"},
    ]

    for s in scenarios:
        result = calculate_position_size(
            s["p_model"], s["p_market"], s["bankroll"], s["signal"]
        )
        print(f"\n{s['label']}")
        print(f"  p_model={s['p_model']} vs p_market={s['p_market']} | Bankroll=${s['bankroll']}")
        print(f"  Full Kelly: {result['kelly_full']:.3f} ({result['kelly_full']*100:.1f}%)")
        print(f"  Quarter Kelly: {result['kelly_fractional']:.3f} ({result['kelly_fractional']*100:.1f}%)")
        print(f"  Position: ${result['position_dollars']} ({result['contracts']} contracts)")
        print(f"  Max profit: ${result['max_profit']} | Max loss: ${result['max_loss']}")
        print(f"  Expected value: ${result['expected_value']}")
        if result['cap_applied']:
            print(f"  ⚠ Position capped at {config.MAX_POSITION_PCT*100:.0f}% of bankroll")
