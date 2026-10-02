"""Publisher agent: publishes posts and builds a static, SEO-ready wedding magazine website.

Pages: home (featured slideshow, latest, reels, topics), /blog/<slug>/ for every post, /category/<c>/ for every
category (even before it has posts, so no menu link is ever dead), /tag/<t>/, /reels/ (full-screen reel
player), and a helpful 404. Every page is fully rendered HTML for search engines, with SEO tags
(title, description, canonical, Open Graph, Twitter, JSON-LD), and theme/app.js adds the reading flow:
the next story loads below the current one, an "Up next" card, swipe and arrow keys, instant search.
Also writes sitemap.xml (with images), robots.txt, feed.xml and data/posts.json.
"""
import html
import json
import logging
import re
import shutil
from datetime import datetime
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import db
from config import (SITE_DIR, SITE_DATA, BASE_DIR, SITE_NAME, SITE_TAGLINE, SITE_URL, SITE_DESCRIPTION,
                    SITE_LANG, CATEGORIES, TIMEZONE, MORNING_TIME, EVENING_TIME)

log = logging.getLogger("publisher")
CONTENT = BASE_DIR / "content"
CONTENT.mkdir(exist_ok=True)
THEME = BASE_DIR / "theme"
GENERATED_DIRS = ("blog", "category", "tag")
THUMB_W = 720

def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", (t or "").lower()).strip("-")[:70].strip("-")

def unique_slug(text):
    base = slugify(text) or "wedding-story"
    slug, n = base, 2
    while db.slug_taken(slug):
        slug, n = f"{base}-{n}", n + 1
    return slug

e = lambda s: html.escape(str(s or ""), quote=True)
url = lambda path: SITE_URL + path
post_path = lambda p: f'/blog/{p["slug"]}/'
cat_path = lambda c: f"/category/{c}/"
tag_path = lambda t: f"/tag/{slugify(t)}/"
cat_name = lambda p: CATEGORIES.get(p.get("category") or "", "Weddings")

def published_at(p):
    hhmm = MORNING_TIME if p["edition"] == "morning" else EVENING_TIME
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(p["publish_date"]).replace(hour=h, minute=m, tzinfo=ZoneInfo(TIMEZONE))

def ld(obj):
    return ('<script type="application/ld+json">'
            + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>")

def fmt_date(p):
    d = published_at(p)
    return f"{d.day} {d:%B %Y}"

def read_minutes(p):
    return (p.get("seo") or {}).get("read_minutes") or max(2, len(" ".join(p["body"]).split()) // 220)

def poster(p):
    return p["reel_path"].replace(".mp4", ".jpg") if p.get("reel_path") else None

# ---------- images ----------

def thumb(file):
    """A smaller copy of an image for cards (built once), so pages load fast."""
    src = SITE_DIR / file
    small = src.with_name(f"{src.stem}-sm.jpg")
    if src.exists() and not small.exists():
        from PIL import Image
        img = Image.open(src).convert("RGB")
        if img.width > THUMB_W:
            img = img.resize((THUMB_W, round(img.height * THUMB_W / img.width)), Image.LANCZOS)
        img.save(small, "JPEG", quality=80, optimize=True, progressive=True)
    return f'{file.rsplit("/", 1)[0]}/{small.name}' if small.exists() else file

def img_tag(im, sizes="(max-width: 700px) 100vw, 50vw", eager=False, cls="", zoom=False):
    if not im:
        return f'<span class="ph" aria-hidden="true">{e(SITE_NAME[0])}</span>'
    w, h = im.get("width"), im.get("height")
    size = f' width="{w}" height="{h}"' if w and h else ""
    load = ' fetchpriority="high"' if eager else ' loading="lazy" decoding="async"'
    srcset = f'/{e(thumb(im["file"]))} {THUMB_W}w, /{e(im["file"])} {w or 1600}w'
    return (f'<img src="/{e(thumb(im["file"]))}" srcset="{srcset}" sizes="{sizes}" alt="{e(im.get("alt"))}"'
            f'{size}{load}{f" class={cls!r}" if cls else ""}'
            + (f' data-zoom="/{e(im["file"])}" tabindex="0" role="button" aria-label="View photo full screen: {e(im.get("alt"))}"' if zoom else "")
            + ">")

def credit(im):
    c = (f'Representative photo, not from this wedding. Photo: <a href="{e(im.get("source_url"))}" '
         f'rel="nofollow noopener" target="_blank">{e(im.get("credit"))}</a> / {e(im.get("site"))}')
    return c + (f', {e(im.get("license"))}' if im.get("license") else "")

# ---------- icons ----------

ICON = {
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "theme": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
    "arrow": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    "left": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m15 6-6 6 6 6"/></svg>',
    "right": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 6 6 6-6 6"/></svg>',
    "play": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
    "heart": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-7.5-4.6-9.5-9.3C1.1 8.3 3.2 4.5 7 4.5c2.1 0 3.6 1.1 5 3 1.4-1.9 2.9-3 5-3 3.8 0 5.9 3.8 4.5 7.2C19.5 16.4 12 21 12 21z"/></svg>',
    "eye": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    "close": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    "share": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/></svg>',
}

# ---------- layout ----------

def head(title, description, path, image=None, og_type="website", extra="", keywords=None, preload=None,
         robots="index, follow, max-image-preview:large, max-snippet:-1"):
    img = url("/" + image) if image else url("/assets/logo.png")
    return f"""<!DOCTYPE html>
<html lang="{SITE_LANG}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
{f'<meta name="keywords" content="{e(", ".join(k for k in keywords if k))}">' if keywords else ""}
<meta name="robots" content="{robots}">
<meta name="theme-color" content="#FBF7F2" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#131012" media="(prefers-color-scheme: dark)">
<link rel="canonical" href="{e(url(path))}">
<link rel="alternate" hreflang="{SITE_LANG}" href="{e(url(path))}">
<link rel="alternate" type="application/rss+xml" title="{e(SITE_NAME)}" href="/feed.xml">
<meta property="og:site_name" content="{e(SITE_NAME)}">
<meta property="og:locale" content="{SITE_LANG.replace("-", "_")}">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{e(url(path))}">
<meta property="og:image" content="{e(img)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(description)}">
<meta name="twitter:image" content="{e(img)}">
{f'<link rel="preload" as="image" href="/{e(preload)}" fetchpriority="high">' if preload else ""}
<link rel="icon" href="/assets/logo.png">
<link rel="apple-touch-icon" href="/assets/logo.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/site.css">
<script>document.documentElement.classList.add('js');try{{const t=localStorage.getItem('phera-theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<script src="/assets/app.js" defer></script>
{extra}
</head>
"""

def header(active=None):
    cats = "".join(f'<a href="{cat_path(c)}"{" aria-current=page" if c == active else ""}>{e(n)}</a>'
                   for c, n in CATEGORIES.items())
    return f"""<a class="skip" href="#main">Skip to content</a>
<div class="progress" aria-hidden="true"></div>
<header class="site-head"><div class="wrap">
  <div class="head-row">
    <a class="logo" href="/" aria-label="{e(SITE_NAME)} home"><b>{e(SITE_NAME)}</b></a>
    <button class="search-btn" data-search aria-label="Search stories">{ICON["search"]}<span>Search stories</span><kbd>/</kbd></button>
    <button class="icon-btn" id="themeBtn" aria-label="Switch light or dark mode">{ICON["theme"]}</button>
  </div>
  <nav class="cats" aria-label="Topics"><a href="/"{" aria-current=page" if active == "home" else ""}>Latest</a>{cats}<a class="reels-link" href="/reels/"{" aria-current=page" if active == "reels" else ""}>Reels</a></nav>
</div></header>
"""

def overlays():
    return f"""<div class="overlay" id="search" role="dialog" aria-modal="true" aria-label="Search">
  <div class="search-panel"><div class="search-box">
    <label class="search-field">{ICON["search"]}<span class="sr">Search</span><input id="q" type="search" placeholder="Search weddings, places, rituals…" autocomplete="off"></label>
    <p class="search-hint" id="searchHint">Latest stories</p>
    <div class="results" id="results"></div>
  </div></div>
  <button class="icon-btn close-btn" data-close aria-label="Close search">{ICON["close"]}</button>
</div>
<div class="overlay" id="viewer" role="dialog" aria-modal="true" aria-label="Photo viewer">
  <div class="viewer-stage" id="viewerStage"><img id="viewerImg" alt=""></div>
  <div class="viewer-bar">
    <span id="viewerCount"></span>
    <button class="icon-btn" id="zoomOut" aria-label="Zoom out"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M20 20l-3.5-3.5"/></svg></button>
    <button class="icon-btn" id="zoomIn" aria-label="Zoom in"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M11 8v6M20 20l-3.5-3.5"/></svg></button>
    <button class="icon-btn" data-close aria-label="Close photo">{ICON["close"]}</button>
  </div>
  <button class="icon-btn viewer-nav prev" id="viewerPrev" aria-label="Previous photo">{ICON["left"]}</button>
  <button class="icon-btn viewer-nav next" id="viewerNext" aria-label="Next photo">{ICON["right"]}</button>
  <p class="viewer-cap" id="viewerCap"></p>
</div>
<div class="overlay" id="player" role="dialog" aria-modal="true" aria-label="Reels">
  <div class="player"><div class="player-feed" id="playerFeed"></div></div>
  <button class="icon-btn mute-btn" id="muteBtn" aria-label="Mute"></button>
  <button class="icon-btn close-btn" data-close aria-label="Close reels">{ICON["close"]}</button>
  <p class="swipe-hint" id="swipeHint">Swipe up for the next reel</p>
</div>
"""

def footer():
    cats = "".join(f'<li><a href="{cat_path(c)}">{e(n)}</a></li>' for c, n in CATEGORIES.items())
    return f"""<footer class="site-foot"><div class="wrap">
  <div class="foot-grid">
    <div><a class="logo" href="/"><b>{e(SITE_NAME)}</b></a><p>{e(SITE_DESCRIPTION)}</p></div>
    <div><h4>Topics</h4><ul>{cats}</ul></div>
    <div><h4>Explore</h4><ul><li><a href="/">Latest stories</a></li><li><a href="/reels/">Reels</a></li>
      <li><a href="#" data-search>Search</a></li><li><a href="/feed.xml">RSS feed</a></li></ul></div>
  </div>
  <div class="foot-base"><span>© {datetime.now().year} {e(SITE_NAME)}</span>
  <span>Photos are credited to their owners and used under their licences.</span></div>
</div></footer>
{overlays()}
</body>
</html>"""

def page(head_html, body, active=None, page_id="page"):
    return head_html + f'<body data-page="{page_id}">' + header(active) + f'<main id="main">{body}</main>' + footer()

def org():
    return {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL + "/",
            "logo": {"@type": "ImageObject", "url": url("/assets/logo.png"), "width": 512, "height": 512}}

def breadcrumbs(items):
    """items: [(name, path), ...] -> (html, json-ld)"""
    links = '<span aria-hidden="true">/</span>'.join(
        f'<a href="{e(p)}">{e(n)}</a>' if i < len(items) - 1 else f'<span aria-current="page">{e(n)}</span>'
        for i, (n, p) in enumerate(items))
    data = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": url(p)} for i, (n, p) in enumerate(items)]}
    return f'<nav class="crumbs" aria-label="Breadcrumb">{links}</nav>', ld(data)

# ---------- components ----------

def card(p, variant="", heading="h3"):
    im = (p["images"] or [None])[0]
    reel = '<span class="play-badge" aria-label="Has a reel">' + ICON["play"] + "</span>" if p.get("reel_path") else ""
    return (f'<article class="card {variant} reveal"><div class="thumb">{img_tag(im)}'
            f'<span class="badge">{e(cat_name(p))}</span>{reel}</div>'
            f'<div class="body"><{heading}>{e(p["title"])}</{heading}><p>{e(p["excerpt"])}</p>'
            f'<span class="meta">{fmt_date(p)} · {read_minutes(p)} min read<span class="card-likes" data-likes="{e(p["slug"])}"></span></span></div>'
            f'<a class="cover" href="{post_path(p)}" aria-label="{e(p["title"])}"></a></article>')

def reel_card(p):
    return (f'<a class="reel-card" href="{post_path(p)}" data-reel="/{e(p["reel_path"])}" aria-label="Watch reel: {e(p["title"])}">'
            f'{img_tag((p["images"] or [None])[0], "240px") if p["images"] else f"<img src=/{e(poster(p))} alt=\"\" loading=lazy>"}'
            f'<span class="play">{ICON["play"]}</span>'
            f'<span class="txt"><b>{e(p["title"])}</b><small>{e(p["place"])}</small></span></a>')

def rail(items_html, cls=""):
    return (f'<div class="rail-wrap"><button class="rail-btn prev" aria-label="Scroll left">{ICON["left"]}</button>'
            f'<div class="rail {cls}">{items_html}</div>'
            f'<button class="rail-btn next" aria-label="Scroll right">{ICON["right"]}</button></div>')

def section(title, body, sub="", link=None):
    more = f'<a class="see-all" href="{link[1]}">{e(link[0])} {ICON["arrow"]}</a>' if link else ""
    return (f'<section class="block"><div class="sec-head"><div><h2>{e(title)}</h2>'
            f'{f"<p>{e(sub)}</p>" if sub else ""}</div>{more}</div>{body}</section>')

def topics(posts):
    tiles = []
    for i, (c, n) in enumerate(CATEGORIES.items()):
        ps = [p for p in posts if p.get("category") == c]
        im = next((p["images"][0] for p in ps if p["images"]), None)
        count = f'{len(ps)} {"story" if len(ps) == 1 else "stories"}' if ps else "New stories soon"
        tiles.append(f'<a class="topic t{i % 7}" href="{cat_path(c)}">{img_tag(im, "200px") if im else ""}'
                     f'<span><b>{e(n)}</b><small>{count}</small></span></a>')
    return f'<div class="topics">{"".join(tiles)}</div>'

def neighbours(p, posts):
    """(previous, next) story for continuous reading: next is the next-older story, wrapping to the newest."""
    ids = [q["id"] for q in posts]
    i = ids.index(p["id"])
    nxt = posts[i + 1] if i + 1 < len(posts) else (posts[0] if len(posts) > 1 else None)
    prev = posts[i - 1] if i > 0 else None
    return prev, nxt

def related(p, posts, n=3, exclude=()):
    tags = set(t.lower() for t in p["tags"])
    scored = [(len(tags & set(t.lower() for t in q["tags"])) * 2 + (q.get("category") == p.get("category")), q)
              for q in posts if q["id"] != p["id"] and q["id"] not in exclude]
    return [q for s, q in sorted(scored, key=lambda x: -x[0])[:n]]

# ---------- pages ----------

STOP = {"wedding", "weddings", "with", "from", "into", "this", "that", "their", "season", "trend", "trends", "ideas"}

def matches(topic, posts):
    """True if any published story is about this trend (shares a meaningful word with it)."""
    words = {w for w in re.findall(r"[a-z]{4,}", topic.lower()) if w not in STOP}
    return any(words & set(re.findall(r"[a-z]{4,}", " ".join([p["title"], p["excerpt"], *p["tags"]]).lower())) for p in posts)

def home_page(posts):
    feat = posts[:3] if len(posts) >= 6 else posts[:1]   # slideshow once there are enough stories
    if feat:
        slides = "".join(
            f'<article class="hero slide{" on" if i == 0 else ""}">'
            f'{img_tag((p["images"] or [None])[0], "100vw", eager=i == 0) if p["images"] else "<span class=ph></span>"}'
            f'<div class="hero-body"><span class="kicker">{e(cat_name(p))} · {"Morning" if p["edition"] == "morning" else "Evening"} edition</span>'
            f'<h1>{e(p["title"])}</h1><p>{e(p["excerpt"])}</p>'
            f'<a class="btn light" href="{post_path(p)}">Read the story {ICON["arrow"]}</a>'
            f'<div class="meta">{fmt_date(p)} · {e(p["place"])} · {read_minutes(p)} min read</div></div></article>'
            for i, p in enumerate(feat))
        dots = ('<div class="hero-dots">' + "".join(f'<button aria-label="Story {i + 1}"'
                f'{" aria-current=true" if i == 0 else ""}></button>' for i in range(len(feat))) + "</div>") if len(feat) > 1 else ""
        hero = f'<div class="slides">{slides}{dots}</div>'
    else:
        hero = ('<div class="hero"><span class="ph"></span><div class="hero-body"><span class="kicker">Coming soon</span>'
                '<h1>Beautiful wedding stories</h1><p>The first stories arrive at 7 AM and 7 PM.</p></div></div>')

    body = f'<div class="wrap">{hero}'
    rest = posts[len(feat):]
    if rest:
        big, side = rest[0], rest[1:5]
        body += section("Latest stories", f'<div class="mag">{card(big, "big", "h3")}'
                        f'<div class="side">{"".join(card(p, "row") for p in side)}</div></div>'
                        if side else f'<div class="grid">{card(big)}</div>')
    trending = [t for t in db.current_trends(12) if matches(t["topic"], posts)][:8]   # only trends we have stories on
    if trending:
        chips = "".join(f'<button class="trend-chip" data-search-q="{e(t["topic"])}">{e(t["topic"])}</button>' for t in trending)
        body += section("Trending now", f'<div class="trend-chips">{chips}</div>', "What people are searching for this week")
    favs = [p for p in db.reader_favourites(6) if p["likes"] or p["views"] >= 3]
    if len(favs) >= 2:
        body += section("Readers' favourites", rail("".join(card(p) for p in favs)), "The stories readers loved most")
    reels = [p for p in posts if p.get("reel_path")]
    if reels:
        body += section("Watch the reels", rail("".join(reel_card(p) for p in reels[:12]), "reels"),
                        "Tap to watch, swipe up for the next one", ("All reels", "/reels/"))
    for c, n in CATEGORIES.items():
        ps = [p for p in posts if p.get("category") == c]
        if len(ps) >= 2 or (len(posts) > 6 and ps):
            body += section(n, rail("".join(card(p) for p in ps[:10])), "", (f"All {n.lower()}", cat_path(c)))
    body += section("Explore by topic", topics(posts), "Celebrity weddings, real couples, photography, fashion and more")
    if len(posts) > 5:
        body += section("More stories", f'<div class="grid">{"".join(card(p) for p in posts[5:17])}</div>')
    body += "</div>"

    image = next((p["images"][0]["file"] for p in posts if p["images"]), None)
    extra = ld({"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": SITE_NAME, "url": SITE_URL + "/", "description": SITE_DESCRIPTION,
         "inLanguage": SITE_LANG, "potentialAction": {"@type": "SearchAction", "target": url("/?q={search_term_string}"),
                                                      "query-input": "required name=search_term_string"}},
        org()]})
    first = (posts[0]["images"] or [None])[0] if posts else None
    return page(head(f"{SITE_NAME}: {SITE_TAGLINE}", SITE_DESCRIPTION, "/", image, extra=extra,
                     preload=thumb(first["file"]) if first else None), body, "home", "home")

def post_page(p, posts):
    seo = p.get("seo") or {}
    imgs, cat = p["images"], p.get("category") or "real-weddings"
    hero = imgs[0] if imgs else None
    prev, nxt = neighbours(p, posts)
    crumbs_html, crumbs_ld = breadcrumbs([("Home", "/"), (cat_name(p), cat_path(cat)), (p["title"], post_path(p))])
    pub = published_at(p).isoformat()
    article_ld = {k: v for k, v in {
        "@context": "https://schema.org", "@type": "BlogPosting",
        "headline": p["title"][:110], "description": seo.get("meta_description") or p["excerpt"],
        "datePublished": pub, "dateModified": pub, "inLanguage": SITE_LANG,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url(post_path(p))},
        "author": {"@type": "Organization", "name": f"{SITE_NAME} Editorial", "url": SITE_URL + "/"},
        "publisher": org(), "articleSection": cat_name(p),
        "keywords": ", ".join(k for k in [seo.get("focus_keyword", ""), *seo.get("keywords", []), *p["tags"]] if k),
        "wordCount": seo.get("words"),
        "image": [url("/" + im["file"]) for im in imgs] or [url("/assets/logo.png")],
        "contentLocation": {"@type": "Place", "name": p["place"]} if p["place"] else None,
        "isBasedOn": p["source_url"] or None,
    }.items() if v}
    extra = [ld(article_ld), crumbs_ld]
    faq = seo.get("faq") or []
    if faq:
        extra.append(ld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq]}))
    if p.get("reel_path"):
        extra.append(ld({"@context": "https://schema.org", "@type": "VideoObject", "name": p["title"],
                         "description": p["excerpt"], "uploadDate": pub, "contentUrl": url("/" + p["reel_path"]),
                         "thumbnailUrl": url("/" + poster(p))}))
    extra += [f'<meta property="article:published_time" content="{pub}">',
              f'<meta property="article:section" content="{e(cat_name(p))}">']
    extra += [f'<meta property="article:tag" content="{e(t)}">' for t in p["tags"]]
    if nxt:
        extra.append(f'<link rel="next" href="{post_path(nxt)}">')
    if prev:
        extra.append(f'<link rel="prev" href="{post_path(prev)}">')

    sections = seo.get("sections") or [{"heading": "", "paragraphs": p["body"]}]
    toc = [(slugify(s["heading"]), s["heading"]) for s in sections if s.get("heading")]
    body = "".join(f"<p>{e(t)}</p>" for t in seo.get("intro") or [])
    inline = imgs[1:]
    for i, s in enumerate(sections):
        if s.get("heading"):
            body += f'<h2 id="{slugify(s["heading"])}">{e(s["heading"])}</h2>'
        body += "".join(f"<p>{e(t)}</p>" for t in s["paragraphs"])
        if inline and i in (0, 2):
            im = inline.pop(0)
            body += f'<figure>{img_tag(im, "(max-width: 960px) 100vw, 960px", zoom=True)}<figcaption>{credit(im)}</figcaption></figure>'
        if i == 1 and p.get("reel_path"):
            body += (f'<div class="reel-box"><video src="/{e(p["reel_path"])}" poster="/{e(poster(p))}" controls playsinline '
                     f'preload="none" width="360" height="640"></video><div><span class="kicker">Watch</span>'
                     f'<h3>Watch this story</h3><p>Turn the sound on for the voiceover.</p>'
                     + (f'<p class="src" style="margin-top:.6rem">Music: {e(seo["music"])}</p>' if seo.get("music") else "")
                     + "</div></div>")
    if faq:
        body += '<section class="faq"><h2 id="faq">Frequently asked questions</h2>' + "".join(
            f'<details><summary>{e(f["q"])}</summary><p>{e(f["a"])}</p></details>' for f in faq) + "</section>"

    share = (f'<div class="share"><button class="like-btn" data-like="{e(p["slug"])}" aria-pressed="false" aria-label="Like this story" hidden>'
             f'{ICON["heart"]}<span class="n"></span></button>'
             f'<span class="views" data-views="{e(p["slug"])}" hidden>{ICON["eye"]}<span class="n"></span></span>'
             f'<button class="share-one" data-share aria-label="Share this story">{ICON["share"]}<span>Share</span></button></div>')
    nxt_im = (nxt["images"] or [None])[0] if nxt else None
    next_block = (f'<a class="next-story" href="{post_path(nxt)}">{img_tag(nxt_im, "100vw") if nxt_im else "<span class=ph></span>"}'
                  f'<div class="in"><span class="kicker">Next story</span><h2>{e(nxt["title"])}</h2><p>{e(nxt["excerpt"])}</p>'
                  f'<span class="btn light">Keep reading {ICON["arrow"]}</span></div></a>') if nxt else ""
    title_tag = seo.get("seo_title") or p["title"]
    article = f"""<article class="story" data-url="{post_path(p)}" data-title="{e(p["title"])}" data-doctitle="{e(title_tag)}"
  data-next="{post_path(nxt) if nxt else ""}" data-next-title="{e(nxt["title"]) if nxt else ""}"
  data-next-img="{("/" + e(thumb(nxt_im["file"]))) if nxt_im else ""}" data-prev="{post_path(prev) if prev else ""}">
<div class="wrap">
  <header class="article-head">
    {crumbs_html}
    <a class="kicker" href="{cat_path(cat)}">{e(cat_name(p))}</a>
    <h1>{e(p["title"])}</h1>
    <p class="lede">{e(p["excerpt"])}</p>
    <div class="byline"><time datetime="{pub}">{fmt_date(p)}</time><span class="dot"></span><span>{e(p["place"])}</span>
      <span class="dot"></span><span>{read_minutes(p)} min read</span></div>
    {share}
  </header>
  {f'<figure class="hero-fig">{img_tag(hero, "(max-width: 1120px) 100vw, 1120px", eager=True, zoom=True)}<figcaption>{credit(hero)}</figcaption></figure>' if hero else ""}
  {f'<nav class="toc" aria-label="In this story"><b>In this story</b><ol>{"".join(f"<li><a href=#{i}>{e(h)}</a></li>" for i, h in toc)}</ol></nav>' if len(toc) >= 3 else ""}
  <div class="prose">{body}</div>
  <footer class="post-foot">
    <ul class="tags">{"".join(f'<li><a href="{tag_path(t)}" rel="tag">{e(t)}</a></li>' for t in p["tags"])}</ul>
    <div class="love" data-love="{e(p["slug"])}" hidden><p>Loved this story?</p>
      <button class="like-btn big" data-like="{e(p["slug"])}" aria-pressed="false" aria-label="Like this story">{ICON["heart"]}<span>Like</span><span class="n"></span></button></div>
    {f'<p class="src">Inspired by reporting from <a href="{e(p["source_url"])}" rel="nofollow noopener" target="_blank">{e(p["source_name"])}</a>. Written by {e(SITE_NAME)} and checked for originality and accuracy before publishing.</p>' if p["source_url"] else ""}
  </footer>
  {next_block}
</div>
</article>"""
    rel = related(p, posts, 3, exclude={nxt["id"]} if nxt else ())
    more = section("You may also like", f'<div class="grid">{"".join(card(q) for q in rel)}</div>') if rel else ""
    after = ('<div id="endless" class="loading-next"><span class="spinner"></span>Loading the next story…</div>' if nxt else "")
    upnext = (f'<aside class="upnext" id="upnext" aria-label="Up next"><a href="{post_path(nxt)}">'
              f'<img src="{("/" + e(thumb(nxt_im["file"]))) if nxt_im else ""}" alt=""{"" if nxt_im else " hidden"}>'
              f'<span><small>Up next</small><b>{e(nxt["title"])}</b></span></a>'
              '<button class="x" aria-label="Dismiss">×</button></aside>') if nxt else ""
    keywords = [seo.get("focus_keyword", ""), *seo.get("keywords", [])] if seo else p["tags"]
    h = head(title_tag, seo.get("meta_description") or p["excerpt"], post_path(p), hero["file"] if hero else None,
             "article", "\n".join(extra), keywords, hero["file"] if hero else None)
    float_like = (f'<button class="like-btn float-like" id="floatLike" data-like="{e(p["slug"])}" aria-pressed="false" '
                  f'aria-label="Like this story" hidden>{ICON["heart"]}<span class="n"></span></button>')
    return page(h, article + after + f'<div class="wrap">{more}</div>' + upnext + float_like, cat, "post")

CATEGORY_BLURBS = {
    "celebrity-weddings": "The latest celebrity weddings from Bollywood, Pakistan, Korea: outfits, venues, rituals and photos.",
    "real-weddings": "Real couples, real weddings: ceremonies, decor and the details that made them special.",
    "wedding-photography": "Wedding photography ideas, pre-wedding shoots, candid photographers and the shots couples love.",
    "bridal-fashion": "Bridal lehengas, sarees, jewellery and groomswear trends from Indian and Asian weddings.",
    "traditions": "Wedding rituals and traditions explained, from haldi and pheras to nikah and tea ceremonies.",
    "destination-weddings": "Destination wedding ideas and venues, from Udaipur palaces to Bali cliffs.",
    "planning": "Practical wedding planning tips: budgets, vendors, timelines and guest experience.",
}

def listing_page(title, description, path, ps, crumbs, all_posts, active=None, h1=None):
    crumbs_html, crumbs_ld = breadcrumbs(crumbs)
    coll = ld({"@context": "https://schema.org", "@type": "CollectionPage", "name": title, "description": description,
               "url": url(path), "mainEntity": {"@type": "ItemList", "itemListElement": [
                   {"@type": "ListItem", "position": i + 1, "url": url(post_path(p))} for i, p in enumerate(ps[:30])]}})
    image = next((p["images"][0]["file"] for p in ps if p["images"]), None)
    body = (f'<div class="wrap"><header class="list-head">{crumbs_html}<span class="kicker">{len(ps)} '
            f'{"story" if len(ps) == 1 else "stories"}</span><h1>{e(h1 or title)}</h1><p>{e(description)}</p></header>')
    if ps:
        body += f'<div class="grid">{"".join(card(p, "", "h2") for p in ps)}</div>'
        others = [p for p in all_posts if p not in ps][:6]
        if others:
            body += section("Keep exploring", f'<div class="grid">{"".join(card(p) for p in others)}</div>',
                            link=("Latest stories", "/"))
    else:
        body += ('<div class="empty"><h2>New stories are on the way</h2><p>Fresh stories are published every day at '
                 '7 AM and 7 PM. Meanwhile, here is what readers are enjoying.</p>'
                 f'<a class="btn" href="/">See the latest stories {ICON["arrow"]}</a></div>')
        if all_posts:
            body += section("Latest stories", f'<div class="grid">{"".join(card(p) for p in all_posts[:6])}</div>')
        body += section("Explore by topic", topics(all_posts))
    body += "</div>"
    return page(head(f"{title} | {SITE_NAME}", description, path, image, extra=coll + crumbs_ld), body, active, "list")

def reels_page(posts):
    reels = [p for p in posts if p.get("reel_path")]
    body = ('<div class="wrap"><header class="list-head"><span class="kicker">Reels</span><h1>Wedding reels</h1>'
            '<p>Short films of every story. Tap one to watch full screen, then swipe up for the next.</p></header>')
    body += (f'<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr))">'
             f'{"".join(reel_card(p) for p in reels)}</div>' if reels else
             f'<div class="empty"><h2>Reels are on the way</h2><p>Each new story gets its own reel.</p>'
             f'<a class="btn" href="/">See the latest stories {ICON["arrow"]}</a></div>')
    body += "</div>"
    image = poster(reels[0]) if reels else None
    return page(head(f"Wedding reels | {SITE_NAME}", "Short wedding reels: celebrity weddings, "
                     "real couples, rituals and bridal fashion.", "/reels/", image), body, "reels", "reels")

def not_found_page(posts):
    body = ('<div class="wrap"><header class="list-head"><span class="kicker">Page not found</span>'
            '<h1>This page has moved on</h1><p>But there are plenty of beautiful weddings to see.</p>'
            f'<p style="margin-top:1.25rem"><button class="btn" data-search>{ICON["search"]} Search stories</button></p></header>')
    if posts:
        body += section("Latest stories", f'<div class="grid">{"".join(card(p) for p in posts[:6])}</div>')
    body += section("Explore by topic", topics(posts)) + "</div>"
    return page(head(f"Page not found | {SITE_NAME}", "This page doesn't exist.", "/404.html", robots="noindex"), body)

# ---------- site files ----------

def sitemap(posts, cats, tags):
    def u(path, lastmod, images=()):
        imgs = "".join(f"<image:image><image:loc>{e(url('/' + im['file']))}</image:loc>"
                       f"<image:title>{e(im.get('alt'))}</image:title></image:image>" for im in images)
        return f"<url><loc>{e(url(path))}</loc><lastmod>{lastmod}</lastmod>{imgs}</url>"
    latest = posts[0]["publish_date"] if posts else datetime.now().date().isoformat()
    rows = [u("/", latest), u("/reels/", latest)]
    rows += [u(post_path(p), p["publish_date"], p["images"]) for p in posts]
    rows += [u(cat_path(c), max(p["publish_date"] for p in ps)) for c, ps in cats.items() if ps]
    rows += [u(tag_path(t), max(p["publish_date"] for p in ps)) for t, ps in tags.items()]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n' + "\n".join(rows) + "\n</urlset>\n")

def rss(posts):
    items = "".join(f"""<item><title>{e(p["title"])}</title><link>{e(url(post_path(p)))}</link>
<guid isPermaLink="true">{e(url(post_path(p)))}</guid><pubDate>{format_datetime(published_at(p))}</pubDate>
<category>{e(cat_name(p))}</category><description>{e(p["excerpt"])}</description>
{f'<enclosure url="{e(url("/" + p["images"][0]["file"]))}" type="image/jpeg" length="0"/>' if p["images"] else ""}</item>
""" for p in posts[:30])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<title>{e(SITE_NAME)}</title><link>{e(SITE_URL)}/</link><description>{e(SITE_DESCRIPTION)}</description>
<language>{SITE_LANG.lower()}</language><atom:link href="{e(url('/feed.xml'))}" rel="self" type="application/rss+xml"/>
{items}</channel></rss>
"""

def write_logo():
    """A simple square logo (favicon and structured data), drawn once."""
    path = SITE_DIR / "assets" / "logo.png"
    if path.exists():
        return
    from PIL import Image, ImageDraw
    from agents.reels import font
    img = Image.new("RGB", (512, 512), (179, 18, 46))
    d = ImageDraw.Draw(img)
    d.ellipse((40, 40, 472, 472), outline=(233, 162, 27), width=14)
    d.text((256, 270), SITE_NAME[0], font=font(300), fill=(255, 248, 246), anchor="mm")
    img.save(path)

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def export_site():
    """Rebuild every generated page and file from the published posts."""
    posts = db.posts("published")
    assets = SITE_DIR / "assets"
    assets.mkdir(exist_ok=True)
    for f in ("site.css", "app.js"):
        shutil.copy(THEME / f, assets / f)
    write_logo()
    for d in GENERATED_DIRS:
        shutil.rmtree(SITE_DIR / d, ignore_errors=True)

    for p in posts:
        write(SITE_DIR / "blog" / p["slug"] / "index.html", post_page(p, posts))
    cats = {c: [p for p in posts if (p.get("category") or "real-weddings") == c] for c in CATEGORIES}
    tags = {}
    for p in posts:
        for t in p["tags"]:
            if slugify(t):
                tags.setdefault(t.lower(), []).append(p)
    for c, ps in cats.items():   # every category gets a page, so menu links always work
        n = CATEGORIES[c]
        write(SITE_DIR / "category" / c / "index.html",
              listing_page(n, CATEGORY_BLURBS.get(c, f"{n}."), cat_path(c), ps,
                           [("Home", "/"), (n, cat_path(c))], posts, c))
    for t, ps in tags.items():
        title = t.title()
        write(SITE_DIR / "tag" / slugify(t) / "index.html",
              listing_page(f"{title} wedding stories", f"Wedding stories about {t} on {SITE_NAME}.",
                           tag_path(t), ps, [("Home", "/"), (title, tag_path(t))], posts, h1=title))
    write(SITE_DIR / "reels" / "index.html", reels_page(posts))
    write(SITE_DIR / "index.html", home_page(posts))
    write(SITE_DIR / "404.html", not_found_page(posts))
    write(SITE_DIR / "sitemap.xml", sitemap(posts, cats, tags))
    write(SITE_DIR / "robots.txt", f"User-agent: *\nAllow: /\nDisallow: /admin/\n\nSitemap: {SITE_URL}/sitemap.xml\n")
    write(SITE_DIR / "feed.xml", rss(posts))

    data = [{**{k: p[k] for k in ("id", "slug", "edition", "publish_date", "region", "country", "place", "title",
                                   "excerpt", "tags", "category")},
             "url": post_path(p), "category_name": cat_name(p),
             "thumb": "/" + thumb(p["images"][0]["file"]) if p["images"] else "",
             "reel": "/" + p["reel_path"] if p.get("reel_path") else "",
             "reel_poster": "/" + poster(p) if p.get("reel_path") else ""} for p in posts]
    write(SITE_DATA / "posts.json", json.dumps(data, ensure_ascii=False))
    log.info("site built: %d posts, %d categories, %d tags, reels page, sitemap and feed",
             len(posts), len(cats), len(tags))

def write_markdown(p):
    seo = p.get("seo") or {}
    path = CONTENT / f'{p["publish_date"]}-{p["edition"]}-{p["slug"]}.md'
    front = "\n".join([
        "---", f'title: {json.dumps(p["title"], ensure_ascii=False)}',
        f'seo_title: {json.dumps(seo.get("seo_title", p["title"]), ensure_ascii=False)}',
        f'description: {json.dumps(seo.get("meta_description", p["excerpt"]), ensure_ascii=False)}',
        f'focus_keyword: {json.dumps(seo.get("focus_keyword", ""), ensure_ascii=False)}',
        f'keywords: {json.dumps(seo.get("keywords", []), ensure_ascii=False)}',
        f'slug: {p["slug"]}', f'date: {p["publish_date"]}', f'edition: {p["edition"]}',
        f'category: {p.get("category")}', f'region: {p["region"]}', f'place: {json.dumps(p["place"], ensure_ascii=False)}',
        f'tags: {json.dumps(p["tags"], ensure_ascii=False)}', f'source: {p["source_url"]}', "---", ""])
    parts = list(seo.get("intro") or [])
    for s in seo.get("sections") or [{"heading": "", "paragraphs": p["body"]}]:
        parts += ([f'## {s["heading"]}'] if s.get("heading") else []) + s["paragraphs"]
    if seo.get("faq"):
        parts.append("## Frequently asked questions")
        for f in seo["faq"]:
            parts += [f'### {f["q"]}', f["a"]]
    path.write_text(front + "\n\n".join(parts) + "\n", encoding="utf-8")

def publish(post_id):
    db.update_post(post_id, status="published")
    p = next(x for x in db.posts() if x["id"] == post_id)
    write_markdown(p)
    export_site()
    log.info("published: %s -> %s", p["title"], url(post_path(p)))
