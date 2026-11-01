"""Shared quick image check: claim/semantic mismatch (local Ollama VLM), recycled media (existing pHash index) and a
manipulation signal (EXIF presence + Error Level Analysis). Reuses the pipeline's primitives; never declares media
authentic. Verdicts: CLAIM_MISMATCH | RECYCLED_MEDIA | SUSPICIOUS_MANIPULATION | NO_STRONG_FAKE_SIGNAL."""
import base64
import hashlib
import io
import json

import httpx
import numpy as np
from PIL import Image, ImageOps

from . import db, forensics, preprocessing, vision

# Same ELA rule the report pipeline uses (forensics.check_manipulation): block p99/median ratio > 12 AND p99 > 6.
ELA_RATIO_MIN, ELA_P99_MIN = 12, 6
SEM_SCHEMA = {"type": "object", "required": ["objects_visible", "disaster_cue_visible", "reason"], "properties": {
    "objects_visible": {"type": "array", "items": {"type": "string"}},
    "disaster_cue_visible": {"type": "string", "enum": ["YES", "NO", "UNCERTAIN"]}, "reason": {"type": "string"}}}


def _semantic(img_bytes, category, description):
    p = vision._provider
    if not isinstance(p, vision.OllamaProvider) or p.status()["state"] != "READY":
        return None, "vision model unavailable"
    prompt = (f"Claimed category: {category or 'not given'}. Claimed description: {description or 'not given'}. Examine the image. "
              "disaster_cue_visible means: is the CLAIMED hazard itself visibly present in this image? Reply with JSON: "
              '{"objects_visible":[..],"disaster_cue_visible":"YES|NO|UNCERTAIN","reason":"one sentence"}. Do NOT guess if the image is edited.')
    body = {"model": p.model, "stream": False, "format": SEM_SCHEMA, "options": {"temperature": 0}, "keep_alive": vision.OLLAMA_KEEP_ALIVE,
            "messages": [{"role": "system", "content": "You are a visual forensics assistant. Answer ONLY with JSON."},
                         {"role": "user", "content": prompt, "images": [base64.b64encode(img_bytes).decode()]}]}
    try:
        with p._lock:
            r = httpx.post(f"{p.url}/api/chat", json=body, timeout=p.timeout)
        r.raise_for_status()
        raw = r.json().get("message", {}).get("content", "")
    except Exception as e:
        return None, f"vision model unavailable ({type(e).__name__})"
    try:
        out = json.loads(raw)
        if out.get("disaster_cue_visible") not in ("YES", "NO", "UNCERTAIN"):
            raise ValueError
        return {**out, "model_raw": raw[:600]}, None
    except (ValueError, TypeError, AttributeError):
        return {"objects_visible": [], "disaster_cue_visible": "UNCERTAIN", "model_raw": raw[:600]}, "model reply was not valid JSON"


def _recycled(img, sha):
    qh = {"original": preprocessing.phash(img), **preprocessing.phash_variants(img)}
    best = None
    for r in db.query("SELECT m.report_id, m.sha256, m.phash, m.frame_phashes_json, m.phash_variants_json FROM media m WHERE m.phash IS NOT NULL"):
        d = 0 if r["sha256"] == sha else forensics.best_match(qh, forensics.stored_hashes(r))[0]
        if d is not None and d <= forensics.NEAR_DUP_PHASH and (not best or d < best[1]):
            best = (f"report #{r['report_id']}", d)
    for a in db.query("SELECT id, phash FROM archive_media WHERE phash IS NOT NULL"):
        d = forensics.best_match(qh, {"original": a["phash"]})[0]
        if d is not None and d <= forensics.NEAR_DUP_PHASH and (not best or d < best[1]):
            best = (f"archive item #{a['id']}", d)
    return best


def _ela_png(path):
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((800, 800))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
        re = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        diff = np.clip(np.abs(np.asarray(im, dtype=np.int16) - np.asarray(re, dtype=np.int16)) * 10, 0, 255).astype(np.uint8)
    out = io.BytesIO(); Image.fromarray(diff).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


def analyze_image(image_path: str, claimed_category: str | None, claimed_description: str | None) -> dict:
    raw = open(image_path, "rb").read()
    with Image.open(io.BytesIO(raw)) as im0:
        exif_present = bool(im0.getexif())
        img = ImageOps.exif_transpose(im0).convert("RGB")
    img.thumbnail((1024, 1024))
    jbuf = io.BytesIO(); img.save(jbuf, "JPEG", quality=88)
    reasons = []
    sem, sem_err = _semantic(jbuf.getvalue(), claimed_category, claimed_description)
    if sem_err:
        reasons.append(sem_err)
    cue = (sem or {}).get("disaster_cue_visible", "UNCERTAIN")
    semantic = "UNCERTAIN" if not sem or cue == "UNCERTAIN" else ("RELEVANT" if cue == "YES" else "MISMATCH")
    rec = _recycled(img, hashlib.sha256(raw).hexdigest())
    ela = forensics._ela_score(image_path)
    ela_high = bool(ela and ela["ratio"] > ELA_RATIO_MIN and ela["block_p99"] > ELA_P99_MIN)
    if cue == "NO" and claimed_category:
        verdict, conf = "CLAIM_MISMATCH", 60
        reasons.insert(0, f"The image does not visibly show the claimed '{claimed_category}': {sem.get('reason') or 'no cue seen'}")
    elif rec:
        verdict, conf = "RECYCLED_MEDIA", 95 if rec[1] <= 4 else 80
        reasons.insert(0, f"Perceptual-hash match with {rec[0]} (Hamming distance {rec[1]} of 64): this image was seen before")
    elif not exif_present and ela_high:
        verdict, conf = "SUSPICIOUS_MANIPULATION", 40
        reasons.insert(0, f"No EXIF metadata and uneven Error Level Analysis (p99/median {ela['ratio']}); possible local editing, not proof")
    else:
        verdict, conf = "NO_STRONG_FAKE_SIGNAL", None
        reasons.insert(0, "No strong fake signal from these three checks. This is NOT a statement that the image is authentic.")
    if rec and verdict != "RECYCLED_MEDIA":
        reasons.append(f"Also: perceptual-hash match with {rec[0]} (distance {rec[1]}); this image was seen before")
    if sem and sem.get("reason") and verdict != "CLAIM_MISMATCH":
        reasons.append(f"Vision model: {sem['reason']}")
    if not exif_present:
        reasons.append("No EXIF metadata (common for screenshots and social-media re-uploads; not evidence of fakery by itself)")
    return {"verdict": verdict, "reasons": reasons,
            "visual_evidence": {"objects_visible": (sem or {}).get("objects_visible", []), "disaster_cue_visible": cue,
                                "model_raw": (sem or {}).get("model_raw", "")},
            "signals": {"semantic": semantic, "phash_match": bool(rec), "exif_present": exif_present, "ela_high": ela_high},
            "ela": ela, "ela_threshold": f"block p99/median > {ELA_RATIO_MIN} and p99 > {ELA_P99_MIN}",
            "ela_png_b64": _ela_png(image_path), "confidence": conf,
            "confidence_basis": "fixed per-verdict signal strength (heuristic, not a probability); null when no fake signal was found",
            "model": f"{vision._provider.name}:{getattr(vision._provider, 'model', '')}"}
