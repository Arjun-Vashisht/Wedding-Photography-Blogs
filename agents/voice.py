"""Voice agent: turns the reel's narration into a female voiceover. Free, runs offline, no account.

Two engines:
- Kokoro (default, most natural): VOICE=hf_alpha or hf_beta (Indian female, English with an Indian accent),
  af_heart / af_bella (American female), bf_emma / bf_isabella (British female). Model files in voices/kokoro/.
- Piper: VOICE=en_GB-cori-high, en_US-lessac-high, hi_IN-priyamvada-medium, ... (downloaded into voices/).
"""
import logging
import re
import subprocess
import sys
import wave
from functools import lru_cache

import numpy as np
from config import BASE_DIR, VOICE, VOICE_SPEED

log = logging.getLogger("voice")
VOICES_DIR = BASE_DIR / "voices"

FEMALE_VOICES = {   # Kokoro voices (already downloaded with the model); any Piper voice name also works for VOICE
    "hf_alpha": "Indian female, English with an Indian accent (default)",
    "hf_beta": "Indian female, softer",
    "af_heart": "American female, warm",
    "af_bella": "American female, bright",
    "bf_emma": "British female",
    "bf_isabella": "British female, lighter",
}
current = VOICE   # change with use()

def use(name):
    """Switch voice for this run (e.g. from --voice)."""
    global current
    current = name
    load.cache_clear(); speak.cache_clear()

KOKORO_DIR = VOICES_DIR / "kokoro"
KOKORO_FILES = {"kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
                "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"}

def is_kokoro(name):
    return bool(re.fullmatch(r"[abhjefipz][fm]_[a-z]+", name or ""))

@lru_cache(maxsize=1)
def kokoro():
    from kokoro_onnx import Kokoro
    import requests
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    for f, u in KOKORO_FILES.items():
        if not (KOKORO_DIR / f).exists():
            log.info("downloading %s (one time)", f)
            (KOKORO_DIR / f).write_bytes(requests.get(u, timeout=600).content)
    return Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))

@lru_cache(maxsize=1)
def load():
    from piper import PiperVoice
    model = VOICES_DIR / f"{current}.onnx"
    if not model.exists():
        log.info("downloading voice %s (one time)", current)
        VOICES_DIR.mkdir(exist_ok=True)
        subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(VOICES_DIR), current],
                       check=True, capture_output=True)
    return PiperVoice.load(str(model))

def samples(text="Welcome to Phera. Tonight, a candlelit wedding in Udaipur, with haldi, pheras and a thousand flickering lights."):
    """Save a short sample of every female voice to voices/samples/ so you can choose by ear."""
    out_dir = VOICES_DIR / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for name, desc in FEMALE_VOICES.items():
        try:
            use(name)
            clip, sr = speak(text)
        except Exception as e:
            log.warning("could not make a sample for %s: %s", name, e)
            continue
        path = out_dir / f"{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(clip.tobytes())
        made.append((name, desc, path))
    use(VOICE)
    return made

# How Indian words should sound (the voices read English spelling rules otherwise: "you-day-pour").
# Only the spoken audio uses these; on-screen text keeps the normal spelling. Add your own here.
PRONOUNCE = {
    "udaipur": "Oodaypoor", "jaipur": "Jaipoor", "jodhpur": "Jodhpoor", "rajasthan": "Raajasthaan",
    "pheras": "pheyraas", "phera": "pheyraa", "haldi": "huldee", "mehndi": "mehndee", "mehendi": "mehndee",
    "sangeet": "sungeet", "lehenga": "lehngaa", "lehengas": "lehngaas", "mandap": "mundup",
    "baraat": "baraat", "shaadi": "shaadee", "nikah": "nikaah", "dulhan": "dulhun", "sherwani": "sherwaanee",
    "varmala": "varmaalaa", "jaimala": "jaimaalaa", "kalire": "kaleeray", "sindoor": "sindoor",
}

def pronounce(text):
    import re
    return re.sub(r"[A-Za-z]+", lambda m: PRONOUNCE.get(m.group(0).lower(), m.group(0)), text)

@lru_cache(maxsize=64)
def speak(text):
    """Return (samples as int16 numpy array, sample_rate) for one line of narration."""
    if is_kokoro(current):
        lang = "en-gb" if current.startswith("b") else "en-us"   # Indian voices speak English, in their own accent
        audio, sr = kokoro().create(pronounce(text), voice=current, speed=VOICE_SPEED, lang=lang)
        return (np.clip(audio, -1, 1) * 32767).astype(np.int16), sr
    from piper import SynthesisConfig
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        load().synthesize_wav(pronounce(text), w, syn_config=SynthesisConfig(length_scale=1 / VOICE_SPEED))
    buf.seek(0)
    with wave.open(buf) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()

def track(lines, starts, total, path):
    """Place each spoken line at its start time (seconds) on one silent track and save it as a WAV."""
    clips = [speak(t) if t else (np.zeros(0, np.int16), 22050) for t in lines]
    sr = next((r for c, r in clips if len(c)), 22050)
    out = np.zeros(int(total * sr) + sr, dtype=np.int16)
    for (clip, _), start in zip(clips, starts):
        i = int(start * sr)
        out[i:i + len(clip)] = clip[:max(0, len(out) - i)]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(out.tobytes())
    return path

def durations(lines):
    """Seconds of speech for each line (so scenes can be timed to the narration)."""
    out = []
    for t in lines:
        clip, sr = speak(t) if t else (np.zeros(0), 22050)
        out.append(len(clip) / sr)
    return out
