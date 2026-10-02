"""Selection agent: keeps the 70/30 India/Asia mix, checks photos are available,
balances topics, and learns from editor ratings."""
import logging
from agents.base import shape
import db
from agents import images
from config import INDIA_SHARE, QUOTA_WINDOW, CATEGORIES

log = logging.getLogger("selector")

def needed_region():
    """Pick the region that keeps the rolling mix closest to the target share."""
    recent = db.recent_regions(QUOTA_WINDOW)
    total, india = len(recent), recent.count("india")
    if_india = (india + 1) / (total + 1)
    if_asia = india / (total + 1)
    return "india" if abs(if_india - INDIA_SHARE) <= abs(if_asia - INDIA_SHARE) else "asia"

SYSTEM = """You are the commissioning editor of Phera, a wedding blog for Indian and Asian readers.
Pick the ONE candidate that will make the best original blog story for this slot.
What makes a winner:
- Rich, specific detail in the source (names, outfits, rituals, venue, photographer, decor) so the post
  can be accurate and long without guessing.
- Search demand: celebrity weddings, trending couples, popular wedding photography and fashion ideas
  get the most readers. Fresh news beats old news.
- Free photos available (photos > 0). Strongly prefer stories that have them.
- Variety: avoid the same category or topic as the most recent posts.
- Balance: real weddings (actual couples and their celebrations) and what is happening in wedding photography
  (photographers' work, awards, exhibitions, techniques and trends) matter as much as celebrity weddings.
  Pre-wedding photoshoots are fine now and then but must not dominate.
- The editor approves or rejects every post. Their ratings, reasons and notes below are the strongest signal:
  pick stories like the ones they approved and rated highly, and avoid what they rejected and why.
Morning edition: practical, bright and useful (fashion, photography ideas, traditions explained, planning).
Evening edition: story-driven and immersive (celebrity and real weddings, culture, craft).
Learn from the editor's ratings below: favour what they liked, avoid what they disliked."""

def select(llm, edition, exclude=()):
    """Pick the best candidate story for this edition. Returns (article, reason) or None."""
    region = needed_region()
    pool = [c for c in db.candidates(region) if c["id"] not in exclude]
    if not pool:
        other = "asia" if region == "india" else "india"
        log.warning("no %s candidates, falling back to %s", region, other)
        region, pool = other, [c for c in db.candidates(other) if c["id"] not in exclude]
    if not pool:
        return None

    pool = pool[:10]
    for c in pool:
        c["photos"] = images.availability(c)
    best, worst = db.feedback_examples()
    listing = "\n".join(
        f'- id={c["id"]} | {c["title"]} | {CATEGORIES.get(c.get("category"), "?")} | '
        f'{c["place"] or c["country"]} | published {c.get("published_at") or "?"} | quality {c["quality"]} | '
        f'{len(c["text"].split())} words | photos {c["photos"]} | {c["text"][:280]}' for c in pool)
    prompt = f"""Slot: {edition} edition. Region needed: {region}.

Recent posts (don't repeat these topics):
{chr(10).join("- " + t for t in db.recent_titles()) or "- none yet"}
Recent categories: {", ".join(filter(None, db.recent_categories())) or "none yet"}

Editor approved and liked:
{chr(10).join(f'- {b["title"]} [{CATEGORIES.get(b["category"], "?")}] ({b["rating"]}/5){": " + b["note"] if b["note"] else ""}' for b in best) or "- no ratings yet"}

Editor rejected or disliked:
{chr(10).join(f'- {w["title"]} [{CATEGORIES.get(w["category"], "?")}] ({w["rating"]}/5){" because: " + w["reason"] if w["reason"] else ""}{": " + w["note"] if w["note"] else ""}' for w in worst) or "- no ratings yet"}

Average editor rating by category: {", ".join(f"{CATEGORIES.get(k, k)} {v[0]}/5 ({v[1]})" for k, v in db.category_scores().items()) or "none yet"}

Candidates:
{listing}

Return JSON: {{"id": <candidate id>, "reason": "<one sentence>"}}"""
    try:
        pick = shape(llm.json(SYSTEM, prompt, max_tokens=1000))
        chosen = next(c for c in pool if c["id"] == int(pick["id"]))
        reason = pick.get("reason", "")
    except Exception as e:
        log.warning("selection fell back to top-quality candidate: %s", e)
        chosen = max(pool, key=lambda c: (c["photos"] > 0, c["quality"] or 0))
        reason = "highest quality candidate with photos"
    log.info("selected [%s/%s] %s (photos available: %d)", region, chosen.get("category"), chosen["title"], chosen["photos"])
    return chosen, reason
