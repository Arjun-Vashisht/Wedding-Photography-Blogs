"""Offline test of the whole pipeline with a fake model (no API key or network needed).
Everything is written to a temporary folder, so your real site and database are untouched.
Run:  python tests/test_offline.py
"""
import json, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
tmp = Path(tempfile.mkdtemp())
real_index = config.SITE_DIR / "index.html"
config.DB_PATH = tmp / "test.db"
config.SITE_DIR, config.SITE_DATA = tmp / "site", tmp / "site" / "data"
config.IMAGES_DIR, config.REELS_DIR = tmp / "site" / "images", tmp / "site" / "reels"
config.BASE_DIR = tmp
for d in (config.SITE_DATA, config.IMAGES_DIR, config.REELS_DIR):
    d.mkdir(parents=True, exist_ok=True)
shutil.copytree(Path(__file__).resolve().parents[1] / "theme", tmp / "theme")

import db; db.DB_PATH = config.DB_PATH
from agents import plagiarism, reels, seo, images
images.availability = lambda article: 5          # no network
images.find_images = lambda *a, **k: []
from agents import music
music.pick = lambda *a, **k: None               # no network; music mixing is checked separately
import orchestrator
orchestrator.REQUIRE_APPROVAL = False   # the test publishes directly; approval is tested separately

SECTION = ("The {kw} began with a haldi morning, turmeric paste and marigolds, then moved to a sangeet "
           "with family dances and a live dhol, before the pheras under a jasmine mandap at dusk number {k}.")

class FakeModel:
    """Returns canned JSON shaped like the real agents expect."""
    def __init__(self): self.n = 0
    def json(self, system, prompt, **kw):
        self.n += 1
        if "commissioning editor" in system:
            first_id = int(prompt.split("id=")[1].split(" ")[0]); return {"id": first_id, "reason": "test pick"}
        if "originality reviewer" in system:
            return {"flagged": [], "verdict": "pass"}
        if "Instagram Reel" in system:
            return {"scenes": [{"text": "A wedding you have to see"}, {"text": "Marigolds everywhere"},
                               {"text": "Read the full story, link in bio"}], "caption": "Test caption",
                    "hashtags": ["#indianwedding"]}
        if "senior writer" in system:
            k, kw = self.n, "jaipur palace wedding"
            para = lambda i: f"{SECTION.format(kw=kw if i == 0 else 'celebration', k=k)} Detail {i} of story {k}."
            return {"focus_keyword": kw, "keywords": ["rajasthani wedding", "palace venue", "haldi", "pheras"],
                    "title": f"Jaipur palace wedding story number {k}: colour and family",
                    "seo_title": f"Jaipur Palace Wedding {k}: Rituals, Decor and Outfits",
                    "meta_description": f"Inside a Jaipur palace wedding {k}: the haldi, sangeet, pheras, outfits and "
                                        "decor that made this Rajasthani celebration special.",
                    "slug": f"jaipur-palace-wedding-{k}", "excerpt": "A short original excerpt.",
                    "intro": ["This jaipur palace wedding mixed old rituals with new ideas.", para(1)],
                    "sections": [{"heading": h, "paragraphs": [para(i * 4 + j) for j in range(4)]}
                                 for i, h in enumerate(["The jaipur palace wedding venue", "Haldi and sangeet",
                                                        "The pheras", "What the couple wore", "The reception"])],
                    "faq": [{"q": f"Question {i}?", "a": "A short, factual answer."} for i in range(3)],
                    "category": "real-weddings", "tags": ["Jaipur", "Palace Weddings"], "place": "Jaipur",
                    "image_queries": ["indian wedding"], "image_alt": "Bride at a jaipur palace wedding"}
        return {}

def seed(n_india=20, n_asia=10):
    for i in range(n_india + n_asia):
        r = "india" if i < n_india else "asia"
        db.add_article({"url": f"https://example.com/{i}", "title": f"Source {i}", "summary": "s",
            "text": "The bride wore red and the family danced all night under strings of marigold flowers. " * 20,
            "source": "Example", "is_wedding": 1, "region": r, "country": "India" if r == "india" else "Sri Lanka",
            "place": "", "topics": [], "quality": 4, "category": "real-weddings"})

def test_overlap():
    src = "the bride wore a red lehenga embroidered with gold thread and walked in under a canopy of marigolds"
    assert plagiarism.overlap(src, src) == 1.0
    assert plagiarism.longest_run(src, src) == len(src.split())
    assert plagiarism.overlap("completely different words describing a seaside ceremony at dusk", src) == 0
    print("overlap checks: ok")

def test_seo_audit():
    weak = {"focus_keyword": "kim woo bin wedding", "title": "A lovely day", "seo_title": "A lovely day",
            "meta_description": "Short.", "slug": "lovely-day", "intro": ["Hello."], "body": ["Hello."],
            "sections": [], "faq": [], "keywords": []}
    r = seo.audit(weak)
    assert not r["passed"] and r["score"] < 40, r
    print(f"seo audit: weak draft scored {r['score']}/100 and was sent back: ok")

def test_pipeline():
    db.init(); seed()
    model = FakeModel()
    real_make, calls = reels.make_reel, {"n": 0}
    def make_some(*a, **k):  # render a real video for the first post only, to keep the test quick
        calls["n"] += 1
        return real_make(*a, **k) if calls["n"] <= 1 else (None, "", "")
    orchestrator.reels.make_reel = make_some
    for day in range(10):
        for ed in ("morning", "evening"):
            orchestrator.run_edition(model, ed, date=f"2026-10-{day+1:02d}")
    pubs = db.posts("published")
    india = sum(p["region"] == "india" for p in pubs)
    print(f"published {len(pubs)} posts, India share {india/len(pubs):.0%}")
    assert len(pubs) == 20 and abs(india / len(pubs) - 0.70) <= 0.05
    assert all(p["seo_score"] >= 85 for p in pubs), [p["seo_score"] for p in pubs]
    assert any(p["reel_path"] for p in pubs), "reel should render (pip install imageio-ffmpeg)"

    site = config.SITE_DIR
    page = (site / "blog" / pubs[0]["slug"] / "index.html").read_text()
    for must in ("<title>", 'name="description"', 'rel="canonical"', 'property="og:title"', '"BlogPosting"',
                 '"BreadcrumbList"', '"FAQPage"', "<h1>", "<h2>"):
        assert must in page, f"post page is missing {must}"
    assert page.count("<h1>") == 1
    assert (site / "category" / "real-weddings" / "index.html").exists()
    assert "<loc>" in (site / "sitemap.xml").read_text()
    assert "Sitemap:" in (site / "robots.txt").read_text()
    assert "<item>" in (site / "feed.xml").read_text()
    home = (site / "index.html").read_text()
    assert 'rel="canonical"' in home and "/blog/" in home
    assert len(json.loads((site / "data" / "posts.json").read_text())) == 20
    print("pipeline and SEO site build: ok")

def test_admin_review():
    import review
    from agents import writer
    model = FakeModel()
    seed(2, 0)
    a = orchestrator.run_edition(model, "morning", draft_only=True)
    b = orchestrator.run_edition(model, "evening", draft_only=True)
    assert db.get_post(a)["status"] == db.get_post(b)["status"] == "pending_review"
    review.approve(a, 5, "Loved the detail about the rituals")
    review.reject(b, "repeat", "We covered Jaipur palaces last week")
    assert db.get_post(a)["status"] == "published" and db.get_post(b)["status"] == "rejected"
    guide = writer.editor_guidance()
    assert "Loved the detail" in guide and "Too similar to a recent post" in guide, guide
    best, worst = db.feedback_examples()
    assert any(x["note"] == "Loved the detail about the rituals" for x in best)
    assert any(x["reason"] == "Too similar to a recent post" for x in worst)
    print("admin approve/reject and agent learning: ok")

if __name__ == "__main__":
    try:
        test_overlap(); test_seo_audit(); test_pipeline(); test_admin_review()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all tests passed")
