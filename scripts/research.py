"""
scripts/research.py — Step 2: Gather intelligence for each market
Scrapes news, Reddit, and sentiment signals. Outputs research briefs.
"""

import json
import logging
import os
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RESEARCH] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE)]
)
logger = logging.getLogger(__name__)


# ── Safety: Strip any embedded instructions from scraped content ──────────────
INJECTION_PATTERNS = [
    r"ignore (previous|all|above|your).*instruction",
    r"new (system|instruction|prompt|task)",
    r"you (are|must|should) now",
    r"disregard.*rules",
    r"forget.*previous",
]

def sanitize(text: str) -> str:
    """Remove potential prompt injection attempts from scraped content."""
    if not text:
        return ""
    text = text[:3000]  # truncate
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning(f"Potential prompt injection detected, stripping content")
            return "[CONTENT SANITIZED: potential injection pattern detected]"
    return text


# ── News API ──────────────────────────────────────────────────────────────────

def search_news(query: str, days_back: int = 3) -> list[dict]:
    """Search NewsAPI for recent articles about a market topic."""
    if not config.NEWS_API_KEY:
        logger.debug("NEWS_API_KEY not set, skipping news search")
        return []
    try:
        from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "from": from_date,
                "sortBy": "publishedAt",
                "pageSize": 10,
                "apiKey": config.NEWS_API_KEY,
            },
            timeout=10
        )
        articles = resp.json().get("articles", [])
        results = []
        for a in articles:
            text = f"{a.get('title','')} {a.get('description','')} {a.get('content','')}"
            results.append({
                "source": "news",
                "title": sanitize(a.get("title", "")),
                "content": sanitize(text),
                "published_at": a.get("publishedAt", ""),
                "url": a.get("url", ""),
            })
        return results
    except Exception as e:
        logger.error(f"NewsAPI error: {e}")
        return []


# ── Reddit ─────────────────────────────────────────────────────────────────────

def search_reddit(query: str, subreddits: list[str] = None) -> list[dict]:
    """Search Reddit for posts related to the market topic."""
    if not config.REDDIT_CLIENT_ID or not config.REDDIT_SECRET:
        logger.debug("Reddit credentials not set, using public search")
        return _reddit_public_search(query)

    if subreddits is None:
        subreddits = ["politics", "investing", "news", "worldnews", "Economics"]

    try:
        # Get token
        auth = requests.auth.HTTPBasicAuth(config.REDDIT_CLIENT_ID, config.REDDIT_SECRET)
        token_resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth,
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": "predict-market-bot/1.0"},
            timeout=10
        )
        token = token_resp.json().get("access_token", "")
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "predict-market-bot/1.0"
        }

        results = []
        sub_str = "+".join(subreddits)
        resp = requests.get(
            f"https://oauth.reddit.com/r/{sub_str}/search",
            params={"q": query, "sort": "new", "limit": 15, "t": "week"},
            headers=headers,
            timeout=10
        )
        posts = resp.json().get("data", {}).get("children", [])
        for post in posts:
            d = post.get("data", {})
            results.append({
                "source": "reddit",
                "title": sanitize(d.get("title", "")),
                "content": sanitize(d.get("selftext", "") or d.get("title", "")),
                "score": d.get("score", 0),
                "published_at": datetime.utcfromtimestamp(d.get("created_utc", 0)).isoformat(),
                "url": d.get("url", ""),
            })
        return results
    except Exception as e:
        logger.error(f"Reddit API error: {e}")
        return []


def _reddit_public_search(query: str) -> list[dict]:
    """Fallback: Reddit public JSON search (no auth)."""
    try:
        resp = requests.get(
            "https://www.reddit.com/search.json",
            params={"q": query, "sort": "new", "t": "week", "limit": 10},
            headers={"User-Agent": "predict-market-bot/1.0"},
            timeout=10
        )
        posts = resp.json().get("data", {}).get("children", [])
        results = []
        for post in posts:
            d = post.get("data", {})
            results.append({
                "source": "reddit",
                "title": sanitize(d.get("title", "")),
                "content": sanitize(d.get("selftext", "") or d.get("title", "")),
                "score": d.get("score", 0),
                "published_at": datetime.utcfromtimestamp(d.get("created_utc", 0)).isoformat(),
                "url": d.get("permalink", ""),
            })
        return results
    except Exception as e:
        logger.error(f"Reddit public search error: {e}")
        return []


# ── Sentiment Analysis ────────────────────────────────────────────────────────

BULLISH_WORDS = {
    "yes", "likely", "will", "confirm", "pass", "win", "approve", "rise",
    "increase", "positive", "growth", "higher", "beat", "exceed", "strong",
    "confirmed", "announced", "signed", "agreed", "approved"
}
BEARISH_WORDS = {
    "no", "unlikely", "won't", "denied", "fail", "lose", "reject", "fall",
    "decrease", "negative", "decline", "lower", "miss", "delay", "weak",
    "cancelled", "dropped", "blocked", "vetoed", "rejected"
}

def classify_sentiment(text: str) -> float:
    """
    Rule-based sentiment score from -1.0 (bearish) to +1.0 (bullish).
    -1.0 means "NO is likely", +1.0 means "YES is likely".
    """
    if not text:
        return 0.0
    words = set(text.lower().split())
    bull_count = len(words & BULLISH_WORDS)
    bear_count = len(words & BEARISH_WORDS)
    total = bull_count + bear_count
    if total == 0:
        return 0.0
    return round((bull_count - bear_count) / total, 3)


def recency_weight(published_at: str) -> float:
    """Give more weight to recent content."""
    if not published_at:
        return 1.0
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
        if age_hours < 2:
            return 3.0
        elif age_hours < 24:
            return 1.5
        else:
            return 1.0
    except Exception:
        return 1.0


def extract_keywords(question: str) -> str:
    """Extract searchable keywords from a market question."""
    stopwords = {"will", "the", "a", "an", "be", "is", "are", "in", "on",
                 "by", "at", "to", "of", "and", "or", "for", "with", "by",
                 "than", "more", "less", "this", "that", "have", "has"}
    words = question.lower().split()
    keywords = [w.strip("?.,!") for w in words if w not in stopwords and len(w) > 2]
    return " ".join(keywords[:6])


# ── Main Research Function ────────────────────────────────────────────────────

def research_market(market: dict) -> dict:
    """
    Research a single market. Returns a research brief.
    """
    question = market.get("question", "")
    market_id = market.get("market_id", "")
    current_price = market.get("current_price", 0.5)

    logger.info(f"Researching: {question[:60]}")
    keywords = extract_keywords(question)

    # Gather sources
    news = search_news(keywords, days_back=3)
    reddit = search_reddit(keywords)
    all_sources = news + reddit

    if not all_sources:
        logger.info(f"  No sources found for: {keywords}")
        return {
            "market_id": market_id,
            "question": question,
            "sources_checked": 0,
            "sentiment_score": 0.0,
            "estimated_probability": current_price,  # fallback to market
            "confidence_level": "LOW",
            "narrative_summary": "No external data found. Defaulting to market price.",
            "last_updated": datetime.utcnow().isoformat(),
        }

    # Compute weighted sentiment
    total_weight = 0.0
    weighted_sentiment = 0.0
    for source in all_sources:
        sentiment = classify_sentiment(source.get("content", "") + " " + source.get("title", ""))
        weight = recency_weight(source.get("published_at", ""))
        weighted_sentiment += sentiment * weight
        total_weight += weight

    if total_weight > 0:
        avg_sentiment = weighted_sentiment / total_weight
    else:
        avg_sentiment = 0.0

    # Convert sentiment score to probability adjustment
    # Sentiment of +1.0 nudges estimated_prob toward 1.0
    # Sentiment of -1.0 nudges toward 0.0
    sentiment_adjustment = avg_sentiment * 0.10  # max ±10% adjustment
    estimated_prob = max(0.01, min(0.99, current_price + sentiment_adjustment))

    # Confidence based on source count and agreement
    if len(all_sources) >= 8 and abs(avg_sentiment) > 0.3:
        confidence = "HIGH"
    elif len(all_sources) >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Build narrative
    top_headlines = [s.get("title", "") for s in all_sources[:3] if s.get("title")]
    narrative = (
        f"Found {len(all_sources)} sources ({len(news)} news, {len(reddit)} Reddit). "
        f"Composite sentiment: {avg_sentiment:+.3f} "
        f"({'BULLISH' if avg_sentiment > 0.1 else 'BEARISH' if avg_sentiment < -0.1 else 'NEUTRAL'}). "
        f"Market price: {current_price:.2f} → estimated: {estimated_prob:.2f}. "
    )
    if top_headlines:
        narrative += f"Key headlines: " + " | ".join(top_headlines[:2])

    result = {
        "market_id": market_id,
        "platform": market.get("platform"),
        "question": question,
        "keywords_searched": keywords,
        "sources_checked": len(all_sources),
        "news_count": len(news),
        "reddit_count": len(reddit),
        "sentiment_score": round(avg_sentiment, 4),
        "estimated_probability": round(estimated_prob, 4),
        "market_price": current_price,
        "confidence_level": confidence,
        "narrative_summary": narrative,
        "top_headlines": top_headlines,
        "last_updated": datetime.utcnow().isoformat(),
    }

    logger.info(f"  Sentiment: {avg_sentiment:+.3f} | Est prob: {estimated_prob:.2f} "
                f"(market: {current_price:.2f}) | Confidence: {confidence}")
    return result


def run_research(scan_results_path: str = None) -> list[dict]:
    """Research all markets from the latest scan."""
    if scan_results_path is None:
        scan_results_path = Path(config.DATA_DIR) / "scan_results.json"

    try:
        scan_data = json.loads(Path(scan_results_path).read_text())
        markets = scan_data.get("top_markets", [])
    except FileNotFoundError:
        logger.error(f"Scan results not found at {scan_results_path}. Run scan_markets.py first.")
        return []

    logger.info(f"Researching {len(markets)} markets...")
    results = []
    for market in markets:
        try:
            brief = research_market(market)
            results.append(brief)
        except Exception as e:
            logger.error(f"Research failed for {market.get('market_id')}: {e}")

    # Save
    output = {
        "researched_at": datetime.utcnow().isoformat(),
        "markets_researched": len(results),
        "research_results": results,
    }
    out_path = Path(config.DATA_DIR) / "research_results.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"Research complete: {len(results)} markets → {out_path}")
    return results


if __name__ == "__main__":
    results = run_research()
    print(f"\n{'='*60}")
    print("RESEARCH RESULTS")
    print(f"{'='*60}")
    for r in results[:5]:
        gap = r["estimated_probability"] - r["market_price"]
        print(f"\n{r['question'][:65]}")
        print(f"  Market: {r['market_price']:.2f} | Estimated: {r['estimated_probability']:.2f} | "
              f"Gap: {gap:+.2f} | Confidence: {r['confidence_level']}")
        print(f"  {r['narrative_summary'][:120]}")
