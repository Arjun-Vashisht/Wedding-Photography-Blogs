"""Editor decisions on drafts, shared by the admin page and `python main.py review`.

Approving publishes the draft now and records the rating; rejecting removes it and records why.
Both go into the feedback table, which the selector and writer agents read to learn what to publish.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import db
from config import SITE_DIR, REELS_DIR, TIMEZONE

log = logging.getLogger("review")

REJECT_REASONS = {
    "off-topic": "Not a wedding story readers want",
    "inaccurate": "Facts look wrong or made up",
    "weak-writing": "Writing is dull or repetitive",
    "bad-photos": "Photos don't fit the story",
    "repeat": "Too similar to a recent post",
    "too-thin": "Not enough real detail",
    "not-for-us": "Not right for our audience",
}

def approve(pid, rating=4, note=""):
    from agents import publisher
    p = db.get_post(pid)
    if not p or p["status"] != "pending_review":
        raise ValueError("this draft is no longer waiting for review")
    now = datetime.now(ZoneInfo(TIMEZONE))
    db.update_post(pid, publish_date=now.date().isoformat(), edition="morning" if now.hour < 13 else "evening")
    db.add_feedback(pid, max(1, min(5, int(rating))), note.strip(), "approved")
    publisher.publish(pid)
    log.info("approved and published: %s (%s/5)", p["title"], rating)

def reject(pid, reason="", note=""):
    p = db.get_post(pid)
    if not p or p["status"] != "pending_review":
        raise ValueError("this draft is no longer waiting for review")
    db.update_post(pid, status="rejected")
    db.set_article_status(p["article_id"], "rejected")
    label = REJECT_REASONS.get(reason, reason)
    db.add_feedback(pid, 1, note.strip(), "rejected", label or None)
    # Free the disk space the draft's photos and reel used (they were never public).
    for im in p["images"]:
        for f in (SITE_DIR / im["file"], (SITE_DIR / im["file"]).with_name((SITE_DIR / im["file"]).stem + "-sm.jpg")):
            f.unlink(missing_ok=True)
    for f in REELS_DIR.glob(f'{p["slug"]}*'):
        f.unlink(missing_ok=True)
    log.info("rejected: %s (%s)", p["title"], label or "no reason")

def learning_summary():
    """What the agents have learned from the editor so far, in plain words, for the admin page."""
    from config import CATEGORIES
    scores = db.category_scores()
    liked = sorted(((v[0], CATEGORIES.get(k, k or "Other")) for k, v in scores.items() if v[0] >= 3.5), reverse=True)
    disliked = sorted((v[0], CATEGORIES.get(k, k or "Other")) for k, v in scores.items() if v[0] < 2.5)
    with db.conn() as c:
        reasons = c.execute("""SELECT reason, COUNT(*) n FROM feedback WHERE decision='rejected' AND reason IS NOT NULL
                               GROUP BY reason ORDER BY n DESC LIMIT 3""").fetchall()
        totals = dict(c.execute("SELECT decision, COUNT(*) FROM feedback WHERE decision IS NOT NULL GROUP BY decision").fetchall())
    return {"approved": totals.get("approved", 0), "rejected": totals.get("rejected", 0),
            "liked": [n for _, n in liked][:3], "disliked": [n for _, n in disliked][:3],
            "reasons": [f"{r['reason']} ({r['n']})" for r in reasons]}
