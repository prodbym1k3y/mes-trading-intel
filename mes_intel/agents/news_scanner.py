"""News Scanner Agent — Phase 2 Enhanced.

Sources:
  - Finnhub WebSocket (real-time news stream, falls back to HTTP polling)
  - Twitter/X API v2 filtered stream (Trump, DeItaone, Zerohedge, Fed)
  - RSS feeds: Reuters, Bloomberg, Zerohedge, CNBC (parallel async polling)
  - Economic calendar (Finnhub)

Phase 2 additions:
  - Finnhub WebSocket for sub-second news delivery
  - RSS feed monitoring with 15s poll interval
  - Parallel multi-source threading with priority queue
  - Richer BULLISH/BEARISH/NEUTRAL classification
  - Historical impact prediction from DB patterns
  - Impact score calibration per source/category
"""
from __future__ import annotations

import logging
import re
import time
import json
import threading
import queue
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from xml.etree import ElementTree

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)

# Keywords that typically move ES/MES
MARKET_MOVERS = {
    "tariff": 3, "fed": 3, "rate": 2, "inflation": 2, "recession": 3,
    "jobs": 2, "nonfarm": 3, "cpi": 3, "ppi": 2, "gdp": 2,
    "fomc": 3, "powell": 3, "yellen": 2, "treasury": 2,
    "war": 3, "sanctions": 2, "china": 2, "trade deal": 3,
    "default": 3, "shutdown": 2, "debt ceiling": 3,
    "earnings": 1, "guidance": 2, "buyback": 1,
    "trump": 3, "executive order": 3,
    # Phase 2 additions
    "nuclear": 3, "invasion": 3, "coup": 3, "assassination": 3,
    "emergency": 3, "pandemic": 3, "default": 3,
    "opec": 2, "oil": 2, "bitcoin": 1, "crypto": 1,
    "bank failure": 3, "liquidity": 2, "margin call": 3,
}

# Simple sentiment words
POSITIVE_WORDS = {
    "surge", "rally", "gain", "rise", "jump", "soar", "boom",
    "bullish", "optimistic", "strong", "beat", "exceed", "record",
    "deal", "agreement", "stimulus", "easing", "dovish",
    "recovery", "rebound", "upgrade", "breakout", "expansion",
}

NEGATIVE_WORDS = {
    "crash", "plunge", "drop", "fall", "sink", "tumble", "collapse",
    "bearish", "pessimistic", "weak", "miss", "decline", "recession",
    "crisis", "default", "hawkish", "tighten", "tariff", "war",
    "sell-off", "selloff", "panic", "fear",
    "downgrade", "layoffs", "bankruptcy", "contagion", "stagflation",
}

# Sentiment classification thresholds
BULLISH_THRESHOLD = 0.2
BEARISH_THRESHOLD = -0.2

# Key Twitter/X accounts to monitor
TRACKED_ACCOUNTS = {
    "@realDonaldTrump": {"priority": 10, "category": "politics"},
    "@DeItaone": {"priority": 8, "category": "breaking"},
    "@ZeroHedge": {"priority": 7, "category": "breaking"},
    "@federalreserve": {"priority": 9, "category": "fed_policy"},
    "@SecYellen": {"priority": 7, "category": "fed_policy"},
    "@GeraldoRivera": {"priority": 5, "category": "politics"},
    "@business": {"priority": 5, "category": "general"},
    "@axios": {"priority": 6, "category": "breaking"},
    "@PeterSchiff": {"priority": 5, "category": "economics"},
    "@unusual_whales": {"priority": 7, "category": "options_flow"},
}

# RSS feeds to monitor
RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "source": "reuters", "priority": 7},
    {"url": "https://feeds.bloomberg.com/markets/news.rss", "source": "bloomberg", "priority": 8},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "source": "cnbc", "priority": 7},
    {"url": "https://feeds.feedburner.com/zerohedge/feed", "source": "zerohedge", "priority": 6},
    {"url": "https://www.marketwatch.com/rss/topstories", "source": "marketwatch", "priority": 6},
]

# Flash colors for dashboard
FLASH_COLORS = {
    "BULLISH": "#00FF41",   # Matrix green
    "BEARISH": "#FF0040",   # Hot red
    "BREAKING": "#FFD700",  # Gold
    "TRUMP": "#FF6600",     # Orange
}

# Audio alert defaults
DEFAULT_ALERT_SOUNDS = {
    "breaking": "breaking_news.wav",
    "trump": "trump_alert.wav",
    "high_impact": "high_impact.wav",
}


@dataclass
class NewsImpactRecord:
    """Historical record of a news event and its measured price impact."""
    timestamp: float
    headline: str
    category: str
    sentiment: str  # BULLISH, BEARISH, NEUTRAL
    sentiment_score: float
    predicted_impact: float
    actual_impact_points: float = 0.0  # MES points moved in first N minutes
    actual_impact_duration_sec: float = 0.0
    price_at_news: float = 0.0
    price_after_2min: float = 0.0
    price_after_5min: float = 0.0
    matched_pattern: str = ""  # which historical pattern this matched

    @property
    def prediction_error(self) -> float:
        if self.predicted_impact == 0:
            return 0.0
        return abs(self.actual_impact_points - self.predicted_impact)


@dataclass
class PreMarketCatalyst:
    """Upcoming economic event or earnings release."""
    timestamp: float  # when it's scheduled
    name: str
    category: str  # economic_data, earnings, fed_speech, etc.
    expected_impact: int  # 1-3
    consensus: str = ""
    previous: str = ""
    notes: str = ""


class NewsScanner:
    """News scanner agent — monitors feeds, estimates market impact,
    tracks historical patterns, and delivers multi-channel alerts."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus

        # Track which categories actually move price (feedback from meta-learner)
        self.category_effectiveness: dict[str, float] = {}

        # Historical impact database: pattern -> list of outcomes
        self.historical_impacts: dict[str, list[NewsImpactRecord]] = defaultdict(list)

        # Recent headlines buffer (dedup + display)
        self.recent_headlines: deque[dict] = deque(maxlen=200)

        # Pre-market catalysts for the current session
        self.catalysts: list[PreMarketCatalyst] = []

        # Streaming state
        self._running = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

        # Priority queue for multi-source headline ingestion
        # Items: (priority_neg, timestamp, headline_dict) — lower priority_neg = higher priority
        self._headline_queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=1000)
        self._processor_thread: Optional[threading.Thread] = None

        # Audio/notification availability (checked lazily)
        self._plyer_available: Optional[bool] = None
        self._audio_available: Optional[bool] = None

        # Alert sound paths
        self.sound_dir = Path(__file__).parent.parent.parent / "var" / "sounds"
        self.alert_sounds: dict[str, str] = dict(DEFAULT_ALERT_SOUNDS)

        # Current MES price for impact tracking
        self._current_price: float = 0.0
        self._pending_impact_checks: list[dict] = []

        self.bus.subscribe(EventType.PERFORMANCE_REPORT, self._on_performance_feedback)
        self.bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        self.bus.subscribe(EventType.LESSON_LEARNED, self._on_lesson_learned)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.QUANT_SIGNAL, self._on_quant_signal)

        # Regime-aware news weighting
        # In quiet/ranging regimes news matters MORE; in trending/volatile, trend > news
        self._current_regime: str = "unknown"
        self._regime_news_multipliers: dict[str, float] = {
            "trending":  0.7,   # trend overpowers news
            "ranging":   1.3,   # news breaks ranges
            "volatile":  0.8,   # already volatile, extra noise
            "quiet":     1.5,   # news has outsize effect in quiet markets
            "breakout":  1.1,
            "unknown":   1.0,
        }

        log.info("News Scanner initialized (Phase 2 — multi-source + historical tracking)")

    # ------------------------------------------------------------------
    # Core headline processing (preserved from Phase 1 + enhanced)
    # ------------------------------------------------------------------

    def process_headline(self, headline: str, source: str = "unknown",
                         url: str = "", timestamp: Optional[float] = None) -> dict:
        """Process a news headline — score sentiment and market impact."""
        ts = timestamp or time.time()
        headline_lower = headline.lower()

        # Dedup check
        for recent in self.recent_headlines:
            if recent["headline"] == headline and (ts - recent["timestamp"]) < 300:
                return recent  # Already processed within 5 minutes

        # Check for Trump
        is_trump = "trump" in headline_lower

        # Sentiment scoring
        sentiment = self._score_sentiment(headline_lower)

        # Classify direction
        direction = self._classify_direction(sentiment)

        # Market impact estimation (enhanced with historical context)
        impact = self._estimate_impact(headline_lower)

        # Categorize
        category = self._categorize(headline_lower)

        # Historical context lookup
        historical = self.get_historical_context(headline)

        # Adjust impact based on historical data
        if historical["similar_events"]:
            avg_past_impact = sum(
                abs(e.actual_impact_points) for e in historical["similar_events"]
            ) / len(historical["similar_events"])
            # Blend estimated impact with historical average
            if avg_past_impact > 0:
                impact = (impact + avg_past_impact / 5.0) / 2.0  # normalize to 0-3 scale

        # Regime-aware impact scaling
        regime_mult = self._regime_news_multipliers.get(self._current_regime, 1.0)
        impact = min(3.0, impact * regime_mult)

        # Store
        news_data = {
            "timestamp": ts,
            "headline": headline,
            "source": source,
            "sentiment_score": sentiment,
            "market_impact": impact,
            "category": category,
            "url": url,
            "is_trump": 1 if is_trump else 0,
            "direction": direction,
            "historical_match_count": len(historical["similar_events"]),
        }
        news_id = self.db.insert_news({
            k: v for k, v in news_data.items()
            if k in ("timestamp", "headline", "source", "sentiment_score",
                      "market_impact", "category", "url", "is_trump")
        })
        news_data["news_id"] = news_id

        # Buffer
        self.recent_headlines.append(news_data)

        # Create impact tracking record
        impact_record = NewsImpactRecord(
            timestamp=ts,
            headline=headline,
            category=category,
            sentiment=direction,
            sentiment_score=sentiment,
            predicted_impact=impact,
            price_at_news=self._current_price,
            matched_pattern=historical.get("best_pattern", ""),
        )
        self._schedule_impact_measurement(impact_record)

        # Determine event type and alert level
        is_breaking = impact >= 2.5 or (is_trump and impact >= 2)
        event_type = EventType.TRUMP_ALERT if is_trump and impact >= 2 else EventType.NEWS_ALERT

        event_data = {
            "news_id": news_id,
            "headline": headline,
            "sentiment_score": sentiment,
            "market_impact": impact,
            "category": category,
            "direction": direction,
            "is_trump": is_trump,
            "source": source,
            "is_breaking": is_breaking,
        }

        # Add historical context to event
        if historical["similar_events"]:
            event_data["historical_context"] = historical["summary"]

        self.bus.publish(Event(
            type=event_type,
            source="news_scanner",
            data=event_data,
            priority=8 if is_breaking else 3,
        ))

        # Breaking news: publish flash event + trigger alerts
        if is_breaking:
            flash_color = FLASH_COLORS.get(
                "TRUMP" if is_trump else direction,
                FLASH_COLORS["BREAKING"],
            )
            self.bus.publish(Event(
                type=EventType.BREAKING_NEWS,
                source="news_scanner",
                data={
                    "headline": headline,
                    "direction": direction,
                    "impact": impact,
                    "flash_color": flash_color,
                    "category": category,
                    "source": source,
                    "historical_context": historical.get("summary", ""),
                },
                priority=9,
            ))
            self._send_desktop_notification(headline, category, impact)
            self._play_alert_sound("trump" if is_trump else "breaking")

        if impact >= 2:
            log.info("NEWS [impact=%.1f %s]: %s (sentiment=%.2f, %s, %s)",
                     impact, direction, headline[:80], sentiment, category, source)

        return news_data

    # ------------------------------------------------------------------
    # Sentiment & classification
    # ------------------------------------------------------------------

    def _score_sentiment(self, text: str) -> float:
        """Score sentiment using FinBERT (ML) with keyword fallback. Returns -1 to +1."""
        # Try FinBERT first (much more accurate for financial text)
        if not hasattr(self, '_finbert'):
            self._finbert = None
            self._finbert_tokenizer = None
            try:
                from transformers import AutoTokenizer, AutoModelForSequenceClassification
                import torch
                self._finbert_tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
                self._finbert = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
                self._finbert.eval()
                log.info("FinBERT loaded for ML-based sentiment scoring")
            except Exception:
                log.debug("FinBERT not available — using keyword sentiment")

        if self._finbert is not None and self._finbert_tokenizer is not None:
            try:
                import torch
                inputs = self._finbert_tokenizer(text, return_tensors="pt",
                                                  truncation=True, max_length=128)
                with torch.no_grad():
                    outputs = self._finbert(**inputs)
                probs = torch.nn.functional.softmax(outputs.logits, dim=-1)[0]
                # FinBERT labels: positive=0, negative=1, neutral=2
                pos_prob = float(probs[0])
                neg_prob = float(probs[1])
                return round(pos_prob - neg_prob, 4)  # -1 to +1
            except Exception:
                pass  # fall through to keyword method

        # Keyword fallback
        words = set(re.findall(r'\w+', text))
        pos = len(words & POSITIVE_WORDS)
        neg = len(words & NEGATIVE_WORDS)

        total = pos + neg
        if total == 0:
            return 0.0

        return (pos - neg) / total

    def _classify_direction(self, sentiment_score: float) -> str:
        """Classify as BULLISH, BEARISH, or NEUTRAL."""
        if sentiment_score >= BULLISH_THRESHOLD:
            return "BULLISH"
        elif sentiment_score <= BEARISH_THRESHOLD:
            return "BEARISH"
        return "NEUTRAL"

    def _estimate_impact(self, text: str) -> float:
        """Estimate market impact 0-3 based on keyword matching."""
        max_impact = 0
        for keyword, impact in MARKET_MOVERS.items():
            if keyword in text:
                max_impact = max(max_impact, impact)

        # Boost if category has historically moved price
        category = self._categorize(text)
        effectiveness = self.category_effectiveness.get(category, 1.0)
        adjusted = max_impact * effectiveness

        return min(adjusted, 3.0)

    def _categorize(self, text: str) -> str:
        """Categorize a headline."""
        if any(w in text for w in ("fed", "fomc", "powell", "rate", "dovish", "hawkish")):
            return "fed_policy"
        if any(w in text for w in ("trump", "executive order", "white house")):
            return "politics"
        if any(w in text for w in ("tariff", "trade deal", "sanctions", "china")):
            return "trade"
        if any(w in text for w in ("cpi", "ppi", "jobs", "nonfarm", "gdp", "inflation")):
            return "economic_data"
        if any(w in text for w in ("earnings", "revenue", "guidance")):
            return "earnings"
        if any(w in text for w in ("war", "conflict", "military")):
            return "geopolitical"
        return "general"

    # ------------------------------------------------------------------
    # Historical context engine
    # ------------------------------------------------------------------

    def get_historical_context(self, headline: str) -> dict:
        """Look up similar past headlines and their price impact.

        Returns dict with:
          - similar_events: list of NewsImpactRecord
          - summary: human-readable summary string
          - best_pattern: the pattern key that matched
          - avg_impact: average MES points impact
        """
        headline_lower = headline.lower()
        best_pattern = ""
        best_matches: list[NewsImpactRecord] = []

        for pattern, records in self.historical_impacts.items():
            if pattern in headline_lower:
                # Weight by recency — more recent records matter more
                if len(records) > len(best_matches):
                    best_matches = records
                    best_pattern = pattern

        if not best_matches:
            return {
                "similar_events": [],
                "summary": "",
                "best_pattern": "",
                "avg_impact": 0.0,
            }

        avg_impact = sum(r.actual_impact_points for r in best_matches) / len(best_matches)
        avg_duration = sum(r.actual_impact_duration_sec for r in best_matches) / len(best_matches)

        # Build summary
        direction = "dropped" if avg_impact < 0 else "rallied"
        summary = (
            f"Last {len(best_matches)} times '{best_pattern}' appeared, "
            f"MES {direction} avg {abs(avg_impact):.1f} pts in "
            f"{avg_duration / 60:.0f} min"
        )

        return {
            "similar_events": best_matches[-10:],  # last 10
            "summary": summary,
            "best_pattern": best_pattern,
            "avg_impact": avg_impact,
        }

    def record_impact(self, record: NewsImpactRecord):
        """Store a completed impact record for future pattern matching."""
        # Extract pattern keys from headline
        patterns = self._extract_patterns(record.headline)
        for pattern in patterns:
            self.historical_impacts[pattern].append(record)
            # Cap per-pattern history
            if len(self.historical_impacts[pattern]) > 100:
                self.historical_impacts[pattern] = \
                    self.historical_impacts[pattern][-100:]

    def _extract_patterns(self, headline: str) -> list[str]:
        """Extract searchable pattern keys from a headline."""
        headline_lower = headline.lower()
        patterns = []
        for keyword in MARKET_MOVERS:
            if keyword in headline_lower:
                patterns.append(keyword)
        # Compound patterns (e.g., "trump tariff", "fed rate")
        if "trump" in headline_lower and "tariff" in headline_lower:
            patterns.append("trump tariff")
        if "fed" in headline_lower and "rate" in headline_lower:
            patterns.append("fed rate")
        if "china" in headline_lower and "tariff" in headline_lower:
            patterns.append("china tariff")
        return patterns if patterns else ["general"]

    def _schedule_impact_measurement(self, record: NewsImpactRecord):
        """Schedule price checks at 2min and 5min after the news event."""
        self._pending_impact_checks.append({
            "record": record,
            "check_2min": record.timestamp + 120,
            "check_5min": record.timestamp + 300,
            "done_2min": False,
            "done_5min": False,
        })
        # Cap pending checks
        if len(self._pending_impact_checks) > 200:
            self._pending_impact_checks = self._pending_impact_checks[-200:]

    def _on_price_update(self, event: Event):
        """Track current price and measure impact of pending news events."""
        price = event.data.get("price", 0.0)
        if price <= 0:
            return
        self._current_price = price
        now = time.time()

        completed = []
        for i, check in enumerate(self._pending_impact_checks):
            rec = check["record"]

            if not check["done_2min"] and now >= check["check_2min"]:
                rec.price_after_2min = price
                check["done_2min"] = True

            if not check["done_5min"] and now >= check["check_5min"]:
                rec.price_after_5min = price
                rec.actual_impact_points = (price - rec.price_at_news) if rec.price_at_news > 0 else 0.0
                rec.actual_impact_duration_sec = 300
                check["done_5min"] = True
                # Record completed impact
                self.record_impact(rec)
                completed.append(i)

        # Remove completed checks (iterate in reverse to preserve indices)
        for i in reversed(completed):
            self._pending_impact_checks.pop(i)

    # ------------------------------------------------------------------
    # Multi-source streaming
    # ------------------------------------------------------------------

    def start_streaming(self):
        """Start all configured news streaming sources in background threads."""
        self._running = True
        self.bus.publish(Event(
            type=EventType.AGENT_STARTED,
            source="news_scanner",
            data={"agent": "news_scanner"},
        ))

        # Central headline processor thread
        self._processor_thread = threading.Thread(
            target=self._process_headline_queue,
            name="news-processor", daemon=True,
        )
        self._processor_thread.start()

        # Finnhub WebSocket (real-time) or HTTP polling fallback
        finnhub_key = self.config.news.finnhub_key
        if finnhub_key:
            # Try WebSocket first
            t = threading.Thread(
                target=self._stream_finnhub_websocket, args=(finnhub_key,),
                name="news-finnhub-ws", daemon=True,
            )
            self._threads.append(t)
            t.start()
            log.info("News Scanner: Finnhub WebSocket started")

        # Twitter/X streaming
        twitter_bearer = self.config.news.twitter_bearer
        if twitter_bearer:
            t = threading.Thread(
                target=self._stream_twitter, args=(twitter_bearer,),
                name="news-twitter", daemon=True,
            )
            self._threads.append(t)
            t.start()
            log.info("News Scanner: Twitter/X streaming started")

        # RSS feeds (always on — no API key needed)
        t = threading.Thread(
            target=self._stream_rss_feeds,
            name="news-rss", daemon=True,
        )
        self._threads.append(t)
        t.start()
        log.info("News Scanner: RSS feed monitoring started (%d feeds)", len(RSS_FEEDS))

        if not finnhub_key and not twitter_bearer:
            log.info("News Scanner: No API keys — RSS only mode")

        log.info("News Scanner streaming started (%d source threads)", len(self._threads))

    def _enqueue_headline(self, headline: str, source: str, url: str = "",
                          timestamp: Optional[float] = None, priority: int = 5):
        """Enqueue a headline for central processing. Thread-safe."""
        try:
            self._headline_queue.put_nowait((
                -priority,  # negative because PriorityQueue is min-heap
                time.time(),
                {"headline": headline, "source": source, "url": url,
                 "timestamp": timestamp or time.time()},
            ))
        except queue.Full:
            log.debug("Headline queue full — dropping: %s", headline[:60])

    def _process_headline_queue(self):
        """Central processor: dequeue and process headlines from all sources."""
        while self._running:
            try:
                _, enqueue_time, item = self._headline_queue.get(timeout=1.0)
                latency_ms = (time.time() - enqueue_time) * 1000
                if latency_ms > 5000:
                    log.debug("Stale headline dropped (%.0fms old): %s",
                              latency_ms, item["headline"][:50])
                    continue
                self.process_headline(
                    headline=item["headline"],
                    source=item["source"],
                    url=item.get("url", ""),
                    timestamp=item.get("timestamp"),
                )
            except queue.Empty:
                continue
            except Exception:
                log.exception("Error processing headline from queue")

    def stop_streaming(self):
        """Stop all streaming threads."""
        self._running = False
        # Threads are daemonic and will die with the process,
        # but we wait briefly for clean shutdown
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()
        self.bus.publish(Event(
            type=EventType.AGENT_STOPPED,
            source="news_scanner",
            data={"agent": "news_scanner"},
        ))
        log.info("News Scanner streaming stopped")

    def _stream_finnhub_websocket(self, api_key: str):
        """Stream Finnhub news via WebSocket. Falls back to HTTP polling."""
        try:
            import websocket  # websocket-client library
            self._stream_finnhub_ws_real(api_key, websocket)
        except ImportError:
            log.info("websocket-client not installed, using Finnhub HTTP polling")
            self._stream_finnhub_http(api_key)

    def _stream_finnhub_ws_real(self, api_key: str, websocket_module):
        """Real WebSocket streaming from Finnhub."""
        ws_url = f"wss://ws.finnhub.io?token={api_key}"
        reconnect_delay = 5

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("type") == "news":
                    for item in data.get("data", []):
                        headline = item.get("headline", "")
                        if headline and self._is_market_relevant(headline):
                            self._enqueue_headline(
                                headline=headline,
                                source="finnhub_ws",
                                url=item.get("url", ""),
                                timestamp=item.get("datetime"),
                                priority=8,
                            )
            except Exception:
                log.debug("Finnhub WS message parse error", exc_info=True)

        def on_error(ws, error):
            log.warning("Finnhub WebSocket error: %s", error)

        def on_close(ws, close_status_code, close_msg):
            log.info("Finnhub WebSocket closed (%s)", close_status_code)

        def on_open(ws):
            log.info("Finnhub WebSocket connected")
            # Subscribe to news
            ws.send(json.dumps({"type": "subscribe-news", "symbol": "AAPL"}))
            ws.send(json.dumps({"type": "subscribe-news", "symbol": "SPY"}))

        while self._running:
            try:
                ws = websocket_module.WebSocketApp(
                    ws_url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                log.exception("Finnhub WebSocket connection failed")
            if self._running:
                log.info("Finnhub WebSocket reconnecting in %ds", reconnect_delay)
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

    def _stream_finnhub_http(self, api_key: str):
        """HTTP polling fallback for Finnhub news."""
        import urllib.request
        import urllib.error

        base_url = "https://finnhub.io/api/v1/news"
        seen_ids: set[str] = set()
        poll_interval = self.config.news.poll_interval_sec

        while self._running:
            try:
                url = f"{base_url}?category=general&token={api_key}"
                req = urllib.request.Request(url, headers={"User-Agent": "mes-intel/2.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    items = json.loads(resp.read().decode())

                for item in items:
                    item_id = str(item.get("id", item.get("headline", "")))
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)

                    headline = item.get("headline", "")
                    if not headline or not self._is_market_relevant(headline):
                        continue

                    self._enqueue_headline(
                        headline=headline,
                        source="finnhub",
                        url=item.get("url", ""),
                        timestamp=item.get("datetime"),
                        priority=7,
                    )

                if len(seen_ids) > 5000:
                    seen_ids = set(list(seen_ids)[-2000:])

            except Exception:
                log.exception("Finnhub HTTP polling error")

            time.sleep(poll_interval)

    def _stream_rss_feeds(self):
        """Poll RSS feeds in parallel threads, one per feed."""
        feed_threads = []
        for feed_cfg in RSS_FEEDS:
            t = threading.Thread(
                target=self._poll_rss_feed,
                args=(feed_cfg,),
                name=f"rss-{feed_cfg['source']}",
                daemon=True,
            )
            t.start()
            feed_threads.append(t)

        # Wait for all RSS threads (they loop internally)
        for t in feed_threads:
            t.join()

    def _poll_rss_feed(self, feed_cfg: dict):
        """Poll a single RSS feed and enqueue market-relevant headlines."""
        import urllib.request
        import urllib.error

        url = feed_cfg["url"]
        source = feed_cfg["source"]
        priority = feed_cfg.get("priority", 5)
        seen_guids: set[str] = set()
        poll_interval = 15  # RSS: 15 second poll

        while self._running:
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "MES-Intel-NewsBot/2.0",
                        "Accept": "application/rss+xml, application/xml, text/xml",
                    },
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    content = resp.read()

                root = ElementTree.fromstring(content)
                # Handle both RSS and Atom
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                items = root.findall(".//item") or root.findall(".//atom:entry", ns)

                for item in items:
                    # Get guid/id
                    guid_el = item.find("guid") or item.find("atom:id", ns)
                    guid = guid_el.text if guid_el is not None else ""

                    # Get title
                    title_el = item.find("title") or item.find("atom:title", ns)
                    title = title_el.text if title_el is not None else ""
                    if not title:
                        continue

                    # Dedup
                    dedup_key = guid or title
                    if dedup_key in seen_guids:
                        continue
                    seen_guids.add(dedup_key)

                    # Filter
                    if not self._is_market_relevant(title):
                        continue

                    link_el = item.find("link") or item.find("atom:link", ns)
                    link = (link_el.text if link_el is not None else
                            link_el.get("href", "") if link_el is not None else "")

                    self._enqueue_headline(
                        headline=title,
                        source=source,
                        url=link or "",
                        priority=priority,
                    )

                if len(seen_guids) > 2000:
                    seen_guids = set(list(seen_guids)[-1000:])

            except ElementTree.ParseError:
                log.debug("RSS parse error for %s", source)
            except Exception as e:
                log.debug("RSS poll error for %s: %s", source, e)

            time.sleep(poll_interval)

    def _stream_twitter(self, bearer_token: str):
        """Stream tweets from tracked accounts via Twitter/X API v2."""
        import urllib.request
        import urllib.error

        # Build filtered stream rules for tracked accounts
        accounts = list(TRACKED_ACCOUNTS.keys())
        poll_interval = max(self.config.news.poll_interval_sec, 15)  # rate limit safe
        seen_ids: set[str] = set()

        while self._running:
            for account in accounts:
                if not self._running:
                    break
                try:
                    username = account.lstrip("@")
                    # Use user tweets endpoint (simplified — real impl would use
                    # streaming filtered rules endpoint)
                    url = (
                        f"https://api.twitter.com/2/tweets/search/recent"
                        f"?query=from:{username}&max_results=10"
                        f"&tweet.fields=created_at,text"
                    )
                    req = urllib.request.Request(url, headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "User-Agent": "mes-intel/2.0",
                    })
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())

                    for tweet in data.get("data", []):
                        tweet_id = tweet.get("id", "")
                        if tweet_id in seen_ids:
                            continue
                        seen_ids.add(tweet_id)

                        text = tweet.get("text", "")
                        if not text:
                            continue

                        account_info = TRACKED_ACCOUNTS.get(account, {})
                        # Priority based on account
                    acc_priority = TRACKED_ACCOUNTS.get(account, {}).get("priority", 5)
                    self._enqueue_headline(
                        headline=f"[{account}] {text}",
                        source=f"twitter/{username}",
                        priority=acc_priority,
                    )

                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        log.debug("Twitter rate limited for %s, backing off", account)
                        time.sleep(60)
                    else:
                        log.warning("Twitter API error for %s: %s", account, e)
                except Exception:
                    log.debug("Twitter fetch error for %s", account, exc_info=True)

            # Cap seen_ids
            if len(seen_ids) > 5000:
                seen_ids = set(list(seen_ids)[-2000:])

            time.sleep(poll_interval)

    def _is_market_relevant(self, headline: str) -> bool:
        """Quick filter — does this headline have any market-moving keywords?"""
        hl = headline.lower()
        return any(kw in hl for kw in MARKET_MOVERS)

    # ------------------------------------------------------------------
    # Pre-market catalyst scanner
    # ------------------------------------------------------------------

    def scan_premarket_catalysts(self) -> list[PreMarketCatalyst]:
        """Scan for upcoming economic events and earnings releases.

        Uses Finnhub economic calendar if API key available, otherwise returns
        any manually-added catalysts.
        """
        finnhub_key = self.config.news.finnhub_key
        if not finnhub_key:
            return self.catalysts

        import urllib.request
        from datetime import datetime, timedelta

        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            url = (
                f"https://finnhub.io/api/v1/calendar/economic"
                f"?from={today}&to={tomorrow}&token={finnhub_key}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "mes-intel/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for event in data.get("economicCalendar", []):
                name = event.get("event", "")
                impact_str = event.get("impact", "low")

                impact_map = {"low": 1, "medium": 2, "high": 3}
                impact = impact_map.get(impact_str, 1)

                catalyst = PreMarketCatalyst(
                    timestamp=time.time(),
                    name=name,
                    category="economic_data",
                    expected_impact=impact,
                    consensus=str(event.get("estimate", "")),
                    previous=str(event.get("prev", "")),
                )
                self.catalysts.append(catalyst)

            log.info("Pre-market scan found %d catalysts", len(self.catalysts))

        except Exception:
            log.exception("Pre-market catalyst scan failed")

        return self.catalysts

    def add_catalyst(self, name: str, category: str, impact: int,
                     scheduled_time: Optional[float] = None):
        """Manually add a pre-market catalyst."""
        self.catalysts.append(PreMarketCatalyst(
            timestamp=scheduled_time or time.time(),
            name=name,
            category=category,
            expected_impact=min(max(impact, 1), 3),
        ))

    # ------------------------------------------------------------------
    # Desktop notifications
    # ------------------------------------------------------------------

    def _send_desktop_notification(self, headline: str, category: str, impact: float):
        """Send a desktop notification for breaking news. Graceful fallback."""
        title = f"BREAKING [{category.upper()}] Impact: {impact:.0f}/3"
        message = headline[:200]

        # Try plyer first (cross-platform)
        if self._check_plyer():
            try:
                from plyer import notification
                notification.notify(
                    title=title,
                    message=message,
                    app_name="MES Intel",
                    timeout=10,
                )
                return
            except Exception:
                log.debug("plyer notification failed", exc_info=True)

        # Fallback: macOS native notification
        try:
            import subprocess
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                timeout=5,
                capture_output=True,
            )
        except Exception:
            log.debug("Desktop notification unavailable")

    def _check_plyer(self) -> bool:
        """Check if plyer is available (cached)."""
        if self._plyer_available is None:
            try:
                import plyer  # noqa: F401
                self._plyer_available = True
            except ImportError:
                self._plyer_available = False
        return self._plyer_available

    # ------------------------------------------------------------------
    # Audio alerts
    # ------------------------------------------------------------------

    def _play_alert_sound(self, alert_type: str = "breaking"):
        """Play an audio alert for breaking news. Graceful fallback."""
        sound_file = self.alert_sounds.get(alert_type, self.alert_sounds.get("breaking"))
        if not sound_file:
            return

        sound_path = self.sound_dir / sound_file
        if not sound_path.exists():
            log.debug("Alert sound not found: %s", sound_path)
            # Try system beep as fallback
            self._system_beep()
            return

        # Try platform-appropriate audio playback
        if self._check_audio():
            try:
                import simpleaudio as sa
                wave_obj = sa.WaveObject.from_wave_file(str(sound_path))
                wave_obj.play()
                return
            except Exception:
                pass

        # Fallback: macOS afplay
        try:
            import subprocess
            subprocess.Popen(
                ["afplay", str(sound_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._system_beep()

    def _check_audio(self) -> bool:
        """Check if simpleaudio is available (cached)."""
        if self._audio_available is None:
            try:
                import simpleaudio  # noqa: F401
                self._audio_available = True
            except ImportError:
                self._audio_available = False
        return self._audio_available

    @staticmethod
    def _system_beep():
        """Last-resort audible alert."""
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Meta-learner feedback
    # ------------------------------------------------------------------

    def _on_performance_feedback(self, event: Event):
        """Receive feedback from meta-learner about news effectiveness."""
        if event.data.get("target") != "news_scanner":
            return

        # Update category effectiveness based on outcome
        outcome = event.data.get("outcome", "")
        category = event.data.get("category", "")

        if category and outcome in ("win", "loss"):
            multiplier = 1.05 if outcome == "win" else 0.95
            bound = (2.0, 0.3) if outcome == "win" else (0.3, 2.0)
            if category:
                current = self.category_effectiveness.get(category, 1.0)
                self.category_effectiveness[category] = max(
                    bound[0] if outcome == "loss" else 0.3,
                    min(bound[1] if outcome == "loss" else 2.0,
                        current * multiplier),
                )
            return

        # Legacy behavior: adjust all categories
        if outcome == "win":
            for cat in self.category_effectiveness:
                self.category_effectiveness[cat] = min(
                    self.category_effectiveness.get(cat, 1.0) * 1.05, 2.0
                )
        elif outcome == "loss":
            for cat in self.category_effectiveness:
                self.category_effectiveness[cat] = max(
                    self.category_effectiveness.get(cat, 1.0) * 0.95, 0.3
                )

    def _on_lesson_learned(self, event: Event):
        """Receive cross-agent lessons about news impact patterns."""
        data = event.data
        target = data.get("target_agent", "")
        if target not in ("news_scanner", "all"):
            return
        lesson_type = data.get("lesson_type", "")
        description = data.get("description", "")
        impact = data.get("impact_score", 0.0)
        try:
            self.db.upsert_agent_knowledge(
                agent_name="news_scanner",
                knowledge_type=f"lesson:{lesson_type}",
                key=f"ts_{int(event.timestamp)}",
                value={"description": description, "impact": impact},
                confidence=min(1.0, abs(impact)),
            )
        except Exception:
            pass

    def _on_trade_result(self, event: Event):
        """Learn from trade outcome — calibrate category effectiveness with persistence."""
        outcome = event.data.get("outcome", "")
        pnl = event.data.get("pnl", 0)
        if not outcome:
            return
        # Persist category effectiveness to DB
        try:
            for cat, eff in self.category_effectiveness.items():
                self.db.upsert_agent_knowledge(
                    agent_name="news_scanner",
                    knowledge_type="category_effectiveness",
                    key=cat,
                    value={"effectiveness": eff, "last_outcome": outcome, "last_pnl": pnl},
                    confidence=min(1.0, eff / 2.0),
                )
        except Exception:
            pass

    def _on_regime_change(self, event: Event):
        """Update current regime for impact weighting."""
        self._current_regime = event.data.get("to_regime", "unknown")

    def _on_quant_signal(self, event: Event):
        """Receive quant state — update regime from Market Brain."""
        regime = event.data.get("regime", "unknown")
        if regime and regime != "unknown":
            self._current_regime = regime

    # ------------------------------------------------------------------
    # Stats / info
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return summary stats for dashboard display."""
        now = time.time()
        recent_count = sum(
            1 for h in self.recent_headlines if (now - h["timestamp"]) < 3600
        )
        patterns_tracked = sum(len(v) for v in self.historical_impacts.values())

        return {
            "headlines_last_hour": recent_count,
            "total_buffered": len(self.recent_headlines),
            "historical_patterns": len(self.historical_impacts),
            "historical_records": patterns_tracked,
            "pending_impact_checks": len(self._pending_impact_checks),
            "active_catalysts": len(self.catalysts),
            "streaming_threads": len([t for t in self._threads if t.is_alive()]),
            "category_effectiveness": dict(self.category_effectiveness),
        }
