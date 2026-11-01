"""AI Visual Analysis Engine (Module 4) — provider-independent, local-only, free.

Providers (VISION_PROVIDER):
  * "ollama" — a vision model served by a LOCAL Ollama instance (default model qwen2.5vl:3b; gemma3:4b also supported).
  * "none"   — visual AI disabled.
There is deliberately NO cloud provider and NO automatic fallback to any paid service. Media is sent only to an
endpoint on this machine (loopback); private-network hosts require VISION_ALLOW_LAN=true; anything else is refused.

The model returns structured OBSERVATIONS only. It never decides whether media is genuine or fake: any
authenticity verdict the model emits is stripped before storage. If the provider is unreachable, the model is not
installed, the call times out or the response is malformed, the analysis is recorded as unavailable/failed and the
rest of the pipeline continues. Nothing is ever fabricated.
"""
import base64
import io
import ipaddress
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

from .config import (OLLAMA_KEEP_ALIVE, OLLAMA_MODEL, OLLAMA_TIMEOUT_SEC, OLLAMA_URL, VISION_ALLOW_LAN, VISION_MAX_EDGE, VISION_MIN_FREE_MB,
                     VISION_MODE, VISION_PROVIDER)

CATEGORIES = ["flood", "fire", "smoke", "landslide", "building_damage", "road_blockage",
              "infrastructure_damage", "cyclone_storm", "other"]
STRENGTHS = ["weak", "moderate", "strong"]
RECOMMENDED_MODELS = {
    "qwen2.5vl:3b": "Default. ~3.2 GB download. Strong at reading signboards/text (OCR) and following a JSON schema; smallest capable option for 8 GB RAM, CPU-only laptops.",
    "gemma3:4b": "Alternative. ~3.3 GB download. Good general scene description; slightly larger memory footprint.",
}

PROMPT = """You are assisting a disaster-verification analyst. Report ONLY what is visible in this image.
You must NOT judge whether the image is real, fake, edited, AI-generated or authentic — another system does that.
Return JSON matching the schema:
  description: one or two sentences describing the visible scene,
  disaster_indicators: list of {category: one of %s, evidence: short visible cue, strength: weak|moderate|strong},
  objects_and_conditions: list of short strings,
  visible_text: legible signboard/shop/vehicle text exactly as seen (empty if none),
  landmarks: identifiable landmarks or place clues actually visible (empty if none),
  context_inconsistencies: visible cues that may not fit a recent real-world disaster photo, e.g. a news watermark,
    screenshot borders, rendered look, seasonal mismatch (empty if none),
  limitations: what cannot be determined from the image.
Do not guess locations without visible evidence.""" % CATEGORIES

SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "disaster_indicators": {"type": "array", "items": {"type": "object", "properties": {
            "category": {"type": "string", "enum": CATEGORIES}, "evidence": {"type": "string"},
            "strength": {"type": "string", "enum": STRENGTHS}}, "required": ["category", "evidence", "strength"]}},
        "objects_and_conditions": {"type": "array", "items": {"type": "string"}},
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "landmarks": {"type": "array", "items": {"type": "string"}},
        "context_inconsistencies": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "disaster_indicators", "objects_and_conditions", "visible_text", "landmarks",
                 "context_inconsistencies", "limitations"],
}
# An indicator whose own evidence denies it (small models do this when forced into a schema) is dropped.
NEGATED_EVIDENCE = re.compile(r"\b(no (visible )?(signs?|evidence|indication)|not (visible|present|damaged|flooded)|"
                              r"(appear|appears|seem|seems|look|looks) (to be )?(intact|undamaged|normal|dry)|without (any )?(damage|water)|intact|undamaged)\b", re.I)
VERDICT_KEYS = re.compile(r"(fake|real|genuine|authentic|manipulat|forg|verdict|deepfake|ai[_ ]?generated|is_edited|tamper)", re.I)


# ------------------------------------------------------------------ endpoint privacy guard
def endpoint_check(url=None):
    """Return (allowed, reason). Media may only go to this machine (or the LAN with explicit opt-in)."""
    url = url or OLLAMA_URL
    try:
        u = urlparse(url)
    except ValueError:
        return False, "invalid URL"
    if u.scheme not in ("http", "https") or not u.hostname:
        return False, "invalid URL"
    host = u.hostname
    if host in ("localhost",):
        return True, "loopback"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False, f"'{host}' is not a local address — media is never sent to non-local services"
    if ip.is_loopback:
        return True, "loopback"
    if ip.is_private and VISION_ALLOW_LAN:
        return True, "private network (VISION_ALLOW_LAN=true)"
    if ip.is_private:
        return False, f"{host} is on the local network; set VISION_ALLOW_LAN=true to allow it"
    return False, f"{host} is a public address — media is never sent to non-local services"


# ------------------------------------------------------------------ provider: Ollama (local)
class OllamaProvider:
    name = "ollama"

    def __init__(self, url, model, timeout):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self._lock = threading.Semaphore(1)  # CPU inference: one image at a time
        self._cache = {"t": 0, "status": None}

    def status(self, force=False):
        if not force and self._cache["status"] and time.time() - self._cache["t"] < 30:
            return self._cache["status"]
        ok, why = endpoint_check(self.url)
        base = {"provider": self.name, "model": self.model, "endpoint": self.url, "mode": mode(), "configured": True,
                "recommended_models": RECOMMENDED_MODELS, "paid": False, "media_leaves_machine": False}
        if not ok:
            st = {**base, "state": "BLOCKED_NON_LOCAL", "detail": why}
        else:
            try:
                r = httpx.get(f"{self.url}/api/tags", timeout=5)
                names = [m.get("name", "") for m in r.json().get("models", [])] if r.status_code == 200 else []
                if r.status_code != 200:
                    st = {**base, "state": "ERROR", "detail": f"Ollama responded HTTP {r.status_code}"}
                elif not any(n == self.model or n == f"{self.model}:latest" or n.split(":")[0] == self.model for n in names):
                    st = {**base, "state": "MODEL_NOT_INSTALLED", "installed_models": names,
                          "detail": f"Ollama is running but '{self.model}' is not installed. Run: ollama pull {self.model}"}
                else:
                    st = {**base, "state": "READY", "installed_models": names, "detail": f"Ollama running; {self.model} installed"}
            except (httpx.HTTPError, ValueError) as e:
                st = {**base, "state": "UNREACHABLE",
                      "detail": f"Ollama not reachable at {self.url} ({type(e).__name__}). Install from https://ollama.com and start it."}
        from .db import now_iso
        st["checked_at"] = now_iso()
        self._cache = {"t": time.time(), "status": st}
        return st

    def analyse(self, path, mime, claimed_category):
        p = Path(path)
        if not p.is_file():
            return {"provider": self.name, "status": "failed", "error": f"image file not found: {p.name}"}
        st = self.status()
        if st["state"] != "READY":
            return {"provider": f"{self.name}:{self.model}", "status": "unavailable", "error": f"{st['state']}: {st['detail']}"}
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE))  # faster CPU inference; stays on this machine
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88)
        except Exception as e:
            return {"provider": self.name, "status": "failed", "error": f"image could not be decoded: {e}"}
        body = {"model": self.model, "stream": False, "format": SCHEMA, "options": {"temperature": 0}, "keep_alive": OLLAMA_KEEP_ALIVE,
                "messages": [{"role": "user", "content": PROMPT + f"\nThe reporter claims category: {claimed_category}.",
                              "images": [base64.b64encode(buf.getvalue()).decode()]}]}
        t0 = time.time()
        try:
            with self._lock:
                r = httpx.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        except httpx.TimeoutException:
            return {"provider": f"{self.name}:{self.model}", "status": "failed", "error": f"model timed out after {self.timeout}s"}
        except httpx.HTTPError as e:
            self._cache["t"] = 0
            return {"provider": f"{self.name}:{self.model}", "status": "unavailable", "error": f"Ollama request failed: {type(e).__name__}"}
        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return {"provider": f"{self.name}:{self.model}", "status": "failed", "error": f"Ollama HTTP {r.status_code}: {r.text[:200]}"}
        try:
            content = r.json()["message"]["content"]
        except (ValueError, KeyError, TypeError):
            return {"provider": f"{self.name}:{self.model}", "status": "failed", "error": "malformed Ollama response envelope"}
        out, problems = sanitize(content)
        if out is None:
            return {"provider": f"{self.name}:{self.model}", "status": "failed", "error": f"malformed model output: {problems[0]}"}
        out["_meta"] = {"latency_ms": ms, "model": self.model, "provider": "ollama (local)", "sanitizer_notes": problems,
                        "image_max_edge_px": VISION_MAX_EDGE}
        return {"provider": f"{self.name}:{self.model}", "status": "completed", "output": out, "description": out.get("description")}


class DisabledProvider:
    name = "none"

    def status(self, force=False):
        return {"provider": "none", "model": None, "endpoint": None, "mode": mode(), "configured": False,
                "state": "NOT_CONFIGURED", "detail": "Visual AI disabled (VISION_PROVIDER=none).", "paid": False,
                "media_leaves_machine": False, "recommended_models": RECOMMENDED_MODELS, "checked_at": None}

    def analyse(self, path, mime, claimed_category):
        return {"provider": "none", "status": "unavailable",
                "error": "Visual AI not configured (VISION_PROVIDER=none) — analysis skipped. Remaining pipeline continues."}


# ------------------------------------------------------------------ output validation
def _strs(v, limit=12, n=200):
    if not isinstance(v, list):
        return []
    return [str(x)[:n] for x in v if isinstance(x, (str, int, float)) and str(x).strip()][:limit]


def sanitize(content):
    """Parse and validate model output against the schema. Returns (clean_dict, notes) or (None, [reason]).
    Authenticity verdicts are removed: the model observes, PRALAY's evidence pipeline concludes."""
    if isinstance(content, str):
        txt = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
        try:
            data = json.loads(txt)
        except ValueError as e:
            return None, [f"not valid JSON ({e.__class__.__name__})"]
    else:
        data = content
    if not isinstance(data, dict):
        return None, ["top-level value is not an object"]
    notes = []
    removed = [k for k in data if VERDICT_KEYS.search(k) and k != "context_inconsistencies"]
    if removed:
        notes.append(f"removed authenticity claims from model output: {removed}")
    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        return None, ["missing 'description'"]
    if VERDICT_KEYS.search(desc) and re.search(r"\b(is|looks|appears)\s+(fake|real|genuine|authentic|manipulated|ai[- ]generated)", desc, re.I):
        desc = re.sub(r"[^.]*\b(fake|genuine|authentic|manipulated|ai[- ]generated)\b[^.]*\.?", "", desc, flags=re.I).strip() or "(description removed: contained an authenticity verdict)"
        notes.append("removed authenticity verdict from description")
    inds = []
    for i in data.get("disaster_indicators") or []:
        if not isinstance(i, dict):
            continue
        cat = str(i.get("category", "")).strip().lower()
        strength = str(i.get("strength", "")).strip().lower()
        if cat not in CATEGORIES or strength not in STRENGTHS:
            notes.append(f"dropped invalid indicator {str(i)[:80]}")
            continue
        if NEGATED_EVIDENCE.search(str(i.get("evidence", ""))):
            notes.append(f"dropped self-contradictory indicator '{cat}' (evidence denies it: {str(i.get('evidence', ''))[:80]})")
            continue
        inds.append({"category": cat, "evidence": str(i.get("evidence", ""))[:200], "strength": strength})
    clean = {"description": desc[:600], "disaster_indicators": inds[:10],
             "objects_and_conditions": _strs(data.get("objects_and_conditions")), "visible_text": _strs(data.get("visible_text"), 8, 120),
             "landmarks": _strs(data.get("landmarks"), 6, 120), "context_inconsistencies": _strs(data.get("context_inconsistencies"), 8),
             "limitations": _strs(data.get("limitations"), 8)}
    return clean, notes


# ------------------------------------------------------------------ module API (used by the pipeline)
_provider = OllamaProvider(OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SEC) if VISION_PROVIDER == "ollama" else DisabledProvider()


def provider():
    return _provider


def available():
    return _provider.status()["state"] == "READY"


def status(force=False):
    st = dict(_provider.status(force))
    st["keep_alive"] = OLLAMA_KEEP_ALIVE
    st["min_free_mb"] = VISION_MIN_FREE_MB
    try:
        from . import db
        q = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM visual_analyses WHERE status IN ('pending','running') GROUP BY status")}
        st["deferred_queue"] = {"pending": q.get("pending", 0), "running": q.get("running", 0), "postponed_low_memory": LOW_MEMORY["active"]}
    except Exception:
        pass
    return st


LOW_MEMORY = {"active": False, "available_mb": None}


def enough_memory():
    """Deferred analysis waits while available RAM is below VISION_MIN_FREE_MB (protects small laptops)."""
    try:
        import psutil
        avail = psutil.virtual_memory().available // (1024 * 1024)
    except Exception:
        return True
    LOW_MEMORY.update(active=avail < VISION_MIN_FREE_MB, available_mb=avail)
    return not LOW_MEMORY["active"]


def validate():
    return _provider.status(force=True)


_mode = {"value": VISION_MODE}


def mode():
    return _mode["value"]


def set_mode(m):
    """Test mode only (via /api/test/vision-mode)."""
    if m not in ("deferred", "inline"):
        raise ValueError("mode must be deferred or inline")
    _mode["value"] = m
    _provider.__dict__.get("_cache", {}).update(t=0)


def analyse_image(path: str, mime: str, claimed_category: str):
    return _provider.analyse(path, mime, claimed_category)


def category_match(output, claimed):
    """Does the model see indicators of the claimed category? Returns 'match' | 'related' | 'no_match' | None.
    Only the claimed category itself (or a true synonym) is a match. An associated hazard (e.g. cyclone_storm for a
    flood claim) is 'related': it is NOT evidence for the claim and goes to review, because small models readily
    invent such indicators from unrelated scenes (a real helmet photo produced a 'strong' cyclone_storm indicator)."""
    if not output:
        return None
    cats = {i.get("category") for i in output.get("disaster_indicators", []) if i.get("strength") in ("moderate", "strong")}
    if not cats:
        return "no_match"
    same = {"fire": {"fire", "smoke"}, "smoke": {"fire", "smoke"}}.get(claimed, {claimed})
    related = {"flood": {"cyclone_storm"}, "cyclone_storm": {"flood", "infrastructure_damage"},
               "building_damage": {"infrastructure_damage"}, "landslide": {"road_blockage"}}.get(claimed, set())
    return "match" if cats & same else "related" if cats & related else "no_match"
