"""Image agent: finds free-to-use photos that match the story, picks the best, and saves web-optimised copies.

Sources, in order: Pexels (if PEXELS_API_KEY), Pixabay (if PIXABAY_API_KEY),
otherwise Openverse, which needs no key (Creative Commons photos; credit is required and shown on the site).
Only openly licensed photos are used, never photos copied from news articles.
"""
import io
import logging
from agents.base import shape
import re
import requests
from PIL import Image
from config import PEXELS_API_KEY, PIXABAY_API_KEY, IMAGES_DIR, USER_AGENT, FAST_MODEL

log = logging.getLogger("images")
MAX_W = 1600          # saved width; big enough for a hero image, small enough to load fast
_cache = {}

def search_pexels(query, per_page=8):
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_API_KEY},
                     params={"query": query, "per_page": per_page, "orientation": "landscape"}, timeout=15)
    r.raise_for_status()
    return [{"id": f'px{p["id"]}', "alt": p.get("alt", ""), "src": p["src"]["large2x"],
             "credit": p.get("photographer", ""), "source_url": p.get("url", ""), "site": "Pexels",
             "license": "Pexels License"}
            for p in r.json().get("photos", [])]

def search_pixabay(query, per_page=8):
    r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_API_KEY, "q": query[:100],
                     "image_type": "photo", "orientation": "horizontal", "safesearch": "true",
                     "per_page": max(per_page, 3)}, timeout=15)
    r.raise_for_status()
    return [{"id": f'pb{p["id"]}', "alt": p.get("tags", ""), "src": p["largeImageURL"],
             "credit": p.get("user", ""), "source_url": p.get("pageURL", ""), "site": "Pixabay",
             "license": "Pixabay Content License"}
            for p in r.json().get("hits", [])]

def search_openverse(query, per_page=8):
    r = requests.get("https://api.openverse.org/v1/images/", headers={"User-Agent": USER_AGENT},
                     params={"q": query, "license_type": "commercial,modification",
                             "mature": "false", "page_size": per_page * 2}, timeout=20)
    r.raise_for_status()
    out = []
    for p in r.json().get("results", []):
        if (p.get("width") or 0) < 900 or (p.get("height") or 0) > (p.get("width") or 1) * 1.6:
            continue  # too small, or too tall for a cover
        lic = f'CC {p["license"].upper()} {p.get("license_version") or ""}'.strip()
        out.append({"id": f'ov{p["id"]}', "alt": p.get("title") or "", "src": p["url"],
                    "credit": p.get("creator") or "Unknown", "license": lic,
                    "source_url": p.get("foreign_landing_url") or p.get("license_url", ""),
                    "site": (p.get("source") or "Openverse").replace("_", " ").title()})
    return out[:per_page]

def search(query):
    if query in _cache:
        return _cache[query]
    if PEXELS_API_KEY:
        res = search_pexels(query)
    elif PIXABAY_API_KEY:
        res = search_pixabay(query)
    else:
        res = search_openverse(query)
    _cache[query] = res
    return res

def queries_for(article, post=None):
    """Search phrases from most to least specific. Names are only searched for celebrities
    (private couples have no public photos, and their names match unrelated images)."""
    celeb = (article.get("category") == "celebrity-weddings")
    people = (article.get("people") or [])[:2] if celeb else []
    place, country = article.get("place") or "", article.get("country") or ""
    broad = (["indian wedding", "indian bride"] if article.get("region") == "india"
             else [f"{country} wedding", "asian wedding"])
    qs = [*people, *((post or {}).get("image_queries") or [])[:4],
          f"{place.split(',')[0]} wedding", *broad]
    return [q.strip() for q in dict.fromkeys(qs) if q.strip() and q.strip() != "wedding"]

def availability(article):
    """How many free photos exist for a candidate story (0 if none)."""
    n = 0
    for q in queries_for(article)[:3]:
        try:
            n += len(search(q))
        except Exception as e:
            log.debug("availability search failed for %r: %s", q, e)
    return n

def optimise(data, path):
    """Resize to MAX_W wide and save as a progressive JPEG. Returns (width, height)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.width > MAX_W:
        img = img.resize((MAX_W, round(img.height * MAX_W / img.width)), Image.LANCZOS)
    img.save(path, "JPEG", quality=82, optimize=True, progressive=True)
    return img.width, img.height

def describe(photo, post):
    """Alt text that says what the photo actually shows (its own description), never the story's details:
    these are representative photos, not pictures of the couple in the story."""
    t = re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", photo.get("alt") or "")).strip()
    if len(t) < 6 or re.fullmatch(r"(img|dsc|dscn|p|photo|image)?\s*\d+[\w ]*", t, re.I):
        t = ""
    kw = post.get("focus_keyword") or "wedding"
    return (f"{t} (representative photo for {kw})" if t else f"Representative photo for {kw}")[:150]

def find_images(llm, post, slug, n=3, article=None):
    article = article or {}
    people = (article.get("people") or []) if article.get("category") == "celebrity-weddings" else []
    photos = {}
    for q in queries_for(article, post)[:7]:
        if len(photos) >= 40:
            break
        try:
            for p in search(q):
                photos[p["id"]] = p
        except Exception as e:
            log.warning("image search failed for %r: %s", q, e)
    if not photos:
        log.info("no free photos found (the site uses a generated cover)")
        return []
    listing = "\n".join(f'- id={pid}: {p["alt"] or "no description"}' for pid, p in photos.items())
    try:
        pick = llm.json(
            "You choose illustrative photos for a wedding blog post. Good picks show the story's culture, place "
            "or theme: weddings, brides and grooms, rituals, mehndi, decor, flowers, outfits, jewellery, or the "
            "city or venue. They don't need to show the actual couple. Reject the wrong culture (for example a "
            "Western white-gown wedding for a Tamil story), unrelated subjects (traffic, documents, speakers, "
            "sculptures) and anything inappropriate. For a celebrity, a photo is fine only if its description "
            "names that celebrity. Prefer photos with a clear description over 'no description'.",
            f'Story: {post["title"]} — {post["excerpt"]}\nPlace: {post.get("place")}\n'
            f'People: {", ".join(people) or "none"}\n\nPhotos:\n{listing}\n\n'
            f'Return JSON: {{"ids": [up to {n} photo ids exactly as written, best first]}} '
            '(an empty list if none fit).', model=FAST_MODEL, max_tokens=500)
        pick = shape(pick, "ids")
        ids = [str(i) for i in pick.get("ids", []) if str(i) in photos][:n]
        log.info("picked %d of %d photos", len(ids), len(photos))
    except Exception as e:
        log.warning("image pick fell back to search order: %s", e)
        ids = list(photos)[:n]

    saved = []
    for k, pid in enumerate(ids):
        p = photos[pid]
        path = IMAGES_DIR / f"{slug}-{k + 1}.jpg"
        try:
            img = requests.get(p["src"], headers={"User-Agent": USER_AGENT}, timeout=30)
            if img.status_code == 403:  # Flickr's image CDN rejects custom user agents; retry with the default
                img = requests.get(p["src"], timeout=30)
            img.raise_for_status()
            w, h = optimise(img.content, path)
        except Exception as e:
            log.warning("download failed: %s", e)
            continue
        saved.append({"file": f"images/{path.name}", "alt": describe(p, post), "title": p["alt"], "width": w, "height": h,
                      "credit": p["credit"], "license": p.get("license", ""),
                      "source_url": p["source_url"], "site": p["site"]})
    log.info("images saved: %d", len(saved))
    return saved
