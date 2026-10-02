"""SEO agent: audits a draft against on-page SEO rules (no API calls) and returns fixes for the writer.

Blocking issues send the draft back for a rewrite; minor ones are only reported.
"""
import logging
import re

log = logging.getLogger("seo")

def _has(text, kw):
    return bool(kw) and kw in re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())

def _norm(kw):
    return re.sub(r"[^a-z0-9 ]+", " ", (kw or "").lower()).strip()

def audit(post):
    kw = _norm(post.get("focus_keyword"))
    body = " ".join(post["body"])
    words = len(body.split()) + sum(len((f["q"] + " " + f["a"]).split()) for f in post.get("faq", []))
    first = post["intro"][0] if post.get("intro") else ""
    headings = [s["heading"] for s in post.get("sections", [])]
    uses = len(re.findall(r"\b" + re.escape(kw) + r"\b", _norm(body))) if kw else 0
    density = uses * max(1, len(kw.split())) / max(1, words) * 100

    checks = [
        # (passed, blocking, message for the writer, points)
        (bool(kw), True, "Choose a focus keyword.", 10),
        (_has(post["title"], kw), True, f'Put the focus keyword "{kw}" in the title.', 10),
        (_has(post["seo_title"], kw), True, f'Put the focus keyword "{kw}" in the seo_title.', 8),
        (30 <= len(post["seo_title"]) <= 62, False,
         f'seo_title is {len(post["seo_title"])} characters; make it 40-60.', 6),
        (_has(post["meta_description"], kw), True, f'Put the focus keyword "{kw}" in the meta_description.', 8),
        (110 <= len(post["meta_description"]) <= 160, False,
         f'meta_description is {len(post["meta_description"])} characters; make it 130-155.', 6),
        (_has(first, kw), False, f'Use the focus keyword "{kw}" in the first sentence of the intro.', 8),
        (any(_has(h, kw) for h in headings), False, f'Use the focus keyword "{kw}" in one H2 heading.', 6),
        (all(w in post.get("slug", "") for w in kw.split()[:2]), False, "Put the focus keyword in the slug.", 4),
        (words >= 600, True, f"The post is {words} words; write at least 850 (expand with background and general advice, not invented details).", 12),
        (words >= 800, False, f"The post is {words} words; aim for 850-1100.", 4),
        (len(headings) >= 4, True, f"Use at least 4 sections with H2 headings (you have {len(headings)}).", 6),
        (len(set(h.lower() for h in headings)) == len(headings), False, "Make every H2 heading different.", 2),
        (len(post.get("faq", [])) >= 3, False, "Add 3 FAQs.", 4),
        (len(post.get("keywords", [])) >= 4, False, "List 4-6 related keywords.", 2),
        (uses >= 2, False, f'The focus keyword appears {uses} times in the text; use it naturally 3-6 times.', 2),
        (density <= 3, True, f"The focus keyword is overused ({density:.1f}%); keep it under 2.5%.", 2),
    ]
    score = sum(p for ok, _, _, p in checks if ok)
    total = sum(p for *_, p in checks)
    blocking = [m for ok, b, m, _ in checks if not ok and b]
    minor = [m for ok, b, m, _ in checks if not ok and not b]
    result = {"score": round(score / total * 100), "passed": not blocking, "issues": blocking + minor,
              "blocking": blocking, "words": words}
    log.info("seo: score %d/100, %d words, %s", result["score"], words, "PASS" if result["passed"] else "FAIL")
    return result
