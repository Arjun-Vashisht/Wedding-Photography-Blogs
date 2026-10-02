"""Writer agent: turns a selected source into an original, SEO-ready Phera blog post,
and revises its own draft when the originality or SEO agents send it back."""
import html
import json
import logging
from agents.base import shape
from config import SITE_NAME, CATEGORIES

log = logging.getLogger("writer")

SYSTEM = f"""You are the senior writer and SEO editor of {SITE_NAME}, a wedding blog for Indian and Asian readers.
You write ORIGINAL posts inspired by one source story, built to rank on Google and be genuinely useful.

Accuracy (most important):
- Use ONLY facts stated in the source: names, places, dates, designers, photographers, numbers, quotes.
- Never invent quotes, prices, statistics, guest counts, rules or details. If you don't know it, leave it out.
- For celebrities, report only what the source says. No speculation or rumours. When you attribute a fact,
  name the publication ("according to Pinkvilla"); never write "the source" or "the article".
- You may add well-known general background (what a haldi ceremony is, why red is worn) but keep it general.
- Never describe things about this wedding the source doesn't mention: how many days it lasted, decor,
  architecture, food, what photographers did, how guests felt. Write it as general advice instead.

Originality:
- Your own angle, structure and wording. Never copy sentences or distinctive phrases from the source.

SEO:
- Pick one focus keyword people actually search (2-5 words, e.g. "kim woo bin wedding",
  "rajasthani bridal jewellery", "pre wedding shoot ideas").
- Put the focus keyword in the title, the SEO title, the meta description, the first sentence of the intro,
  one H2 heading and the slug. Use it naturally 3-6 times in total; never stuff it.
- Use 4-6 related keywords naturally in headings and text.
- Headings are H2s that say what the section answers. Short paragraphs (2-4 sentences).
- FAQs answer real questions readers search for, using facts from the source or general knowledge.

Style: warm, specific, vivid, plain English. No clichés ("fairytale", "dream wedding", "match made in heaven").
Explain rituals and terms briefly for readers from other regions.
Morning edition: practical and bright (ideas, how-tos, what to know). Evening: story-driven and immersive."""

FORMAT = f"""Return JSON with these keys:
"focus_keyword": the main search phrase, lowercase,
"keywords": 4-6 related search phrases,
"title": the H1 headline, 45-70 characters, contains the focus keyword,
"seo_title": for the <title> tag, 40-60 characters, focus keyword near the start,
"meta_description": 130-155 characters, contains the focus keyword, makes people want to click,
"slug": 3-6 lowercase words joined by hyphens, contains the focus keyword,
"excerpt": one sentence, under 30 words,
"intro": 2 short paragraphs as a list; the first sentence uses the focus keyword,
"sections": 5-6 objects {{"heading": "...", "paragraphs": ["...", "...", "..."]}}, each section 3-4 paragraphs
   of 50-80 words. The whole post should be 850-1100 words. Add depth with what the source says, general
   background on the rituals, places or styles involved, and clearly general advice for readers
   ("Couples who want this look can..."). Never pad with invented details about this wedding or these people,
"faq": 3 objects {{"q": "...", "a": "..."}} with 1-3 sentence answers,
"category": one of {", ".join(CATEGORIES)},
"tags": 3-5 short tags,
"place": city, state or country,
"image_queries": 4 stock-photo search phrases for photos that fit the story's culture and setting
   (e.g. "rajasthani bride", "indian wedding mandap flowers"); for celebrities use the person's name"""

def editor_guidance():
    """The admin's recent notes and rejection reasons, so the writer learns their taste."""
    import db
    notes = db.editor_notes()
    if not notes:
        return ""
    lines = [f'- ({n["decision"] or "rated"} {n["rating"]}/5) {n["reason"] or ""}{": " if n["reason"] and n["note"] else ""}{n["note"] or ""}'
             for n in notes]
    return "Your editor's recent feedback on past posts. Follow it:\n" + "\n".join(lines) + "\n"

def write(llm, article, edition, feedback=None, previous=None):
    people = ", ".join(article.get("people") or []) or "none named"
    prompt = f"""Edition: {edition}
Region: {article["region"]} | Country: {article["country"]} | Place: {article["place"]}
Category: {article.get("category")} | People: {people}
Source title: {article["title"]}
Source: {article["source"]} (name it as "{article["source"]}" when attributing), published {article.get("published_at") or "recently"}: {article["url"]}

Source material:
\"\"\"{article["text"][:9000]}\"\"\"
{editor_guidance()}
{revision(previous, feedback)}
{FORMAT}"""
    post = shape(llm.json(SYSTEM, prompt, max_tokens=7000))
    post = normalise(post, article)
    log.info("draft written: %s (%d words)", post["title"], word_count(post))
    return post

def revision(previous, feedback):
    """Instructions for revising the last draft instead of starting over (so fixes don't create new problems)."""
    if not feedback:
        return ""
    if not previous:
        return "Your last draft was sent back. Fix ALL of these issues:\n" + feedback + "\n"
    keep = {k: previous.get(k) for k in ("focus_keyword", "keywords", "title", "seo_title", "meta_description",
                                         "slug", "excerpt", "intro", "sections", "faq")}
    return f"""Your previous draft is below. It was sent back by the originality and SEO editors.
REVISE it: keep what works, rephrase every quoted sentence completely in your own words,
expand short sections with more useful detail, and fix ALL of these issues:
{feedback}

Previous draft:
{json.dumps(keep, ensure_ascii=False)}
"""

def normalise(post, article):
    """Fill gaps and derive `body` (flat paragraphs) so every agent can read the post the same way."""
    un = lambda x: html.unescape(html.unescape(str(x or ""))).strip()   # feeds often double-escape (&amp;amp;)
    clean = lambda xs: [un(x) for x in (xs or []) if un(x)]
    post["intro"] = clean(post.get("intro") if isinstance(post.get("intro"), list) else [post.get("intro", "")])
    post["sections"] = [{"heading": un(s.get("heading")), "paragraphs": clean(s.get("paragraphs"))}
                        for s in post.get("sections", []) if isinstance(s, dict) and s.get("paragraphs")]
    post["faq"] = [{"q": un(f.get("q")), "a": un(f.get("a"))}
                   for f in post.get("faq", []) if isinstance(f, dict) and f.get("q") and f.get("a")]
    post["body"] = post["intro"] + [p for s in post["sections"] for p in s["paragraphs"]]
    post["focus_keyword"] = str(post.get("focus_keyword", "")).lower().strip()
    post["keywords"] = clean(post.get("keywords"))
    post["tags"] = clean(post.get("tags"))[:5]
    post["title"] = un(post.get("title")) or un(article["title"])
    post["seo_title"] = un(post.get("seo_title")) or post["title"]
    post["meta_description"] = un(post.get("meta_description")) or un(post.get("excerpt"))
    post["excerpt"] = un(post.get("excerpt")) or post["meta_description"]
    post["place"] = un(post.get("place")) or article.get("place") or ""
    post["image_alt"] = un(post.get("image_alt"))
    post["category"] = post.get("category") if post.get("category") in CATEGORIES else article.get("category") or "real-weddings"
    post["image_queries"] = clean(post.get("image_queries"))
    post["read_minutes"] = max(2, round(word_count(post) / 220))
    return post

def word_count(post):
    text = " ".join(post["body"] + [f["q"] + " " + f["a"] for f in post.get("faq", [])])
    return len(text.split())
