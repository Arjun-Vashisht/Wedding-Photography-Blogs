/* Phera — site interactions. Copied to site/assets/app.js by the publisher.
   Everything works without JavaScript; this layer makes moving between stories effortless. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let posts = null;
  const loadPosts = async () => posts ??= await fetch("/data/posts.json").then(r => r.ok ? r.json() : []).catch(() => []);

  /* theme */
  $("#themeBtn")?.addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    store.set("phera-theme", root.dataset.theme);
  });

  /* header shadow + reading progress */
  const head = $(".site-head"), bar = $(".progress");
  const onScroll = () => {
    head?.classList.toggle("scrolled", scrollY > 8);
    if (bar) {
      const art = currentArticle();
      if (art) {
        const r = art.getBoundingClientRect();
        const p = Math.min(1, Math.max(0, -r.top / Math.max(1, r.height - innerHeight)));
        bar.style.transform = `scaleX(${p})`;
        maybeShowUpNext(art, p);
      }
    }
  };
  addEventListener("scroll", onScroll, { passive: true });

  /* reveal on scroll */
  const io = "IntersectionObserver" in window ? new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { rootMargin: "0px 0px -8% 0px" }) : null;
  const reveal = root => $$(".reveal", root).forEach(el => io ? io.observe(el) : el.classList.add("in"));
  reveal(document);

  /* prefetch pages on hover/touch, and the next story when idle */
  const prefetched = new Set();
  const prefetch = href => {
    if (!href || prefetched.has(href) || !href.startsWith("/")) return;
    prefetched.add(href);
    const l = document.createElement("link"); l.rel = "prefetch"; l.href = href; document.head.appendChild(l);
  };
  document.addEventListener("pointerover", e => prefetch(e.target.closest("a")?.getAttribute("href")), { passive: true });
  document.addEventListener("touchstart", e => prefetch(e.target.closest("a")?.getAttribute("href")), { passive: true });

  /* horizontal rails with arrow buttons */
  $$(".rail-wrap").forEach(w => {
    const rail = $(".rail", w), prev = $(".rail-btn.prev", w), next = $(".rail-btn.next", w);
    if (!rail) return;
    const upd = () => { if (prev) prev.disabled = rail.scrollLeft < 8;
      if (next) next.disabled = rail.scrollLeft + rail.clientWidth > rail.scrollWidth - 8; };
    prev?.addEventListener("click", () => rail.scrollBy({ left: -rail.clientWidth * .85, behavior: "smooth" }));
    next?.addEventListener("click", () => rail.scrollBy({ left: rail.clientWidth * .85, behavior: "smooth" }));
    rail.addEventListener("scroll", upd, { passive: true }); upd();
  });

  /* hero slideshow */
  const slides = $$(".slide");
  if (slides.length > 1) {
    const dots = $$(".hero-dots button");
    let i = 0, timer;
    const show = n => { i = (n + slides.length) % slides.length;
      slides.forEach((s, k) => s.classList.toggle("on", k === i));
      dots.forEach((d, k) => d.setAttribute("aria-current", k === i)); };
    const auto = () => { clearInterval(timer); timer = setInterval(() => show(i + 1), 6500); };
    dots.forEach((d, k) => d.addEventListener("click", () => { show(k); auto(); }));
    $(".slides")?.addEventListener("pointerenter", () => clearInterval(timer));
    $(".slides")?.addEventListener("pointerleave", auto);
    auto();
  }

  /* share */
  document.addEventListener("click", async e => {
    const b = e.target.closest("[data-share]"); if (!b) return;
    const art = b.closest("article") || document;
    const url = location.origin + (art.dataset?.url || location.pathname), title = art.dataset?.title || document.title;
    if (navigator.share) { try { await navigator.share({ title, url }); } catch {} return; }
    try { await navigator.clipboard.writeText(url); b.setAttribute("aria-label", "Link copied"); b.classList.add("done");
      setTimeout(() => b.classList.remove("done"), 1500); } catch {}
  });

  /* ---------- article: up next, keyboard/swipe, endless reading ---------- */
  const articles = () => $$("article.story");
  function currentArticle() {
    const list = articles(); if (!list.length) return null;
    // the last story whose top has scrolled past 40% of the screen (so the footer area counts as the last story)
    const above = list.filter(a => a.getBoundingClientRect().top < innerHeight * .4);
    return above[above.length - 1] || list[0];
  }
  let activeUrl = location.pathname;
  const upnext = $("#upnext");
  let upDismissed = false;
  function maybeShowUpNext(art, p) {
    // keep the address bar and title in step with the story being read
    if (art.dataset.url && art.dataset.url !== activeUrl) {
      activeUrl = art.dataset.url;
      history.replaceState(null, "", activeUrl);
      document.title = art.dataset.doctitle || art.dataset.title || document.title;
    }
    if (!upnext || upDismissed || !art.dataset.next) return upnext?.classList.remove("show");
    const show = p > .45 && p < .97;
    if (show && upnext.dataset.for !== art.dataset.url) {
      upnext.dataset.for = art.dataset.url;
      $("a", upnext).href = art.dataset.next;
      $("b", upnext).textContent = art.dataset.nextTitle || "";
      const img = $("img", upnext);
      if (img) { img.src = art.dataset.nextImg || ""; img.hidden = !art.dataset.nextImg; }
      prefetch(art.dataset.next);
    }
    upnext.classList.toggle("show", show);
  }
  $("#upnext .x")?.addEventListener("click", () => { upDismissed = true; upnext.classList.remove("show"); });

  const go = dir => {
    const art = currentArticle(); if (!art) return;
    const href = dir > 0 ? art.dataset.next : art.dataset.prev;
    if (href) location.href = href;
  };
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") return closeOverlays();   // works even while typing in search
    if (e.target.closest("input,textarea,[contenteditable]")) return;
    if (e.key === "/" || (e.key === "k" && (e.metaKey || e.ctrlKey))) { e.preventDefault(); openSearch(); }
    else if (!$(".overlay.open") && articles().length && !e.metaKey && !e.ctrlKey && !e.altKey) {
      if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1);
    }
  });
  let sx = 0, sy = 0, st = 0;
  document.addEventListener("touchstart", e => { const t = e.touches[0]; sx = t.clientX; sy = t.clientY; st = Date.now(); }, { passive: true });
  document.addEventListener("touchend", e => {
    if (!articles().length || $(".overlay.open") || e.target.closest(".rail,video")) return;
    const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
    if (Math.abs(dx) > 90 && Math.abs(dy) < 40 && Date.now() - st < 600) go(dx < 0 ? 1 : -1);
  }, { passive: true });

  // Endless reading: when the reader reaches the end of a story, the next one loads right below it.
  const loaded = new Set(articles().map(a => a.dataset.url));
  let loading = false;
  const sentinel = $("#endless");
  if (sentinel && io) {
    const endObs = new IntersectionObserver(async es => {
      if (!es[0].isIntersecting || loading) return;
      const list = articles(), last = list[list.length - 1];
      const href = last?.dataset.next;
      if (!href || loaded.has(href) || list.length >= 12) { endObs.disconnect(); sentinel.hidden = true; return; }
      loading = true; sentinel.classList.add("busy");
      try {
        const html = await fetch(href).then(r => r.text());
        const doc = new DOMParser().parseFromString(html, "text/html");
        const art = $("article.story", doc);
        if (!art) throw 0;
        const div = document.createElement("div");
        div.className = "story-divider"; div.textContent = "Next story";
        $$(".next-story", last).forEach(n => n.remove());
        sentinel.before(div, art);
        loaded.add(art.dataset.url);
        reveal(art);
        if (!art.dataset.next || loaded.has(art.dataset.next)) { endObs.disconnect(); sentinel.hidden = true; }
      } catch { endObs.disconnect(); sentinel.hidden = true; }
      loading = false; sentinel.classList.remove("busy");
    }, { rootMargin: "600px 0px" });
    endObs.observe(sentinel);
  }

  /* ---------- search ---------- */
  const searchEl = $("#search");
  const input = $("#q"), results = $("#results");
  async function openSearch() {
    if (!searchEl) return;
    closeOverlays(); searchEl.classList.add("open"); document.body.style.overflow = "hidden";
    input.value = ""; await loadPosts(); renderResults(""); setTimeout(() => input.focus(), 30);
  }
  const hi = (text, q) => !q ? esc(text) : esc(text).replace(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"), "<mark>$1</mark>");
  let sel = 0;
  function renderResults(q) {
    q = q.trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    const list = (posts || []).filter(p => {
      const hay = [p.title, p.excerpt, p.place, p.category_name, ...(p.tags || [])].join(" ").toLowerCase();
      return words.every(w => hay.includes(w));
    }).slice(0, 12);
    sel = 0;
    $("#searchHint").textContent = q ? `${list.length} ${list.length === 1 ? "story" : "stories"} found` : "Latest stories";
    results.innerHTML = list.map((p, i) => `<a class="result${i === 0 ? " sel" : ""}" href="${esc(p.url)}">
      ${p.thumb ? `<img src="${esc(p.thumb)}" alt="" loading="lazy">` : `<span class="ph"></span>`}
      <span><b>${hi(p.title, q)}</b><small>${esc(p.category_name)} · ${esc(p.place || "")}</small></span></a>`).join("")
      || `<p class="search-hint">No stories match “${esc(q)}”. Try a place, a ritual or a name.</p>`;
  }
  input?.addEventListener("input", () => renderResults(input.value));
  input?.addEventListener("keydown", e => {
    const items = $$(".result", results);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(1, items.length);
      items.forEach((it, i) => it.classList.toggle("sel", i === sel)); items[sel]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && items[sel]) location.href = items[sel].getAttribute("href");
  });
  $$("[data-search]").forEach(b => b.addEventListener("click", openSearch));

  /* ---------- reel player: full screen, swipe up for the next reel ---------- */
  const player = $("#player"), feed = $("#playerFeed");
  let muted = store.get("phera-muted") === "1";
  const vidObs = "IntersectionObserver" in window ? new IntersectionObserver(es => es.forEach(e => {
    const v = e.target;
    if (e.isIntersecting && e.intersectionRatio > .6) { v.muted = muted; v.play().catch(() => { v.muted = true; v.play().catch(() => {}); }); }
    else v.pause();
  }), { threshold: [0, .6, 1] }) : null;
  async function openPlayer(startUrl) {
    if (!player) return;
    const list = (await loadPosts()).filter(p => p.reel);
    if (!list.length) return;
    const start = Math.max(0, list.findIndex(p => p.reel === startUrl));
    const ordered = [...list.slice(start), ...list.slice(0, start)];
    feed.innerHTML = ordered.map(p => `<div class="player-item">
        <video src="${esc(p.reel)}" poster="${esc(p.reel_poster || "")}" playsinline loop preload="metadata"></video>
        <div class="player-info"><b>${esc(p.title)}</b>
        <a class="btn light" href="${esc(p.url)}">Read the full story
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a></div></div>`).join("");
    closeOverlays(); player.classList.add("open"); document.body.style.overflow = "hidden";
    feed.scrollTop = 0;
    $("#swipeHint").hidden = ordered.length < 2;
    $$("video", feed).forEach(v => { vidObs?.observe(v); v.addEventListener("click", () => v.paused ? v.play() : v.pause()); });
    updMute();
  }
  const updMute = () => { $("#muteBtn")?.setAttribute("aria-label", muted ? "Turn sound on" : "Mute");
    $("#muteBtn") && ($("#muteBtn").innerHTML = muted ? ICON.muted : ICON.sound);
    $$("video", feed || document.createElement("div")).forEach(v => v.muted = muted); };
  $("#muteBtn")?.addEventListener("click", () => { muted = !muted; store.set("phera-muted", muted ? "1" : "0"); updMute(); });
  document.addEventListener("click", e => {
    const r = e.target.closest("[data-reel]");
    if (r && player) { e.preventDefault(); openPlayer(r.dataset.reel); }
  });

  /* ---------- photo viewer ---------- */
  const viewer = $("#viewer"), stage = $("#viewerStage"), vImg = $("#viewerImg");
  let gallery = [], gi = 0, z = { s: 1, x: 0, y: 0 }, base = { w: 0, h: 0, l: 0, t: 0 };
  const MAX = 5;
  const apply = (anim) => { stage.classList.toggle("anim", !!anim); stage.classList.toggle("zoomed", z.s > 1.01);
    vImg.style.transform = `translate(${z.x}px,${z.y}px) scale(${z.s})`; };
  const measure = () => { vImg.style.transform = "none"; const r = vImg.getBoundingClientRect();
    base = { w: r.width, h: r.height, l: r.left, t: r.top }; z = { s: 1, x: 0, y: 0 }; apply(); };
  const clamp = () => {   // keep the zoomed photo covering the screen, no empty gaps when panning
    const w = base.w * z.s, h = base.h * z.s;
    z.x = w <= innerWidth ? (base.w - w) / 2 : Math.min(-base.l, Math.max(innerWidth - base.l - w, z.x));
    z.y = h <= innerHeight ? (base.h - h) / 2 : Math.min(-base.t, Math.max(innerHeight - base.t - h, z.y));
  };
  const zoomAt = (ns, cx, cy, anim) => {   // zoom toward the point under the finger / cursor
    ns = Math.min(MAX, Math.max(1, ns));
    const px = (cx - base.l - z.x) / z.s, py = (cy - base.t - z.y) / z.s;
    z.x = cx - base.l - px * ns; z.y = cy - base.t - py * ns; z.s = ns;
    if (ns === 1) { z.x = 0; z.y = 0; } else clamp();
    apply(anim);
  };
  function showPhoto(i) {
    gi = (i + gallery.length) % gallery.length;
    const it = gallery[gi];
    vImg.onload = measure; vImg.src = it.src; vImg.alt = it.alt;
    if (vImg.complete) measure();
    $("#viewerCap").innerHTML = it.cap; $("#viewerCap").hidden = !it.cap;
    $("#viewerCount").textContent = gallery.length > 1 ? `${gi + 1} / ${gallery.length}` : "";
    $("#viewerPrev").hidden = $("#viewerNext").hidden = gallery.length < 2;
    const nxt = gallery[(gi + 1) % gallery.length]; if (nxt) new Image().src = nxt.src;   // preload the next photo
  }
  function openViewer(img) {
    const art = img.closest("article") || document;
    const imgs = $$("img[data-zoom]", art);
    gallery = imgs.map(im => ({ src: im.dataset.zoom, alt: im.alt, cap: im.closest("figure")?.querySelector("figcaption")?.innerHTML || "" }));
    closeOverlays(); viewer.classList.add("open"); document.body.style.overflow = "hidden";
    showPhoto(imgs.indexOf(img));
  }
  document.addEventListener("click", e => { const im = e.target.closest("img[data-zoom]"); if (im && viewer) openViewer(im); });
  document.addEventListener("keydown", e => {
    if (e.target.matches?.("img[data-zoom]") && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openViewer(e.target); }
    if (!viewer?.classList.contains("open")) return;
    if (e.key === "ArrowRight") { e.stopImmediatePropagation(); showPhoto(gi + 1); }
    else if (e.key === "ArrowLeft") { e.stopImmediatePropagation(); showPhoto(gi - 1); }
    else if (e.key === "+" || e.key === "=") zoomAt(z.s * 1.5, innerWidth / 2, innerHeight / 2, true);
    else if (e.key === "-") zoomAt(z.s / 1.5, innerWidth / 2, innerHeight / 2, true);
  }, true);
  $("#viewerNext")?.addEventListener("click", () => showPhoto(gi + 1));
  $("#viewerPrev")?.addEventListener("click", () => showPhoto(gi - 1));
  $("#zoomIn")?.addEventListener("click", () => zoomAt(z.s * 1.6, innerWidth / 2, innerHeight / 2, true));
  $("#zoomOut")?.addEventListener("click", () => zoomAt(z.s / 1.6, innerWidth / 2, innerHeight / 2, true));
  stage?.addEventListener("wheel", e => { e.preventDefault(); zoomAt(z.s * Math.exp(-e.deltaY * 0.0022), e.clientX, e.clientY); }, { passive: false });
  // Pointer gestures: pinch to zoom, drag to pan, double-tap to zoom, swipe to change photo, tap outside to close
  const pts = new Map(); let pinch = null, drag = null, lastTap = 0, moved = false, start = null;
  stage?.addEventListener("pointerdown", e => {
    stage.setPointerCapture(e.pointerId); pts.set(e.pointerId, { x: e.clientX, y: e.clientY }); moved = false;
    start = { x: e.clientX, y: e.clientY, t: Date.now() };
    if (pts.size === 2) { const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), s: z.s }; drag = null; }
    else drag = { x: e.clientX, y: e.clientY, zx: z.x, zy: z.y };
  });
  stage?.addEventListener("pointermove", e => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > 8) moved = true;
    if (pinch && pts.size === 2) { const [a, b] = [...pts.values()];
      zoomAt(pinch.s * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2); }
    else if (drag && z.s > 1.01) { stage.classList.add("dragging");
      z.x = drag.zx + e.clientX - drag.x; z.y = drag.zy + e.clientY - drag.y; clamp(); apply(); }
  });
  const end = e => {
    pts.delete(e.pointerId); stage.classList.remove("dragging");
    if (pts.size < 2) pinch = null;
    if (pts.size) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (z.s <= 1.01 && Math.abs(dx) > 60 && Math.abs(dy) < 60 && gallery.length > 1) return showPhoto(gi + (dx < 0 ? 1 : -1));
    if (z.s <= 1.01 && dy > 110 && Math.abs(dx) < 60) return closeOverlays();          // swipe down to close
    if (!moved) {
      const now = Date.now();
      if (now - lastTap < 300) { zoomAt(z.s > 1.01 ? 1 : 2.5, e.clientX, e.clientY, true); lastTap = 0; }
      else { lastTap = now;
        if (e.target === stage && z.s <= 1.01) setTimeout(() => { if (lastTap === now) closeOverlays(); }, 300); }
    }
    drag = null;
  };
  stage?.addEventListener("pointerup", end);
  stage?.addEventListener("pointercancel", end);
  addEventListener("resize", () => viewer?.classList.contains("open") && measure());

  function closeOverlays() {
    $$(".overlay.open").forEach(o => o.classList.remove("open"));
    $$("video", feed || document.createElement("div")).forEach(v => v.pause());
    document.body.style.overflow = "";
  }
  $$("[data-close]").forEach(b => b.addEventListener("click", closeOverlays));
  searchEl?.addEventListener("click", e => { if (e.target === searchEl.firstElementChild) closeOverlays(); });

  const ICON = {
    sound: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>',
    muted: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5 6 9H3v6h3l5 4z"/><path d="m22 9-6 6M16 9l6 6"/></svg>',
  };

  // Deep link: /reels/ opens the player straight away
  if (document.body.dataset.page === "reels") {
    const first = $("[data-reel]");
    if (first) openPlayer(first.dataset.reel);
  }
  onScroll();
})();
