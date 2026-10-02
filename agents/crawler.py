"""Crawler agent: finds wedding stories (celebrity weddings, real weddings, photography, fashion,
traditions) in RSS feeds and Bing News, fetches the full article text and tags each story.

Respects robots.txt. Stories without enough real text are skipped, so the writer never has to guess.
"""
import calendar
import logging
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import feedparser
import requests
from bs4 import BeautifulSoup

import db
from config import (FEEDS, NEWS_SEARCHES, bing_news, USER_AGENT, FAST_MODEL, ASIAN_COUNTRIES_EX_INDIA,
                    MAX_AGE_DAYS, EVERGREEN_AGE_DAYS, MIN_SOURCE_WORDS, WEDDING_WORDS, CATEGORIES)

log = logging.getLogger("crawler")
_robots = {}

def allowed(url):
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        try:
            r = requests.get(host + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
            # RFC 9309: missing robots.txt (4xx) allows everything; a server error (5xx) means stay out
            rp.parse(r.text.splitlines() if r.status_code == 200 else
                     ["User-agent: *", "Disallow: /"] if r.status_code >= 500 else [])
        except Exception:
            rp = None
        _robots[host] = rp
    rp = _robots[host]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)

def html_text(html):
    """Main readable text of an article page or feed body."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "figure", "noscript"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    paras = [p.get_text(" ", strip=True) for p in root.find_all(["p", "li", "h2", "h3"])]
    text = "\n".join(p for p in paras if len(p.split()) > 6)
    if not text:  # feed bodies are often bare text
        text = soup.get_text(" ", strip=True)
    return text[:12000]

def fetch_text(url, timeout=12):
    """Return (final_url, main_text). Empty text if blocked or unreadable."""
    if not allowed(url):
        log.debug("robots.txt disallows %s", url)
        return url, ""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        log.debug("fetch failed %s: %s", url, e)
        return url, ""
    return r.url, html_text(r.text)

def clean(markup):
    import html
    return html.unescape(BeautifulSoup(markup or "", "html.parser").get_text(" ", strip=True))

def is_wedding_text(t):
    t = " " + t.lower() + " "
    return any(w in t for w in WEDDING_WORDS)

def entry_date(e):
    st = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(st), timezone.utc) if st else None

def real_link(link):
    """Bing News wraps links in a redirect; the real article URL is in the `url` parameter."""
    if "bing.com" in urlparse(link).netloc:
        return parse_qs(urlparse(link).query).get("url", [link])[0]
    return link

def discover(per_feed=25):
    """Yield candidate entries from every feed and news search (not yet fetched)."""
    sources = [(f["url"], f.get("wedding", False)) for f in FEEDS]
    from agents.trends import search_queries
    sources += [(bing_news(q), False) for q in dict.fromkeys([*search_queries(), *NEWS_SEARCHES])]
    seen = set()
    for url, wedding_feed in sources:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            feed = feedparser.parse(r.content)
        except Exception as e:
            log.warning("feed failed %s: %s", url[:80], e)
            continue
        kept = 0
        for e in feed.entries[:per_feed]:
            link = real_link(e.get("link") or "")
            if not link or link in seen or db.article_exists(link):
                continue
            title, summary = clean(e.get("title", "")), clean(e.get("summary", ""))
            if not wedding_feed and not is_wedding_text(title + " " + summary):
                continue
            when = entry_date(e)
            max_age = EVERGREEN_AGE_DAYS if wedding_feed else MAX_AGE_DAYS  # blog posts stay relevant longer than news
            if when and (datetime.now(timezone.utc) - when).days > max_age:
                continue
            seen.add(link)
            kept += 1
            body = (e.get("content") or [{}])[0].get("value", "")
            yield {"url": link, "title": title, "summary": summary[:600], "feed_text": html_text(body) if body else "",
                   "source": (e.get("source") or {}).get("title") or urlparse(link).netloc.replace("www.", ""),
                   "published_at": when.date().isoformat() if when else None}
        log.info("source done: %s (%d wedding items)", url.split("?")[0][:70] if "bing" not in url else
                 "news search: " + parse_qs(urlparse(url).query)["q"][0], kept)

CLASSIFY_SYSTEM = f"""You tag articles for an Asian wedding blog.
For each article decide:
- is_wedding: true if it's about a wedding: a celebrity or real couple's wedding, engagement or
  pre-wedding shoot, wedding photography, bridal fashion, wedding traditions, venues or planning.
  Crime, dowry disputes, divorce, politics and gossip with no wedding in it are false.
- region: "india" if mainly about a wedding in India or Indian wedding traditions,
  "asia" if mainly about another Asian country ({", ".join(ASIAN_COUNTRIES_EX_INDIA)}),
  "other" otherwise. An Indian couple marrying in Bali is "asia".
- category: one of {", ".join(CATEGORIES)}.
- country, place (city/state if known, else ""), topics (2-4 short tags),
- people: names of the couple or celebrities in the story (empty list if none),
- quality: 1-5, how good a basis it is for a detailed, original, accurate blog story.
  5 = rich specific details (names, places, outfits, rituals, photographer, decor). 1 = almost no facts."""

def classify(batch, llm):
    items = [{"i": i, "title": a["title"], "text": a["text"][:1000]} for i, a in enumerate(batch)]
    prompt = ("Tag these articles. Return a JSON object {\"articles\": [...]} where each item has keys "
              "i, is_wedding, region, category, country, place, topics, people, quality.\n\n" + str(items))
    try:
        tags = llm.json(CLASSIFY_SYSTEM, prompt, model=FAST_MODEL, max_tokens=4000)
    except Exception as e:
        log.error("classification failed: %s", e)
        return 0
    if isinstance(tags, dict):  # expected {"articles": [...]}; accept any wrapped list
        tags = next((v for v in tags.values() if isinstance(v, list)), [])
    kept = 0
    for t in tags:
        try:
            a = batch[int(t["i"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        a.update({k: t.get(k) for k in ("is_wedding", "region", "country", "place", "topics", "people", "quality")})
        a["category"] = t.get("category") if t.get("category") in CATEGORIES else "real-weddings"
        if not a.get("is_wedding") or a.get("region") not in ("india", "asia"):
            a["status"] = "rejected"
        else:
            kept += 1
        db.add_article(a)
    return kept

def crawl(llm, per_feed=25):
    found, thin = [], 0
    for item in discover(per_feed):
        _, page_text = fetch_text(item["url"])
        feed_text = item.pop("feed_text")
        text = max(page_text, feed_text, key=lambda t: len(t.split()))
        if len(text.split()) < MIN_SOURCE_WORDS:
            thin += 1
            db.add_article({**item, "text": "", "status": "rejected"})  # remember it so we don't refetch
            continue
        found.append({**item, "text": text})
        time.sleep(0.5)  # be polite
    log.info("%d stories with full text, %d skipped (blocked or too thin)", len(found), thin)
    kept = sum(classify(found[i:i + 10], llm) for i in range(0, len(found), 10))
    log.info("crawl finished: %d new wedding stories ready", kept)
    return kept
