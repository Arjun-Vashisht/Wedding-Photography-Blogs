"""SQLite storage shared by all agents."""
import json
import sqlite3
from datetime import datetime
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT UNIQUE, title TEXT, summary TEXT, text TEXT, source TEXT,
  is_wedding INTEGER, region TEXT, country TEXT, place TEXT, topics TEXT, quality INTEGER,
  crawled_at TEXT, status TEXT DEFAULT 'new',         -- new | used | rejected
  category TEXT, people TEXT, published_at TEXT
);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER, slug TEXT UNIQUE, edition TEXT, publish_date TEXT,
  region TEXT, country TEXT, place TEXT, title TEXT, excerpt TEXT, body TEXT, tags TEXT,
  images TEXT, reel_path TEXT, reel_caption TEXT, source_url TEXT, source_name TEXT,
  overlap REAL, selection_reason TEXT,
  status TEXT,                                        -- pending_review | published | rejected
  created_at TEXT,
  category TEXT, seo TEXT, seo_score INTEGER
);
CREATE TABLE IF NOT EXISTS trends(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run TEXT, topic TEXT, score INTEGER, why TEXT, queries TEXT, sources TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS views(                    -- one row per reader per post per day
  post_id INTEGER, visitor TEXT, day TEXT, PRIMARY KEY(post_id, visitor, day)
);
CREATE TABLE IF NOT EXISTS likes(                    -- one like per reader per post
  post_id INTEGER, visitor TEXT, created_at TEXT, PRIMARY KEY(post_id, visitor)
);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER, rating INTEGER, note TEXT, created_at TEXT,
  decision TEXT, reason TEXT                          -- approved | rejected, quick reason chosen by the admin
);
"""

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

# Columns added after the first version; added to older databases on startup.
MIGRATIONS = {
    "articles": {"category": "TEXT", "people": "TEXT", "published_at": "TEXT"},
    "posts": {"category": "TEXT", "seo": "TEXT", "seo_score": "INTEGER"},
    "feedback": {"decision": "TEXT", "reason": "TEXT"},
}

def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

def now():
    return datetime.now().isoformat(timespec="seconds")

def article_exists(url):
    with conn() as c:
        return c.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone() is not None

def add_article(a: dict):
    with conn() as c:
        c.execute("""INSERT OR IGNORE INTO articles
          (url,title,summary,text,source,is_wedding,region,country,place,topics,quality,crawled_at,status,
           category,people,published_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (a["url"], a["title"], a.get("summary", ""), a.get("text", ""), a.get("source", ""),
           int(bool(a.get("is_wedding", 0))), a.get("region"), a.get("country"), a.get("place"),
           json.dumps(a.get("topics") or []), int(a.get("quality") or 0), now(), a.get("status", "new"),
           a.get("category"), json.dumps(a.get("people") or []), a.get("published_at")))

def candidates(region=None, limit=25):
    q = "SELECT * FROM articles WHERE status='new' AND is_wedding=1 AND length(text) > 0"
    args = []
    if region:
        q += " AND region=?"; args.append(region)
    q += " ORDER BY quality DESC, COALESCE(published_at, crawled_at) DESC LIMIT ?"; args.append(limit)
    with conn() as c:
        rows = [dict(r) for r in c.execute(q, args)]
    for r in rows:
        r["topics"] = json.loads(r["topics"] or "[]")
        r["people"] = json.loads(r.get("people") or "[]")
    return rows

def get_article(aid):
    with conn() as c:
        r = c.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

def set_article_status(aid, status):
    with conn() as c:
        c.execute("UPDATE articles SET status=? WHERE id=?", (status, aid))

def recent_regions(n):
    with conn() as c:
        return [r["region"] for r in c.execute(
            "SELECT region FROM posts WHERE status!='rejected' ORDER BY id DESC LIMIT ?", (n,))]

def recent_titles(n=12):
    with conn() as c:
        return [r["title"] for r in c.execute(
            "SELECT title FROM posts WHERE status!='rejected' ORDER BY id DESC LIMIT ?", (n,))]

def recent_categories(n=6):
    with conn() as c:
        return [r["category"] for r in c.execute(
            "SELECT category FROM posts WHERE status!='rejected' ORDER BY id DESC LIMIT ?", (n,))]

def slug_taken(slug):
    with conn() as c:
        return c.execute("SELECT 1 FROM posts WHERE slug=?", (slug,)).fetchone() is not None

def post_for_slot(date, edition):
    with conn() as c:
        r = c.execute("SELECT * FROM posts WHERE publish_date=? AND edition=? AND status!='rejected'",
                      (date, edition)).fetchone()
        return dict(r) if r else None

def add_post(p: dict) -> int:
    cols = ["article_id","slug","edition","publish_date","region","country","place","title","excerpt",
            "body","tags","images","reel_path","reel_caption","source_url","source_name","overlap",
            "selection_reason","status","created_at","category","seo","seo_score"]
    p = {**p, "body": json.dumps(p["body"]), "tags": json.dumps(p["tags"]),
         "images": json.dumps(p.get("images", [])), "seo": json.dumps(p.get("seo") or {}), "created_at": now()}
    with conn() as c:
        cur = c.execute(f"INSERT INTO posts({','.join(cols)}) VALUES({','.join('?'*len(cols))})",
                        [p.get(k) for k in cols])
        return cur.lastrowid

def update_post(pid, **fields):
    sets = ",".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE posts SET {sets} WHERE id=?", [*fields.values(), pid])

def posts(status=None, limit=500):
    q, args = "SELECT * FROM posts", []
    if status:
        q += " WHERE status=?"; args.append(status)
    q += " ORDER BY publish_date DESC, CASE edition WHEN 'evening' THEN 0 ELSE 1 END LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = [dict(r) for r in c.execute(q, args)]
    for r in rows:
        for k in ("body", "tags", "images"):
            r[k] = json.loads(r[k] or "[]")
        r["seo"] = json.loads(r.get("seo") or "{}")
    return rows

def add_feedback(post_id, rating, note="", decision=None, reason=None):
    with conn() as c:
        c.execute("INSERT INTO feedback(post_id,rating,note,created_at,decision,reason) VALUES(?,?,?,?,?,?)",
                  (post_id, rating, note, now(), decision, reason))

def feedback_examples(n=6):
    """Best and worst rated posts (most recent first), used to teach the selection agent."""
    q = """SELECT p.title, p.region, p.place, p.edition, p.category, f.rating, f.note, f.reason
           FROM feedback f JOIN posts p ON p.id=f.post_id WHERE f.rating {} ORDER BY f.id DESC LIMIT ?"""
    with conn() as c:
        best = [dict(r) for r in c.execute(q.format(">= 4"), (n,))]
        worst = [dict(r) for r in c.execute(q.format("<= 2"), (n,))]
    return best, worst

def category_scores():
    """Average admin rating per category: what the editor tends to approve."""
    with conn() as c:
        return {r["category"]: (round(r["avg"], 1), r["n"]) for r in c.execute(
            """SELECT p.category, AVG(f.rating) avg, COUNT(*) n FROM feedback f JOIN posts p ON p.id=f.post_id
               GROUP BY p.category""")}

def editor_notes(n=8):
    """Recent written notes from the admin (used to teach the writer)."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT f.rating, f.note, f.reason, f.decision, p.title FROM feedback f JOIN posts p ON p.id=f.post_id
               WHERE (f.note IS NOT NULL AND f.note != '') OR f.reason IS NOT NULL ORDER BY f.id DESC LIMIT ?""", (n,))]

def get_post(pid):
    return next((p for p in posts() if p["id"] == pid), None)

# ---------- trends ----------

def save_trends(items, run):
    with conn() as c:
        c.executemany("INSERT INTO trends(run,topic,score,why,queries,sources,created_at) VALUES(?,?,?,?,?,?,?)",
                      [(run, t["topic"], int(t.get("score") or 0), t.get("why", ""), json.dumps(t.get("queries") or []),
                        json.dumps(t.get("sources") or []), now()) for t in items])

def current_trends(limit=15):
    """Trends from the most recent analysis, strongest first."""
    with conn() as c:
        run = c.execute("SELECT run FROM trends ORDER BY id DESC LIMIT 1").fetchone()
        if not run:
            return []
        rows = [dict(r) for r in c.execute("SELECT * FROM trends WHERE run=? ORDER BY score DESC LIMIT ?", (run[0], limit))]
    for r in rows:
        r["queries"], r["sources"] = json.loads(r["queries"] or "[]"), json.loads(r["sources"] or "[]")
    return rows

# ---------- reader engagement ----------

def add_view(post_id, visitor):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO views(post_id,visitor,day) VALUES(?,?,?)", (post_id, visitor, now()[:10]))

def set_like(post_id, visitor, liked):
    with conn() as c:
        if liked:
            c.execute("INSERT OR IGNORE INTO likes(post_id,visitor,created_at) VALUES(?,?,?)", (post_id, visitor, now()))
        else:
            c.execute("DELETE FROM likes WHERE post_id=? AND visitor=?", (post_id, visitor))

def engagement(visitor=None):
    """{post_id: {"views", "likes", "liked"}} for every post with any activity."""
    with conn() as c:
        out = {r[0]: {"views": r[1], "likes": 0, "liked": False}
               for r in c.execute("SELECT post_id, COUNT(*) FROM views GROUP BY post_id")}
        for pid, n in c.execute("SELECT post_id, COUNT(*) FROM likes GROUP BY post_id"):
            out.setdefault(pid, {"views": 0, "likes": 0, "liked": False})["likes"] = n
        if visitor:
            for (pid,) in c.execute("SELECT post_id FROM likes WHERE visitor=?", (visitor,)):
                out.setdefault(pid, {"views": 0, "likes": 0, "liked": False})["liked"] = True
    return out

def reader_favourites(n=6):
    """Published posts readers engaged with most (a like counts as much as 10 views), for the agents and the homepage."""
    stats = engagement()
    ranked = []
    for p in posts("published"):
        s = stats.get(p["id"])
        if s and (s["views"] or s["likes"]):
            ranked.append({**p, "views": s["views"], "likes": s["likes"], "score": s["views"] + 10 * s["likes"]})
    return sorted(ranked, key=lambda p: -p["score"])[:n]

def reader_category_stats():
    """Average views and likes per published post, by category: what readers respond to."""
    stats, agg = engagement(), {}
    for p in posts("published"):
        s = stats.get(p["id"], {"views": 0, "likes": 0})
        a = agg.setdefault(p.get("category"), {"posts": 0, "views": 0, "likes": 0})
        a["posts"] += 1; a["views"] += s["views"]; a["likes"] += s["likes"]
    return {k: {**v, "views_per_post": round(v["views"] / v["posts"], 1), "likes_per_post": round(v["likes"] / v["posts"], 1)}
            for k, v in agg.items()}
