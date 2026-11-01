"""Evidence Fusion & Credibility Engine (Module 8).

Two SEPARATE assessments:
  A. Incident confidence — does evidence support that the event occurred here and then?
  B. Report confidence  — does evidence support that THIS report/media belongs to the claimed
     incident, location and time?

Scores are transparent additive heuristics on 0-100 (documented in README). They are NOT
calibrated probabilities. Missing evidence never counts as contradiction.
"""
from datetime import timedelta

from . import db, utils, evidence_policy as policy


def requires_evidence_review(incident):
    """Pre-policy assessments are held for explicit review, never bulk re-scored."""
    previous = db.jload(incident.get("assessment_json"), {})
    end = utils.parse_time(incident.get("event_time_end") or incident.get("event_time_start"))
    historical = end and (utils.utcnow() - end).days > 30
    return bool(historical) or (bool(previous) and previous.get("evidence_policy") != policy.VERSION)


def independent_reports(inc, reports):
    eff = lambda r: r.get("override_status") or r["verification_status"]
    indep = [r for r in reports if eff(r) != "CONTRADICTED" and not r.get("is_duplicate_of")
             and r.get("location_confidence") != "unresolved"
             and visual_state(r["id"], r.get("category") or inc.get("category"))["visual_relevance"] not in ("MISMATCH", "RELATED_ONLY")]  # images not showing the hazard never corroborate
    geo = utils.resolve_india(inc.get("place_name")).get("geo") if indep else None
    geo_ok = (geo and inc.get("country_code") in (None, "in") and inc.get("center_lat") is not None and inc.get("center_lon") is not None
              and utils.haversine_km(geo["lat"], geo["lon"], inc["center_lat"], inc["center_lon"]) <= 25)
    name = inc.get("event_name") or policy.storm_name(inc.get("title"))
    return [r for r in indep if geo_ok and not policy.WARN_RX.search(r.get("description") or "")
            and not policy.FORECAST_RX.search(r.get("description") or "")
            and (not name or policy.storm_name(r.get("description")) == name)]

REPORT_WEIGHTS = {
    "loc:high": 20, "loc:medium": 10, "loc:low": 0, "loc:unresolved": -10, "loc:contradicted": -40,
    "TIME_CONSISTENT": 10, "OLD_MEDIA": -40, "PUBLISHED_BEFORE_EVENT": -40, "TIME_INCONSISTENT": -10, "TIME_CLAIM_INVALID": -10,
    "PREVIOUSLY_KNOWN_RECYCLED": -40, "EXACT_DUPLICATE": -5, "NEAR_DUPLICATE": -5,
    "POTENTIALLY_MANIPULATED": -20, "WEAK_MANIPULATION_INDICATOR": -5, "VISUAL_CONTEXT_CONCERN": -5,
    "METADATA_INCONSISTENT": -10, "vision:match": 10, "vision:no_match": -10, "vision:related": -10, "news:validated": 15, "news:trusted_outlet": 25, "reporter:trusted": 10, "reporter:untrusted": -10,
    "incident:CORROBORATED": 15, "incident:CONTRADICTED": -15,
}


def _ip_ban_strikes():
    import os
    return int(os.getenv("IP_BAN_STRIKES", "3"))  # 0 disables the anti-spam ban


# Ban keys: device fingerprint (primary; client-generated, so it can be omitted or forged) and IP (fallback, cannot be
# chosen by the client). A submission is refused if EITHER is banned. Bans expire after IP_BAN_HOURS (default 24; 0 = permanent).
_BANS = {"ip": ("banned_ips", "ip_address", "ip_address"), "fp": ("banned_fingerprints", "fingerprint", "device_fingerprint")}


def ban_active(kind, value):
    import os
    table, key, _ = _BANS[kind]
    b = db.one(f"SELECT * FROM {table} WHERE {key}=?", (value,)) if value else None
    hours = float(os.getenv("IP_BAN_HOURS", "24"))
    if b and (hours == 0 or utils.parse_time(b["banned_at"]) > utils.utcnow() - timedelta(hours=hours)):
        return b
    return None


def ip_ban_active(ip):
    return ban_active("ip", ip)


def strike(kind, r):
    """Count claim-mismatch reports from this key submitted since its previous ban (old strikes are not re-counted);
    the current report counts as a strike. IP_BAN_STRIKES (default 3) strikes -> ban."""
    table, key, col = _BANS[kind]
    value = r.get(col)
    if not value:
        return
    prev = db.one(f"SELECT banned_at FROM {table} WHERE {key}=?", (value,))
    since = prev["banned_at"] if prev else ""
    n = 1 + db.one(f"SELECT COUNT(*) n FROM reports WHERE {col}=? AND id<>? AND upload_time>? "
                   "AND json_extract(assessment_json,'$.fields.needs_review')", (value, r["id"], since))["n"]
    if _ip_ban_strikes() and n >= _ip_ban_strikes() and not ban_active(kind, value):
        db.execute(f"INSERT INTO {table}({key}, reason, banned_at) VALUES(?,?,?) ON CONFLICT({key}) DO UPDATE SET "
                   "reason=excluded.reason, banned_at=excluded.banned_at", (value, f"{n} fake reports submitted (claim mismatch)", db.now_iso()))
        import logging
        logging.getLogger("pralay").warning("%s %s banned after %d claim-mismatch reports", "Device" if kind == "fp" else "IP", value, n)


def strike_ip(r):
    strike("ip", r)


def reporter_credibility(r):
    """Credibility of the reporter, derived from the outcomes of their reports (same input -> same score).
    50 base; +5 per CORROBORATED report; -10 per CONTRADICTED or claim-mismatch report; clipped 0-100.
    `prior` excludes the report being assessed (it already carries its own evidence) to avoid double counting.
    Anonymous reports (no reporter reference) have no credibility history."""
    import hashlib
    ref = " ".join((r.get("reporter_ref") or "").lower().split())
    if not ref:
        return None
    uid = hashlib.sha256(ref.encode()).hexdigest()[:16]
    rows = db.query("SELECT id, COALESCE(override_status, verification_status) st, "
                    "json_extract(assessment_json,'$.fields.needs_review') mm FROM reports "
                    "WHERE lower(trim(reporter_ref))=? AND processing_state IN ('PROCESSED','MAPPED')", (ref,))
    def score(rs):
        return max(0, min(100, 50 + sum(5 if x["st"] == "CORROBORATED" else -10 if (x["st"] == "CONTRADICTED" or x["mm"]) else 0 for x in rs)))
    total, prior = score(rows), score([x for x in rows if x["id"] != r["id"]])
    now = db.now_iso()
    db.execute("INSERT INTO users(id,reporter_name,credibility_score,reports_count,created_at,updated_at) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET credibility_score=excluded.credibility_score, reports_count=excluded.reports_count, "
               "updated_at=excluded.updated_at", (uid, r["reporter_ref"].strip(), total, len(rows), now, now))
    return {"user_id": uid, "score": total, "prior_score": prior, "reports": len(rows),
            "label": "TRUSTED" if prior > 80 else "UNTRUSTED" if prior < 30 else "NEUTRAL"}


def visual_state(report_id, claimed):
    """Separate, explicit visual fields. The user's category is a CLAIM, never visual evidence."""
    from .vision import category_match
    vis = db.one("SELECT * FROM visual_analyses WHERE report_id=? ORDER BY id DESC LIMIT 1", (report_id,))
    st = {"user_claimed_category": claimed, "ai_detected_category": None, "visual_relevance": "NOT_ANALYSED",
          "visual_confidence": None, "analysis_status": vis["status"] if vis else "none"}
    if not vis or vis["status"] != "completed":
        return st
    out = db.jload(vis["output_json"], {}) or {}
    inds = sorted(out.get("disaster_indicators", []), key=lambda i: {"strong": 0, "moderate": 1, "weak": 2}.get(i.get("strength"), 3))
    st["ai_detected_category"] = inds[0]["category"] if inds and inds[0].get("strength") in ("moderate", "strong") else "none_detected"
    st["visual_confidence"] = inds[0]["strength"] if inds else "none"
    m = category_match(out, claimed)
    st["visual_relevance"] = {"match": "MATCH", "related": "RELATED_ONLY"}.get(m, "MISMATCH")
    st["model_description"] = out.get("description")
    return st


def _clip(x):
    return max(0, min(100, x))


def assess_report(report_id, trigger="pipeline"):
    r = db.one("SELECT * FROM reports WHERE id=?", (report_id,))
    findings = db.query("SELECT * FROM media_findings WHERE report_id=? ORDER BY id", (report_id,))
    vis = db.one("SELECT * FROM visual_analyses WHERE report_id=? ORDER BY id DESC LIMIT 1", (report_id,))
    inc = db.one("SELECT * FROM incidents WHERE id=?", (r["incident_id"],)) if r["incident_id"] else None
    inc_status = (inc["override_status"] or inc["status"]) if inc else None
    if inc and requires_evidence_review(inc):
        # An unreviewed legacy corroboration must not promote a new submission.
        inc_status = None
    supporting, contradicting, concerns, missing, contributions = [], [], [], [], []
    score = 50

    def add(key, text, bucket):
        nonlocal score
        w = REPORT_WEIGHTS.get(key, 0)
        score += w
        contributions.append({"factor": key, "weight": w})
        bucket.append(text)

    loc = r["location_confidence"] or "unresolved"
    add(f"loc:{loc}", f"Location: {r['location_assessment']}",
        supporting if loc in ("high", "medium") else contradicting if loc == "contradicted" else missing)
    for f in findings:
        lab, sev = f["finding"], f["severity"]
        text = f"{f['check_type'].replace('_', ' ')}: {lab.replace('_', ' ').lower()} ({f['method']})"
        if lab in REPORT_WEIGHTS:
            add(lab, text, contradicting if sev == "contradiction" else concerns if REPORT_WEIGHTS[lab] < 0 else supporting)
        elif sev == "contradiction":
            contradicting.append(text)
        elif lab in ("METADATA_UNAVAILABLE", "INSUFFICIENT_EVIDENCE", "TIME_UNVERIFIED", "NO_HISTORICAL_MATCH"):
            missing.append(text + (f" — {f['limitation']}" if f["limitation"] else ""))
    from .vision import category_match
    if vis and vis["status"] == "completed":
        cm = category_match(db.jload(vis["output_json"], {}), r["category"])
        if cm:
            add(f"vision:{cm}", f"Visual AI {'sees' if cm == 'match' else 'does not see'} indicators of the claimed category"
                + (" (only a related hazard; not evidence for the claim)" if cm == "related" else ""),
                supporting if cm == "match" else concerns)
    else:
        missing.append("Visual AI analysis unavailable" + (f": {vis['error']}" if vis and vis["error"] else ""))
    if inc_status in ("CORROBORATED", "CONTRADICTED"):
        add(f"incident:{inc_status}", f"Associated incident #{inc['id']} is {inc_status}",
            supporting if inc_status == "CORROBORATED" else concerns)
    elif inc:
        missing.append(f"Associated incident #{inc['id']} is not yet corroborated")
    else:
        missing.append("Not associated with any incident")

    hard = [f for f in findings if f["severity"] == "contradiction"]
    labels = {f["finding"] for f in findings}
    vstate = visual_state(report_id, r["category"])
    mismatch = vstate["visual_relevance"] in ("MISMATCH", "RELATED_ONLY")
    # Validated same-day news (stored by news_ingestion.refresh; no network here). News supports the EVENT, not this photo:
    # when the image does not show the claimed hazard, the boost is withheld.
    news = [{"source": o["label"].split(" | ", 1)[0], "title": o["label"].split(" | ", 1)[-1], "published": o["time_value"], "link": o["limitation"],
             "trusted": is_trusted(o["label"].split(" | ", 1)[0])}
            for o in db.query("SELECT * FROM observations WHERE report_id=? AND kind='news' ORDER BY id", (report_id,))]
    if mismatch:
        strike("fp", r)
        strike("ip", r)
    cred = reporter_credibility(r)  # based on the reporter's OTHER reports
    if cred and cred["label"] == "TRUSTED":
        add("reporter:trusted", f"Trusted reporter (credibility {cred['prior_score']}/100 from {cred['reports'] - 1} earlier report(s))", supporting)
    elif cred and cred["label"] == "UNTRUSTED":
        add("reporter:untrusted", f"Untrusted reporter (credibility {cred['prior_score']}/100): earlier reports were contradicted or mismatched; "
            "needs manual review", concerns)
    if news and mismatch:
        concerns.append(f"{len(news)} validated news article(s) found, but not credited: the image does not show the claimed hazard.")
    elif news:
        for a in news[:3]:
            add("news:validated", f"Validated news ({a['source']}): {a['title'][:140]}", supporting)
            if a["trusted"]:
                add("news:trusted_outlet", f"Trusted outlet: {a['source']}", supporting)
    if mismatch:
        concerns.append(f"Category mismatch: reporter claimed '{r['category']}' but the image shows "
                        f"{'no disaster indicators' if vstate['ai_detected_category'] == 'none_detected' else vstate['ai_detected_category']}"
                        f"{' (a related hazard only)' if vstate['visual_relevance'] == 'RELATED_ONLY' else ''}"
                        f" ({vstate.get('model_description') or 'no description'}). The image is not evidence for the claimed hazard.")
    elif vstate["visual_relevance"] == "NOT_ANALYSED":
        missing.append(f"Image not analysed by visual AI ({vstate['analysis_status']}); the claimed category '{r['category']}' is unconfirmed by the image")
    if hard:
        status, reason = "CONTRADICTED", "Strong contradicting evidence: " + "; ".join(sorted({f["finding"] for f in hard}))
    elif mismatch:
        status, reason = "UNVERIFIED", (f"Needs review: image does not show the claimed '{r['category']}' hazard "
                                        f"(visual relevance {vstate['visual_relevance']}); not counted as evidence for the incident.")
    elif inc_status == "CORROBORATED" and loc in ("high", "medium") and "POTENTIALLY_MANIPULATED" not in labels:
        status, reason = "CORROBORATED", "Incident corroborated, report location is medium/high confidence and no strong contradictions."
    else:
        why = []
        if inc_status != "CORROBORATED":
            why.append("incident not corroborated")
        if loc not in ("high", "medium"):
            why.append(f"location confidence is {loc}")
        if "POTENTIALLY_MANIPULATED" in labels:
            why.append("potential manipulation indicators")
        status, reason = "UNVERIFIED", "Insufficient evidence: " + ", ".join(why)
    score = _clip(score)
    from .forensics import media_classification
    assessment = {"status": status, "score": score, "reason": reason, "supporting": supporting, "contradicting": contradicting,
                  "fields": {**vstate, "media_authenticity_status": media_classification(findings) if findings else "NO_MEDIA",
                             "report_verification_status": status, "needs_review": mismatch,
                             "reporter_credibility": cred, "reporter_review": bool(cred and cred["label"] == "UNTRUSTED"),
                             "score_basis": "heuristic: base 50 + listed factors; 50 with no factors means NO evidence either way, not 50% confidence"},
                  "concerns": concerns, "missing": missing, "contributions": contributions, "news_evidence": news,
                  "score_note": "Additive heuristic from base 50 (see README). Not a calibrated probability.",
                  "assessed_at": db.now_iso()}
    changed = (r["verification_status"], r["report_confidence"]) != (status, score)
    db.execute("UPDATE reports SET verification_status=?, report_confidence=?, assessment_json=?, updated_at=? WHERE id=?",
               (status, score, db.jdump(assessment), db.now_iso(), report_id))
    if changed:
        db.execute("INSERT INTO assessments(entity_type,entity_id,status,score,reasons_json,trigger,created_at) VALUES('report',?,?,?,?,?,?)",
                   (report_id, status, score, db.jdump({"reason": reason}), trigger, db.now_iso()))
    return assessment


# Exact publisher keys (evidence_policy.publisher_key: lowercase, domain suffix and punctuation removed). Exact-match only:
# a trusted name raises the weight of an article that ALREADY passed every validation check; it never validates one.
TRUSTED_OUTLETS = {
    "reuters", "associatedpress", "apnews", "bbc", "bbcnews", "bbccouk", "ndtv", "timesofindia", "thetimesofindia",
    "timesofindiaindiatimes", "thehindu", "hindustantimes", "indianexpress", "theindianexpress", "indiatoday",
    "presstrustofindia", "ptinews",
}


def is_trusted(publisher):
    return policy.publisher_key(publisher) in TRUSTED_OUTLETS


def assess_incident(incident_id, trigger="pipeline", include_report=None):
    """include_report: a report still in PROCESSING (the one being processed now) that should count."""
    inc = db.one("SELECT * FROM incidents WHERE id=?", (incident_id,))
    if requires_evidence_review(inc):
        return {**db.jload(inc["assessment_json"], {}), "review_required": True,
                "review_reason": "Legacy assessment retained pending explicit evidence/geography review."}, False
    reports = db.query("SELECT * FROM reports WHERE incident_id=? AND (processing_state NOT IN ('RECEIVED','PROCESSING') OR id=?)",
                       (incident_id, include_report or -1))
    ev = db.query("SELECT * FROM evidence WHERE incident_id=?", (incident_id,))
    eff = lambda x: x["override_status"] or x["verification_status"]
    contradicted = [r for r in reports if eff(r) == "CONTRADICTED"]
    # Forwarded alerts/forecasts are not independent eyewitness confirmations.
    # Country validation also gates crowd-only corroboration, not just connectors.
    indep = independent_reports(inc, reports)
    strong = [r for r in indep if r["location_confidence"] in ("high", "medium")]
    sup = policy.supporting_items(inc, ev)
    con = [e for e in ev if e["stance"] == "contradicts"]
    groups = {e["independence_group"] for e in sup if e["independence_group"]}
    nonweather = {g for g in groups if not g.startswith("weather:")}
    official = {g for g in groups if g.startswith("official:")}
    weather = groups - nonweather
    n_crowd = len(indep)

    rules = {
        "R1_crowd_consensus": len(strong) >= 3,
        "R2_crowd_plus_external": n_crowd >= 2 and len(nonweather) >= 1,
        "R3_crowd_plus_official": n_crowd >= 1 and len(official) >= 1,
        "R4_two_independent_external": len(nonweather) >= 2,
        "R5_crowd_external_weather": n_crowd >= 1 and len(nonweather) >= 1 and len(weather) >= 1,
    }
    met = [k for k, v in rules.items() if v]
    if met and not con:
        status = "CORROBORATED"
        reason = "Corroboration rule(s) met: " + ", ".join(met)
    elif reports and len(contradicted) == len(reports) and not sup:
        status = "CONTRADICTED"
        reason = "All associated reports are contradicted and no independent source supports the incident."
    elif con and not met:
        status = "CONTRADICTED" if len(con) > len(groups) else "UNVERIFIED"
        reason = f"{len(con)} contradicting source(s) vs {len(groups)} supporting group(s)."
    else:
        status = "UNVERIFIED"
        reason = (f"Not enough independent evidence yet: {n_crowd} independent report(s) ({len(strong)} with medium/high location), "
                  f"{len(nonweather)} independent external supporting source group(s), weather support: {bool(weather)}.")
    # Only validated supporting items reach `sup`; a trusted outlet among them weighs +30 instead of +15.
    trusted = {g for g in nonweather if g.startswith("news:") and g[5:] in TRUSTED_OUTLETS}
    score = _clip(25 + 10 * min(n_crowd, 4) + 5 * min(len(strong), 3) + 15 * min(len(nonweather), 3) + 15 * min(len(trusted), 3)
                  + 8 * len(weather) - 10 * len(contradicted) - 15 * len(con))
    missing = []
    if not nonweather:
        missing.append("No independent news or official source has confirmed this incident yet.")
    if not weather and inc["category"] in ("flood", "landslide", "cyclone_storm"):
        missing.append("No supporting heavy-rainfall signal in modelled weather data (does not disprove the event).")
    if len(strong) < 2:
        missing.append("Fewer than two reports with medium/high location confidence.")
    unavailable = [e["source_name"] for e in ev if e["stance"] == "unavailable"]
    assessment = {
        "evidence_policy": policy.VERSION,
        "status": status, "score": score, "reason": reason, "rules": rules,
        "supporting": [f"{e['source_name']} ({e['publisher'] or e['source_origin']}): {e['summary'][:200]}" for e in sup]
                      + [f"Independent report #{r['id']} ({r['location_confidence']} location confidence)" for r in indep],
        "contradicting": [f"{e['source_name']}: {e['summary'][:200]}" for e in con]
                         + [f"Report #{r['id']} contradicted" for r in contradicted],
        "missing": missing, "sources_unavailable": sorted(set(unavailable)),
        "sources_considered": sorted({e["source_name"] for e in ev}),
        "independent_groups": sorted(groups), "trusted_supporting_groups": sorted(trusted), "independent_reports": n_crowd, "duplicate_reports": sum(1 for r in reports if r["is_duplicate_of"]),
        "score_note": "Heuristic 0-100 (see README). Not a calibrated probability.", "assessed_at": db.now_iso(),
    }
    changed = (inc["status"], inc["confidence_score"]) != (status, score)
    db.execute("UPDATE incidents SET status=?, confidence_score=?, assessment_json=?, updated_at=? WHERE id=?",
               (status, score, db.jdump(assessment), db.now_iso(), incident_id))
    if changed:
        db.execute("INSERT INTO assessments(entity_type,entity_id,status,score,reasons_json,trigger,created_at) VALUES('incident',?,?,?,?,?,?)",
                   (incident_id, status, score, db.jdump(assessment), trigger, db.now_iso()))
    return assessment, changed
