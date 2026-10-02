"""Phera wedding blog agents.

  python main.py go                   do everything now (with REQUIRE_APPROVAL: prepares drafts for the admin)
  python main.py drafts [--count N]   prepare N drafts for the admin page (default DRAFTS_PER_DAY)
  python main.py init                 create the database
  python main.py crawl                crawler agent: collect and tag new stories
  python main.py run morning|evening  run one edition now (select, write, check, images, reel, publish)
  python main.py schedule             run everything automatically (crawl 5:30/17:30, publish 7:00/19:00 IST)
  python main.py review               approve or reject posts waiting for review
  python main.py feedback             rate published posts so the selection agent learns
  python main.py status               show the region mix and pipeline counts
  python main.py voices               save a sample of each female voice to voices/samples/ to listen to
  python main.py remake-reel          re-render reels (all posts, or --slug S) e.g. with --voice NAME
  python main.py build                rebuild the website (pages, sitemap, feed) from published posts
  python main.py serve                open the website at http://localhost:8000 (admin at /admin/)
"""
import argparse
import functools
import http.server
import logging

import db
from config import (SITE_DIR, TIMEZONE, MORNING_TIME, EVENING_TIME, CRAWL_TIMES, INDIA_SHARE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-12s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("main")

def llm():
    from agents.base import LLM
    return LLM()

def cmd_go(a):
    from orchestrator import go
    go(llm(), a.edition, crawl=not a.no_crawl)

def cmd_drafts(a):
    from orchestrator import make_drafts
    make_drafts(llm(), count=a.count, crawl=not a.no_crawl)

def cmd_build(_):
    from agents.publisher import export_site
    export_site()

def cmd_voices(_):
    from agents.voice import samples
    from config import VOICE
    made = samples()
    print("\nListen to these and pick one:\n")
    for name, desc, path in made:
        print(f"  {name:36} {desc}\n      {path}")
    print(f"\nCurrent voice: {VOICE}")
    print("To switch, set VOICE=<name> in .env, then remake existing reels with:  python main.py remake-reel")

def cmd_remake_reel(a):
    import json
    from agents import reels, voice, publisher
    if a.voice:
        voice.use(a.voice)
    posts = [p for p in db.posts("published") if not a.slug or p["slug"] == a.slug]
    if not posts:
        print("No matching published posts."); return
    c = llm()
    for p in posts:
        post = {"title": p["title"], "excerpt": p["excerpt"], "place": p["place"], "body": p["body"],
                "focus_keyword": (p["seo"] or {}).get("focus_keyword", "")}
        path, caption, credit = reels.make_reel(c, post, p["images"], p["slug"],
                                                region=p["region"], edition=p["edition"])
        if path:
            seo = {**(p["seo"] or {}), "music": credit}
            db.update_post(p["id"], reel_path=path, reel_caption=caption, seo=json.dumps(seo))
            print(f"remade: {path}")
    publisher.export_site()

def cmd_crawl(_):
    from agents.crawler import crawl
    crawl(llm())

def cmd_run(a):
    from orchestrator import run_edition
    run_edition(llm(), a.edition, a.date)

def cmd_schedule(_):
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from agents.crawler import crawl
    from orchestrator import run_edition
    c = llm()
    s = BlockingScheduler(timezone=TIMEZONE)
    for t in CRAWL_TIMES:
        h, m = t.strip().split(":")
        s.add_job(crawl, CronTrigger(hour=h, minute=m), args=[c], name=f"crawl {t}", misfire_grace_time=3600)
    from config import REQUIRE_APPROVAL, DRAFTS_TIME, DRAFTS_PER_DAY
    if REQUIRE_APPROVAL:   # the admin approves what goes live
        from orchestrator import make_drafts
        h, m = DRAFTS_TIME.split(":")
        s.add_job(make_drafts, CronTrigger(hour=h, minute=m), args=[c], kwargs={"crawl": False},
                  name=f"{DRAFTS_PER_DAY} drafts for the admin at {DRAFTS_TIME}", misfire_grace_time=3600)
    else:
        for edition, t in (("morning", MORNING_TIME), ("evening", EVENING_TIME)):
            h, m = t.split(":")
            s.add_job(run_edition, CronTrigger(hour=h, minute=m), args=[c, edition],
                      name=f"{edition} edition {t}", misfire_grace_time=3600)
    for j in s.get_jobs():
        log.info("scheduled: %s", j.name)
    log.info("scheduler running (%s). Press Ctrl+C to stop.", TIMEZONE)
    s.start()

def cmd_review(_):
    import review
    pending = db.posts("pending_review")
    if not pending:
        print("Nothing waiting for review.")
    for p in pending:
        print(f'\n[{p["id"]}] {p["region"]} | {p["place"]} | SEO {p.get("seo_score")}/100')
        print(p["title"]); print(p["excerpt"])
        print(f'Why picked: {p["selection_reason"]} | overlap with source {p["overlap"]:.1%}')
        choice = input("Approve (a), reject (r), skip (enter)? ").strip().lower()
        if choice == "a":
            r = input("Rating 1-5 [4]: ").strip()
            review.approve(p["id"], int(r) if r.isdigit() else 4, input("Note (optional): ").strip())
        elif choice == "r":
            print("Reasons:", ", ".join(review.REJECT_REASONS))
            review.reject(p["id"], input("Reason: ").strip(), input("Note (teaches the agents): ").strip())

def cmd_feedback(_):
    rated = set()
    with db.conn() as c:
        rated = {r[0] for r in c.execute("SELECT post_id FROM feedback")}
    todo = [p for p in db.posts("published", 30) if p["id"] not in rated]
    if not todo:
        print("All recent posts are rated.")
    for p in todo:
        print(f'\n[{p["id"]}] {p["publish_date"]} {p["edition"]} | {p["place"]}\n{p["title"]}')
        r = input("Rating 1-5 (enter to skip, q to quit): ").strip()
        if r == "q":
            break
        if r.isdigit() and 1 <= int(r) <= 5:
            db.add_feedback(p["id"], int(r), input("Note (optional): ").strip())

def cmd_status(_):
    pubs = db.posts("published")
    india = sum(p["region"] == "india" for p in pubs)
    total = len(pubs) or 1
    with db.conn() as c:
        counts = dict(c.execute("SELECT status, COUNT(*) FROM articles GROUP BY status").fetchall())
        fb = c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    print(f"Published posts: {len(pubs)}  |  India {india/total:.0%}  Asia {(len(pubs)-india)/total:.0%}"
          f"  (target {INDIA_SHARE:.0%} / {1-INDIA_SHARE:.0%})")
    print(f"Articles: {counts}")
    print(f"Pending review: {len(db.posts('pending_review'))}  |  Ratings given: {fb}")
    from agents.selector import needed_region
    print(f"Next post will come from: {needed_region()}")

def cmd_serve(a):
    import admin
    admin.password()   # make sure an admin password exists (created and shown once)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if not admin.handle_get(self):
                super().do_GET()
        def do_HEAD(self):
            if not admin.handle_get(self):
                super().do_HEAD()
        def do_POST(self):
            if not admin.handle_post(self):
                self.send_error(405)
        def send_error(self, code, message=None, explain=None):
            page = SITE_DIR / "404.html"
            if code == 404 and page.exists():   # show the site's own 404 page, like a real host does
                body = page.read_bytes()
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
            super().send_error(code, message, explain)
        def list_directory(self, path):   # never show raw folder listings
            self.send_error(404)
        def log_message(self, *a):
            pass
    handler = functools.partial(Handler, directory=str(SITE_DIR))
    http.server.ThreadingHTTPServer.allow_reuse_address = True   # restart without "address already in use"
    with http.server.ThreadingHTTPServer(("", a.port), handler) as httpd:
        print(f"Website running at http://localhost:{a.port}  (admin: http://localhost:{a.port}/admin/)  Ctrl+C to stop")
        httpd.serve_forever()

def main():
    db.init()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("go", help="crawl, then make and publish the next edition")
    g.add_argument("--edition", choices=["morning", "evening"], help="default: the next empty slot today")
    g.add_argument("--no-crawl", action="store_true", help="skip crawling and use stories already collected")
    g.set_defaults(f=cmd_go)
    sub.add_parser("build").set_defaults(f=cmd_build)
    dr = sub.add_parser("drafts", help="prepare drafts for the admin to review")
    dr.add_argument("--count", type=int, help="how many (default: DRAFTS_PER_DAY)")
    dr.add_argument("--no-crawl", action="store_true", help="use stories already collected")
    dr.set_defaults(f=cmd_drafts)
    sub.add_parser("voices", help="save voice samples to listen to").set_defaults(f=cmd_voices)
    rr = sub.add_parser("remake-reel", help="re-render reels for published posts")
    rr.add_argument("--slug", help="only this post (default: all published posts)")
    rr.add_argument("--voice", help="voice to use this time, e.g. en_US-lessac-high (default: VOICE in .env)")
    rr.set_defaults(f=cmd_remake_reel)
    sub.add_parser("init").set_defaults(f=lambda a: print("Database ready."))
    sub.add_parser("crawl").set_defaults(f=cmd_crawl)
    r = sub.add_parser("run"); r.add_argument("edition", choices=["morning", "evening"])
    r.add_argument("--date", help="YYYY-MM-DD, defaults to today in India"); r.set_defaults(f=cmd_run)
    sub.add_parser("schedule").set_defaults(f=cmd_schedule)
    sub.add_parser("review").set_defaults(f=cmd_review)
    sub.add_parser("feedback").set_defaults(f=cmd_feedback)
    sub.add_parser("status").set_defaults(f=cmd_status)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8000); s.set_defaults(f=cmd_serve)
    a = p.parse_args()
    a.f(a)

if __name__ == "__main__":
    main()
