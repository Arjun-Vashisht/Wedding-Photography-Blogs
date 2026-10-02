"""Plagiarism agent: measures copied text locally, then asks the model to review close paraphrase."""
import logging
from agents.base import RateLimited, shape
import re
from config import SHINGLE_SIZE, MAX_OVERLAP, MAX_COPIED_RUN, FAST_MODEL

log = logging.getLogger("plagiarism")

def words(t):
    return re.findall(r"[a-z0-9']+", (t or "").lower())

def shingles(ws, n=SHINGLE_SIZE):
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}

def overlap(draft, source):
    """Share of the draft's 5-word sequences that also appear in the source (0..1)."""
    d, s = shingles(words(draft)), shingles(words(source))
    return len(d & s) / len(d) if d else 0.0

def longest_run(draft, source):
    """Longest run of identical consecutive words shared with the source."""
    a, b = words(draft), words(source)
    if not a or not b:
        return 0
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best

REVIEW_SYSTEM = """You are an originality reviewer for a blog. Compare a draft with its source.
Flag any sentence in the draft that copies the source or paraphrases it so closely that it
would count as plagiarism (same structure and wording with a few words swapped).
Common phrases and facts are fine."""

def sentences(t):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", t or "") if len(x.split()) >= 5]

def close_to_source(sentence, source_words, src_tri):
    """True if a sentence really is near-copied: most of its 3-word phrases, or an 8-word run, are in the source."""
    ws = words(sentence)
    tri = shingles(ws, 3)
    share = len(tri & src_tri) / len(tri) if tri else 0
    return share >= 0.5 or longest_run(sentence, " ".join(source_words)) >= 10

def check(llm, post, article, review=True):
    """review=False skips the model's paraphrase review (used to re-check a trimmed draft)."""
    parts = [post["title"], post["excerpt"], *post["body"], *(f["q"] + " " + f["a"] for f in post.get("faq", []))]
    draft = " ".join(parts)
    source = " ".join([article["title"], article["summary"] or "", article["text"] or ""])
    ov, run = overlap(draft, source), longest_run(draft, source)
    src_words, src_tri = words(source), shingles(words(source), 3)
    issues = []
    if ov > MAX_OVERLAP:
        issues.append(f"{ov:.0%} of the draft's 5-word phrases appear in the source (limit {MAX_OVERLAP:.0%}).")
    copied, confirmed = [], []
    if run >= MAX_COPIED_RUN:
        copied = [x for x in sentences(" ".join(parts)) if longest_run(x, source) >= MAX_COPIED_RUN]
        issues += [f'Rewrite in your own words, it copies the source: "{x}"' for x in copied[:6]] or \
                  [f"A run of {run} identical words is copied from the source."]
    if review and len(src_words) > 80:
        try:
            review = llm.json(REVIEW_SYSTEM,
                f'Source:\n"""{source[:7000]}"""\n\nDraft:\n"""{draft}"""\n\n'
                'Return JSON: {"flagged": [<draft sentences that are too close>], "verdict": "pass" or "fail"}',
                model=FAST_MODEL, max_tokens=1500)
            review = shape(review, "flagged")
            flagged = [str(x) for x in review.get("flagged", []) if str(x).strip()]
            # The reviewer can be over-cautious: keep only flags the text comparison confirms.
            confirmed = [x for x in flagged if close_to_source(x, src_words, src_tri)]
            if len(flagged) != len(confirmed):
                log.info("paraphrase review: %d flagged, %d confirmed", len(flagged), len(confirmed))
            issues += [f'Too close to the source, rephrase: "{x}"' for x in confirmed[:6]
                       if not any(x in i for i in issues)]
        except RateLimited:
            raise   # never publish an unchecked draft
        except Exception as e:
            log.warning("paraphrase review skipped: %s", e)
    result = {"passed": not issues, "overlap": round(ov, 4), "longest_run": run, "issues": issues,
              "sentences": copied + confirmed}
    log.info("originality: overlap %.1f%%, longest run %d, %s", ov * 100, run, "PASS" if result["passed"] else "FAIL")
    return result
