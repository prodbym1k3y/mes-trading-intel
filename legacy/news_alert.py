#!/usr/bin/env python3
"""
/MES Catalyst Feed v3 — What's Moving Futures RIGHT NOW
Direction arrows + time-decay + aggressive noise kill
"""

import json
import os
import re
import subprocess
import time
import hashlib
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

load_dotenv()
console = Console()

# ─── Configuration ───────────────────────────────────────────────────────────

POLL_INTERVAL = 30
IMPACT_THRESHOLD = 3
MAX_DISPLAY = 12
SEEN_EXPIRY = 3600 * 8
MAX_DETAIL_LINES = 4
ARTICLE_FETCH_TIMEOUT = 8
DESKTOP_NOTIFY_THRESHOLD = 4

# ─── macOS Notifications ─────────────────────────────────────────────────────

def notify(title: str, message: str):
    try:
        t = title.replace('"', '\\"')
        m = message.replace('"', '\\"')
        subprocess.Popen(
            ["osascript", "-e", f'display notification "{m}" with title "{t}" sound name "Glass"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

# ─── Direction Analysis ──────────────────────────────────────────────────────
#
# Tags every headline as BULLISH / BEARISH / NEUTRAL for /MES
# so the user knows which way to lean before even reading details.

_BEARISH_PATTERNS = [
    re.compile(p, re.I) for p in [
        # Crashes, selloffs, drops
        r'\b(crash|plunge|tumble|rout|selloff|sell-off|tank|collapse|meltdown|bloodbath|carnage|limit down)\b',
        r'\b(drop|fall|fell|sink|sank|slide|slid|decline|lose|lost|shed|down)\b.*(percent|%|\d+\s*point|sharply|heavily|worst)',
        # Fear/risk
        r'\bvix\b.*(spike|surge|soar|jump|above|hit \d{2})',
        r'\b(fear|panic|risk.off|flight to safety|safe haven|capitulat)',
        # Negative macro
        r'\b(recession|contract|shrink|shrank|miss|worse.than|disappoint|weak|slow)',
        r'\b(jobless|unemployment)\b.*(surge|spike|jump|rise|rose|higher|worst)',
        r'\b(cpi|inflation|pce)\b.*(hot|higher.than|above|surge|accelerat|sticky)',
        # Hawkish fed
        r'\bfed\b.*(hike|hawkish|tighten|no cut|fewer cut|delay cut)',
        r'\b(rate hike|rate increase)\b',
        # Tariffs/trade war escalation
        r'\btariff\b.*(impose|raise|increase|escalat|retali|new|sweep|blanket|reciprocal)',
        r'\btrade war\b.*(escalat|intensif|expand)',
        # Geopolitical escalation
        r'\b(attack|strike|bomb|missile|invade|invasion|offensive|escalat|casualties|killed|war)\b',
        r'\b(nuclear|nuke)\b.*(threat|launch|test|deploy)',
        # Credit stress
        r'\b(bank (run|fail|collapse)|credit (freeze|crisis)|contagion|systemic|default)',
        r'\b(downgrade|negative outlook)\b.*(sovereign|u\.?s\.?|credit|rating)',
        # Government dysfunction
        r'\b(shutdown|debt ceiling)\b.*(begin|start|imminent|fail|default|breach)',
        # Trump negative for markets
        r'\btrump\b.*(tariff|ban|restrict|sanction|threaten|demand|fire|oust|attack).*(market|stock|trade|china|fed|econom)',
        # Oil surge (inflationary)
        r'\b(oil|crude|brent|wti)\b.*(surge|spike|soar|jump|\$[89]\d|\$1[0-9]\d)',
    ]
]

_BULLISH_PATTERNS = [
    re.compile(p, re.I) for p in [
        # Rallies, gains, highs
        r'\b(rally|surge|soar|jump|gain|climb|rise|rose|recover|rebound|bounce|rip|melt.?up|limit up)\b.*(percent|%|\d+\s*point|sharply|record|all.time)',
        r'\b(record high|all.time high|new high|breakout|bull)\b',
        # Positive macro
        r'\b(beat|better.than|above.expect|surprise|strong|robust|solid|healthy|boom|expand)',
        r'\b(cpi|inflation|pce)\b.*(cool|lower|below|slow|deceler|ease|soft|fall|fell|drop|decline)',
        r'\b(jobs?|payroll|employ)\b.*(added|created|strong|robust|beat|surge|boom)',
        # Dovish fed
        r'\bfed\b.*(cut|dovish|ease|pivot|pause|patient|accommodat)',
        r'\b(rate cut|rate reduction|lower rate)\b',
        # Trade deal / de-escalation
        r'\btariff\b.*(delay|pause|exempt|roll.?back|remove|suspend|lift|reduce|deal|agree)',
        r'\b(trade deal|trade agreement|de-?escalat|ceasefire|peace|truce|stand down|withdraw)',
        # VIX crush
        r'\bvix\b.*(crush|collapse|drop|fall|fell|low|below)',
        # Trump positive for markets
        r'\btrump\b.*(deal|agree|exempt|delay|pause|cut tax|deregulat|boost)',
        # Oil drop (deflationary)
        r'\b(oil|crude|brent|wti)\b.*(crash|plunge|drop|fall|fell|tumble|slide)',
        # Stimulus/fiscal boost
        r'\b(stimulus|spending bill|infrastructure|fiscal boost|tax cut)\b.*(pass|sign|approve|announce)',
    ]
]

def get_direction(text: str) -> str:
    """Return BEAR, BULL, or — based on headline sentiment for /MES."""
    bear_hits = sum(1 for p in _BEARISH_PATTERNS if p.search(text))
    bull_hits = sum(1 for p in _BULLISH_PATTERNS if p.search(text))
    if bear_hits > bull_hits and bear_hits > 0:
        return "BEAR"
    elif bull_hits > bear_hits and bull_hits > 0:
        return "BULL"
    return "—"

# ─── Article Detail Extraction ───────────────────────────────────────────────

_DETAIL_RULES: list[tuple[re.Pattern, float, str]] = [
    # Hard numbers
    (re.compile(r'\b\d+\.?\d*\s*(%|percent|basis points?|bps)\b', re.I), 3.0, 'DATA'),
    (re.compile(r'\$[\d,.]+\s*(billion|trillion|million|B|T|M)\b', re.I), 3.0, 'DATA'),
    # Beat/miss
    (re.compile(r'\b(beat|miss|exceeded|fell short|above|below|versus|vs\.?|estimate|consensus|expected)\b', re.I), 3.0, 'BEAT/MISS'),
    (re.compile(r'\b(surprise|unexpected|shock|better.than|worse.than|hotter|cooler)\b', re.I), 3.5, 'BEAT/MISS'),
    (re.compile(r'\b(first time since|highest since|lowest since|since \d{4}|record)\b', re.I), 3.0, 'BEAT/MISS'),
    # Market reaction
    (re.compile(r'\b(futures?|e-?mini|es |spy|spx|s&p)\b.*(rose|fell|drop|jump|surge|rally|tumble|plunge)', re.I), 3.5, 'MKT MOVE'),
    (re.compile(r'\bvix\b.*(rose|fell|spike|surge|jump|above|hit|\d)', re.I), 2.5, 'MKT MOVE'),
    (re.compile(r'\b(yield|10[- ]?year|treasury)\b.*(rose|fell|drop|jump|surge|spike)', re.I), 2.5, 'MKT MOVE'),
    # Forward / guidance
    (re.compile(r'\b(guidance|outlook|forecast|project|dot plot|rate path)\b', re.I), 2.5, 'OUTLOOK'),
    # Key voices
    (re.compile(r'"[^"]{25,200}"', re.I), 2.0, 'QUOTE'),
    (re.compile(r'\b(powell|trump|yellen|waller|goolsbee|dimon|buffett)\b', re.I), 2.0, 'QUOTE'),
    # Tariff / trade
    (re.compile(r'\btariff\b.*(impose|raise|retali|escalat|exempt|delay|pause)', re.I), 2.5, 'TRADE'),
    # Positioning
    (re.compile(r'\b(dark pool|block trade|unusual|sweep|gamma|0dte|put.call)\b', re.I), 2.5, 'FLOW'),
]

_JUNK_SENTENCE = re.compile(
    r'\b(click here|subscribe|sign up|read more|newsletter|see also|'
    r'ad[- ]?free|premium|paywall|cookie|privacy policy|copyright|'
    r'download the app|share this|facebook|instagram|tiktok|'
    r'recommended for you|trending now|most read)\b', re.I
)

_TAG_LABELS = {
    'DATA': 'DATA', 'BEAT/MISS': 'BEAT/MISS', 'OUTLOOK': 'OUTLOOK',
    'MKT MOVE': 'MKT MOVE', 'QUOTE': 'QUOTE', 'TRADE': 'TRADE', 'FLOW': 'FLOW',
}


def extract_article_details(html: str, title: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'aside',
                              'header', 'form', 'iframe', 'noscript', 'svg', 'button']):
        tag.decompose()

    article = (
        soup.find('article') or
        soup.find('div', class_=re.compile(r'article|story|content|post-body|entry-content', re.I)) or
        soup.find('div', {'id': re.compile(r'article|story|content', re.I)}) or
        soup.body
    )
    if not article:
        return []

    paragraphs = article.find_all('p')
    full_text = ' '.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 25)
    if len(full_text) < 100:
        return []

    full_text = re.sub(r'(?<=[A-Z])\.(?=[A-Z])', '_DOT_', full_text)
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    sentences = [s.replace('_DOT_', '.').strip() for s in sentences]
    sentences = [s for s in sentences if 35 < len(s) < 400]

    if not sentences:
        return []

    title_words = set(re.findall(r'\b\w{4,}\b', title.lower()))
    scored: list[tuple[float, str, str]] = []

    for sent in sentences:
        if _JUNK_SENTENCE.search(sent):
            continue
        total_score = 0.0
        best_tag = ''
        best_tag_score = 0.0
        for pattern, weight, tag in _DETAIL_RULES:
            if pattern.search(sent):
                total_score += weight
                if weight > best_tag_score:
                    best_tag_score = weight
                    best_tag = tag
        sent_words = set(re.findall(r'\b\w{4,}\b', sent.lower()))
        overlap = len(title_words & sent_words)
        if overlap >= 2:
            total_score += 0.3 * overlap
        if len(re.findall(r'\b\d+[\d,.]*', sent)) >= 2:
            total_score += 1.0
        if overlap >= len(title_words) * 0.8 and len(title_words) > 3:
            total_score -= 3.0
        if total_score >= 2.0:
            scored.append((total_score, best_tag, sent))

    scored.sort(key=lambda x: -x[0])

    details = []
    seen_tags: dict[str, int] = {}
    seen_content: list[set] = []

    for score, tag, sent in scored:
        if len(details) >= MAX_DETAIL_LINES:
            break
        seen_tags[tag] = seen_tags.get(tag, 0) + 1
        if seen_tags[tag] > 2:
            continue
        sent_words = set(re.findall(r'\b\w{4,}\b', sent.lower()))
        if any(len(sent_words & prev) > len(sent_words) * 0.6 for prev in seen_content):
            continue
        seen_content.append(sent_words)
        if len(sent) > 200:
            sent = sent[:197] + "..."
        tag_label = _TAG_LABELS.get(tag, tag)
        details.append(f"[{tag_label}] {sent}")

    return details


# ─── Article Fetcher ──────────────────────────────────────────────────────────

class ArticleFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        self.cache: dict[str, list[str]] = {}
        self.pending: set[str] = set()
        self.lock = threading.Lock()

    def fetch_details(self, item: 'NewsItem'):
        if item.fingerprint in self.cache:
            return
        with self.lock:
            if item.fingerprint in self.pending:
                return
            self.pending.add(item.fingerprint)
        try:
            url = item.url
            if not url or 'news.google.com' in url:
                url = None
            if url:
                resp = self.session.get(url, timeout=ARTICLE_FETCH_TIMEOUT, allow_redirects=True)
                if resp.status_code == 200:
                    details = extract_article_details(resp.text, item.title)
                    if details:
                        with self.lock:
                            self.cache[item.fingerprint] = details
                            self.pending.discard(item.fingerprint)
                        return
            # Fallback: RSS description
            if item.description and len(item.description) > 50:
                details = self._from_description(item.description, item.title)
                with self.lock:
                    self.cache[item.fingerprint] = details
                    self.pending.discard(item.fingerprint)
                return
            with self.lock:
                self.cache[item.fingerprint] = []
                self.pending.discard(item.fingerprint)
        except Exception:
            with self.lock:
                self.cache[item.fingerprint] = []
                self.pending.discard(item.fingerprint)

    def _from_description(self, desc: str, title: str) -> list[str]:
        sentences = re.split(r'(?<=[.!?])\s+', desc)
        sentences = [s.strip() for s in sentences if 30 < len(s.strip()) < 400]
        title_words = set(re.findall(r'\b\w{4,}\b', title.lower()))
        results = []
        for sent in sentences:
            if _JUNK_SENTENCE.search(sent):
                continue
            sent_words = set(re.findall(r'\b\w{4,}\b', sent.lower()))
            if len(title_words & sent_words) >= len(title_words) * 0.7 and len(title_words) > 3:
                continue
            best_tag = 'DATA'
            for pattern, weight, tag in _DETAIL_RULES:
                if pattern.search(sent) and weight > 1.5:
                    best_tag = tag
                    break
            if len(sent) > 200:
                sent = sent[:197] + "..."
            tag_label = _TAG_LABELS.get(best_tag, best_tag)
            results.append(f"[{tag_label}] {sent}")
            if len(results) >= MAX_DETAIL_LINES:
                break
        return results

    def get_details(self, fp: str) -> list[str] | None:
        with self.lock:
            return self.cache.get(fp)

    def fetch_batch(self, items: list['NewsItem']):
        to_fetch = [i for i in items if i.url
                    and i.fingerprint not in self.cache
                    and i.fingerprint not in self.pending][:12]
        if not to_fetch:
            return
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(self.fetch_details, item): item for item in to_fetch}
            for f in as_completed(futs, timeout=ARTICLE_FETCH_TIMEOUT + 3):
                try:
                    f.result()
                except Exception:
                    pass


# ─── Noise Kill Filters ──────────────────────────────────────────────────────
#
# AGGRESSIVE. If in doubt, kill it. The user wants ONLY catalysts.

KILL_NOISE = re.compile(
    r'\b(movie|film|tv show|album|song|concert|recipe|fashion|'
    r'celebrity|kardashian|grammy|oscar|emmy|nfl|nba|mlb|nhl|super bowl|'
    r'world cup|olympic|wedding|divorce|baby|pet|horoscope|zodiac|'
    r'crypto|bitcoin|ethereum|solana|meme coin|nft|'
    r'personal finance|save money|budget|credit card reward|'
    r'best (stock|etf|fund)s? to buy|top \d+ (stock|pick)|retirement plan|'
    r'how to invest|beginner|for dummies|guide to|'
    r'what you need to know|here.s what|things to watch|weekly recap|'
    r'morning brief|evening brief|daily roundup|market wrap|'
    r'what it means for|what to expect|what to know|key takeaway|'
    r'explained|everything you|all you need|in (focus|brief|review)|'
    r'pros and cons|round.?up|cheat sheet|'
    r'touchdown|quarterback|coach|roster|bench|playoff|draft pick|'
    r'free agent|injury report|game \d|season \d|championship|preseason|'
    r'halftime|lineup|penalty|goalkeeper|mornings with maria|book tour)\b', re.I
)

KILL_QUESTIONS = re.compile(
    r'^(will|can|could|should|are|does|do|has|have|was|were|would|might|may)\b.{10,}', re.I
)
KILL_IS_QUESTION = re.compile(
    r'^is\s+(the|a|an|this|it|that|there|inflation|the fed|rate|oil|stock|market)\b', re.I
)
KILL_WH_QUESTION = re.compile(r'^(what|how|why|where|when)\b.*\?\s*$', re.I)

KILL_CLICKBAIT = re.compile(
    r'^(watch|forget|ignore|don.t miss|don.t bet|here.s (why|how|what)|'
    r'the (real|true|big|biggest) (reason|problem|risk|question)|'
    r'the case (for|against)|why you should|you should|this is (why|how)|'
    r'everything you|all you need|it.s time to|time to|'
    r'brace for|prepare for|get ready|ready for|'
    r'\d+ (reason|thing|way|tip|sign|stock|pick)s?\b)', re.I
)

KILL_SPECULATION = re.compile(
    r'\b(fed|federal reserve)\b.*(could|might|may|likely|unlikely|possibly|expected to|seen|viewed|perceived).*(cut|hike|hold|pause|rate)|'
    r'\b(rate cut|rate hike)\b.*(odds|probability|pricing|expect|bet|imply|market|futures|chance|percent)|'
    r'\binflation\b.*(expectation|forecast|outlook|projected|seen|viewed|trajectory|path|trend)|'
    r'\b(hawkish|dovish)\b.*(tone|stance|lean|tilt|signal|sentiment|expectation)|'
    r'\bfed\b.*(on track|steady|patient|wait|data.dependent|no rush|in no hurry)|'
    r'\b(fairly|cautiously|remains?) optimistic\b|'
    r'\bsees? (circumstances|scenario|possibility|chance)\b|'
    r'\bretains? .*(projection|forecast|view|outlook)\b', re.I
)

KILL_FED_ADMIN = re.compile(
    r'federal reserve board announces (approval|termination|appointment|it will hold)|'
    r'(external review|internal review|inspector general|audit|investigation).*(fed|federal reserve|bank failure)', re.I
)

KILL_OPINION = re.compile(
    r'\b(opinion|editorial|column|commentary|my take|'
    r'motley fool|seeking alpha|analyst (picks|favorites)|'
    r'stock screener|watchlist|portfolio ideas?|what investors should)\b', re.I
)

KILL_RETRO = re.compile(
    r'\b(review of|lessons from|looking back|anniversary|years? ago|'
    r'what we learned|post-?mortem|retrospective|in hindsight|'
    r'history (of|shows)|could have|should have|would have|'
    r'new (book|study|paper) (on|about|finds|shows))\b', re.I
)

KILL_PREVIEW = re.compile(
    r'\b(ahead of|looking ahead|what to watch|preview|previewing|'
    r'to watch this week|to watch today|on the radar|on tap|'
    r'week ahead|day ahead|things to know|before the bell|'
    r'what.s next|what comes next|eyes on|all eyes|'
    r'traders (eye|await|watch|brace|prepare)|market (eyes?|awaits?))\b', re.I
)

KILL_NON_MES = re.compile(
    r'\b(EUR/USD|GBP/USD|USD/JPY|USD/CHF|AUD/USD|NZD/USD|USD/CAD|'
    r'forex pair|currency pair|indian bonds?|japan bonds?|german bund|gilts|jgb|'
    r'gold price|silver price|copper price|palladium|platinum|'
    r'kospi|nikkei|hang seng|dax|ftse|cac|stoxx|asx|sensex|nifty|'
    r'shanghai composite|bovespa)\b', re.I
)


def is_killed(headline: str, full_text: str) -> bool:
    """Returns True if this headline should be completely filtered out."""
    h = headline.strip()

    # Questions
    if KILL_QUESTIONS.match(h):
        return True
    if KILL_IS_QUESTION.match(h):
        return True
    if KILL_WH_QUESTION.match(h):
        return True
    # Ends in ? (any headline)
    if h.endswith('?'):
        return True
    # Clickbait framing
    if KILL_CLICKBAIT.match(h):
        return True

    # Content filters on full text (headline + description)
    if KILL_FED_ADMIN.search(full_text):
        return True
    if KILL_NON_MES.search(full_text):
        return True
    if KILL_NOISE.search(full_text):
        return True

    # Speculation without actual data/decision
    if KILL_SPECULATION.search(full_text):
        has_decision = re.search(r'\bfed\b.*(vote|decided|announce|statement|cut.*basis|hike.*basis)', full_text, re.I)
        has_data = re.search(r'\b(cpi|pce|nfp|payroll|gdp|jobless|ppi)\b.*(data|report|print|came in|release)', full_text, re.I)
        if not has_decision and not has_data:
            return True

    # Preview/lookahead — not a catalyst, just anticipation
    if KILL_PREVIEW.search(full_text):
        has_actual = re.search(r'\b(just|breaking|now|confirmed|announced|signed|passed|launched|struck|attacked)\b', full_text, re.I)
        if not has_actual:
            return True

    # Opinion/retrospective only if no hard financial context
    has_fin = bool(re.search(r'\b(futures?|spx|spy|s&p|tariff|fed vote|trump sign|attack|strike|bomb)\b', full_text, re.I))
    if KILL_OPINION.search(full_text) and not has_fin:
        return True
    if KILL_RETRO.search(full_text) and not has_fin:
        return True

    return False


# ─── Impact Scoring ──────────────────────────────────────────────────────────
#
# TIER 5 = this is happening NOW — check /MES immediately
# TIER 4 = real catalyst with confirmed data/action — will move /MES
# TIER 3 = notable — real event but may be priced in

IMPACT_RULES: list[tuple[str, int, str]] = [
    # ═══════════════════════════════════════════
    # TIER 5 — HAPPENING NOW, CHECK /MES
    # ═══════════════════════════════════════════

    # FOMC actual decision
    (r'\bfed\b.*(vote|voted|votes)\b.*(hold|cut|hike|raise|lower|unchanged)', 5, 'FED'),
    (r'\bfomc\b.*(decision|statement)\b.*(cut|hike|hold|unchanged|basis|bps)', 5, 'FED'),
    (r'\bfederal reserve\b.*(cut|hike|raise|lower)s?\b.*(rate|basis|bps)', 5, 'FED'),
    (r'\b(emergency|surprise|inter-?meeting).*(rate|fed\b|fomc)', 5, 'FED'),

    # Live crisis
    (r'\bcircuit breaker\b.*(trigger|hit|halt|activated)', 5, 'CRISIS'),
    (r'\b(trading halt|market halt|limit down|limit up)\b.*(trigger|hit|activated)', 5, 'CRISIS'),
    (r'\bflash crash\b', 5, 'CRISIS'),
    (r'\b(bank run|bank collapse)\b.*(today|now|this morning|breaking|underway)', 5, 'CRISIS'),

    # Nuclear / massive escalation
    (r'\b(nuclear|nuke)\b.*(threat|test|deploy|launch|warn|escalat|alert)', 5, 'GEO'),

    # ═══════════════════════════════════════════
    # TIER 4 — CONFIRMED CATALYST
    # ═══════════════════════════════════════════

    # Trump — THE /MES mover
    (r'\btrump\b.*(tariff|executive order|ban|restrict|sanction|sign|order|direct|demand)', 4, 'TRUMP'),
    (r'\btrump\b.*(truth social|post|tweet|said|says|warn|threat|vow|promise|announce)', 4, 'TRUMP'),
    (r'\btrump\b.*(fire|replace|remove|oust|demote).*(chair|chief|director|secretary|general|adviser)', 4, 'TRUMP'),
    (r'\btrump\b.*(china|eu|europe|canada|mexico|japan|korea|nato|iran|russia)', 4, 'TRUMP'),
    (r'\bwhite house\b.*(announce|tariff|executive order|sanction|ban|restrict|trade|tax)', 4, 'TRUMP'),
    (r'\btruth social\b', 4, 'TRUMP'),

    # War / active military (the user's #1 ask)
    (r'\b(military strike|air strike|airstrike|bomb|attack|missile|drone strike)\b.*(iran|israel|china|taiwan|russia|ukraine|nato|u\.?s\.?|yemen|houthi|lebanon|gaza)', 4, 'WAR'),
    (r'\b(iran|israel|russia|china|ukraine|taiwan|hamas|hezbollah|houthi)\b.*(attack|strike|launch|invade|escalat|retali|fire|shell|intercept|offensive|bomb)', 4, 'WAR'),
    (r'\b(ceasefire|peace deal|truce|armistice)\b.*(break|collapse|sign|announce|reach|agree|reject|violat)', 4, 'WAR'),
    (r'\b(war|conflict|invasion|bombing|airstrike|troops|casualties|killed|wounded)\b.*(break|develop|confirm|launch|begin|start)', 4, 'WAR'),
    (r'\b(nato|pentagon|defense secretary|joint chiefs)\b.*(deploy|mobilize|alert|announce|order|activate)', 4, 'WAR'),
    (r'\b(coup|regime change|assassination|overthrow)\b', 4, 'WAR'),
    (r'\b(strait of hormuz|taiwan strait|red sea|suez)\b.*(block|close|disrupt|attack|threat|military)', 4, 'WAR'),
    (r'\b(middle east|gaza|lebanon|hezbollah|hamas|iran|israel)\b.*(escalat|intensif|expand|ground|offensive|invasion|ceasefire)', 4, 'WAR'),
    (r'\b(sanction|embargo)\b.*(impose|new|expand|escalat|announce).*(russia|china|iran)', 4, 'WAR'),

    # CPI / PCE — actual data
    (r'\bcpi\b.*(data|report|reading|print|came in|rose|fell|higher|lower|surprise|hot|cool|release|show|\d+\.?\d*\s*%)', 4, 'CPI'),
    (r'\bpce\b.*(data|index|reading|came in|rose|fell|show|\d+\.?\d*\s*%)', 4, 'PCE'),
    (r'\bcore (cpi|pce|inflation)\b.*(rose|fell|came|print|data|higher|lower|\d)', 4, 'CPI'),

    # Jobs — actual data
    (r'\b(non-?farm|payrolls?)\b.*(report|data|added|lost|beat|miss|surge|plunge|came in|\d)', 4, 'NFP'),
    (r'\bjobless claims\b.*(rose|fell|jump|drop|surge|spike|surprise|\d)', 4, 'CLAIMS'),
    (r'\bunemployment rate\b.*(rose|fell|hit|reach|surprise|\d)', 4, 'JOBS'),

    # GDP
    (r'\bgdp\b.*(grew|shrank|contract|expand|revised|surprise|miss|beat|came in|\d+\.?\d*\s*%)', 4, 'GDP'),
    (r'\brecession\b.*(official|confirm|enter|declare|technical)', 4, 'GDP'),

    # Other key macro
    (r'\b(retail sales|consumer spending)\b.*(fell|rose|drop|surge|miss|beat|came in|\d)', 4, 'DATA'),
    (r'\bism\b.*(manufacturing|services).*(contract|expand|came in|miss|beat|\d)', 4, 'DATA'),
    (r'\b(producer price|ppi)\b.*(data|report|rose|fell|came in)', 4, 'DATA'),

    # Tariffs — action (not discussion)
    (r'\btariff\b.*(impose|announce|raise|increase|retali|new|sweep|blanket|reciprocal|escalat|effective|begin)', 4, 'TARIFF'),
    (r'\btariff\b.*(delay|pause|exempt|roll.?back|remove|suspend|lift|reduce)', 4, 'TARIFF'),
    (r'\btrade war\b.*(escalat|intensif|new|cease|deal|agreement)', 4, 'TARIFF'),
    (r'\b(china|eu|europe|canada|mexico)\b.*(retali|counter|respond).*(tariff|trade|sanction)', 4, 'TARIFF'),

    # Debt ceiling / fiscal crisis
    (r'\bdebt ceiling\b.*(default|deadline|breach|crisis|deal|vote|pass|fail)', 4, 'FISCAL'),
    (r'\bcredit (downgrade|rating).*(u\.?s\.?|united states|sovereign)', 4, 'FISCAL'),
    (r'\bgovernment shutdown\b.*(begin|start|avert|deal|vote|imminent)', 4, 'FISCAL'),

    # Mega-cap earnings RESULTS
    (r'\b(apple|aapl|microsoft|msft|nvidia|nvda|amazon|amzn)\b.*(earn|revenue|beat|miss|guidance|results|eps)', 4, 'EARNINGS'),
    (r'\b(alphabet|google|goog|meta|tesla|tsla)\b.*(earn|revenue|beat|miss|guidance|results|eps)', 4, 'EARNINGS'),
    (r'\b(berkshire|broadcom|avgo|jpmorgan|jpm)\b.*(earn|revenue|beat|miss|results|eps)', 4, 'EARNINGS'),

    # Powell live speech/testimony
    (r'\bpowell\b.*(testimony|press conference|speaks|speaking|testif|live)', 4, 'FED'),
    (r'\bdot plot\b.*(shift|change|show|reveal|surprise)', 4, 'FED'),
    (r'\bfed\b.*(pivot|unanimous|dissent)', 4, 'FED'),

    # Yields — significant moves
    (r'\b(10[- ]?year|treasury|bond) yield\b.*(surge|spike|plunge|hit|record|above|below)', 4, 'YIELDS'),
    (r'\byield curve\b.*(invert|un-?invert|steepen|flatten)', 4, 'YIELDS'),

    # VIX
    (r'\bvix\b.*(spike|surge|soar|above|hit|record|jump|explod|\d{2})', 4, 'VIX'),

    # Oil — big moves
    (r'\b(crude|oil|brent|wti)\b.*(surge|spike|crash|plunge|soar|jump|tumble|\$\d{2,3})', 4, 'OIL'),
    (r'\bopec\b.*(cut|boost|surprise|emergency|agree|output)', 4, 'OIL'),

    # /MES direct
    (r'\b(s&p 500|spx|spy|mes|e-?mini)\b.*(crash|correction|bear|record|all[- ]?time|halt|worst|best)', 4, 'MES'),
    (r'\bstock (futures?|market)\b.*(crash|rout|plunge|selloff|sell-off|tumble|surge|soar)', 4, 'FUTURES'),
    (r'\bfutures?\b.*(plunge|crash|surge|soar|limit|gap|tumble|rout|rally)', 4, 'FUTURES'),

    # Options flow
    (r'\b(dark pool|block trade|unusual (option|volume|activity))\b', 4, 'FLOW'),
    (r'\b(gamma (squeeze|exposure|flip)|0dte|dealer (hedg|position|gamma))\b', 4, 'FLOW'),

    # ═══════════════════════════════════════════
    # TIER 3 — NOTABLE (real but may be priced in)
    # ═══════════════════════════════════════════

    # Trump softer mentions
    (r'\btrump\b.*(market|stock|econom|trade|tariff|china|fed|deal|negotiat)', 3, 'TRUMP'),

    # War — tension/buildup (not active combat)
    (r'\b(iran|israel|russia|ukraine|china|taiwan)\b.*(tension|military|buildup|deploy|threaten|warn|ultimatum)', 3, 'WAR'),

    # Fed speakers — only if they say something NEW
    (r'\b(powell|waller|bostic|kashkari|williams|daly|barkin|goolsbee|bowman|kugler)\b.*(warn|surprise|shift|reverse|pivot|disagree|dissent|push back)', 3, 'FED'),

    # Broad index moves
    (r'\b(dow|nasdaq|russell)\b.*(drop|fall|crash|surge|rally|record|worst|best)', 3, 'INDEX'),
    (r'\b(bear market|correction)\b.*(enter|official|territory)', 3, 'INDEX'),
    (r'\b(stock market|wall street)\b.*(crash|rout|plunge|selloff|rally|surge|soar|rebound)', 3, 'INDEX'),

    # Tariff discussion
    (r'\btariff\b.*(consider|plan|propose|discuss|threaten|may|could|weigh)', 3, 'TARIFF'),

    # Earnings broader
    (r'\bearnings (season|beat rate|miss rate)\b', 3, 'EARNINGS'),
    (r'\bprofit warning\b', 3, 'EARNINGS'),

    # Oil smaller moves
    (r'\b(crude|oil)\b.*(rose|fell|drop|gain|up|down)\b.*(%|\$|barrel)', 3, 'OIL'),

    # Layoffs at scale
    (r'\b(layoff|job cut)\b.*(thousand|massive|major|wave|\d{3,})', 3, 'LABOR'),

    # Credit stress
    (r'\b(credit spread|high yield)\b.*(widen|blow|spike)', 3, 'CREDIT'),

    # Dollar big moves
    (r'\b(dollar|dxy)\b.*(surge|spike|plunge|crash|soar|record)', 3, 'DOLLAR'),
]

_COMPILED_RULES = [(re.compile(p, re.I), w, c) for p, w, c in IMPACT_RULES]


def score_headline(text: str) -> tuple[int, list[str]]:
    """Score headline for /MES impact. Returns (score, [categories])."""
    max_score = 0
    cats = []
    for pattern, weight, category in _COMPILED_RULES:
        if pattern.search(text):
            if weight > max_score:
                max_score = weight
            cats.append(category)
    return max_score, list(dict.fromkeys(cats))


# ─── RSS Feeds ───────────────────────────────────────────────────────────────
#
# Lean set — fast wire services + targeted Google searches.
# No Fed direct feed (admin noise), no generic financial news.

RSS_FEEDS = [
    # ── Wire services (fastest breaking) ──
    ("CNBC Economy",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("CNBC Finance",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch",         "https://feeds.marketwatch.com/marketwatch/marketpulse"),
    ("WSJ Markets",         "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    ("ZeroHedge",           "https://feeds.feedburner.com/zerohedge/feed"),

    # ── Trump — everything he does/says ──
    ("G:Trump",             "https://news.google.com/rss/search?q=%22Trump%22+AND+(%22tariff%22+OR+%22executive+order%22+OR+%22Truth+Social%22+OR+%22trade+war%22+OR+%22sanctions%22+OR+%22ban%22+OR+%22signs%22+OR+%22fires%22+OR+%22threatens%22+OR+%22announces%22+OR+%22demands%22+OR+%22orders%22+OR+%22posts%22+OR+%22China%22+OR+%22market%22)&when=6h&hl=en-US&gl=US&ceid=US:en"),

    # ── War / Middle East / Geopolitics ──
    ("G:MidEast",           "https://news.google.com/rss/search?q=(%22Israel%22+OR+%22Iran%22+OR+%22Hezbollah%22+OR+%22Hamas%22+OR+%22Gaza%22+OR+%22Houthi%22+OR+%22Red+Sea%22+OR+%22Lebanon%22)+AND+(%22strike%22+OR+%22attack%22+OR+%22escalat%22+OR+%22ceasefire%22+OR+%22war%22+OR+%22missile%22+OR+%22bomb%22+OR+%22killed%22+OR+%22troops%22+OR+%22offensive%22)&when=6h&hl=en-US&gl=US&ceid=US:en"),
    ("G:Russia/Ukraine",    "https://news.google.com/rss/search?q=(%22Russia%22+OR+%22Ukraine%22)+AND+(%22escalat%22+OR+%22nuclear%22+OR+%22NATO%22+OR+%22ceasefire%22+OR+%22offensive%22+OR+%22sanctions%22+OR+%22attack%22+OR+%22strike%22+OR+%22troops%22)&when=12h&hl=en-US&gl=US&ceid=US:en"),
    ("G:China/Taiwan",      "https://news.google.com/rss/search?q=(%22China%22+OR+%22Taiwan%22)+AND+(%22military%22+OR+%22invasion%22+OR+%22blockade%22+OR+%22strait%22+OR+%22sanctions%22+OR+%22chip+ban%22+OR+%22trade+war%22+OR+%22troops%22+OR+%22attack%22)&when=12h&hl=en-US&gl=US&ceid=US:en"),

    # ── Breaking / Crisis ──
    ("G:Breaking",          "https://news.google.com/rss/search?q=%22breaking%22+AND+(%22market%22+OR+%22stocks%22+OR+%22Trump%22+OR+%22war%22+OR+%22attack%22+OR+%22sanctions%22+OR+%22crash%22+OR+%22tariff%22+OR+%22futures%22)&when=4h&hl=en-US&gl=US&ceid=US:en"),
    ("G:Futures/Crisis",    "https://news.google.com/rss/search?q=%22stock+futures%22+OR+%22S%26P+500+futures%22+OR+%22market+crash%22+OR+%22circuit+breaker%22+OR+%22flash+crash%22+OR+%22limit+down%22+OR+%22VIX+spike%22&when=6h&hl=en-US&gl=US&ceid=US:en"),

    # ── Macro data releases ──
    ("G:Macro Data",        "https://news.google.com/rss/search?q=%22CPI+report%22+OR+%22CPI+data%22+OR+%22jobs+report%22+OR+%22nonfarm+payrolls%22+OR+%22GDP+report%22+OR+%22jobless+claims%22+OR+%22retail+sales+data%22+OR+%22PCE+data%22+OR+%22PPI+data%22&when=12h&hl=en-US&gl=US&ceid=US:en"),

    # ── Tariff actions ──
    ("G:Tariffs",           "https://news.google.com/rss/search?q=%22tariff%22+AND+(%22impose%22+OR+%22retaliat%22+OR+%22escalat%22+OR+%22delay%22+OR+%22exempt%22+OR+%22reciprocal%22+OR+%22effective%22+OR+%22announce%22)&when=6h&hl=en-US&gl=US&ceid=US:en"),

    # ── Mega-cap earnings ──
    ("G:Earnings",          "https://news.google.com/rss/search?q=(%22AAPL%22+OR+%22MSFT%22+OR+%22NVDA%22+OR+%22AMZN%22+OR+%22GOOG%22+OR+%22META%22+OR+%22TSLA%22)+AND+(%22earnings%22+OR+%22beat%22+OR+%22miss%22+OR+%22guidance%22+OR+%22results%22)&when=1d&hl=en-US&gl=US&ceid=US:en"),

    # ── Oil ──
    ("G:Oil",               "https://news.google.com/rss/search?q=(%22crude+oil%22+OR+%22WTI%22+OR+%22Brent%22+OR+%22OPEC%22)+AND+(%22surge%22+OR+%22crash%22+OR+%22spike%22+OR+%22plunge%22+OR+%22cut%22+OR+%22production%22)&when=12h&hl=en-US&gl=US&ceid=US:en"),
]

# ── Twitter/X Accounts ──

TWITTER_BEARER = os.getenv("TWITTER_BEARER_TOKEN", "")

TWITTER_ACCOUNTS = [
    ("X:Trump",            "realDonaldTrump"),
    ("X:WalterBloomberg",  "DeItaone"),
    ("X:FirstSquawk",      "FirstSquawk"),
    ("X:LiveSquawk",       "LiveSquawk"),
    ("X:unusual_whales",   "unusual_whales"),
    ("X:zaborhedge",       "zaborhedge"),
    ("X:Fxhedgers",        "Fxhedgers"),
    ("X:FinancialJuice",   "financialjuice"),
]

# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published: datetime
    score: int = 0
    categories: list[str] = field(default_factory=list)
    fingerprint: str = ""
    description: str = ""
    direction: str = "—"
    is_tweet: bool = False

    def __post_init__(self):
        if not self.fingerprint:
            clean = re.sub(r'\s*[-–—|]\s*[\w\s.]+$', '', self.title)
            norm = re.sub(r'\s+', ' ', clean.lower().strip())
            self.fingerprint = hashlib.md5(norm.encode()).hexdigest()[:12]

    @property
    def title_words(self) -> set[str]:
        return set(re.findall(r'\b\w{4,}\b', self.title.lower()))

    @property
    def age_minutes(self) -> int:
        try:
            pub = self.published if self.published.tzinfo else self.published.replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - pub).total_seconds() / 60)
        except Exception:
            return 0

    @property
    def display_score(self) -> int:
        """Score with time-decay applied. Old news drops in display priority."""
        age_h = self.age_minutes / 60
        if age_h > 24:
            return max(2, self.score - 2)
        elif age_h > 6:
            return max(2, self.score - 1)
        return self.score


# ─── Core Engine ─────────────────────────────────────────────────────────────

class NewsEngine:
    def __init__(self):
        self.seen: dict[str, float] = {}
        self.items: list[NewsItem] = []
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        self.lock = threading.Lock()
        self.last_poll = None
        self.feed_status: dict[str, str] = {}
        self.fetcher = ArticleFetcher()
        self._twitter_user_ids: dict[str, str] = {}
        self._last_twitter_poll: float = 0
        self._twitter_poll_interval = 3600

    def _process_entry(self, name: str, title: str, link: str, pub_dt: datetime,
                       desc_text: str) -> NewsItem | None:
        """Score and filter a single RSS entry. Returns None if killed."""
        combined = f"{title} {desc_text[:500]}" if desc_text else title
        headline_clean = re.sub(r'\s*[-–—|]\s*[\w\s.]+$', '', title).strip()

        # Kill filter first
        if is_killed(headline_clean, combined):
            return None

        score, cats = score_headline(combined)
        if score < IMPACT_THRESHOLD:
            return None

        direction = get_direction(combined)
        is_tweet = name.startswith("X:")

        # Trump's tweets always matter
        if name == "X:Trump" and score < 4:
            score = 4
            if 'TRUMP' not in cats:
                cats = ['TRUMP'] + cats

        return NewsItem(
            title=title, source=name, url=link.strip(),
            published=pub_dt, score=score, categories=cats,
            description=desc_text[:500], direction=direction,
            is_tweet=is_tweet,
        )

    def parse_rss(self, name: str, url: str) -> list[NewsItem]:
        items = []
        try:
            resp = self.session.get(url, timeout=8)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('.//item')
            if not entries:
                entries = root.findall('.//atom:entry', ns)

            for entry in entries:
                title_el = entry.find('title')
                if title_el is None:
                    title_el = entry.find('atom:title', ns)
                if title_el is None or not title_el.text:
                    continue
                title = title_el.text.strip()

                link_el = entry.find('link')
                if link_el is None:
                    link_el = entry.find('atom:link', ns)
                link = ""
                if link_el is not None:
                    link = link_el.text or link_el.get('href', '') or ""

                pub_el = entry.find('pubDate')
                if pub_el is None:
                    pub_el = entry.find('atom:published', ns)
                if pub_el is None:
                    pub_el = entry.find('atom:updated', ns)
                pub_dt = datetime.now(timezone.utc)
                if pub_el is not None and pub_el.text:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub_el.text)
                    except Exception:
                        pass

                desc_el = entry.find('description')
                if desc_el is None:
                    desc_el = entry.find('atom:summary', ns)
                    if desc_el is None:
                        desc_el = entry.find('atom:content', ns)
                desc_text = ""
                if desc_el is not None and desc_el.text:
                    desc_text = BeautifulSoup(desc_el.text, 'html.parser').get_text()

                item = self._process_entry(name, title, link, pub_dt, desc_text)
                if item:
                    items.append(item)

            self.feed_status[name] = "OK"
        except requests.exceptions.Timeout:
            self.feed_status[name] = "TIMEOUT"
        except Exception as e:
            self.feed_status[name] = f"ERR: {str(e)[:30]}"
        return items

    # ── Twitter API v2 ──

    def _twitter_headers(self) -> dict:
        return {"Authorization": f"Bearer {TWITTER_BEARER}"}

    def _resolve_twitter_user_id(self, username: str) -> str | None:
        if username in self._twitter_user_ids:
            return self._twitter_user_ids[username]
        try:
            resp = requests.get(
                f"https://api.x.com/2/users/by/username/{username}",
                headers=self._twitter_headers(), timeout=8,
            )
            if resp.status_code == 200:
                uid = resp.json().get("data", {}).get("id")
                if uid:
                    self._twitter_user_ids[username] = uid
                    return uid
        except Exception:
            pass
        return None

    def _fetch_user_tweets(self, display_name: str, username: str) -> list[NewsItem]:
        items = []
        try:
            user_id = self._resolve_twitter_user_id(username)
            if not user_id:
                self.feed_status[display_name] = "NO USER ID"
                return []

            since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = requests.get(
                f"https://api.x.com/2/users/{user_id}/tweets",
                headers=self._twitter_headers(),
                params={
                    "max_results": 15,
                    "start_time": since,
                    "tweet.fields": "created_at,text",
                    "exclude": "retweets,replies",
                },
                timeout=8,
            )

            if resp.status_code == 429:
                self.feed_status[display_name] = "RATE LIMIT"
                return []
            if resp.status_code != 200:
                self.feed_status[display_name] = f"HTTP {resp.status_code}"
                return []

            for tweet in resp.json().get("data", []):
                text = tweet.get("text", "").strip()
                if not text or len(text) < 10:
                    continue

                pub_dt = datetime.now(timezone.utc)
                created = tweet.get("created_at", "")
                if created:
                    try:
                        pub_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    except Exception:
                        pass

                tweet_id = tweet.get("id", "")
                url = f"https://x.com/{username}/status/{tweet_id}" if tweet_id else ""

                item = self._process_entry(display_name, text[:280], url, pub_dt, text)
                if item:
                    items.append(item)

            self.feed_status[display_name] = "OK"
        except requests.exceptions.Timeout:
            self.feed_status[display_name] = "TIMEOUT"
        except Exception as e:
            self.feed_status[display_name] = f"ERR: {str(e)[:30]}"
        return items

    def _fetch_twitter_feeds(self) -> list[NewsItem]:
        if not TWITTER_BEARER:
            for name, _ in TWITTER_ACCOUNTS:
                self.feed_status[name] = "NO API KEY"
            return []

        all_items: list[NewsItem] = []
        results: list[list[NewsItem]] = [[] for _ in TWITTER_ACCOUNTS]

        def fetch(idx, name, user):
            results[idx] = self._fetch_user_tweets(name, user)

        threads = []
        for i, (name, user) in enumerate(TWITTER_ACCOUNTS):
            t = threading.Thread(target=fetch, args=(i, name, user), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=12)
        for batch in results:
            all_items.extend(batch)
        return all_items

    # ── Main Poll ──

    def poll_all(self) -> list[NewsItem]:
        rss_results: list[list[NewsItem]] = [[] for _ in RSS_FEEDS]
        twitter_items: list[NewsItem] = []
        twitter_done = threading.Event()

        now_epoch = time.time()
        should_poll_twitter = (now_epoch - self._last_twitter_poll) >= self._twitter_poll_interval

        def fetch_rss(idx, name, url):
            rss_results[idx] = self.parse_rss(name, url)

        def fetch_twitter():
            nonlocal twitter_items
            twitter_items = self._fetch_twitter_feeds()
            self._last_twitter_poll = time.time()
            twitter_done.set()

        threads = []
        for i, (name, url) in enumerate(RSS_FEEDS):
            t = threading.Thread(target=fetch_rss, args=(i, name, url), daemon=True)
            threads.append(t)
            t.start()

        if should_poll_twitter:
            threading.Thread(target=fetch_twitter, daemon=True).start()
        else:
            twitter_done.set()

        for t in threads:
            t.join(timeout=10)
        twitter_done.wait(timeout=15)

        now = time.time()
        # Expire old seen entries
        for k in [k for k, v in self.seen.items() if now - v > SEEN_EXPIRY]:
            del self.seen[k]

        # Combine all results
        all_raw: list[NewsItem] = []
        for batch in rss_results:
            all_raw.extend(batch)
        all_raw.extend(twitter_items)

        new_items = []
        for item in all_raw:
            if item.fingerprint not in self.seen:
                self.seen[item.fingerprint] = now
                new_items.append(item)

        # Sort: display_score (time-decayed) then newest
        new_items.sort(key=lambda x: (-x.display_score, -x.published.timestamp()))

        # Dedup — prefer direct-link sources
        deduped: list[NewsItem] = []
        for item in new_items:
            is_similar = False
            for existing in deduped:
                overlap = len(item.title_words & existing.title_words)
                min_len = min(len(item.title_words), len(existing.title_words))
                if min_len > 0 and overlap / min_len > 0.6:
                    is_similar = True
                    if 'news.google.com' in (existing.url or '') and 'news.google.com' not in (item.url or ''):
                        deduped[deduped.index(existing)] = item
                    break
            if not is_similar:
                deduped.append(item)
        new_items = deduped

        with self.lock:
            combined = new_items + [i for i in self.items if i.fingerprint not in {n.fingerprint for n in new_items}]
            # Re-sort with time-decay
            combined.sort(key=lambda x: (-x.display_score, -x.published.timestamp()))
            # Final dedup
            final: list[NewsItem] = []
            for item in combined:
                if not any(
                    len(item.title_words & e.title_words) / max(min(len(item.title_words), len(e.title_words)), 1) > 0.6
                    for e in final
                ):
                    final.append(item)
            # Drop items that have decayed below threshold
            self.items = [i for i in final if i.display_score >= IMPACT_THRESHOLD][:MAX_DISPLAY]

        # Background fetch details (not for tweets)
        to_fetch = [i for i in new_items if not i.is_tweet]
        if to_fetch:
            threading.Thread(target=self.fetcher.fetch_batch, args=(to_fetch,), daemon=True).start()

        self.last_poll = datetime.now(timezone.utc)
        return new_items


# ─── Display ─────────────────────────────────────────────────────────────────

SCORE_STYLES = {
    5: ("bold red",    "CRITICAL"),
    4: ("bold yellow", "HIGH"),
    3: ("yellow",      "NOTABLE"),
}

DIRECTION_DISPLAY = {
    "BULL": "[bold green]▲ BULL[/]",
    "BEAR": "[bold red]▼ BEAR[/]",
    "—":    "[dim]— FLAT[/]",
}

TAG_COLORS = {
    'DATA': 'bright_cyan', 'BEAT/MISS': 'bright_green', 'OUTLOOK': 'bright_magenta',
    'MKT MOVE': 'bright_yellow', 'QUOTE': 'white', 'TRADE': 'bright_red',
    'FLOW': 'bright_cyan',
}

def format_age(published: datetime, now: datetime) -> str:
    try:
        pub = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
        mins = int((now - pub).total_seconds() / 60)
        if mins < 1:
            return "NOW"
        elif mins < 60:
            return f"{mins}m"
        elif mins < 1440:
            return f"{mins // 60}h{mins % 60}m"
        else:
            return f"{mins // 1440}d"
    except Exception:
        return "?"


def render_dashboard(engine: NewsEngine) -> Panel:
    now = datetime.now(timezone.utc)
    now_local = datetime.now().strftime('%H:%M:%S')

    with engine.lock:
        items = list(engine.items)

    if not items:
        return Panel(
            "[dim]Scanning for /MES catalysts...[/]",
            title=f"[bold white]/MES CATALYST FEED[/] — {now_local}",
            border_style="red",
        )

    table = Table(
        show_header=True, header_style="bold white",
        border_style="red", show_lines=True,
        padding=(0, 1), expand=True,
    )
    table.add_column("/MES", width=10, justify="center")
    table.add_column("IMPACT", width=10, justify="center")
    table.add_column("CATALYST", width=10)
    table.add_column("HEADLINE & DETAILS", ratio=5)
    table.add_column("SRC", width=16)
    table.add_column("AGE", width=5, justify="right")

    for item in items:
        ds = item.display_score
        color, label = SCORE_STYLES.get(ds, ("dim", "?"))
        age = format_age(item.published, now)
        cats_str = ",".join(item.categories[:2]) if item.categories else ""
        direction = DIRECTION_DISPLAY.get(item.direction, "[dim]—[/]")

        # Dim old items
        age_h = item.age_minutes / 60
        title_style = f"{color} bold" if age_h < 2 else f"{color}" if age_h < 6 else "dim"

        src = f"[bright_cyan]{item.source}[/]" if item.is_tweet else f"[dim]{item.source}[/]"

        parts = [f"[{title_style}]{item.title}[/]"]

        fetched = engine.fetcher.get_details(item.fingerprint)
        if fetched:
            for line in fetched[:MAX_DETAIL_LINES]:
                tag_match = re.match(r'\[([A-Z /]+)\]\s*(.*)', line)
                if tag_match:
                    tag = tag_match.group(1)
                    text = tag_match.group(2).replace("[", "\\[")
                    tag_color = TAG_COLORS.get(tag, 'dim')
                    parts.append(f"  [{tag_color}]▸ {tag}:[/] [dim]{text}[/]")
                else:
                    clean = line.replace("[", "\\[")
                    parts.append(f"  [dim]▸ {clean}[/]")
        elif item.url and not item.is_tweet:
            if item.fingerprint in engine.fetcher.pending:
                parts.append("  [dim]⟳ loading...[/]")

        table.add_row(
            direction,
            f"[{color}]{label}[/]",
            f"[{color}]{cats_str}[/]",
            "\n".join(parts),
            src,
            f"[dim]{age}[/]",
        )

    feeds_ok = sum(1 for s in engine.feed_status.values() if s == "OK")
    total = len(RSS_FEEDS) + len(TWITTER_ACCOUNTS)
    twitter_ok = sum(1 for n, _ in TWITTER_ACCOUNTS if engine.feed_status.get(n, "") == "OK")

    subtitle = (
        f"[dim]{feeds_ok} RSS + {twitter_ok} X/{total} total | "
        f"▲=bullish ▼=bearish | "
        f"old items auto-decay | "
        f"every {POLL_INTERVAL}s[/]"
    )

    return Panel(
        table,
        title=f"[bold white]/MES CATALYST FEED[/] — {now_local}",
        subtitle=subtitle,
        border_style="red",
        padding=(0, 0),
    )


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    engine = NewsEngine()

    console.print(Panel(
        "[bold white]/MES — Micro E-Mini S&P 500 Catalyst Feed v3[/]\n\n"
        f"[dim]{len(RSS_FEEDS)} RSS + {len(TWITTER_ACCOUNTS)} Twitter/X\n"
        f"▲ BULL / ▼ BEAR direction on every headline\n"
        f"Trump | War/Middle East | Tariffs | CPI/NFP/GDP | Earnings | Oil | VIX\n"
        f"Kills: questions, previews, speculation, opinion, fluff, Fed noise\n"
        f"Old news auto-decays — fresh catalysts always on top[/]\n\n"
        "[dim]Press Ctrl+C to exit[/]",
        border_style="red",
        padding=(1, 2),
    ))

    console.print("[dim]Scanning...[/]")
    new = engine.poll_all()

    ok = sum(1 for s in engine.feed_status.values() if s == "OK")
    total = len(RSS_FEEDS) + len(TWITTER_ACCOUNTS)
    twitter_ok = sum(1 for n, _ in TWITTER_ACCOUNTS if engine.feed_status.get(n, "") == "OK")
    console.print(f"[dim]Sources: {ok}/{total} ({twitter_ok} Twitter)[/]")

    for name, status in engine.feed_status.items():
        if status != "OK":
            console.print(f"  [dim red]✗ {name}: {status}[/]")

    if new:
        console.print(f"[bold green]{len(new)} catalysts[/]")

    time.sleep(3)

    try:
        while True:
            console.clear()
            console.print(render_dashboard(engine))
            for tick in range(POLL_INTERVAL * 2):
                time.sleep(0.5)
                if tick % 6 == 5:
                    console.clear()
                    console.print(render_dashboard(engine))
            new_items = engine.poll_all()
            for item in new_items:
                if item.score >= DESKTOP_NOTIFY_THRESHOLD:
                    d = "▲" if item.direction == "BULL" else "▼" if item.direction == "BEAR" else "—"
                    cats = ",".join(item.categories[:2])
                    notify(f"/MES {d} [{cats}]", item.title)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")


if __name__ == "__main__":
    main()
