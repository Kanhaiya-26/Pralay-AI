"""Media Authenticity & Duplicate Detection Engine (Module 5).

Each check returns a finding dict that preserves: finding label, severity, evidence, method,
confidence, limitation and status. Duplicate detection and authenticity investigation are kept as
separate checks. No check declares media "fake"; the strongest label is POTENTIALLY_MANIPULATED,
which requires at least two independent indicators.
"""
import io

import numpy as np
from PIL import Image

from . import db
from .preprocessing import hamming
from .utils import hours_between, parse_time

NEAR_DUP_PHASH = 8        # Hamming distance (of 64 bits) treated as near-duplicate
NEAR_DUP_DHASH = 12
RECYCLE_GAP_H = 72        # media known >72h before the claimed event is "previously known"
EDITING_SOFTWARE = ("photoshop", "gimp", "lightroom", "snapseed", "picsart", "canva", "facetune",
                    "pixlr", "affinity", "paint.net", "faceapp", "midjourney", "stable diffusion", "dall")


def F(check_type, finding, severity, evidence, method, confidence, limitation=None, status="completed",
      related_media_id=None, related_report_id=None):
    return dict(check_type=check_type, finding=finding, severity=severity, evidence=evidence, method=method,
                confidence=confidence, limitation=limitation, status=status,
                related_media_id=related_media_id, related_report_id=related_report_id)


def _ref_time(report):
    return parse_time(report.get("claimed_event_time")) or parse_time(report["upload_time"])


def query_hashes(media):
    """All perceptual hashes describing the NEW media: original, transformed variants and video frames."""
    hs = {}
    if media.get("phash"):
        hs["original"] = media["phash"]
    for k, v in (media.get("phash_variants") or {}).items():
        hs[k] = v
    for i, v in enumerate(media.get("frame_phashes") or []):
        hs[f"frame{i}"] = v
    return hs


def stored_hashes(row):
    """Hashes of a STORED media row (original + video frames)."""
    hs = {"original": row["phash"]} if row.get("phash") else {}
    for i, v in enumerate(db.jload(row.get("frame_phashes_json"), []) or []):
        hs[f"frame{i}"] = v
    # Stored centre-crop variant catches the case where the NEW upload is a crop of the stored image.
    crop = (db.jload(row.get("phash_variants_json"), {}) or {}).get("crop80")
    if crop:
        hs["stored_crop80"] = crop
    return hs


def best_match(qh, sh):
    """Smallest Hamming distance between any query hash and any stored hash -> (distance, query_kind, stored_kind)."""
    best = (None, None, None)
    for qk, qv in qh.items():
        for sk, sv in sh.items():
            d = hamming(qv, sv)
            if d is not None and (best[0] is None or d < best[0]):
                best = (d, qk, sk)
    return best


def check_exact_duplicates(report, media):
    rows = db.query(
        "SELECT m.id mid, m.sha256, m.content_hash, r.id rid, r.upload_time, r.claimed_event_time, r.claimed_location_text, r.incident_id "
        "FROM media m JOIN reports r ON r.id=m.report_id WHERE (m.sha256=? OR (m.content_hash IS NOT NULL AND m.content_hash=?)) "
        "AND m.report_id<>? ORDER BY r.upload_time",
        (media["sha256"], media.get("content_hash") or "-", report["id"]))
    method = "SHA-256 of file bytes + SHA-256 of decoded pixels, compared with all stored media"
    if not rows:
        return [F("exact_duplicate", "NO_EXACT_DUPLICATE", "clear", {"sha256": media["sha256"], "content_hash": media.get("content_hash")},
                  method, "high", "Re-encoded, resized or cropped copies differ at the pixel level; see near-duplicate check.")]
    first = rows[0]
    matches = [{"report_id": r["rid"], "uploaded": r["upload_time"], "claimed_location": r["claimed_location_text"],
                "match_type": "identical file bytes" if r["sha256"] == media["sha256"] else "identical pixels, different file (metadata stripped/changed)"}
               for r in rows]
    return [F("exact_duplicate", "EXACT_DUPLICATE", "concern", {"sha256": media["sha256"], "matches": matches}, method, "high",
              "An identical copy is not independent evidence; it may be a re-share of the same original.",
              related_media_id=first["mid"], related_report_id=first["rid"])]


def check_near_duplicates(report, media):
    qh = query_hashes(media)
    if not qh:
        return [F("near_duplicate", "INSUFFICIENT_EVIDENCE", "info", {}, "Perceptual hash", "low",
                  "Perceptual hash unavailable for this media.", status="unavailable")]
    rows = db.query("SELECT m.id mid, m.report_id rid, m.phash, m.dhash, m.sha256, m.content_hash, m.frame_phashes_json, m.phash_variants_json, m.kind, "
                    "r.upload_time, r.claimed_location_text FROM media m JOIN reports r ON r.id=m.report_id "
                    "WHERE m.report_id<>? AND (m.phash IS NOT NULL OR m.frame_phashes_json IS NOT NULL)", (report["id"],))
    matches = []
    for r in rows:
        if r["sha256"] == media["sha256"] or (media.get("content_hash") and r["content_hash"] == media["content_hash"]):
            continue  # covered by exact check
        d, qk, sk = best_match(qh, stored_hashes(r))
        if d is None or d > NEAR_DUP_PHASH:
            continue
        # dHash confirmation only makes sense when both sides are untransformed originals.
        dd = hamming(media.get("dhash"), r["dhash"]) if qk == "original" and sk == "original" and media.get("dhash") and r["dhash"] else None
        if dd is not None and dd > NEAR_DUP_DHASH:
            continue  # dHash disagrees: likely a coincidental pHash collision
        matches.append({"report_id": r["rid"], "media_id": r["mid"], "phash_distance": d, "dhash_distance": dd,
                        "transform": qk, "matched_against": f"{r['kind']}:{sk}",
                        "uploaded": r["upload_time"], "claimed_location": r["claimed_location_text"]})
    method = (f"pHash (DCT 64-bit) of original + rotated/mirrored/cropped variants + video frames; Hamming <= {NEAR_DUP_PHASH}, "
              f"dHash <= {NEAR_DUP_DHASH} confirmation for untransformed matches")
    if not matches:
        return [F("near_duplicate", "NO_NEAR_DUPLICATE", "clear", {"hashes_compared": len(qh), "stored_media_compared": len(rows)},
                  method, "medium", "Heavy edits, overlays, collages or strong colour changes may evade perceptual hashing.")]
    matches.sort(key=lambda m: m["phash_distance"])
    best = matches[0]
    return [F("near_duplicate", "NEAR_DUPLICATE", "concern", {"matches": matches[:10]}, method,
              "high" if best["phash_distance"] <= 4 else "medium",
              "Visually similar media can also be distinct photos of the same scene taken moments apart.",
              related_media_id=best["media_id"], related_report_id=best["report_id"])]


def check_recycled(report, media):
    """Historical / recycled media: the local media archive plus all previously stored reports.
    A reverse-image-search API is not integrated (no legitimate free API available)."""
    ref = _ref_time(report)
    qh = query_hashes(media)
    hits = []
    for a in db.query("SELECT * FROM archive_media"):
        d, qk, _ = best_match(qh, {"original": a["phash"]} if a["phash"] else {})
        exact = a["sha256"] == media["sha256"] or (a["content_hash"] and a["content_hash"] == media.get("content_hash"))
        if exact or (d is not None and d <= NEAR_DUP_PHASH + 2):
            pub = parse_time(a["first_published"])
            hits.append({"source": a["source_name"], "url": a["source_url"], "first_published": a["first_published"],
                         "description": a["description"], "phash_distance": 0 if exact else d, "transform": "exact" if exact else qk,
                         "is_demo_archive": bool(a["is_demo"]), "archive_id": a["id"],
                         "hours_before_event": round((ref - pub).total_seconds() / 3600, 1) if pub and ref else None})
    for r in db.query("SELECT m.phash, m.sha256, m.content_hash, m.frame_phashes_json, m.phash_variants_json, m.exif_json, r.id rid, r.upload_time "
                      "FROM media m JOIN reports r ON r.id=m.report_id WHERE m.report_id<>?", (report["id"],)):
        d, qk, _ = best_match(qh, stored_hashes(r))
        exact = r["sha256"] == media["sha256"] or (r["content_hash"] and r["content_hash"] == media.get("content_hash"))
        if not (exact or (d is not None and d <= NEAR_DUP_PHASH)):
            continue
        # Earliest evidence of existence: the stored copy's upload, or its EXIF capture time if older.
        up = parse_time(r["upload_time"])
        cap = parse_time((db.jload(r["exif_json"], {}) or {}).get("capture_time"))
        seen = min(t for t in (up, cap) if t) if (up or cap) else None
        if seen and ref and (ref - seen).total_seconds() / 3600 > RECYCLE_GAP_H:
            hits.append({"source": f"PRALAY report #{r['rid']}", "first_published": seen.isoformat(),
                         "basis": "EXIF capture time of stored copy" if cap and seen == cap else "upload time of stored copy",
                         "phash_distance": 0 if exact else d, "transform": "exact" if exact else qk,
                         "hours_before_event": round((ref - seen).total_seconds() / 3600, 1)})
    old = [h for h in hits if h.get("hours_before_event") is not None and h["hours_before_event"] > RECYCLE_GAP_H]
    lim = ("Searched only the local PRALAY media archive and stored reports. No external reverse-image search "
           "is integrated, so absence of a match does NOT prove the media is new or authentic.")
    if old:
        return [F("recycled", "PREVIOUSLY_KNOWN_RECYCLED", "contradiction", {"matches": old},
                  "Hash match against archive / earlier reports published before the claimed event", "high",
                  "The match shows the media existed earlier; it may still be legitimately re-shared as context.")]
    if hits:
        return [F("recycled", "HISTORICAL_MATCH_SAME_PERIOD", "info", {"matches": hits},
                  "Hash match against archive / earlier reports (within the event window)", "medium", lim)]
    return [F("recycled", "NO_HISTORICAL_MATCH", "info", {"archive_size": db.one("SELECT COUNT(*) c FROM archive_media")["c"]},
              "pHash/SHA-256 lookup against local archive", "low", lim)]


def _ela_score(path):
    """Error Level Analysis: re-save at JPEG q=90 and measure how unevenly error is distributed.
    A high ratio between the most and typical block error can indicate locally edited regions."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > 1600:
            im.thumbnail((1600, 1600))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        re = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        diff = np.abs(np.asarray(im, dtype=np.int16) - np.asarray(re, dtype=np.int16)).mean(axis=2)
    h, w = diff.shape
    b = 32
    blocks = [diff[y:y + b, x:x + b].mean() for y in range(0, h - b + 1, b) for x in range(0, w - b + 1, b)]
    if len(blocks) < 16:
        return None
    blocks = np.array(blocks)
    med = float(np.median(blocks)) + 0.25
    p99 = float(np.percentile(blocks, 99))
    return {"block_median": round(med, 3), "block_p99": round(p99, 3), "ratio": round(p99 / med, 2)}


def check_manipulation(report, media, exif, visual):
    indicators, evidence = [], {}
    soft = (exif or {}).get("software") or ""
    if soft and any(s in soft.lower() for s in EDITING_SOFTWARE):
        indicators.append(f"EXIF Software tag names editing/generation tool: '{soft}'")
    dims = (exif or {}).get("exif_dims")
    if dims and media.get("width") and sorted(dims) != sorted([media["width"], media["height"]]):
        indicators.append(f"EXIF dimensions {dims} differ from actual {media['width']}x{media['height']} (resized/cropped after capture)")
    cap, mod = parse_time((exif or {}).get("capture_time")), parse_time((exif or {}).get("modified_time"))
    if cap and mod and (mod - cap).total_seconds() > 86400:
        indicators.append(f"EXIF modify time {exif['modified_time']} is >24h after capture {exif['capture_time']} (file re-saved later)")
    ela = None
    if media["kind"] == "image" and media["mime"] == "image/jpeg":
        try:
            ela = _ela_score(media["stored_path"])
        except Exception as e:
            evidence["ela_error"] = str(e)
        if ela:
            evidence["ela"] = ela
            if ela["ratio"] > 12 and ela["block_p99"] > 6:
                indicators.append(f"Error Level Analysis: uneven compression error (p99/median ratio {ela['ratio']})")
    vis_flags = []
    if visual and visual.get("status") == "completed":
        vis_flags = visual["output"].get("context_inconsistencies") or []
    evidence["indicators"] = indicators
    lim = ("Heuristic indicators only. Social-media platforms strip and re-compress images, which can trigger or "
           "hide these signals. Not proof of manipulation.")
    out = []
    if len(indicators) >= 2:
        out.append(F("manipulation", "POTENTIALLY_MANIPULATED", "concern", evidence,
                     "EXIF consistency + editing-software tag + Error Level Analysis", "medium", lim))
    elif indicators:
        out.append(F("manipulation", "WEAK_MANIPULATION_INDICATOR", "info", evidence,
                     "EXIF consistency + editing-software tag + Error Level Analysis", "low", lim))
    else:
        out.append(F("manipulation", "NO_MANIPULATION_INDICATORS_FOUND", "clear", evidence,
                     "EXIF consistency + editing-software tag + Error Level Analysis", "low",
                     lim + " Absence of indicators does not prove authenticity."))
    if vis_flags:
        out.append(F("visual_context", "VISUAL_CONTEXT_CONCERN", "concern", {"model_observations": vis_flags},
                     f"AI visual model ({visual['provider']}) observations", "low",
                     "AI model observation only — never treated as proof that media is fake."))
    elif visual and visual.get("status") != "completed":
        out.append(F("visual_context", "INSUFFICIENT_EVIDENCE", "info", {"reason": visual.get("error")},
                     "AI visual context check", "low", "Visual AI unavailable; context check not performed.",
                     status="unavailable"))
    return out


def check_metadata(report, media, exif):
    up = parse_time(report["upload_time"])
    if media["kind"] == "video":
        vm = media.get("video_meta") or {}
        ct = parse_time(vm.get("creation_time"))
        ref = parse_time(report.get("claimed_event_time")) or up
        if ct and ref and (ref - ct).total_seconds() / 3600 > RECYCLE_GAP_H:
            return [F("metadata", "VIDEO_CREATED_BEFORE_EVENT", "concern", vm, "MP4/MOV 'mvhd' container creation time", "medium",
                      f"Container time is {((ref - ct).total_seconds() / 86400):.1f} days before the claimed event/upload. "
                      "Editing apps can rewrite this field, so it is a concern rather than proof.")]
        if ct:
            return [F("metadata", "VIDEO_TIME_CONSISTENT", "clear", vm, "MP4/MOV 'mvhd' container creation time", "low", vm.get("limitation"))]
        return [F("metadata", "VIDEO_METADATA_LIMITED", "info", vm, "OpenCV + MP4 box inspection", "low",
                  vm.get("limitation") or "Video capture time unavailable.")]
    if not exif or not exif.get("present"):
        return [F("metadata", "METADATA_UNAVAILABLE", "info", {},
                  "EXIF inspection (Pillow)", "high",
                  "Most social platforms strip EXIF. Missing metadata is unavailable evidence, NOT a sign of fakery.")]
    issues, notes = [], []
    off = exif.get("capture_offset")
    cap = parse_time((exif.get("capture_time") or "") + (off if off and off[0] in "+-" else "")) if exif.get("capture_time") else None
    if cap and up and (cap - up).total_seconds() > 3600 * 14:  # beyond any time-zone ambiguity
        issues.append(f"EXIF capture time {exif['capture_time']} is after the upload time")
    gps = exif.get("gps") or {}
    g_utc = parse_time(gps.get("utc_time"))
    if cap and g_utc:
        gap_h = abs((cap - g_utc).total_seconds()) / 3600
        if off and gap_h > 1:
            issues.append(f"Camera clock ({exif['capture_time']} {off}) and GPS satellite clock ({gps['utc_time']}) differ by {gap_h:.1f} h")
        elif not off and gap_h > 14:
            issues.append(f"Camera clock and GPS satellite clock differ by {gap_h:.1f} h (beyond time-zone ambiguity)")
        else:
            notes.append(f"Camera clock agrees with GPS satellite clock (gap {gap_h:.1f} h{'; time zone unknown' if not off else ''})")
    if exif.get("gps_rejected"):
        notes.append(f"EXIF GPS ignored: {exif['gps_rejected']['reason']}")
    if not gps and exif.get("make"):
        notes.append("Camera make present but no GPS (GPS may be disabled — not suspicious by itself)")
    ev = {"tags": exif.get("tags"), "gps": exif.get("gps"), "capture_time": exif.get("capture_time"), "capture_offset": off}
    if issues:
        return [F("metadata", "METADATA_INCONSISTENT", "concern", {**ev, "issues": issues, "notes": notes}, "EXIF inspection (Pillow)",
                  "medium", "Device clocks can be wrong; EXIF is editable.")]
    return [F("metadata", "METADATA_PRESENT", "clear", {**ev, "notes": notes}, "EXIF inspection (Pillow)", "medium",
              "EXIF is evidence, not proof — it can be edited.")]


def media_classification(findings):
    """Overall label per spec ordering."""
    labels = {f["finding"] for f in findings}
    for lab in ("PREVIOUSLY_KNOWN_RECYCLED", "EXACT_DUPLICATE", "NEAR_DUPLICATE", "POTENTIALLY_MANIPULATED"):
        if lab in labels:
            return lab
    if "NO_HISTORICAL_MATCH" in labels or "HISTORICAL_MATCH_SAME_PERIOD" in labels:
        return "NO_HISTORICAL_MATCH"
    return "INSUFFICIENT_EVIDENCE"


CONCLUSION_TEXT = {
    "PREVIOUSLY_KNOWN_RECYCLED": "This media (or a near-identical copy) existed before the claimed event. It should not be treated as evidence of the current event.",
    "EXACT_DUPLICATE": "This is an identical copy of media already in PRALAY. It adds no independent evidence.",
    "NEAR_DUPLICATE": "This is visually near-identical to media already in PRALAY (possibly re-encoded, resized, rotated or cropped). It adds little independent evidence.",
    "POTENTIALLY_MANIPULATED": "Two or more independent manipulation indicators were found. This warrants human review; it is not proof of fakery.",
    "NO_HISTORICAL_MATCH": "No earlier copy was found in PRALAY's own archive. This does NOT prove the media is new or authentic (no external reverse-image search).",
    "INSUFFICIENT_EVIDENCE": "Not enough information to assess this media.",
}


def media_conclusion(findings):
    """Conclusion kept separate from evidence: which finding(s) triggered it, plus explicit uncertainty."""
    cls = media_classification(findings)
    trig = [f for f in findings if f["finding"] == cls]
    concerns = [f for f in findings if f["severity"] in ("concern", "contradiction") and f["finding"] != cls]
    uncertainty = sorted({f["limitation"] for f in trig + concerns if f.get("limitation")})
    unavailable = [f"{f['check_type'].replace('_', ' ')}: {f['finding'].replace('_', ' ').lower()}" for f in findings if f["status"] == "unavailable"]
    return {"classification": cls, "conclusion": CONCLUSION_TEXT[cls],
            "triggered_by": [{"id": f.get("id"), "check": f["check_type"], "finding": f["finding"], "method": f["method"],
                              "confidence": f["confidence"]} for f in trig],
            "other_concerns": [{"id": f.get("id"), "check": f["check_type"], "finding": f["finding"], "severity": f["severity"]} for f in concerns],
            "checks_unavailable": unavailable, "uncertainty": uncertainty,
            "never_fake_note": "PRALAY never labels media 'fake' from an AI prediction; conclusions rest on the recorded evidence above."}
