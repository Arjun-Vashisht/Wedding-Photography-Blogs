"""Admin dashboard at /admin/ (served by `python main.py serve`, never part of the public static site).

Log in with ADMIN_PASSWORD from .env. Review the day's drafts: preview each one, approve it with a 1-5 rating
(it publishes immediately) or reject it with a reason. Every decision is stored as feedback that the
selector and writer agents read next time, so they learn what to publish. You can also ask the agents
to prepare new drafts from here.
"""
import hashlib
import hmac
import html
import json
import logging
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import db
import review
import config
from config import BASE_DIR, CATEGORIES, SITE_NAME, DRAFTS_PER_DAY

log = logging.getLogger("admin")
COOKIE = "phera_admin"
SESSION_DAYS = 7
e = lambda s: html.escape(str(s or ""), quote=True)

# ---------- password and sessions ----------

def password():
    """The admin password, read fresh from .env each time (so a new password works as soon as the file is
    saved, no restart). If none is set, create one, save it to .env and show it once."""
    from dotenv import dotenv_values
    pw = (dotenv_values(BASE_DIR / ".env").get("ADMIN_PASSWORD") or config.ADMIN_PASSWORD or "").strip()
    if not pw:
        pw = config.ADMIN_PASSWORD = secrets.token_urlsafe(9)
        env = BASE_DIR / ".env"
        with env.open("a") as f:
            f.write(f"\n# Password for the admin page (http://localhost:8000/admin/)\nADMIN_PASSWORD={pw}\n")
        print(f"\n  Admin password created and saved to .env: {pw}\n")
    return pw

def _sign(msg):
    key = hashlib.sha256(("phera-admin:" + password()).encode()).digest()
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()

def make_session():
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{exp}.{_sign(exp)}"

def authed(handler):
    c = SimpleCookie(handler.headers.get("Cookie", ""))
    if COOKIE not in c:
        return False
    exp, _, sig = c[COOKIE].value.partition(".")
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp))

def same_origin(handler):
    """Block cross-site form posts (CSRF): POSTs must come from this site."""
    origin = handler.headers.get("Origin")
    return not origin or origin.split("://", 1)[-1] == handler.headers.get("Host", "")

# ---------- background job: prepare drafts ----------

JOB = {"running": False, "lines": [], "made": 0, "error": ""}

class _JobLog(logging.Handler):
    def emit(self, record):
        if JOB["running"]:
            JOB["lines"] = (JOB["lines"] + [f"{time.strftime('%H:%M:%S')}  {record.getMessage()}"])[-60:]

def start_drafts(count):
    if JOB["running"]:
        return False
    JOB.update(running=True, lines=[], made=0, error="")
    handler = _JobLog(logging.INFO)
    logging.getLogger().addHandler(handler)

    def run():
        try:
            from agents.base import LLM
            from orchestrator import make_drafts
            JOB["made"] = len(make_drafts(LLM(), count=count, crawl=True))
        except Exception as ex:   # shown on the dashboard
            JOB["error"] = str(ex)[:300]
            log.exception("draft job failed")
        finally:
            JOB["running"] = False
            logging.getLogger().removeHandler(handler)
    threading.Thread(target=run, daemon=True).start()
    return True

# ---------- data for the dashboard ----------

def draft_json(p):
    from agents.publisher import thumb
    seo = p.get("seo") or {}
    art = db.get_article(p["article_id"]) or {}
    return {"id": p["id"], "title": p["title"], "excerpt": p["excerpt"], "slug": p["slug"],
            "category": CATEGORIES.get(p.get("category"), "Weddings"), "region": p["region"], "place": p["place"],
            "thumb": "/" + thumb(p["images"][0]["file"]) if p["images"] else "",
            "photos": len(p["images"]), "reel": "/" + p["reel_path"] if p.get("reel_path") else "",
            "seo": p.get("seo_score"), "overlap": round((p.get("overlap") or 0) * 100, 1),
            "words": seo.get("words"), "keyword": seo.get("focus_keyword", ""),
            "source": p["source_url"], "source_name": p["source_name"], "why": p.get("selection_reason") or "",
            "source_date": art.get("published_at"), "created": p.get("created_at", "")[:16].replace("T", " ")}

def state():
    pending = db.posts("pending_review")
    with db.conn() as c:
        recent = [dict(r) for r in c.execute(
            """SELECT p.id, p.title, p.slug, p.status, f.decision, f.rating, f.reason, f.note, f.created_at
               FROM feedback f JOIN posts p ON p.id=f.post_id ORDER BY f.id DESC LIMIT 25""")]
        counts = dict(c.execute("SELECT status, COUNT(*) FROM articles WHERE length(text)>0 GROUP BY status").fetchall())
    return {"pending": [draft_json(p) for p in pending], "recent": recent, "learning": review.learning_summary(),
            "job": JOB, "reasons": review.REJECT_REASONS, "stories_waiting": counts.get("new", 0),
            "published": len(db.posts("published")), "drafts_per_day": DRAFTS_PER_DAY}

# ---------- request handling (called from main.py's server) ----------

def _send(h, code, body, ctype="text/html; charset=utf-8", headers=()):
    data = body.encode() if isinstance(body, str) else body
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Robots-Tag", "noindex, nofollow")
    h.send_header("X-Frame-Options", "SAMEORIGIN")
    for k, v in headers:
        h.send_header(k, v)
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(data)

def _json(h, obj, code=200):
    _send(h, code, json.dumps(obj, ensure_ascii=False, default=str), "application/json")

def _body(h):
    n = int(h.headers.get("Content-Length") or 0)
    return h.rfile.read(min(n, 100_000)).decode("utf-8", "replace") if n else ""

def handle_get(h):
    path = h.path.split("?")[0]
    if not path.startswith("/admin"):
        return False
    if path == "/admin":
        _send(h, 301, "", headers=[("Location", "/admin/")]); return True
    if not authed(h):
        if path.startswith("/admin/api/"):
            _json(h, {"error": "login required"}, 401)
        else:
            _send(h, 200, LOGIN_PAGE.replace("{{error}}", "Wrong password, try again." if "e=1" in h.path else ""))
        return True
    if path == "/admin/":
        _send(h, 200, DASHBOARD.replace("{{site}}", e(SITE_NAME)))
    elif path == "/admin/api/state":
        _json(h, state())
    elif path.startswith("/admin/preview/"):
        from agents.publisher import post_page
        pid = path.rsplit("/", 1)[-1]
        p = db.get_post(int(pid)) if pid.isdigit() else None
        if not p:
            _send(h, 404, "Draft not found")
        else:
            page = post_page(p, [p] + db.posts("published"))
            banner = ('<div style="position:sticky;top:0;z-index:99;background:#24161B;color:#fff;text-align:center;'
                      'padding:.6rem;font:600 .9rem DM Sans,system-ui">Preview: this draft is not published</div>')
            _send(h, 200, page.replace('<meta name="robots" content="index', '<meta name="robots" content="noindex')
                  .replace('<body data-page="post">', '<body data-page="post">' + banner, 1))
    else:
        _send(h, 404, "Not found")
    return True

def handle_post(h):
    path = h.path.split("?")[0]
    if not path.startswith("/admin"):
        return False
    if not same_origin(h):
        _send(h, 403, "Forbidden"); return True
    if path == "/admin/login":
        pw = parse_qs(_body(h)).get("password", [""])[0]
        if hmac.compare_digest(pw.encode(), password().encode()):
            cookie = f"{COOKIE}={make_session()}; Path=/admin; HttpOnly; SameSite=Strict; Max-Age={SESSION_DAYS * 86400}"
            _send(h, 303, "", headers=[("Location", "/admin/"), ("Set-Cookie", cookie)])
        else:
            time.sleep(1.2)   # slow down password guessing
            _send(h, 303, "", headers=[("Location", "/admin/?e=1")])
        return True
    if path == "/admin/logout":
        _send(h, 303, "", headers=[("Location", "/admin/"), ("Set-Cookie", f"{COOKIE}=; Path=/admin; Max-Age=0")])
        return True
    if not authed(h):
        _json(h, {"error": "login required"}, 401); return True
    try:
        data = json.loads(_body(h) or "{}")
        if path == "/admin/api/decide":
            if data.get("action") == "approve":
                review.approve(int(data["id"]), int(data.get("rating") or 4), data.get("note", ""))
            elif data.get("action") == "reject":
                review.reject(int(data["id"]), data.get("reason", ""), data.get("note", ""))
            else:
                raise ValueError("unknown action")
            _json(h, {"ok": True})
        elif path == "/admin/api/generate":
            ok = start_drafts(max(1, min(5, int(data.get("count") or DRAFTS_PER_DAY))))
            _json(h, {"ok": ok, "error": "" if ok else "the agents are already preparing drafts"})
        else:
            _json(h, {"error": "not found"}, 404)
    except Exception as ex:
        _json(h, {"error": str(ex)}, 400)
    return True

# ---------- pages ----------

_HEAD = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>Admin · Phera</title><link rel="icon" href="/assets/logo.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&family=Fraunces:opsz,wght@9..144,500;9..144,600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/site.css">
<script>try{const t=localStorage.getItem('phera-theme');if(t)document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
.adm{max-width:1180px;margin:0 auto;padding:0 clamp(14px,3vw,32px) 4rem}
.adm-top{display:flex;align-items:center;gap:1rem;height:68px;border-bottom:1px solid var(--line);margin-bottom:1.75rem}
.adm-top .logo{margin-right:auto}.adm-top .logo small{font-size:.8rem;color:var(--muted);margin-left:.5rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase}
.adm-top a,.adm-top button.link{font-size:.92rem;color:var(--ink-2);background:none;border:0;padding:0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:1.5rem}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:1.1rem 1.25rem}
.stat b{display:block;font:600 2rem/1.1 var(--display)}
.stat span{color:var(--muted);font-size:.88rem}
.learn{background:var(--blush);border-radius:var(--r);padding:1.1rem 1.25rem;margin-bottom:1.75rem;font-size:.95rem;color:var(--ink-2)}
.learn b{color:var(--ink)}
.gen{display:flex;flex-wrap:wrap;align-items:center;gap:.75rem;margin-bottom:2rem}
.gen select{height:46px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:0 1rem;font:inherit}
.joblog{width:100%;background:var(--ink);color:#EADFD7;border-radius:14px;padding:.9rem 1rem;font:500 .8rem/1.55 ui-monospace,monospace;max-height:220px;overflow:auto;white-space:pre-wrap;display:none}
.joblog.on{display:block}
h2.h{font:600 1.6rem var(--display);margin:0 0 1rem;display:flex;align-items:center;gap:.6rem}
h2.h .count{font:600 .85rem var(--body);background:var(--accent);color:var(--accent-ink);border-radius:999px;padding:.15rem .6rem}
.draft{display:grid;grid-template-columns:300px 1fr;gap:1.4rem;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);padding:1rem;margin-bottom:1.25rem;box-shadow:var(--shadow);transition:opacity .35s,transform .35s}
.draft.gone{opacity:0;transform:translateX(40px)}
@media (max-width:820px){.draft{grid-template-columns:1fr}}
.draft .media{position:relative;border-radius:14px;overflow:hidden;background:var(--sunk);aspect-ratio:4/3}
.draft .media img,.draft .media video{width:100%;height:100%;object-fit:cover}
.draft .media .tog{position:absolute;bottom:10px;left:10px;font-size:.78rem;background:rgba(0,0,0,.6);color:#fff;border:0;border-radius:999px;padding:.35rem .75rem}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.5rem}
.chip{font-size:.75rem;font-weight:600;padding:.3rem .65rem;border-radius:999px;background:var(--sunk);color:var(--ink-2)}
.chip.ok{background:color-mix(in srgb,#2E7D4F 16%,transparent);color:#2E7D4F}
.draft h3{font-size:1.4rem;line-height:1.2;margin:.2rem 0 .4rem}
.draft p.ex{margin:0 0 .6rem;color:var(--ink-2)}
.why{font-size:.88rem;color:var(--muted);margin:0 0 .4rem}.why a{text-decoration:underline}
.acts{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem;margin-top:.9rem}
.stars{display:inline-flex;gap:2px}.stars button{background:none;border:0;font-size:1.5rem;line-height:1;color:var(--line);padding:0 1px}
.stars button.on{color:var(--gold)}
.note{width:100%;min-height:62px;margin-top:.7rem;border:1px solid var(--line);border-radius:12px;padding:.6rem .8rem;font:inherit;font-size:.92rem;background:var(--bg);color:var(--ink);resize:vertical}
.btn.ok{background:#2E7D4F;color:#fff}.btn.no{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.btn.sm{height:42px;padding:0 1.1rem;font-size:.92rem}
.reasons{display:none;flex-wrap:wrap;gap:.4rem;margin-top:.8rem}.reasons.on{display:flex}
.reasons button{border:1px solid var(--line);background:var(--bg);border-radius:999px;padding:.4rem .8rem;font-size:.85rem}
.reasons button.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.empty-q{text-align:center;padding:2.5rem 1rem;border:1px dashed var(--line);border-radius:var(--r-lg);color:var(--muted);margin-bottom:2rem}
table{width:100%;border-collapse:collapse;font-size:.9rem;background:var(--surface);border-radius:var(--r);overflow:hidden}
th,td{text-align:left;padding:.65rem .8rem;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.75rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.tag-ok{color:#2E7D4F;font-weight:600}.tag-no{color:var(--accent);font-weight:600}
.toast{position:fixed;left:50%;bottom:24px;translate:-50% 0;background:var(--ink);color:var(--bg);padding:.75rem 1.2rem;border-radius:999px;font-size:.92rem;opacity:0;transition:opacity .25s;z-index:90;pointer-events:none}
.toast.on{opacity:1}
.pv{position:fixed;inset:0;z-index:80;background:rgba(10,6,8,.7);display:none;padding:2.5vh 2.5vw}
.pv.on{display:block}.pv iframe{width:100%;height:100%;border:0;border-radius:18px;background:var(--bg)}
.pv .icon-btn{position:fixed;top:18px;right:18px;z-index:2}
.login{max-width:380px;margin:14vh auto;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);padding:2rem;box-shadow:var(--shadow-lg);text-align:center}
.login input{width:100%;height:50px;border:1px solid var(--line);border-radius:14px;padding:0 1rem;font:inherit;margin:1.2rem 0 .8rem;background:var(--bg);color:var(--ink)}
.login .btn{width:100%;justify-content:center}.login .err{color:var(--accent);font-size:.9rem;min-height:1.2em;margin:.6rem 0 0}
</style></head><body>"""

LOGIN_PAGE = _HEAD + """<form class="login" method="post" action="/admin/login">
<span class="logo" style="justify-content:center;display:flex"><b>Phera</b></span>
<p style="color:var(--muted);margin:.5rem 0 0">Admin sign in</p>
<label class="sr" for="pw">Password</label><input id="pw" name="password" type="password" placeholder="Password" autocomplete="current-password" autofocus required>
<button class="btn" type="submit">Sign in</button><p class="err">{{error}}</p>
</form></body></html>"""

DASHBOARD = _HEAD + """<div class="adm">
<header class="adm-top"><a class="logo" href="/admin/"><b>{{site}}</b><small>Admin</small></a>
  <a href="/" target="_blank" rel="noopener">View site ↗</a>
  <form method="post" action="/admin/logout" style="margin:0"><button class="link">Sign out</button></form></header>

<div class="stats">
  <div class="stat"><b id="sPending">–</b><span>Drafts waiting for you</span></div>
  <div class="stat"><b id="sPublished">–</b><span>Published stories</span></div>
  <div class="stat"><b id="sDecisions">–</b><span>Approved / rejected</span></div>
  <div class="stat"><b id="sStories">–</b><span>Collected stories ready to write</span></div>
</div>
<div class="learn" id="learn"></div>

<div class="gen">
  <button class="btn" id="genBtn">Prepare new drafts</button>
  <select id="genCount" aria-label="How many drafts"><option>1</option><option>2</option><option selected>3</option></select>
  <span id="genNote" style="color:var(--muted);font-size:.9rem">The agents collect fresh stories, then write, check and illustrate each draft. This takes a few minutes.</span>
  <pre class="joblog" id="jobLog"></pre>
</div>

<h2 class="h">Waiting for review <span class="count" id="pCount">0</span></h2>
<div id="drafts"></div>

<h2 class="h" style="margin-top:2.5rem">Your recent decisions</h2>
<div style="overflow-x:auto"><table><thead><tr><th>Story</th><th>Decision</th><th>Rating</th><th>Reason / note</th><th>When</th></tr></thead>
<tbody id="recent"></tbody></table></div>
</div>
<div class="pv" id="pv"><iframe id="pvFrame" title="Draft preview"></iframe><button class="icon-btn" id="pvClose" aria-label="Close preview">✕</button></div>
<div class="toast" id="toast"></div>
<script>
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let S = null, polling = null;
const toast = t => { const el = $("#toast"); el.textContent = t; el.classList.add("on"); setTimeout(() => el.classList.remove("on"), 2600); };
async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  if (r.status === 401) { location.reload(); throw 0; }
  const j = await r.json(); if (j.error) throw new Error(j.error); return j;
}
function draftCard(d) {
  return `<article class="draft" id="d${d.id}">
    <div class="media">${d.thumb ? `<img src="${esc(d.thumb)}" alt="">` : ""}
      ${d.reel ? `<button class="tog" data-reel="${esc(d.reel)}">▶ Play reel</button>` : ""}</div>
    <div>
      <div class="chips"><span class="chip">${esc(d.category)}</span><span class="chip">${d.region === "india" ? "India" : "Asia"} · ${esc(d.place)}</span>
        <span class="chip ok">SEO ${d.seo ?? "–"}/100</span><span class="chip ok">Original (${d.overlap}% overlap)</span>
        <span class="chip ok">Fact-checked</span><span class="chip">${d.words || "?"} words · ${d.photos} photos${d.reel ? " · reel" : ""}</span></div>
      <h3>${esc(d.title)}</h3><p class="ex">${esc(d.excerpt)}</p>
      <p class="why"><b>Why the agent picked it:</b> ${esc(d.why)}</p>
      <p class="why">Source: <a href="${esc(d.source)}" target="_blank" rel="noopener">${esc(d.source_name)}</a>${d.source_date ? " · " + esc(d.source_date) : ""} · Keyword: “${esc(d.keyword)}”</p>
      <div class="acts">
        <button class="btn ghost sm" data-preview="${d.id}">Preview full post</button>
        <span class="stars" role="radiogroup" aria-label="Rating">${[1,2,3,4,5].map(n => `<button data-star="${n}" class="${n <= 4 ? "on" : ""}" aria-label="${n} stars">★</button>`).join("")}</span>
        <button class="btn ok sm" data-approve="${d.id}">Approve & publish</button>
        <button class="btn no sm" data-reject="${d.id}">Reject</button>
      </div>
      <div class="reasons" id="r${d.id}">${Object.entries(S.reasons).map(([k, v]) => `<button data-reason="${k}">${esc(v)}</button>`).join("")}
        <button class="btn no sm" data-confirm="${d.id}" style="margin-left:auto">Confirm reject</button></div>
      <textarea class="note" id="n${d.id}" placeholder="Note for the agents (optional): what was good, what to do differently…"></textarea>
    </div></article>`;
}
function render() {
  $("#sPending").textContent = S.pending.length; $("#pCount").textContent = S.pending.length;
  $("#sPublished").textContent = S.published;
  $("#sDecisions").textContent = `${S.learning.approved} / ${S.learning.rejected}`;
  $("#sStories").textContent = S.stories_waiting;
  const L = S.learning;
  $("#learn").innerHTML = (L.approved + L.rejected) === 0
    ? "<b>The agents learn from you.</b> Every approval, star rating, rejection reason and note is used when they pick and write the next stories."
    : `<b>What the agents have learned so far:</b> you approve ${L.liked.length ? esc(L.liked.join(", ")) : "a mix of topics"}` +
      (L.disliked.length ? `; you tend to reject ${esc(L.disliked.join(", "))}` : "") +
      (L.reasons.length ? `. Most common rejection reasons: ${esc(L.reasons.join(", "))}.` : ".");
  $("#drafts").innerHTML = S.pending.length ? S.pending.map(draftCard).join("") :
    `<div class="empty-q">No drafts waiting. New drafts are prepared every morning, or press “Prepare new drafts”.</div>`;
  $("#recent").innerHTML = S.recent.map(r => `<tr><td>${r.status === "published" ? `<a href="/blog/${esc(r.slug)}/" target="_blank">${esc(r.title)}</a>` : esc(r.title)}</td>
    <td class="${r.decision === "rejected" ? "tag-no" : "tag-ok"}">${esc(r.decision || "rated")}</td><td>${"★".repeat(r.rating || 0)}</td>
    <td>${esc([r.reason, r.note].filter(Boolean).join(" — "))}</td><td>${esc((r.created_at || "").replace("T", " ").slice(0, 16))}</td></tr>`).join("")
    || `<tr><td colspan="5" style="color:var(--muted)">No decisions yet.</td></tr>`;
  renderJob();
}
function renderJob() {
  const j = S.job, log = $("#jobLog"), btn = $("#genBtn");
  btn.disabled = j.running; btn.textContent = j.running ? "Preparing drafts…" : "Prepare new drafts";
  log.classList.toggle("on", j.running || !!j.error || j.lines.length > 0);
  log.textContent = j.lines.join("\\n") + (j.error ? `\\n\\nProblem: ${j.error}` : "") + (!j.running && j.lines.length ? `\\n\\nDone: ${j.made} new draft(s).` : "");
  log.scrollTop = log.scrollHeight;
  if (j.running && !polling) polling = setInterval(load, 3000);
  if (!j.running && polling) { clearInterval(polling); polling = null; }
}
async function load() { try { S = await api("/admin/api/state"); if (!document.querySelector(".draft textarea:focus, .reasons.on")) render(); else renderJob(); } catch {} }
document.addEventListener("click", async ev => {
  const t = ev.target.closest("button"); if (!t) return;
  const card = t.closest(".draft");
  if (t.dataset.star) { [...card.querySelectorAll("[data-star]")].forEach(b => b.classList.toggle("on", +b.dataset.star <= +t.dataset.star)); card.dataset.rating = t.dataset.star; }
  else if (t.dataset.preview) { $("#pvFrame").src = "/admin/preview/" + t.dataset.preview; $("#pv").classList.add("on"); }
  else if (t.dataset.reel) { const m = card.querySelector(".media"); m.innerHTML = `<video src="${esc(t.dataset.reel)}" controls autoplay playsinline></video>`; }
  else if (t.dataset.reject) { $("#r" + t.dataset.reject).classList.toggle("on"); }
  else if (t.dataset.reason) { [...t.parentNode.querySelectorAll("[data-reason]")].forEach(b => b.classList.toggle("on", b === t)); }
  else if (t.dataset.approve || t.dataset.confirm) {
    const id = +(t.dataset.approve || t.dataset.confirm), approve = !!t.dataset.approve;
    const reason = card.querySelector("[data-reason].on")?.dataset.reason || "";
    t.disabled = true; t.textContent = approve ? "Publishing…" : "Rejecting…";
    try {
      await api("/admin/api/decide", {id, action: approve ? "approve" : "reject", rating: +(card.dataset.rating || 4), reason, note: $("#n" + id).value});
      card.classList.add("gone"); toast(approve ? "Published. The agents will learn from your rating." : "Rejected. The agents will avoid stories like this.");
      setTimeout(load, 400);
    } catch (err) { toast(err.message || "Something went wrong"); t.disabled = false; t.textContent = approve ? "Approve & publish" : "Confirm reject"; }
  }
});
$("#genBtn").addEventListener("click", async () => {
  try { const r = await api("/admin/api/generate", {count: +$("#genCount").value}); if (!r.ok) toast(r.error); await load(); } catch (err) { toast(err.message); }
});
const closePv = () => { $("#pv").classList.remove("on"); $("#pvFrame").src = "about:blank"; };
$("#pvClose").addEventListener("click", closePv);
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closePv(); });
load();
</script></body></html>"""
