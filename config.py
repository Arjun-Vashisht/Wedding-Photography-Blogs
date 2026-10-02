"""Central settings for the wedding blog agents. Override anything in .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "pipeline.db"
SITE_DIR = BASE_DIR / "site"
SITE_DATA = SITE_DIR / "data"
IMAGES_DIR = SITE_DIR / "images"
REELS_DIR = SITE_DIR / "reels"
for d in (DATA_DIR, SITE_DATA, IMAGES_DIR, REELS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- Language model (free options) ---
# groq   : free API key at https://console.groq.com/keys (no card needed)
# gemini : free API key at https://aistudio.google.com/apikey
# ollama : runs on your own computer, no key; install from https://ollama.com
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()
_PROVIDERS = {
    #          base URL                                                   main model                 fast model
    "groq":   ("https://api.groq.com/openai/v1",                          "openai/gpt-oss-120b",     "openai/gpt-oss-20b"),  
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash",        "gemini-2.5-flash-lite"),
    "ollama": ("http://localhost:11434/v1",                               "llama3.1:8b",             "llama3.1:8b"),
}
_url, _model, _fast = _PROVIDERS.get(LLM_PROVIDER, _PROVIDERS["groq"])
LLM_BASE_URL = os.getenv("LLM_BASE_URL", _url)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", _model)            # writing, selection, review
FAST_MODEL = os.getenv("LLM_FAST_MODEL", _fast)   # bulk classification
# Used in order when a model's free daily quota runs out (free tiers limit each model separately)
_fallbacks = {"groq": "qwen/qwen3.8-27b,openai/gpt-oss-20b", "gemini": "gemini-2.5-flash-lite", "ollama": ""}
FALLBACK_MODELS = [m for m in os.getenv("LLM_FALLBACK_MODELS", _fallbacks.get(LLM_PROVIDER, "")).split(",") if m]

# --- Reel voiceover (Piper, free and offline) ---
VOICEOVER = os.getenv("VOICEOVER", "true").lower() == "true"
VOICE = os.getenv("VOICE", "hf_alpha")   # Indian female (Kokoro); also hf_beta, af_heart, bf_emma, or a Piper voice
VOICE_SPEED = float(os.getenv("VOICE_SPEED", "1.0"))  # 1.0 = normal; a touch faster suits reels

# --- Images ---
# Uses Pexels if PEXELS_API_KEY is set, else Pixabay if PIXABAY_API_KEY is set,
# else Openverse (Creative Commons photos, no key needed).
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")   # free key at https://pixabay.com/api/docs/

# --- Schedule (India time) ---
TIMEZONE = "Asia/Kolkata"
MORNING_TIME = os.getenv("MORNING_TIME", "07:00")
EVENING_TIME = os.getenv("EVENING_TIME", "19:00")
CRAWL_TIMES = os.getenv("CRAWL_TIMES", "05:30,17:30").split(",")

# --- Content mix ---
INDIA_SHARE = float(os.getenv("INDIA_SHARE", "0.70"))  # 70% India, 30% rest of Asia
QUOTA_WINDOW = int(os.getenv("QUOTA_WINDOW", "20"))    # look at the last N posts when balancing

# --- Originality check ---
SHINGLE_SIZE = 5            # compare 5-word sequences
MAX_OVERLAP = float(os.getenv("MAX_OVERLAP", "0.10"))  # max share of draft 5-grams found in the source
MAX_COPIED_RUN = 12         # longest identical run of words allowed
MAX_REWRITES = 2

# --- Human in the loop ---
# true  -> new posts wait for `python main.py review` before going live
# false -> posts that pass the originality check publish automatically
REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "true").lower() == "true"
DRAFTS_PER_DAY = int(os.getenv("DRAFTS_PER_DAY", "3"))     # drafts prepared each day for the admin to review
DRAFTS_TIME = os.getenv("DRAFTS_TIME", "06:00")             # when the daily drafts are prepared (India time)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")            # password for /admin (set in .env)

USER_AGENT = "PheraWeddingBot/1.0 (+https://example.com/bot)"

ASIAN_COUNTRIES_EX_INDIA = [
    "Sri Lanka", "Nepal", "Bangladesh", "Pakistan", "Bhutan", "Maldives", "Singapore",
    "Malaysia", "Indonesia", "Thailand", "Vietnam", "Philippines", "Cambodia", "Laos",
    "Myanmar", "China", "Hong Kong", "Taiwan", "Japan", "South Korea", "Mongolia",
    "UAE", "Oman", "Qatar", "Saudi Arabia", "Kuwait", "Bahrain", "Kazakhstan", "Uzbekistan",
]

# --- Sources ---
# Two kinds of source, all read with robots.txt respected and full article text fetched:
# 1. RSS feeds of wedding blogs and news sites. wedding=True means every item is about weddings;
#    for general feeds only items that mention weddings are kept.
# 2. Bing News searches, used to discover fresh stories across Asia (links point to the real publisher).
# Add feeds you like (most WordPress blogs have one at /feed/). Check each site's terms first.
FEEDS = [
    # Wedding blogs: real weddings, photographers, planning
    {"url": "https://www.maharaniweddings.com/feed", "wedding": True},
    {"url": "https://www.weddingsonline.in/blog/feed/", "wedding": True},
    {"url": "https://www.thewedcafe.com/feed/", "wedding": True},
    {"url": "https://www.thebridalbox.com/feed/", "wedding": True},
    # Celebrity and lifestyle news (only wedding items are kept)
    {"url": "https://www.pinkvilla.com/rss.xml"},
    {"url": "https://www.bollywoodhungama.com/rss/news.xml"},
    {"url": "https://www.hindustantimes.com/feeds/rss/lifestyle/rssfeed.xml"},
    {"url": "https://www.hindustantimes.com/feeds/rss/entertainment/bollywood/rssfeed.xml"},
    {"url": "https://www.vogue.in/feed/rss"},
    {"url": "https://www.timesnownews.com/feeds/gns-en-lifestyle.xml"},
    {"url": "https://www.siasat.com/feed/"},
    # Rest of Asia
    {"url": "https://www.soompi.com/feed"},
    {"url": "https://www.rappler.com/feed/"},
    {"url": "https://data.gmanetwork.com/gno/rss/showbiz/feed.xml"},
    {"url": "https://coconuts.co/feed/"},
    {"url": "https://www.straitstimes.com/news/life/rss.xml"},
]

NEWS_SEARCHES = [
    # India
    "indian wedding", "celebrity wedding india", "bollywood wedding", "real wedding india",
    "wedding photographer india", "wedding photography award", "wedding photography trends",
    "wedding photographer exhibition", "destination wedding india", "bridal lehenga", "south indian wedding",
    # Rest of Asia
    "pakistani celebrity wedding", "sri lanka wedding", "nepal wedding", "bangladesh wedding",
    "singapore wedding", "malaysia wedding", "bali wedding", "thailand wedding",
    "korean celebrity wedding", "japanese celebrity wedding", "philippines celebrity wedding",
    "chinese wedding tradition", "vietnam wedding", "dubai wedding",
]

def bing_news(q: str) -> str:
    # interval="7": stories from the last week
    return ("https://www.bing.com/news/search?format=rss&setlang=en&qft=interval%3d%227%22&q="
            + q.replace(" ", "+"))

MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "21"))         # ignore news older than this
EVERGREEN_AGE_DAYS = int(os.getenv("EVERGREEN_AGE_DAYS", "120"))  # wedding blogs (wedding=True feeds)
MIN_SOURCE_WORDS = int(os.getenv("MIN_SOURCE_WORDS", "180"))  # thinner sources are skipped (stops invented facts)

# Words that mark an item as wedding-related in general feeds
WEDDING_WORDS = [
    "wedding", "wed ", "weds", "bride", "bridal", "groom", "marriage", "married", "marries", "marry",
    "shaadi", "nikah", "vivah", "mehendi", "mehndi", "sangeet", "haldi", "baraat", "engagement",
    "engaged", "pre-wedding", "honeymoon", "reception", "lehenga", "trousseau", "mangalsutra",
    "varmala", "jaimala", "pheras", "tie the knot", "ties the knot", "tied the knot",
]

CATEGORIES = {
    "celebrity-weddings": "Celebrity Weddings",
    "real-weddings": "Real Weddings",
    "wedding-photography": "Wedding Photography",
    "bridal-fashion": "Bridal Fashion",
    "traditions": "Wedding Traditions",
    "destination-weddings": "Destination Weddings",
    "planning": "Wedding Planning",
}

# --- Website and SEO ---
SITE_NAME = os.getenv("SITE_NAME", "Phera")
SITE_TAGLINE = os.getenv("SITE_TAGLINE", "Wedding stories from India and across Asia")
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000").rstrip("/")  # set to your real domain when you go live
SITE_DESCRIPTION = os.getenv("SITE_DESCRIPTION",
    "Celebrity weddings, real wedding stories, wedding photography and bridal ideas. "
    "Two new stories every day.")
SITE_LANG = "en-IN"
