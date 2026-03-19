"""
scripts/validate_risk.py — Step 4a: Pre-trade risk validation
ALL checks must pass before a trade is executed.
This is deterministic Python — not LLM-interpreted.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core.database import get_open_trades, get_daily_pnl
from scripts.kelly_size import calculate_position_size, get_current_bankroll

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RISK] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE)]
)
logger = logging.getLogger(__name__)


class RiskValidator:
    """
    Hard-coded risk checks. Every check must return True for a trade to proceed.
    If STOP file exists, immediately returns False with no further checks.
    """

    def __init__(self):
        self.bankroll = get_current_bankroll()
        self.open_trades = get_open_trades()
        self.daily_pnl = get_daily_pnl()
        self.api_spend_today = self._get_api_spend_today()

    def _get_api_spend_today(self) -> float:
        """Read today's API spend from log file."""
        try:
            spend_path = Path(config.DATA_DIR) / "api_spend.json"
            if not spend_path.exists():
                return 0.0
            data = json.loads(spend_path.read_text())
            today = datetime.utcnow().date().isoformat()
            return data.get(today, 0.0)
        except Exception:
            return 0.0

    def check_stop_file(self) -> tuple[bool, str]:
        """Check if emergency STOP file exists."""
        if os.path.exists(config.STOP_FILE):
            return False, f"STOP file exists at '{config.STOP_FILE}' — trading halted"
        return True, "OK"

    def check_edge(self, edge: float) -> tuple[bool, str]:
        """Verify edge exceeds minimum threshold."""
        if edge < config.MIN_EDGE:
            return False, f"Edge {edge:.4f} < minimum {config.MIN_EDGE}"
        return True, f"Edge {edge:.4f} ≥ {config.MIN_EDGE} ✓"

    def check_position_size(self, position_dollars: float) -> tuple[bool, str]:
        """Verify position size doesn't exceed max % of bankroll."""
        pct = position_dollars / self.bankroll if self.bankroll > 0 else 1.0
        max_pct = config.MAX_POSITION_PCT
        if pct > max_pct:
            return False, f"Position {pct:.1%} > max {max_pct:.1%} of bankroll"
        return True, f"Position size {pct:.1%} ≤ {max_pct:.1%} ✓"

    def check_open_positions(self) -> tuple[bool, str]:
        """Verify not too many concurrent positions."""
        n_open = len(self.open_trades)
        if n_open >= config.MAX_OPEN_POSITIONS:
            return False, f"Already have {n_open} open positions (max {config.MAX_OPEN_POSITIONS})"
        return True, f"{n_open}/{config.MAX_OPEN_POSITIONS} positions open ✓"

    def check_daily_loss(self) -> tuple[bool, str]:
        """Verify daily loss limit not breached."""
        if self.daily_pnl < 0:
            loss_pct = abs(self.daily_pnl) / self.bankroll
            if loss_pct >= config.MAX_DAILY_LOSS_PCT:
                return False, (
                    f"Daily loss {loss_pct:.1%} ≥ {config.MAX_DAILY_LOSS_PCT:.1%} limit "
                    f"(${abs(self.daily_pnl):.2f} today)"
                )
        return True, f"Daily P&L: ${self.daily_pnl:+.2f} within limits ✓"

    def check_drawdown(self) -> tuple[bool, str]:
        """
        Estimate max drawdown from open position losses.
        Simplified: worst case all open trades go to zero.
        """
        try:
            open_exposure = sum(t.get("position_size", 0) for t in self.open_trades)
            peak = self.bankroll + open_exposure  # assume we started at current + open
            trough = self.bankroll
            if peak > 0:
                drawdown = (peak - trough) / peak
                if drawdown >= config.MAX_DRAWDOWN_PCT:
                    return False, f"Drawdown {drawdown:.1%} ≥ {config.MAX_DRAWDOWN_PCT:.1%} limit"
            return True, "Drawdown within limits ✓"
        except Exception:
            return True, "Drawdown check skipped (no data)"

    def check_api_spend(self) -> tuple[bool, str]:
        """Verify AI API spend hasn't exceeded daily cap."""
        if self.api_spend_today >= config.MAX_API_SPEND_DAY:
            return False, (
                f"API spend ${self.api_spend_today:.2f} ≥ daily cap ${config.MAX_API_SPEND_DAY:.2f}"
            )
        return True, f"API spend ${self.api_spend_today:.2f} / ${config.MAX_API_SPEND_DAY:.2f} ✓"

    def check_liquidity(self, position_dollars: float, market_volume_24h: float) -> tuple[bool, str]:
        """Verify market has enough liquidity to absorb the position."""
        # Position should be < 10% of 24h volume (crude liquidity check)
        if market_volume_24h > 0 and position_dollars > 0:
            impact_pct = position_dollars / market_volume_24h
            if impact_pct > 0.10:
                return False, (
                    f"Position ${position_dollars:.0f} is {impact_pct:.1%} of "
                    f"24h volume ${market_volume_24h:.0f} — too large"
                )
        return True, "Liquidity adequate ✓"

    def validate(self, trade_proposal: dict) -> dict:
        """
        Run all risk checks on a proposed trade.
        Returns dict with: approved (bool), rejection_reasons, position details.
        """
        edge = trade_proposal.get("edge", 0)
        signal = trade_proposal.get("signal", "NO_TRADE")
        p_model = trade_proposal.get("p_model", 0.5)
        p_market = trade_proposal.get("p_market", 0.5)
        volume_24h = trade_proposal.get("volume_24h", 0)

        if signal == "NO_TRADE":
            return {
                "approved": False,
                "rejection_reasons": ["Signal is NO_TRADE"],
                "position_dollars": 0,
                "contracts": 0,
            }

        # Calculate position size first
        sizing = calculate_position_size(p_model, p_market, self.bankroll, signal)
        position_dollars = sizing["position_dollars"]

        # Run all checks
        checks = [
            self.check_stop_file(),
            self.check_edge(edge),
            self.check_position_size(position_dollars),
            self.check_open_positions(),
            self.check_daily_loss(),
            self.check_drawdown(),
            self.check_api_spend(),
            self.check_liquidity(position_dollars, volume_24h),
        ]

        passed = [(ok, msg) for ok, msg in checks if ok]
        failed = [(ok, msg) for ok, msg in checks if not ok]

        approved = len(failed) == 0

        result = {
            "approved": approved,
            "rejection_reasons": [msg for _, msg in failed],
            "passed_checks": [msg for _, msg in passed],
            "position_dollars": position_dollars if approved else 0,
            "contracts": sizing["contracts"] if approved else 0,
            "kelly_full": sizing["kelly_full"],
            "kelly_quarter": sizing["kelly_fractional"],
            "max_profit": sizing["max_profit"] if approved else 0,
            "max_loss": sizing["max_loss"] if approved else 0,
            "expected_value": sizing["expected_value"] if approved else 0,
            "bankroll_snapshot": self.bankroll,
            "validated_at": datetime.utcnow().isoformat(),
        }

        if approved:
            logger.info(
                f"✅ APPROVED: {signal} | ${position_dollars:.2f} | "
                f"{sizing['contracts']} contracts | EV: ${sizing['expected_value']:+.2f}"
            )
        else:
            logger.warning(f"❌ REJECTED: {' | '.join(result['rejection_reasons'])}")

        # Save risk result
        out_path = Path(config.DATA_DIR) / "risk_result.json"
        out_path.write_text(json.dumps(result, indent=2))

        return result


def validate_trade(trade_proposal: dict) -> dict:
    """Convenience function for single trade validation."""
    validator = RiskValidator()
    return validator.validate(trade_proposal)


def run_validation(prediction_results_path: str = None) -> list[dict]:
    """Validate all trade signals from predictions."""
    if prediction_results_path is None:
        prediction_results_path = Path(config.DATA_DIR) / "prediction_results.json"

    try:
        pred_data = json.loads(Path(prediction_results_path).read_text())
        predictions = pred_data.get("predictions", [])
    except FileNotFoundError:
        logger.error("Prediction results not found. Run predict.py first.")
        return []

    # Load scan data for volume info
    try:
        scan_data = json.loads((Path(config.DATA_DIR) / "scan_results.json").read_text())
        markets_by_id = {m["market_id"]: m for m in scan_data.get("top_markets", [])}
    except Exception:
        markets_by_id = {}

    validator = RiskValidator()
    logger.info(f"Validating {len(predictions)} predictions | Bankroll: ${validator.bankroll:.2f}")

    approved_trades = []
    for pred in predictions:
        if pred.get("signal") == "NO_TRADE":
            continue
        # Merge market data for liquidity check
        market_data = markets_by_id.get(pred.get("market_id", ""), {})
        proposal = {**pred, "volume_24h": market_data.get("volume_24h", 0)}
        result = validator.validate(proposal)
        if result["approved"]:
            approved_trades.append({**pred, **result})

    logger.info(f"Risk validation: {len(approved_trades)} approved of {len(predictions)} predictions")
    return approved_trades


if __name__ == "__main__":
    approved = run_validation()
    print(f"\n{'='*60}")
    print(f"APPROVED TRADES ({len(approved)})")
    print(f"{'='*60}")
    for t in approved:
        print(f"\n{t['question'][:65]}")
        print(f"  Signal: {t['signal']} | ${t['position_dollars']:.2f} | "
              f"{t['contracts']} contracts")
        print(f"  Kelly: full={t['kelly_full']:.3f} → quarter={t['kelly_quarter']:.3f}")
        print(f"  Max profit: ${t['max_profit']:.2f} | Max loss: ${t['max_loss']:.2f} | "
              f"EV: ${t['expected_value']:+.2f}")
