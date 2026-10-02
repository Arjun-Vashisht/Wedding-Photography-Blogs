"""Runs the agents end to end.

One edition:  select -> write -> originality, fact and SEO checks (revise if needed) -> images -> reel -> publish.
`go`:         crawl for fresh stories, then fill the next empty edition (morning or evening) today.
"""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import db
from agents import selector, writer, plagiarism, factcheck, seo, images, reels, publisher
from agents.base import RateLimited
from config import TIMEZONE, MAX_REWRITES, REQUIRE_APPROVAL, EVENING_TIME

log = logging.getLogger("orchestrator")

def now():
    return datetime.now(ZoneInfo(TIMEZONE))

def today():
    return now().date().isoformat()

def draft(llm, article, edition):
    """Write, then check originality, facts and SEO; send back for revisions.
    Returns (post, originality, seo) for the best draft that is original and accurate, or None."""
    feedback, best, post = None, None, None
    for attempt in range(MAX_REWRITES + 1):
        try:
            post = writer.write(llm, article, edition, feedback, previous=post)
        except RateLimited:
            raise
        except Exception as e:
            log.error("writer failed: %s", e)
            return best
        orig = plagiarism.check(llm, post, article)
        facts = factcheck.check(llm, post, article)
        cut = facts["claims"] + orig["sentences"]
        if cut:
            # Cut sentences that are invented or too close to the source; only ask for a rewrite
            # if the post would be too thin (or still unoriginal) without them.
            trimmed, removed, unmatched = factcheck.strip(json.loads(json.dumps(post)), cut)
            recheck = plagiarism.check(llm, trimmed, article, review=False)
            if removed and not unmatched and recheck["passed"] and seo.audit(trimmed)["passed"]:
                post, orig, facts = trimmed, recheck, {"passed": True, "issues": []}
        audit = seo.audit(post)
        trusted = orig["passed"] and facts["passed"]
        if trusted and audit["passed"] and (best is None or audit["score"] > best[2]["score"]):
            best = (post, orig, audit)
        if trusted and audit["passed"] and audit["score"] >= 85:
            return best
        problems = orig["issues"] + facts["issues"] + audit["issues"]
        feedback = "\n".join("- " + i for i in problems)
        log.info("rewrite %d requested (%d issues)", attempt + 1, len(problems))
    if not best:
        log.warning("no draft was original, accurate and SEO-ready after %d revisions", MAX_REWRITES)
    return best

def run_edition(llm, edition, date=None, max_sources=3, draft_only=False):
    """Make one post. With draft_only it always waits for the admin (no slot check, never auto-publishes)."""
    date = date or today()
    if not draft_only and db.post_for_slot(date, edition):
        log.info("%s %s edition already exists, skipping", date, edition)
        return None

    tried = set()
    for _ in range(max_sources):
        picked = selector.select(llm, edition, exclude=tried)
        if not picked:
            log.error("no candidates left. Run `python main.py crawl` first.")
            return None
        article, reason = picked
        tried.add(article["id"])

        try:
            result = draft(llm, article, edition)
        except RateLimited:
            log.error("the free model quota is used up for now; nothing was rejected. Run again later.")
            return None
        if not result:
            db.set_article_status(article["id"], "rejected")
            log.warning("source rejected after failed checks, trying another")
            continue
        post, orig, audit = result

        slug = publisher.unique_slug(post.get("slug") or post["focus_keyword"] or post["title"])
        imgs = images.find_images(llm, post, slug, article=article)
        reel_path, caption, music_credit = reels.make_reel(llm, post, imgs, slug,
                                                           region=article["region"], edition=edition)

        pid = db.add_post({
            "article_id": article["id"], "slug": slug, "edition": edition, "publish_date": date,
            "region": article["region"], "country": article["country"],
            "place": post.get("place") or article["place"], "title": post["title"],
            "excerpt": post["excerpt"], "body": post["body"], "tags": post.get("tags", []),
            "images": imgs, "reel_path": reel_path, "reel_caption": caption,
            "source_url": article["url"], "source_name": article["source"],
            "overlap": orig["overlap"], "selection_reason": reason,
            "status": "pending_review" if (REQUIRE_APPROVAL or draft_only) else "published",
            "category": post["category"], "seo_score": audit["score"],
            "seo": {k: post.get(k) for k in ("seo_title", "meta_description", "focus_keyword", "keywords",
                                             "intro", "sections", "faq", "read_minutes")}
                   | {"words": audit["words"], "music": music_credit},
        })
        db.set_article_status(article["id"], "used")
        log.info("post ready: %s | SEO %d/100 | originality overlap %.1f%% | %d photos | reel %s",
                 post["title"], audit["score"], orig["overlap"] * 100, len(imgs), "yes" if reel_path else "no")
        if REQUIRE_APPROVAL or draft_only:
            log.info("draft %d is waiting for the admin: http://localhost:8000/admin/", pid)
        else:
            publisher.publish(pid)
        return pid
    log.error("could not produce a good post for %s %s", date, edition)
    return None

def next_edition(date=None):
    """The next empty slot today: morning first, evening once it's afternoon or the morning is done."""
    date = date or today()
    h, m = (int(x) for x in EVENING_TIME.split(":"))
    order = ["morning", "evening"]
    if (now().hour, now().minute) >= (h - 6, m):  # from 6 hours before the evening slot, evening first
        order.reverse()
    return next((ed for ed in order if not db.post_for_slot(date, ed)), None)

def make_drafts(llm, count=None, crawl=True):
    """Prepare today's drafts for the admin to approve or reject (alternating morning/evening style)."""
    from agents.crawler import crawl as do_crawl
    from config import DRAFTS_PER_DAY
    count = count or DRAFTS_PER_DAY
    if crawl:
        do_crawl(llm)
    made = []
    for i in range(count):
        pid = run_edition(llm, "morning" if i % 2 == 0 else "evening", draft_only=True)
        if pid:
            made.append(pid)
        elif pid is None and not db.candidates():
            break
    log.info("%d drafts ready for review at http://localhost:8000/admin/", len(made))
    return made

def go(llm, edition=None, crawl=True):
    """Everything in one go: crawl, select, write, check, images, reel, publish, rebuild the site."""
    from agents.crawler import crawl as do_crawl
    edition = edition or next_edition()
    if not edition:
        log.info("both of today's editions are already published. Use --date to fill another day.")
        publisher.export_site()
        return None
    if REQUIRE_APPROVAL:   # the admin decides what goes live: prepare drafts instead
        return make_drafts(llm, crawl=crawl)
    if crawl:
        do_crawl(llm)
    log.info("making the %s edition", edition)
    pid = run_edition(llm, edition)
    publisher.export_site()
    return pid
