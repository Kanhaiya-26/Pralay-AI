"""Location & Time Verification Engine (Module 6).

Keeps every observation separately (claimed text, geocode, EXIF GPS, browser/device, landmark,
claimed time, EXIF time, publication time, upload time) with its limitation, then derives an
*investigated* location/time with an explicit method and confidence. Upload time is never used as
capture time.
"""
from . import utils
from .forensics import F
from .utils import haversine_km, parse_time

CONSISTENT_KM = 15
CONTRADICT_KM = 50
OLD_MEDIA_H = 72


def investigate_location(report, exif, visual):
    obs, findings = [], []
    claim = None
    if report.get("claimed_location_text"):
        obs.append(dict(kind="location", source="claimed_text", label=report["claimed_location_text"],
                        limitation="Free-text claim by the reporter."))
        claim = utils.geocode(report["claimed_location_text"])
        if claim:
            obs.append(dict(kind="location", source="geocode", lat=claim["lat"], lon=claim["lon"], label=claim["label"],
                            limitation="OpenStreetMap Nominatim centroid of the named place; may be kilometres from the actual scene."))
        else:
            obs.append(dict(kind="location", source="geocode", label="Not resolved",
                            limitation="Claimed place could not be geocoded (unknown name or geocoder unreachable)."))
    if report.get("claimed_lat") is not None and not claim:
        claim = {"lat": report["claimed_lat"], "lon": report["claimed_lon"], "label": "Pin dropped by reporter", "state": None}
        obs.append(dict(kind="location", source="claimed_pin", lat=claim["lat"], lon=claim["lon"],
                        label=claim["label"], limitation="Map pin placed by the reporter (a claim)."))
    gps = (exif or {}).get("gps")
    if gps:
        obs.append(dict(kind="location", source="exif_gps", lat=gps["lat"], lon=gps["lon"], label="EXIF GPS",
                        limitation="Embedded by the capturing device; can be edited and is often stripped by platforms."))
    br = None
    if report.get("browser_lat") is not None:
        br = {"lat": report["browser_lat"], "lon": report["browser_lon"]}
        obs.append(dict(kind="location", source="browser", lat=br["lat"], lon=br["lon"],
                        label=f"Browser location (±{int(report.get('browser_accuracy_m') or 0)} m)",
                        limitation="Location of the submitting DEVICE at upload time (with consent) — not necessarily where the media was captured."))
    lm = None
    if visual and visual.get("status") == "completed":
        clues = (visual["output"].get("landmarks") or []) + (visual["output"].get("visible_text") or [])
        for c in clues[:3]:
            q = f"{c}, {report.get('claimed_location_text') or ''}".strip(", ")
            g = utils.geocode(q)
            if g:
                lm = {**g, "clue": c}
                obs.append(dict(kind="location", source="landmark", lat=g["lat"], lon=g["lon"], label=f"Visual clue '{c}' → {g['label']}",
                                limitation="Landmark/signboard read by AI and geocoded; names are often ambiguous."))
                break

    inv, method, conf, notes = None, None, "unresolved", []
    d_gps = haversine_km(gps["lat"], gps["lon"], claim["lat"], claim["lon"]) if gps and claim else None
    d_br = haversine_km(br["lat"], br["lon"], claim["lat"], claim["lon"]) if br and claim else None
    d_lm = haversine_km(lm["lat"], lm["lon"], claim["lat"], claim["lon"]) if lm and claim else None
    # Thresholds scale with the size of the claimed place: a district centroid can be 20+ km from a valid photo.
    radius = (claim or {}).get("radius_km") or 2.0
    consistent_km = max(CONSISTENT_KM, radius + 3)
    contradict_km = max(CONTRADICT_KM, 2 * radius + 20)
    severity = "info"
    if exif and exif.get("gps_rejected"):
        notes.append(f"EXIF GPS present but rejected ({exif['gps_rejected']['reason']}).")
    if gps and gps.get("dop") and gps["dop"] > 10:
        notes.append(f"EXIF GPS precision is poor (DOP {gps['dop']}); position may be off by hundreds of metres.")
    if gps and not utils_in_india(gps):
        notes.append("EXIF GPS lies outside India.")
    if gps and claim:
        if d_gps <= consistent_km:
            inv, method, conf, severity = gps, "EXIF GPS consistent with claimed location", "high", "clear"
        elif d_gps > contradict_km:
            inv, method, conf, severity = gps, "EXIF GPS contradicts claimed location", "contradicted", "contradiction"
        else:
            inv, method, conf, severity = claim, "EXIF GPS somewhat distant from claim", "low", "concern"
        notes.append(f"EXIF GPS is {d_gps:.1f} km from the geocoded claim (claimed place extent ≈{radius:.1f} km; "
                     f"agree ≤{consistent_km:.0f} km, contradict >{contradict_km:.0f} km).")
    elif gps:
        inv, method, conf = gps, "EXIF GPS only (no claimed place)", "medium"
    elif claim:
        inv, method, conf = claim, "Claimed location only (geocoded)", "low"
        if d_br is not None and d_br <= consistent_km:
            conf, method = "medium", "Claimed location; submitting device was nearby at upload"
            notes.append(f"Browser/device location is {d_br:.1f} km from the claim.")
        elif d_br is not None:
            notes.append(f"Device was {d_br:.1f} km away at upload — reporter may be forwarding media; not a contradiction.")
    elif br:  # fallback when there is no EXIF GPS and no claimed place: where the device was at upload
        acc = report.get("browser_accuracy_m")
        inv, method = br, "Device location at upload (no EXIF GPS, no claimed place)"
        conf = "medium" if acc is not None and acc <= 1000 else "low"
        notes.append(f"Only the submitting device's location is available (±{int(acc or 0)} m); it shows where the reporter was "
                     "at upload, not necessarily where the media was captured.")
    if lm and claim:
        if d_lm <= consistent_km:
            notes.append(f"Visual clue '{lm['clue']}' geocodes {d_lm:.1f} km from the claim (supporting).")
            if conf == "low":
                conf, method = "medium", method + " + visual landmark consistent"
        else:
            notes.append(f"Visual clue '{lm['clue']}' geocodes {d_lm:.1f} km from the claim (ambiguous clue — not decisive).")
            if severity in ("info", "clear"):
                severity = "concern"
    if conf == "unresolved":
        notes.append("No usable location evidence — report cannot be mapped.")
    finding = {"clear": "LOCATION_CONSISTENT", "contradiction": "LOCATION_CONTRADICTION",
               "concern": "LOCATION_UNCERTAIN"}.get(severity, "LOCATION_CLAIM_ONLY" if conf != "unresolved" else "LOCATION_UNRESOLVED")
    if visual is not None and visual.get("status") == "pending":
        landmark_status = "pending — deferred local AI analysis will add landmark/signboard clues when ready"
    elif visual is None or visual.get("status") != "completed":
        landmark_status = "unavailable — " + ((visual or {}).get("error") or "no image for visual analysis")
    else:
        landmark_status = "clue found and geocoded" if lm else "no usable landmark or signboard identified by the model"
    summary = {
        "claimed": claim and {"label": report.get("claimed_location_text") or claim.get("label"), "lat": claim["lat"], "lon": claim["lon"],
                              "geocoded_as": claim.get("label"), "extent_km": radius, "place_type": claim.get("place_type")},
        "extracted_gps": gps or None, "device": br, "landmark_check": landmark_status,
        "investigated": inv and {"lat": inv["lat"], "lon": inv["lon"], "method": method, "confidence": conf},
        "thresholds_km": {"agree": round(consistent_km, 1), "contradict": round(contradict_km, 1)},
    }
    findings.append(F("location_consistency", finding, severity,
                      {"summary": summary, "claimed": claim, "exif_gps": gps, "browser": br, "landmark": lm,
                       "distance_km": {"exif_vs_claim": d_gps and round(d_gps, 2), "device_vs_claim": d_br and round(d_br, 2),
                                       "landmark_vs_claim": d_lm and round(d_lm, 2)}, "notes": notes},
                      "Cross-check of claim geocode, EXIF GPS, device location and visual landmarks",
                      {"high": "high", "contradicted": "high", "medium": "medium"}.get(conf, "low"),
                      "Location sources each have limitations (see observations)."))
    return {"observations": obs, "findings": findings, "claim": claim, "radius_km": radius if claim else None,
            "investigated": inv, "method": method, "confidence": conf, "assessment": " ".join([method or "Unresolved"] + notes)}


def utils_in_india(p):
    from .preprocessing import in_india_extent
    return in_india_extent(p["lat"], p["lon"])


def investigate_time(report, exif, container_time=None, client_modified=None):
    obs, findings = [], []
    if container_time:
        obs.append(dict(kind="time", source="video_container", time_value=container_time,
                        limitation="MP4/MOV 'mvhd' creation time; rewritten by editing/export apps. Weaker than EXIF."))
    if client_modified:
        obs.append(dict(kind="time", source="client_file_modified", time_value=client_modified,
                        limitation="File last-modified time reported by the submitting browser; changes when files are copied or downloaded. Context only."))
    up = parse_time(report["upload_time"])
    claimed = parse_time(report.get("claimed_event_time"))
    pub = parse_time(report.get("original_publication_time"))
    cap = None
    if exif and exif.get("capture_time"):
        off = exif.get("capture_offset")
        cap = parse_time(exif["capture_time"] + (off if off and off[0] in "+-" else ""))
    g_utc = parse_time(((exif or {}).get("gps") or {}).get("utc_time"))
    if g_utc:
        obs.append(dict(kind="time", source="gps_time", time_value=utils.iso(g_utc),
                        limitation="GPS satellite UTC time written with the location fix; independent of the camera clock but editable."))
    obs.append(dict(kind="time", source="upload", time_value=utils.iso(up),
                    limitation="Server receipt time — an upper bound only; never treated as capture time."))
    if claimed:
        obs.append(dict(kind="time", source="claimed_time", time_value=utils.iso(claimed), limitation="Reporter's claim."))
    if pub:
        obs.append(dict(kind="time", source="publication", time_value=utils.iso(pub),
                        limitation="Original publication time of the post/article as provided."))
    if cap:
        obs.append(dict(kind="time", source="exif_time", time_value=utils.iso(cap),
                        limitation="Device clock at capture (time zone assumed IST if absent); editable."))
    ref = claimed or pub
    notes, severity, finding = [], "info", "TIME_UNVERIFIED"
    if claimed and up and (claimed - up).total_seconds() > 3600:
        notes.append("Claimed event time is in the future relative to upload.")
        severity, finding = "concern", "TIME_CLAIM_INVALID"
    if cap and ref:
        gap = (ref - cap).total_seconds() / 3600
        if gap > OLD_MEDIA_H:
            severity, finding = "contradiction", "OLD_MEDIA"
            notes.append(f"Media was captured {gap / 24:.1f} days before the claimed event time.")
        elif gap < -24:
            severity, finding = "concern", "TIME_INCONSISTENT"
            notes.append(f"Capture time is {-gap:.0f} h after the claimed event time.")
        else:
            severity, finding = "clear", "TIME_CONSISTENT"
            notes.append(f"Capture time within {abs(gap):.0f} h of the claimed event.")
    elif cap and up:
        gap = (up - cap).total_seconds() / 3600
        if gap > OLD_MEDIA_H:
            severity, finding = "contradiction", "OLD_MEDIA"
            notes.append(f"Media was captured {gap / 24:.1f} days before it was submitted as a current report.")
        else:
            severity, finding = "clear", "TIME_CONSISTENT"
            notes.append(f"Captured {gap:.1f} h before upload.")
    else:
        notes.append("No capture timestamp available; event time rests on the claim only.")
    if claimed and pub and (claimed - pub).total_seconds() > 24 * 3600:
        severity, finding = "contradiction", "PUBLISHED_BEFORE_EVENT"
        notes.append("Original post was published more than 24 h before the claimed event.")
    findings.append(F("time_consistency", finding, severity,
                      {"claimed_event_time": utils.iso(claimed), "exif_capture_time": utils.iso(cap),
                       "publication_time": utils.iso(pub), "upload_time": utils.iso(up), "notes": notes},
                      "Comparison of EXIF capture, claimed event, publication and upload times",
                      "medium" if cap else "low", "Device clocks and EXIF can be wrong or edited."))
    event_time = claimed or cap or pub
    return {"observations": obs, "findings": findings, "exif_time": utils.iso(cap), "event_time": event_time,
            "assessment": " ".join(notes)}
