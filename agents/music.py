"""Music agent: picks a free, openly licensed background track for each reel.

Your own tracks come first: put MP3s in music/ (optionally with music/credits.json mapping
file name -> credit line). Otherwise it searches Openverse (Jamendo and Freesound; no key needed) for
instrumental music that fits the edition and region, lets the model choose, caches the file in
music/cache/ and returns the credit the licence requires.
"""
import json
import logging
import re
import requests
from config import BASE_DIR, USER_AGENT, FAST_MODEL
from agents.base import RateLimited, shape

log = logging.getLogger("music")
MUSIC_DIR = BASE_DIR / "music"
CACHE = MUSIC_DIR / "cache"
INDEX = CACHE / "tracks.json"

SEARCHES = {
    ("india", "morning"): ["indian instrumental", "sitar instrumental", "bollywood instrumental", "happy wedding instrumental"],
    ("india", "evening"): ["shehnai", "romantic sitar", "indian romantic instrumental", "wedding instrumental"],
    ("asia", "morning"): ["asian instrumental", "guzheng", "koto", "happy wedding instrumental"],
    ("asia", "evening"): ["asian romantic instrumental", "erhu", "gamelan", "wedding instrumental"],
}
GOOD_TAGS = {"instrumental", "wedding", "romantic", "love", "happy", "peaceful", "indian", "india", "world",
             "ethnic", "sitar", "tabla", "bollywood", "asian", "oriental", "strings", "piano", "acoustic",
             "celebration", "uplifting", "beautiful", "calm", "relaxing"}
BAD_WORDS = re.compile(r"field.?record|soundscape|ambien|noise|market|traffic|street|horror|snuff|death|dark|"
                       r"scary|metal|rap|trap|dubstep|war|kill|blood|sad|funeral|test|loop\b|sfx|effect", re.I)

def _load():
    try:
        return json.loads(INDEX.read_text())
    except Exception:
        return {}

def _save(index):
    CACHE.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1))

def own_track():
    """A track the user supplied in music/, used in rotation."""
    files = sorted(p for p in MUSIC_DIR.glob("*.mp3"))
    if not files:
        return None
    try:
        credits = json.loads((MUSIC_DIR / "credits.json").read_text())
    except Exception:
        credits = {}
    index = _load()
    used = index.get("_own_used", {})
    f = min(files, key=lambda p: used.get(p.name, 0))
    used[f.name] = used.get(f.name, 0) + 1
    index["_own_used"] = used
    _save(index)
    return {"path": str(f), "credit": credits.get(f.name, ""), "duration": None}

def search(q):
    r = requests.get("https://api.openverse.org/v1/audio/", headers={"User-Agent": USER_AGENT},
                     params={"q": q, "license_type": "commercial,modification", "page_size": 20}, timeout=20)
    r.raise_for_status()
    out = []
    for t in r.json().get("results", []):
        tags = {x["name"].lower() for x in t.get("tags") or [] if x.get("name")} | set(t.get("genres") or [])
        text = " ".join([t.get("title") or "", *tags])
        dur = (t.get("duration") or 0) / 1000
        if not (25 <= dur <= 480) or BAD_WORDS.search(text) or not t.get("url"):
            continue
        score = len(tags & GOOD_TAGS) + (3 if t.get("source") == "jamendo" else 0) + (2 if "instrumental" in text.lower() else 0)
        lic = f'CC {t["license"].upper()} {t.get("license_version") or ""}'.strip() if t["license"] != "cc0" else "CC0"
        out.append({"id": t["id"], "title": t.get("title") or "Untitled", "creator": t.get("creator") or "Unknown",
                    "license": lic, "page": t.get("foreign_landing_url") or "", "url": t["url"], "duration": dur,
                    "tags": sorted(tags)[:10], "source": t.get("source"), "score": score})
    return out

def pick(llm, post, region, edition):
    """Return {"path", "credit", "duration"} for the reel's music, or None if nothing suitable was found."""
    own = own_track()
    if own:
        log.info("music: using your track %s", own["path"])
        return own
    found = {}
    for q in SEARCHES.get((region if region in ("india", "asia") else "india", edition), SEARCHES[("india", "evening")]):
        try:
            for t in search(q):
                found[t["id"]] = t
        except Exception as e:
            log.warning("music search failed for %r: %s", q, e)
    if not found:
        log.info("music: no suitable free track found, reel stays silent")
        return None
    index = _load()
    # Prefer good matches that haven't been used much, so reels don't all sound the same.
    pool = sorted(found.values(), key=lambda t: (-t["score"] + index.get(t["id"], {}).get("used", 0) * 2))[:12]
    listing = "\n".join(f'- id={t["id"]}: "{t["title"]}" by {t["creator"]}, {t["duration"]:.0f}s, tags: {", ".join(t["tags"])}'
                        for t in pool)
    try:
        r = llm.json("You pick background music for Instagram wedding reels. Choose the instrumental track "
                        "whose mood fits the story: festive and bright for celebrations and morning posts, "
                        "romantic and warm for evening stories. Avoid anything that sounds sad, scary or comic.",
                        f'Story: {post["title"]}\nEdition: {edition}\nRegion: {region}\n\nTracks:\n{listing}\n\n'
                        'Return JSON: {"id": "<track id exactly as written>"}', model=FAST_MODEL, max_tokens=400)
        r = shape(r)
        chosen = next((t for t in pool if t["id"] == str(r.get("id"))), pool[0])
    except RateLimited:
        chosen = pool[0]
    except Exception as e:
        log.warning("music pick fell back to best match: %s", e)
        chosen = pool[0]

    path = CACHE / f'{chosen["id"]}.mp3'
    if not path.exists():
        try:
            d = requests.get(chosen["url"], headers={"User-Agent": USER_AGENT}, timeout=90)
            d.raise_for_status()
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_bytes(d.content)
        except Exception as e:
            log.warning("music download failed: %s", e)
            return None
    entry = index.get(chosen["id"], {k: chosen[k] for k in ("title", "creator", "license", "page", "duration")})
    entry["used"] = entry.get("used", 0) + 1
    index[chosen["id"]] = entry
    _save(index)
    credit = f'“{chosen["title"]}” by {chosen["creator"]} ({chosen["license"]})' + (f', {chosen["page"]}' if chosen["page"] else "")
    log.info("music: %s", credit)
    return {"path": str(path), "credit": credit, "duration": chosen["duration"]}
