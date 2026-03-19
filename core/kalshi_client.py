"""
core/kalshi_client.py — Kalshi REST API wrapper
Handles authentication, market discovery, and order execution.
Supports both demo and live environments via config.KALSHI_DEMO
"""

import time
import hmac
import hashlib
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


class KalshiClient:
    """
    Thin wrapper around the Kalshi REST API.
    Docs: https://trading-api.readme.io
    """

    def __init__(self):
        self.base_url = config.KALSHI_BASE_URL
        self.api_key = config.KALSHI_API_KEY
        self.api_secret = config.KALSHI_API_SECRET
        self.demo = config.KALSHI_DEMO
        self.session = requests.Session()
        self._token = None
        self._token_expiry = 0

        if not self.api_key:
            logger.warning("KALSHI_API_KEY not set. Running in read-only mode.")

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _get_auth_headers(self, method: str, path: str) -> dict:
        """Generate HMAC-signed headers for Kalshi API requests."""
        timestamp = str(int(time.time() * 1000))
        msg = timestamp + method.upper() + path
        if self.api_secret:
            signature = hmac.new(
                self.api_secret.encode(),
                msg.encode(),
                hashlib.sha256
            ).hexdigest()
        else:
            signature = ""
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    def _request(self, method: str, endpoint: str, params: dict = None, body: dict = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = self._get_auth_headers(method, f"/trade-api/v2{endpoint}")
        try:
            resp = self.session.request(
                method, url, headers=headers,
                params=params, json=body, timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Kalshi HTTP error {e.response.status_code}: {e.response.text}")
            return {}
        except requests.exceptions.ConnectionError:
            logger.error("Kalshi connection error — check network")
            return {}
        except Exception as e:
            logger.error(f"Kalshi request failed: {e}")
            return {}

    # ── Market Discovery ─────────────────────────────────────────────────────

    def get_markets(self, limit: int = 200, cursor: str = None) -> list[dict]:
        """Fetch open markets. Returns list of market dicts."""
        params = {"limit": limit, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = self._request("GET", "/markets", params=params)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        return self._request("GET", f"/markets/{ticker}").get("market", {})

    def get_orderbook(self, ticker: str) -> dict:
        """Get current orderbook for a market."""
        return self._request("GET", f"/markets/{ticker}/orderbook")

    def get_market_history(self, ticker: str, limit: int = 100) -> list[dict]:
        """Get price history for a market."""
        data = self._request("GET", f"/markets/{ticker}/history", params={"limit": limit})
        return data.get("history", [])

    def get_trades(self, ticker: str = None, limit: int = 100) -> list[dict]:
        """Get recent trades, optionally filtered by market."""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/trades", params=params)
        return data.get("trades", [])

    # ── Portfolio ─────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Get account balance in USD cents, converted to dollars."""
        data = self._request("GET", "/portfolio/balance")
        return data.get("balance", 0) / 100.0  # Kalshi uses cents

    def get_positions(self) -> list[dict]:
        """Get all open positions."""
        data = self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    def get_fills(self, limit: int = 100) -> list[dict]:
        """Get order fill history."""
        data = self._request("GET", "/portfolio/fills", params={"limit": limit})
        return data.get("fills", [])

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        ticker: str,
        side: str,          # "yes" or "no"
        action: str,        # "buy" or "sell"
        count: int,         # number of contracts
        limit_price: int,   # price in cents (e.g. 65 = $0.65)
        expiration_ts: int = None,
    ) -> dict:
        """Place a limit order on Kalshi."""
        if os.path.exists(config.STOP_FILE):
            logger.critical("STOP file detected. Refusing to place order.")
            return {"error": "STOP file present"}

        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "limit",
            "count": count,
            "yes_price": limit_price if side == "yes" else None,
            "no_price": limit_price if side == "no" else None,
            "client_order_id": f"pmb_{int(time.time())}",
        }
        if expiration_ts:
            body["expiration_ts"] = expiration_ts

        logger.info(f"Placing order: {action} {count}x {ticker} {side} @ {limit_price}¢")
        result = self._request("POST", "/portfolio/orders", body=body)
        return result

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_orders(self, status: str = "resting") -> list[dict]:
        """Get open orders. Status: resting, cancelled, executed."""
        data = self._request("GET", "/portfolio/orders", params={"status": status})
        return data.get("orders", [])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_spread(self, ticker: str) -> float:
        """Calculate bid-ask spread from orderbook."""
        ob = self.get_orderbook(ticker)
        yes_bids = ob.get("orderbook", {}).get("yes", [])
        no_bids = ob.get("orderbook", {}).get("no", [])
        if not yes_bids or not no_bids:
            return 1.0  # wide spread = untradeable
        best_yes = max(yes_bids, key=lambda x: x[0])[0] / 100
        best_no = max(no_bids, key=lambda x: x[0])[0] / 100
        return round(1.0 - best_yes - best_no, 4)

    def get_mid_price(self, ticker: str) -> float:
        """Get mid-price (not last trade price)."""
        ob = self.get_orderbook(ticker)
        yes_bids = ob.get("orderbook", {}).get("yes", [])
        no_bids = ob.get("orderbook", {}).get("no", [])
        if not yes_bids:
            return 0.5
        best_yes = max(yes_bids, key=lambda x: x[0])[0] / 100
        return round(best_yes, 4)

    def normalize_market(self, raw: dict) -> dict:
        """Convert Kalshi market dict to standard internal format."""
        ticker = raw.get("ticker", "")
        return {
            "market_id": ticker,
            "platform": "kalshi",
            "question": raw.get("title", ""),
            "current_price": raw.get("last_price", 50) / 100,
            "volume_24h": raw.get("volume_24h", 0),
            "expiry_date": raw.get("close_time", ""),
            "spread": self.get_spread(ticker),
            "status": raw.get("status", ""),
            "rules_primary": raw.get("rules_primary", ""),
        }
