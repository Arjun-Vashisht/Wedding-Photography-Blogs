"""Trend agent: finds what people are searching for and reading right now, so the other agents can follow it.

Sources (all free and open to automated reading):
- Google Trends "trending now" feeds for India and other Asian countries, with the news behind each search
- Wikipedia's most-read articles (who people are looking up: celebrities, couples, places)
- Your own readers: the posts and topics with the most views and likes on the site

Pinterest, Reddit, X/Twitter and Instagram forbid automated reading (robots.txt / terms), so they aren't used.
The model picks out what relates to weddings and turns it into trend topics with search queries. The crawler
then searches for those topics and the selector favours stories that match them.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

import db
from agents.base import shape
from config import USER_AGENT, FAST_MODEL, CATEGORIES

log = logging.getLogger("trends")
GEOS = {"IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal", "SG": "Singapore",
        "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines", "TH": "Thailand", "AE": "UAE", "KR": "South Korea",
        "JP": "Japan"}
HT = "{https://trends.google.com/trending/rss}"

def google_trends():
    """[{term, traffic, country, news: [titles]}] from each country's trending-searches feed."""
    out = []
    for geo, country in GEOS.items():
        try:
            r = requests.get(f"https://trends.google.com/trending/rss?geo={geo}", headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            for item in ET.fromstring(r.content).iter("item"):
                news = [n.findtext(f"{HT}news_item_title") or "" for n in item.findall(f"{HT}news_item")]
                traffic = re.sub(r"\D", "", item.findtext(f"{HT}approx_traffic") or "0") or "0"
                out.append({"term": item.findtext("title") or "", "traffic": int(traffic), "country": country,
                            "news": [n for n in news if n][:2]})
        except Exception as e:
            log.warning("Google Trends %s failed: %s", geo, e)
    return out

def wikipedia_top(days_back=1, n=60):
    """Most-read English Wikipedia articles yesterday (people, films, events), minus site pages."""
    d = datetime.now(timezone.utc) - timedelta(days=days_back)
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{d:%Y/%m/%d}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        arts = r.json()["items"][0]["articles"]
    except Exception as e:
        log.warning("Wikipedia pageviews failed: %s", e)
        return []
    skip = re.compile(r"^(Main_Page|Special:|Wikipedia:|Portal:|File:|Help:|Category:)|^(Deaths_in|List_of)")
    return [{"term": a["article"].replace("_", " "), "views": a["views"]} for a in arts if not skip.search(a["article"])][:n]

SYSTEM = f"""You are the trends editor of a wedding blog for Indian and Asian readers.
From today's trending searches, most-read Wikipedia pages and the blog's own reader data, find what this
audience will want to read about now: celebrity weddings, engagements and couples in the news, wedding
fashion and jewellery, wedding photography and photographers, venues and destinations, rituals and
wedding-season topics. Ignore trends with no believable wedding angle (sports scores, politics, disasters).
Celebrities who are trending only matter if their wedding, engagement or partner is in the news.
Every trend must be backed by evidence: quote the exact trending search, Wikipedia page or reader post
it comes from. Never invent trends; if only a few items have a wedding angle, return only those.
You may add up to 3 "seasonal" topics with no evidence, based on the Indian and Asian wedding calendar
for the date given (wedding season, auspicious dates, Navratri, Diwali, Karva Chauth, winter weddings,
Eid, Lunar New Year and so on). Never use Western seasonal themes (fall colours, pumpkin spice, Halloween).
Categories: {", ".join(CATEGORIES.values())}."""

def shortlist(gt, n=45):
    """Keep the prompt within free-tier limits: every wedding-related trend, then the biggest searches.
    Trends written only in non-Latin scripts are skipped (costly in tokens, rarely relevant here)."""
    from config import WEDDING_WORDS
    latin = [t for t in gt if re.search(r"[A-Za-z]", t["term"] + " ".join(t["news"]))]
    wedding = [t for t in latin if any(w in (" " + t["term"] + " " + " ".join(t["news"]) + " ").lower() for w in WEDDING_WORDS)]
    rest = sorted((t for t in latin if t not in wedding), key=lambda t: -t["traffic"])
    return (wedding + rest)[:n]

def analyse(llm):
    """Run the trend analysis and save it. Returns the list of trends."""
    gt, wiki = google_trends(), wikipedia_top()
    fav = db.reader_favourites(5)
    cat_stats = db.reader_category_stats()
    lines = [f'- "{t["term"]}" ({t["country"]}, {t["traffic"]}+){": " + t["news"][0][:90] if t["news"] else ""}'
             for t in shortlist(gt)]
    prompt = f"""Today: {datetime.now():%d %B %Y}

Trending searches (Google Trends, with the news behind them):
{chr(10).join(lines) or "- none available"}

Most-read Wikipedia pages yesterday:
{", ".join(w["term"] for w in wiki[:35]) or "none available"}

Our readers' favourite posts (views, likes):
{chr(10).join(f'- {p["title"]} ({p["views"]} views, {p["likes"]} likes)' for p in fav) or "- no reader data yet"}

Reader response by category (views and likes per post):
{", ".join(f'{CATEGORIES.get(k, k)}: {v["views_per_post"]} views, {v["likes_per_post"]} likes' for k, v in cat_stats.items()) or "none yet"}

Return JSON: {{"trends": [{{"topic": "<short topic>", "score": <1-10, how hot it is for our readers>,
"why": "<one sentence: what is happening>", "queries": ["<2-3 news search phrases to find stories>"],
"source": "google trends" | "wikipedia" | "our readers" | "seasonal",
"evidence": "<the exact trending search, Wikipedia page or post title it comes from; empty for seasonal>"}}]}}
Strongest first; at most 12."""
    r = shape(llm.json(SYSTEM, prompt, model=FAST_MODEL, max_tokens=2500), "trends")
    trends = verify([t for t in r.get("trends", []) if isinstance(t, dict) and t.get("topic")], gt, wiki, fav)
    db.save_trends(trends, datetime.now().isoformat(timespec="seconds"))
    log.info("trends: %d topics from %d trending searches, %d Wikipedia pages: %s", len(trends), len(gt), len(wiki),
             "; ".join(t["topic"] for t in trends[:6]))
    return trends

def verify(trends, gt, wiki, fav):
    """Keep only trends whose evidence really appears in today's data (plus at most 3 seasonal ones)."""
    norm = lambda x: re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip()
    pool = {"google trends": [norm(t["term"]) for t in gt], "wikipedia": [norm(w["term"]) for w in wiki],
            "our readers": [norm(p["title"]) for p in fav]}
    everything = [x for v in pool.values() for x in v]
    kept, seasonal = [], 0
    for t in trends:
        src, ev = str(t.get("source") or "").lower(), norm(t.get("evidence"))
        t["queries"] = [str(q) for q in (t.get("queries") or [])][:3]
        t["score"] = max(1, min(10, int(t.get("score") or 5)))
        if src == "seasonal" or not ev:
            if seasonal >= 3:
                continue
            seasonal += 1
            t.update(sources=["seasonal"], score=min(t["score"], 6))
        elif any(ev and (ev == x or ev in x or x in ev) for x in everything if len(x) > 2):
            t["sources"] = [src or "google trends"]
            t["why"] = f'{t.get("why", "")} (trending: “{t.get("evidence")}”)'
        else:
            log.info("dropped unsupported trend: %s (evidence %r not in today's data)", t["topic"], t.get("evidence"))
            continue
        kept.append(t)
    return sorted(kept, key=lambda t: -t["score"])

def search_queries(min_score=5, limit=10):
    """News searches for the crawler, from the current trends."""
    qs = [q for t in db.current_trends() if t["score"] >= min_score for q in t["queries"]]
    return list(dict.fromkeys(qs))[:limit]

def prompt_block():
    """A short 'trending now' list for the selector."""
    ts = db.current_trends(10)
    if not ts:
        return ""
    return "Trending now (favour stories that match these):\n" + "\n".join(
        f'- {t["topic"]} ({t["score"]}/10): {t["why"]}' for t in ts) + "\n"
