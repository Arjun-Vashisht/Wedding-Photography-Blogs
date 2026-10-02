"""Shared LLM client used by every agent.

Talks to any OpenAI-compatible chat API, so it works with free providers:
Groq (free API key), Gemini (free tier) or Ollama (runs locally, no key).
"""
import json
import logging
import re
import time
import requests
from config import LLM_BASE_URL, LLM_API_KEY, MODEL, LLM_PROVIDER, FALLBACK_MODELS

log = logging.getLogger("agents")

class RateLimited(RuntimeError):
    """Every model is out of quota for now. Try again later; the work itself was fine."""

class LLM:
    def __init__(self):
        if not LLM_API_KEY and LLM_PROVIDER != "ollama":
            raise SystemExit(f"LLM_API_KEY is missing for provider '{LLM_PROVIDER}'. "
                             "Copy .env.example to .env and add your free key (see the comments there).")
        self.url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {LLM_API_KEY or 'ollama'}"}
        self.exhausted = set()   # models whose quota ran out during this run

    def text(self, system: str, prompt: str, model: str = MODEL, max_tokens: int = 2500) -> str:
        """Call `model`; if its quota is used up (free tiers limit each model separately), use the next
        model in FALLBACK_MODELS."""
        chain = [m for m in dict.fromkeys([model, *FALLBACK_MODELS]) if m not in self.exhausted]
        for m in chain:
            try:
                return self._call(m, system, prompt, max_tokens)
            except RateLimited:
                self.exhausted.add(m)
                log.warning("%s is out of free quota for now, switching model", m)
        raise RateLimited("all models are out of free quota; try again later")

    def _call(self, model, system, prompt, max_tokens):
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.7,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}]}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"  # keep thinking short so it doesn't eat the token budget
        for attempt in range(5):
            r = requests.post(self.url, json=body, headers=self.headers, timeout=600)
            wait = float(r.headers.get("retry-after") or 0) or 15 * (attempt + 1)
            if r.status_code == 429 and (wait > 120 or attempt == 4):
                raise RateLimited(r.text[:200])   # a daily limit, not a per-minute one
            if r.status_code in (429, 500, 502, 503) and attempt < 4:
                log.warning("LLM busy or rate-limited (%s), waiting %.0fs", r.status_code, wait)
                time.sleep(min(wait, 120))
                continue
            if not r.ok:
                raise RuntimeError(f"LLM error {r.status_code}: {r.text[:300]}")
            return (r.json()["choices"][0]["message"]["content"] or "").strip()

    def json(self, system: str, prompt: str, **kw):
        system = system + "\n\nRespond with valid JSON only. No markdown fences, no commentary."
        raw = self.text(system, prompt, **kw)
        try:
            return parse_json(raw)
        except ValueError:
            log.warning("model returned invalid JSON, retrying once")
            raw = self.text(system, prompt + "\n\nYour last reply was not valid JSON. Reply with JSON only.", **kw)
            return parse_json(raw)

def shape(reply, key=None):
    """Models sometimes return a bare list where an object was asked for. Normalise:
    with `key`, a list becomes {key: list}; without it, a list becomes its first object."""
    if isinstance(reply, dict):
        return reply
    if isinstance(reply, list):
        if key:
            return {key: reply}
        return next((x for x in reply if isinstance(x, dict)), {})
    return {}

def parse_json(raw: str):
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)  # reasoning models (e.g. qwen3, deepseek-r1)
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    starts = [i for i in (raw.find("{"), raw.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found")
    start = min(starts)
    end = max(raw.rfind("}"), raw.rfind("]"))
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(str(e))
