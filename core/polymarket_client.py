"""
core/polymarket_client.py — Polymarket CLOB API wrapper
Read-only market data works with no auth at all.
Trading requires an Ethereum wallet private key (EIP-712 signing via py-clob-client).
Polygon network — uses USDC as collateral.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


class PolymarketClient:
    def __init__(self):
        self.clob_url = config.POLY_CLOB_URL
        self.gamma_url = config.POLY_GAMMA_URL
        self.private_key = config.POLY_PRIVATE_KEY
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._clob_client = None

    def _gamma(self, endpoint: str, params: dict = None) -> any:
        try:
            r = self.session.get(f"{self.gamma_url}{endpoint}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Polymarket Gamma error {endpoint}: {e}")
            return {}

    def _clob(self, endpoint: str, params: dict = None) -> any:
        try:
            r = self.session.get(f"{self.clob_url}{endpoint}", params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Polymarket CLOB error {endpoint}: {e}")
            return {}

    # ── Market Data (no auth needed) ──────────────────────────

    def get_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        data = self._gamma("/markets", params={
            "limit": limit, "offset": offset,
            "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false"
        })
        if isinstance(data, list):
            return data
        return data.get("data", [])

    def get_orderbook(self, token_id: str) -> dict:
        return self._clob("/book", params={"token_id": token_id})

    def get_mid_price(self, token_id: str) -> float:
        ob = self.get_orderbook(token_id)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids or not asks:
            return 0.5
        return round((float(bids[0]["price"]) + float(asks[0]["price"])) / 2, 4)

    def get_spread(self, token_id: str) -> float:
        ob = self.get_orderbook(token_id)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids or not asks:
            return 1.0
        return round(float(asks[0]["price"]) - float(bids[0]["price"]), 4)

    # ── Trading (needs wallet private key) ────────────────────

    def _init_clob_client(self) -> bool:
        if self._clob_client:
            return True
        if not self.private_key:
            logger.error("POLY_PRIVATE_KEY not set. Add your wallet private key to .env")
            return False
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON
            self._clob_client = ClobClient(
                host=self.clob_url,
                chain_id=POLYGON,
                private_key=self.private_key,
                signature_type=2,  # POLY_GNOSIS_SAFE
            )
            creds = self._clob_client.create_or_derive_api_creds()
            self._clob_client.set_api_creds(creds)
            logger.info("Polymarket CLOB client ready")
            return True
        except ImportError:
            logger.error("py-clob-client not installed: pip install py-clob-client")
            return False
        except Exception as e:
            logger.error(f"Polymarket client init failed: {e}")
            return False

    def get_balance(self) -> float:
        """Get USDC balance on Polygon."""
        if not self._init_clob_client():
            return 0.0
        try:
            data = self._clob_client.get_balance()
            return float(data) / 1_000_000  # USDC has 6 decimals
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

    def place_order(self, token_id: str, side: str, size: float, price: float) -> dict:
        if os.path.exists(config.STOP_FILE):
            return {"error": "STOP file present"}
        if not self._init_clob_client():
            return {"error": "client not initialized — check POLY_PRIVATE_KEY in .env"}
        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            order = self._clob_client.create_and_post_order({
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": BUY if side == "BUY" else SELL,
            })
            logger.info(f"Polymarket order placed: {order}")
            return order
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return {"error": str(e)}

    # ── Normalize to internal format ──────────────────────────

    def normalize_market(self, raw: dict) -> dict:
        tokens = raw.get("tokens", [])
        yes_token = next((t for t in tokens if t.get("outcome", "").lower() == "yes"), {})
        yes_token_id = yes_token.get("token_id", "")

        # Get price — try outcomePrices first, then orderbook
        try:
            prices = raw.get("outcomePrices")
            if prices and len(prices) >= 1:
                price = float(prices[0])
            elif yes_token_id:
                price = self.get_mid_price(yes_token_id)
            else:
                price = 0.5
        except Exception:
            price = 0.5

        volume = float(raw.get("volume24hr") or raw.get("volume") or 0)

        return {
            "market_id": raw.get("conditionId") or raw.get("condition_id") or raw.get("id", ""),
            "platform": "polymarket",
            "question": raw.get("question") or raw.get("title", ""),
            "current_price": round(price, 4),
            "volume_24h": volume,
            "expiry_date": raw.get("endDate") or raw.get("end_date_iso", ""),
            "spread": self.get_spread(yes_token_id) if yes_token_id else 0.05,
            "yes_token_id": yes_token_id,
            "status": "open" if raw.get("active") else "closed",
            "description": raw.get("description", ""),
        }
