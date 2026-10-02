# Phera — multi-agent wedding blog

A team of AI agents, running entirely on free tools, that finds wedding stories (celebrity weddings, real
weddings, wedding photography, bridal fashion and traditions), writes original SEO-ready posts, checks them
for plagiarism and accuracy, adds free photos and a narrated reel, and publishes them to a fast wedding
magazine website. Every day the agents prepare 2-3 drafts; you approve or reject them on an admin page,
and the agents learn from your decisions. About 70% of stories come from India and 30% from the rest of Asia.

## Quick start

You need Python 3.10+.

```bash
git clone <your-repo-url> wedding-agents
cd wedding-agents
python -m venv .venv               # or: uv venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and add a free Groq key (https://console.groq.com/keys, no card needed) as `LLM_API_KEY`,
and choose an `ADMIN_PASSWORD`. Then:

```bash
python main.py init        # create the database
python main.py drafts      # collect stories and prepare today's drafts (a few minutes each)
python main.py serve       # website at http://localhost:8000, admin at http://localhost:8000/admin/
```

On the admin page, preview each draft and approve it (with a 1-5 rating) or reject it (with a reason).
Approved drafts go live immediately. To run everything every day, leave this running:

```bash
python main.py schedule    # crawls at 5:30 and 17:30, prepares drafts at 6:00 (India time)
```

## The agents

| Agent | File | What it does |
|---|---|---|
| Crawler | `agents/crawler.py` | Reads wedding blogs, celebrity and lifestyle news feeds and Bing News searches. Respects robots.txt, fetches the full article text, skips thin stories, and tags each one by region, category, people, place and quality. |
| Selector | `agents/selector.py` | Keeps the 70/30 mix, checks free photos exist, balances real weddings, photography news and celebrity weddings, and learns from your approvals, ratings, rejection reasons and notes. |
| Writer | `agents/writer.py` | Writes an original 850-1100 word post from the source's facts: focus keyword, SEO title, meta description, H2 sections and FAQs. Follows your recent notes. |
| Plagiarism | `agents/plagiarism.py` | Measures copied phrases locally and has the model flag close paraphrase (each flag is double-checked). |
| Fact-check | `agents/factcheck.py` | Flags claims the source doesn't support and cuts them out. |
| SEO | `agents/seo.py` | Scores each draft out of 100 and sends weak drafts back for revision. |
| Images | `agents/images.py` | Finds freely licensed photos (Openverse; Pixabay or Pexels with a key), resizes them and records the credit. |
| Reels | `agents/reels.py` | Writes a 6-scene script and renders a 1080×1920 MP4 with slow zooms, crossfades and text. |
| Voice | `agents/voice.py` | Narrates the reel with a natural female voice (Kokoro, offline). |
| Music | `agents/music.py` | Adds a free, openly licensed instrumental track that dips under the voice. |
| Publisher | `agents/publisher.py` | Builds the static website: post, category, tag and reels pages, sitemap, robots.txt and RSS feed. |

`orchestrator.py` chains the agents, `review.py` records your decisions, `admin.py` is the admin page,
`main.py` is the command line, and `theme/` holds the website's CSS and JavaScript.

## Commands

| Command | What it does |
|---|---|
| `python main.py drafts [--count N]` | Collect stories and prepare N drafts for review |
| `python main.py serve` | Website and admin page on http://localhost:8000 |
| `python main.py schedule` | Run the daily routine automatically |
| `python main.py review` | Approve or reject drafts in the terminal instead of the admin page |
| `python main.py status` | Region mix and counts |
| `python main.py build` | Rebuild the website from published posts |
| `python main.py voices` | Save a sample of each voice to `voices/samples/` |
| `python main.py remake-reel [--voice NAME]` | Re-render reels, e.g. with another voice |
| `python main.py crawl` / `run morning` | Individual steps |

With `REQUIRE_APPROVAL=false` the agents publish on their own at 7 AM and 7 PM instead of waiting for you.

## The website

A static site in `site/` (generated, not committed): homepage, a page per post, all topic pages, tags,
a full-screen reel player and a 404 page. Readers move from story to story without effort: the next story
loads below the current one, an "Up next" card appears halfway through, arrow keys and swipes change stories,
photos open full screen with zoom, and there is instant search.

Every page has full SEO markup: title and meta description, canonical URL, Open Graph and Twitter cards,
JSON-LD (BlogPosting, BreadcrumbList, FAQPage, VideoObject), alt text and image sizes, plus `sitemap.xml`,
`robots.txt` and `feed.xml`. Before going live, set `SITE_URL` in `.env` to your domain and run
`python main.py build`. Upload `site/` to any static host; the admin page needs `python main.py serve`
running on a machine you control.

## Free services used

- Language model: Groq free tier (falls back to other free models when a daily limit is reached), or Gemini, or Ollama.
- Photos: Openverse (Creative Commons; credits shown on every photo), optionally Pixabay or Pexels.
- Music: Jamendo and Freesound tracks via Openverse, credited in the reel caption and post.
- Voice: Kokoro (runs offline; the model downloads once, about 350 MB, into `voices/`).
- Video: ffmpeg bundled with the `imageio-ffmpeg` package.

## Test without a model

```bash
python tests/test_offline.py
```

Runs 10 days of the pipeline with a fake model in a temporary folder: checks the 70/30 mix, originality and
SEO checks, reel rendering, the generated SEO pages, and that admin approvals and rejections reach the agents.

## Notes

- Add feeds to `FEEDS` and searches to `NEWS_SEARCHES` in `config.py`. Check each site's terms first.
- Posts use only the source's facts, link back to it, and credit every photo. Photos are representative
  (labelled "not from this wedding"); photos from news sites are never used.
- To use your own reel music, put MP3s in `music/` (and optionally `music/credits.json`).
