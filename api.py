"""Reader API for views and likes (served by `python main.py serve`).

  POST /api/view   {"slug": ...}                count one view per reader per post per day
  POST /api/like   {"slug": ..., "like": bool}  like or unlike a post (one like per reader)
  GET  /api/stats                               views, likes and "liked by me" for every post

Readers are identified only by a random anonymous cookie (no IP addresses or personal data are stored).
Known bots are ignored. These counts are reader feedback: the selector and trend agents use them.
"""
import json
import re
import secrets

import db

COOKIE = "phera_vid"
BOTS = re.compile(r"bot|crawl|spider|slurp|preview|headless|lighthouse|python-requests|curl|wget", re.I)

def _visitor(h):
    from http.cookies import SimpleCookie
    c = SimpleCookie(h.headers.get("Cookie", ""))
    vid = c[COOKIE].value if COOKIE in c else ""
    return (vid, False) if re.fullmatch(r"[A-Za-z0-9_-]{16,40}", vid) else (secrets.token_urlsafe(16), True)

def _reply(h, obj, vid=None, new=False, code=200):
    data = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-store")
    if new and vid:
        h.send_header("Set-Cookie", f"{COOKIE}={vid}; Path=/; Max-Age=31536000; SameSite=Lax; HttpOnly")
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(data)

def _post_id(slug):
    p = next((p for p in db.posts("published") if p["slug"] == slug), None)
    return p["id"] if p else None

def handle(h):
    """Returns True if the request was an /api/ call (and has been answered)."""
    path = h.path.split("?")[0]
    if not path.startswith("/api/"):
        return False
    vid, new = _visitor(h)
    if path == "/api/stats" and h.command in ("GET", "HEAD"):
        slugs = {p["id"]: p["slug"] for p in db.posts("published")}
        stats = {slugs[pid]: s for pid, s in db.engagement(vid).items() if pid in slugs}
        _reply(h, stats, vid, new)
        return True
    if h.command != "POST":
        _reply(h, {"error": "method not allowed"}, code=405)
        return True
    origin = h.headers.get("Origin")
    if origin and origin.split("://", 1)[-1] != h.headers.get("Host", ""):
        _reply(h, {"error": "forbidden"}, code=403)
        return True
    try:
        n = int(h.headers.get("Content-Length") or 0)
        data = json.loads(h.rfile.read(min(n, 2000)) or b"{}")
        pid = _post_id(str(data.get("slug", "")))
        if not pid:
            _reply(h, {"error": "unknown post"}, code=404)
            return True
        bot = BOTS.search(h.headers.get("User-Agent", ""))
        if path == "/api/view":
            if not bot:
                db.add_view(pid, vid)
        elif path == "/api/like":
            if not bot:
                db.set_like(pid, vid, bool(data.get("like", True)))
        else:
            _reply(h, {"error": "not found"}, code=404)
            return True
        s = db.engagement(vid).get(pid, {"views": 0, "likes": 0, "liked": False})
        _reply(h, s, vid, new)
    except (ValueError, json.JSONDecodeError):
        _reply(h, {"error": "bad request"}, code=400)
    return True
