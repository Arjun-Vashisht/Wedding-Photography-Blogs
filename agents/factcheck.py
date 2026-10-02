"""Fact-check agent: finds claims in a draft that the source doesn't support, and can cut them out.

Specific claims about the story (people, venue, decor, dates, durations, vendors, numbers, what people did,
wore or said) must come from the source. General background and clearly general advice are fine.
"""
import logging
from agents.base import RateLimited, shape
import re
from config import MODEL

log = logging.getLogger("factcheck")

SYSTEM = """You are a fact-checker for a wedding blog. Compare a draft with its source article.
Go through the draft's claims and list the ones the source does not support, giving each a type:
- "story_detail": a specific detail about THIS wedding, couple, celebrity, venue or vendor that the source
  doesn't mention (how many days it lasted, decor, architecture, outfits, food, what someone did or said,
  techniques a photographer used, guest numbers).
- "number": a number, date, price, rule or statistic presented as fact that isn't in the source.
- "quote": words or opinions attributed to a person that the source doesn't give.
- "advice": general advice or tips for readers ("couples can ask their artist to...").
- "background": general knowledge about traditions, places or styles (what a haldi is, Rajasthan's art history).
Facts the source states in other words are supported: don't list them."""

BAD_TYPES = {"story_detail", "number", "quote"}

def check(llm, post, article):
    draft = "\n".join([post["title"], *post["body"], *(f'Q: {f["q"]} A: {f["a"]}' for f in post.get("faq", []))])
    try:
        r = llm.json(SYSTEM,
            f'Source:\n"""{article["text"][:9000]}"""\n\nDraft:\n"""{draft}"""\n\n'
            'Return JSON: {"claims": [{"claim": "<exact sentence or phrase from the draft>", '
            '"type": "story_detail" | "number" | "quote" | "advice" | "background", "why": "<short reason>"}]}',
            model=MODEL, max_tokens=3000)
        r = shape(r, "claims")
    except RateLimited:
        raise   # never publish an unchecked draft
    except Exception as e:
        log.warning("fact-check skipped: %s", e)
        return {"passed": True, "issues": [], "claims": [], "skipped": True}
    bad = [c for c in r.get("claims", []) if isinstance(c, dict) and c.get("claim") and c.get("type") in BAD_TYPES]
    issues = [f'Not in the source ({c["type"].replace("_", " ")}), remove it or make it general: '
              f'"{c["claim"]}"' for c in bad[:10]]
    log.info("fact-check: %d unsupported claims, %s", len(bad), "PASS" if not bad else "FAIL")
    return {"passed": not bad, "issues": issues, "claims": [c["claim"] for c in bad]}

def _tri(t):
    ws = re.findall(r"[a-z0-9']+", t.lower())
    return {tuple(ws[i:i + 3]) for i in range(len(ws) - 2)}

def _sentences(p):
    return [s for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]

def strip(post, claims):
    """Remove every sentence that carries an unsupported claim.
    Returns (post, removed_count, unmatched_claims) — unmatched claims couldn't be located, so can't be trusted."""
    targets = [_tri(c) for c in claims if len(_tri(c)) >= 2]
    everything = [*post["intro"], *(p for s in post["sections"] for p in s["paragraphs"]),
                  *(f["q"] + " " + f["a"] for f in post.get("faq", []))]
    all_sentences = [_tri(s) for p in everything for s in _sentences(p)]
    unmatched = [c for c, t in zip([c for c in claims if len(_tri(c)) >= 2], targets)
                 if not any(st and len(st & t) / min(len(st), len(t)) >= 0.6 for st in all_sentences)]
    unmatched += [c for c in claims if len(_tri(c)) < 2]
    def bad(sentence):
        st = _tri(sentence)
        return any(st and len(st & t) / min(len(st), len(t)) >= 0.6 for t in targets)
    removed = 0
    def clean(paragraphs):
        nonlocal removed
        out = []
        for p in paragraphs:
            keep = [s for s in _sentences(p) if not bad(s)]
            removed += len(_sentences(p)) - len(keep)
            if keep:
                out.append(" ".join(keep))
        return out
    post["intro"] = clean(post["intro"])
    post["sections"] = [{**s, "paragraphs": clean(s["paragraphs"])} for s in post["sections"]]
    post["sections"] = [s for s in post["sections"] if s["paragraphs"]]
    post["faq"] = [f for f in post.get("faq", []) if not bad(f["a"]) and not bad(f["q"])]
    post["body"] = post["intro"] + [p for s in post["sections"] for p in s["paragraphs"]]
    log.info("fact-check: removed %d sentences with unsupported claims (%d claims not located)",
             removed, len(unmatched))
    return post, removed, unmatched
