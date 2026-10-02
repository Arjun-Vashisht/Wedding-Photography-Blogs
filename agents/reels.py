"""Reel agent: writes a short script and renders a 1080x1920 Instagram Reel from the post's photos.

Each scene slowly zooms into a photo (Ken Burns), text fades in, and scenes crossfade.
A female voiceover (voice agent, Piper, offline) narrates each scene, and a free, openly licensed music
track (music agent) plays underneath, dipping automatically while she speaks.
Uses the system ffmpeg if installed, otherwise the copy bundled with the imageio-ffmpeg package.
"""
import logging
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from config import REELS_DIR, SITE_DIR, SITE_NAME, FAST_MODEL, VOICEOVER
from agents import music, voice, factcheck
from agents.base import RateLimited, shape

log = logging.getLogger("reels")
W, H, FPS = 1080, 1920, 30
ZOOM = 1.12             # how far each photo zooms in over its scene
FADE = 0.45             # seconds of crossfade / text fade
PALETTE = [(179, 18, 46), (91, 15, 31), (78, 91, 46), (122, 31, 92), (30, 77, 92)]
MARIGOLD = (233, 162, 27)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

def font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)

def ffmpeg_exe():
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

SYSTEM = f"""You write Instagram Reel scripts for {SITE_NAME}, an Indian and Asian wedding blog.
A reel has 6 scenes. Each scene has on-screen text and a line the narrator (a warm female voice) says.
- "text": on-screen words, at most 9.
- "voice": what she says, 8-18 words, natural spoken English, like telling a friend. It adds to the
  on-screen text rather than repeating it word for word. Spell names as they are pronounced.
Scene 1 is a scroll-stopping hook. Scenes 2-5 each give one specific, true detail from the story.
Scene 6 invites viewers to read the full story at the link in bio.
Use only facts from the story. Then write an Instagram caption (2-3 short lines, include the focus keyword
naturally) and 10-15 relevant hashtags mixing broad (#indianwedding) and specific ones, spelled correctly."""

def script(llm, post):
    return llm.json(SYSTEM,
        f'Story: {post["title"]}\nFocus keyword: {post.get("focus_keyword", "")}\n'
        f'Excerpt: {post["excerpt"]}\nPlace: {post.get("place")}\n\n'
        + "\n".join(post["body"][:6])
        + '\n\nReturn JSON: {"scenes": [{"text": "...", "voice": "..."}], "caption": "...", "hashtags": ["#..."]}',
        model=FAST_MODEL, max_tokens=2000)

def wrap(draw, text, fnt, max_w):
    lines, line = [], ""
    for w in text.split():
        test = (line + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            line = test
        else:
            lines.append(line); line = w
    if line:
        lines.append(line)
    return lines

def background(path, i):
    """A photo scaled to cover the frame with room to zoom, or a patterned card."""
    bw, bh = int(W * ZOOM) + 2, int(H * ZOOM) + 2
    if path and Path(path).exists():
        img = Image.open(path).convert("RGB")
        scale = max(bw / img.width, bh / img.height)
        img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
        left, top = (img.width - bw) // 2, (img.height - bh) // 2
        return img.crop((left, top, left + bw, top + bh))
    img = Image.new("RGB", (bw, bh), PALETTE[i % len(PALETTE)])
    d = ImageDraw.Draw(img)
    for r in range(900, 100, -160):  # simple rangoli rings
        d.ellipse((bw/2 - r, bh/2 - r - 300, bw/2 + r, bh/2 + r - 300), outline=MARIGOLD, width=6)
    return img

def overlay(text, last=False, representative=False):
    """Transparent layer with a bottom gradient, the scene text and the brand."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("L", (1, H))
    for y in range(H):
        grad.putpixel((0, y), int(max(0, (y - H * 0.38) / (H * 0.62)) * 220))
    shade = Image.new("RGBA", (W, H), (25, 4, 10, 255))
    shade.putalpha(grad.resize((W, H)))
    ov = Image.alpha_composite(ov, shade)
    d = ImageDraw.Draw(ov)
    big = font(88 if len(text) < 40 else 76)
    lines = wrap(d, text, big, W - 160)
    y = H - 360 - len(lines) * 104
    for ln in lines:
        d.text((83, y + 3), ln, font=big, fill=(0, 0, 0, 120))   # soft shadow
        d.text((80, y), ln, font=big, fill=(255, 248, 246, 255))
        y += 104
    d.rectangle((80, H - 250, 200, H - 242), fill=MARIGOLD + (255,))
    d.text((80, H - 220), SITE_NAME.upper(), font=font(42), fill=(255, 248, 246, 255))
    if last:
        d.text((80, H - 160), "Full story: link in bio", font=font(36), fill=MARIGOLD + (255,))
    if representative:  # stock photos, not the couple in the story: say so
        f = font(30)
        label = "Representative images"
        tw = d.textlength(label, font=f)
        d.rounded_rectangle((60, 90, 60 + tw + 40, 146), radius=28, fill=(25, 4, 10, 150))
        d.text((80, 100), label, font=f, fill=(255, 248, 246, 230))
    return ov

def render_frame(bg, ov, t, dur, i):
    """Frame at time t (0..dur) of a scene: zoom in (or out on odd scenes) and fade the text in."""
    p = min(1.0, t / dur)
    z = 1 + (ZOOM - 1) * (p if i % 2 == 0 else 1 - p)
    cw, ch = int(bg.width / z), int(bg.height / z)
    left, top = (bg.width - cw) // 2, (bg.height - ch) // 2
    base = bg.crop((left, top, left + cw, top + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
    full = Image.alpha_composite(base, ov)
    a = min(1.0, t / FADE)
    return (full if a >= 1 else Image.blend(base, full, a)).convert("RGB")

VOICE_DELAY = 0.3   # seconds of breath before each line

def check_lines(llm, post, scenes, lines):
    """Fact-check the narration against the (already fact-checked) post; unsupported lines fall back
    to the scene's on-screen text, so the voiceover never says something the post doesn't."""
    draft = {"title": "", "body": [f"{l}." if not l.endswith((".", "!", "?")) else l for l in lines], "faq": []}
    try:
        r = factcheck.check(llm, draft, {"text": " ".join([post["title"], post["excerpt"], *post["body"]])})
    except RateLimited:
        return scenes   # can't check: say only the on-screen text, which is short and safe
    except Exception:
        return lines
    if r["passed"]:
        return lines
    bad = [c.lower() for c in r["claims"]]
    fixed = [scene if any(c[:40] in line.lower() or line.lower()[:40] in c for c in bad) else line
             for scene, line in zip(scenes, lines)]
    log.info("narration: replaced %d unsupported lines", sum(a != b for a, b in zip(fixed, lines)))
    return fixed

def mix_audio(exe, video, track, voice_wav, total, out):
    """Put the voiceover and/or music under the video. Music starts a little in (skipping quiet intros),
    fades in and out, and ducks under the voice; the result is loudness-normalised for Instagram."""
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(video)]
    chains, n = [], 1
    if track:
        dur = track.get("duration") or 0
        start = min(15.0, max(0.0, (dur - total) / 3)) if dur > total + 5 else 0.0
        cmd += (["-ss", f"{start:.1f}"] if start else []) + (["-stream_loop", "-1"] if dur and dur < total else [])
        cmd += ["-i", track["path"]]
        chains.append(f"[{n}:a]atrim=0:{total:.2f},afade=t=in:d=1,afade=t=out:st={total - 2:.2f}:d=2,"
                      f"volume={0.35 if voice_wav else 1.0}[m]")
        n += 1
    if voice_wav:
        cmd += ["-i", str(voice_wav)]
        chains.append(f"[{n}:a]aresample=44100,apad=whole_dur={total:.2f}"
                      + (",asplit=2[v][sc]" if track else "[v]"))
    if track and voice_wav:
        chains.append("[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=400[md]")
        chains.append("[md][v]amix=inputs=2:duration=longest:normalize=0[mix]")
        last = "[mix]"
    else:
        last = "[v]" if voice_wav else "[m]"
    chains.append(f"{last}loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    cmd += ["-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)

def make_reel(llm, post, images, slug, seconds=3.0, region="india", edition="evening"):
    """Returns (reel_path, caption, music_credit)."""
    try:
        s = script(llm, post)
    except Exception as e:
        log.warning("reel script failed: %s", e)
        return None, "", ""
    s = shape(s, "scenes")
    raw = [sc for sc in s.get("scenes", []) if isinstance(sc, dict) and str(sc.get("text", "")).strip()][:7]
    scenes = [str(sc["text"]).strip() for sc in raw] or [post["title"]]
    lines = [str(sc.get("voice") or sc["text"]).strip() for sc in raw] or [post["title"]]
    lines = check_lines(llm, post, scenes, lines)
    tags = [h if h.startswith("#") else "#" + h for h in s.get("hashtags", [])]
    caption = s.get("caption", "").strip() + "\n\n" + " ".join(tags)
    track = None
    try:
        track = music.pick(llm, post, region, edition)
    except Exception as e:
        log.warning("music skipped: %s", e)
    credit = (track or {}).get("credit", "")
    if credit:
        caption += f"\n\nMusic: {credit}"
    (REELS_DIR / f"{slug}-caption.txt").write_text(caption, encoding="utf-8")

    # Time each scene to its narration (at least `seconds`), so the voice never gets cut off.
    spoken = []
    if VOICEOVER:
        try:
            spoken = voice.durations(lines)
        except Exception as e:
            log.warning("voiceover skipped: %s", e)
    secs = [max(seconds, d + VOICE_DELAY + 0.6) for d in spoken] if spoken else [seconds] * len(scenes)
    starts = [sum(secs[:i]) for i in range(len(secs))]
    total = sum(secs)

    paths = [SITE_DIR / im["file"] for im in images] or [None]
    bgs = [background(paths[i % len(paths)], i) for i in range(len(scenes))]
    ovs = [overlay(t, last=(i == len(scenes) - 1), representative=bool(images)) for i, t in enumerate(scenes)]
    render_frame(bgs[0], ovs[0], secs[0] * 0.6, secs[0], 0).save(REELS_DIR / f"{slug}.jpg", quality=85)

    exe = ffmpeg_exe()
    if not exe:
        for i in range(len(scenes)):
            render_frame(bgs[i], ovs[i], secs[i], secs[i], i).save(REELS_DIR / f"{slug}-scene{i}.jpg", quality=88)
        log.warning("ffmpeg not found: saved reel scenes as images instead of a video "
                    "(pip install imageio-ffmpeg)")
        return None, caption, credit

    out = REELS_DIR / f"{slug}.mp4"
    silent = REELS_DIR / f"{slug}.silent.mp4"
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(len(scenes)):
            dur = secs[i]
            for f in range(int(dur * FPS)):
                t = f / FPS
                img = render_frame(bgs[i], ovs[i], t, dur, i)
                if i + 1 < len(scenes) and t > dur - FADE:   # crossfade into the next scene
                    nxt = render_frame(bgs[i + 1], ovs[i + 1], 0, secs[i + 1], i + 1)
                    img = Image.blend(img, nxt, (t - (dur - FADE)) / FADE)
                proc.stdin.write(img.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")
    except Exception as e:
        proc.kill()
        log.warning("reel render failed: %s", e)
        return None, caption, credit
    total = sum(int(d * FPS) for d in secs) / FPS

    voice_wav = None
    if spoken:
        try:
            voice_wav = voice.track(lines, [st + VOICE_DELAY for st in starts], total, REELS_DIR / f"{slug}.voice.wav")
        except Exception as e:
            log.warning("voiceover failed: %s", e)
    if track or voice_wav:
        try:
            mix_audio(exe, silent, track, voice_wav, total, out)
            silent.unlink()
        except Exception as e:
            log.warning("adding audio failed, keeping the silent reel: %s", e)
            credit = ""
    if voice_wav:
        voice_wav.unlink(missing_ok=True)
    if silent.exists():
        silent.replace(out)
    if not credit and "Music:" in caption:
        caption = caption.split("\n\nMusic:")[0]
        (REELS_DIR / f"{slug}-caption.txt").write_text(caption, encoding="utf-8")
    log.info("reel rendered: %s (%d scenes, %.0fs, %s, %s)", out.name, len(scenes), total,
             "female voiceover" if voice_wav else "no voiceover", "music" if credit else "no music")
    return f"reels/{out.name}", caption, credit
