"""AI Investigation Chatbot (Module 14) + Exact Query Cache / Incident Cache (Module 10).

Database-grounded: every answer is assembled from stored incidents, reports, findings and evidence.
Nothing is generated that is not in the database. The flow is:
  normalise query -> exact query cache -> incident cache (existing incidents + fresh evidence)
  -> new external investigation only when needed -> store -> answer with provenance.
"""
import re
import threading
from datetime import timedelta

from . import db, external, pipeline, utils, evidence_policy as policy, fusion
from .config import QUERY_CACHE_TTL_MIN

CATEGORY_SYNONYMS = {
    "flood": ["flood", "flooding", "flooded", "waterlogging", "waterlogged", "inundation", "inundated", "deluge"],
    "fire": ["fire", "fires", "blaze", "burning", "wildfire"], "smoke": ["smoke"],
    "landslide": ["landslide", "landslides", "landslip", "mudslide"],
    "building_damage": ["collapse", "collapsed", "building damage"], "road_blockage": ["road blocked", "road blockage", "blocked road"],
    "infrastructure_damage": ["bridge", "infrastructure"], "cyclone_storm": ["cyclone", "storm", "hurricane", "typhoon"],
    "other": ["avalanche", "earthquake", "cloudburst", "tsunami", "heatwave", "drought"],
}
HAZARD_WORDS = ["avalanche", "earthquake", "cloudburst", "tsunami", "heatwave", "drought", "flood", "landslide", "cyclone", "storm", "fire", "wildfire"]
FOLLOWUP = re.compile(r"\b(check again|search again|any updates?|what happened next|was it confirmed|new developments|look again|try again|i heard there was)\b")
_locks = {}
_glock = threading.Lock()


def parse(q):
    raw = q.strip()
    t = re.sub(r"[^\w\s#'-]", " ", raw.lower())
    t = re.sub(r"\s+", " ", t).strip()
    cat = None
    for c, syns in CATEGORY_SYNONYMS.items():
        if any(re.search(rf"\b{re.escape(s)}\b", t) for s in syns):
            cat = c
            break
    place = None
    m = re.search(r"\b(?:in|near|at|around|across|of)\s+([a-z][a-z .'-]*?)(?=\s+(?:today|now|right|this|last|during|since|yesterday|in the|area|region|district|city)\b|$)", t)
    if m:
        place = m.group(1).strip(" .'-")
        place = re.sub(r"^(the)\s+", "", place)
        if place in ("this", "the", "it"):
            place = None
    window = 72.0
    if "today" in t or "right now" in t or re.search(r"\bnow\b", t):
        window = 24.0
    if "yesterday" in t:
        window = 48.0
    if "week" in t:
        window = 168.0
    mh = re.search(r"last (\d+) (hour|day)", t)
    if mh:
        window = float(mh.group(1)) * (24 if mh.group(2) == "day" else 1)
    inc_id = None
    mi = re.search(r"(?:incident\s*#?|#)(\d+)", t)
    if mi:
        inc_id = int(mi.group(1))
    hazard = next((h for h in HAZARD_WORDS if re.search(rf"\b{h}s?\b", t)), None)
    name = policy.storm_name(t)
    historical = bool(re.search(r"\b(historical|history|past|previous|last year)\b", t))
    if FOLLOWUP.search(t) and not (cat or place or name):
        intent = "followup"
    elif "duplicate" in t:
        intent = "duplicates"
    elif "unverified" in t:
        intent = "unverified"
    elif "contradict" in t or "fake" in t or "recycled" in t or "old media" in t:
        intent = "contradicted"
    elif "grid" in t or "cell" in t:
        intent = "grid"
    elif ("evidence" in t or "support" in t or "source" in t) and (inc_id or "this" in t):
        intent = "evidence"
    elif (cat and place) or name or (hazard and place):
        intent = "investigate"
    elif place or "incident" in t or "active" in t:
        intent = "list"
    elif hazard:
        intent = "investigate"
    else:
        intent = "help"
    key = f"{policy.VERSION}|{intent}|{cat or '-'}|{place or '-'}|{int(window)}|{inc_id or '-'}"
    # hazard only disambiguates when no category matched ("flooding" and "flood" must share one cache key)
    key += f"|{name or '-'}|{(hazard if not cat else '-') or '-'}|{int(historical)}"
    return {"raw": raw, "normalized": t, "intent": intent, "category": cat, "place": place, "window_h": window,
            "incident_id": inc_id, "norm_key": key, "hazard": hazard, "name": name, "historical": historical}


def classify_headline(title, pub):
    return policy.classify(title, pub, utils.utcnow())


def live_search(p, fresh=False):
    """Live investigation via Google News RSS (actually invoked). Syndicated copies collapse; publishers = independence."""
    import xml.etree.ElementTree as ET
    term = " ".join(x for x in [("cyclone " + p["name"]) if p.get("name") else (p.get("hazard") or (p.get("category") or "").replace("_", " ")),
                                p.get("place") or ""] if x).strip()
    q = term + ("" if p.get("historical") else " when:30d")
    res = utils.cached_get("https://news.google.com/rss/search", {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                           ttl_min=15, source="google_news", force=fresh)
    out = {"query": q, "connector": "Google News RSS", "invoked": True, "ok": res.ok, "error": res.error,
           "retrieved_at": res.fetched_at, "from_cache": res.from_cache, "items": []}
    if not res.ok:
        return out
    seen = set()
    try:
        items = list(ET.fromstring(res.body).iter("item"))
    except ET.ParseError as e:
        out.update(ok=False, error=f"parse error: {e}")
        return out
    for it in items[:40]:
        title = (it.findtext("title") or "").strip()
        norm = policy.norm_title(title)
        if (norm in seen or not policy.hazard_matches(title, p.get("category"), p.get("hazard"))
                or (p.get("place") and not policy.mentions(title, p["place"]))
                or (p.get("name") and policy.storm_name(title) != p["name"].lower())):
            continue
        seen.add(norm)
        pub = utils.parse_time(it.findtext("pubDate"))
        srcel = it.find("source")
        out["items"].append({"title": title, "url": (it.findtext("link") or "").strip(), "publisher": srcel.text if srcel is not None else None,
                             "published": utils.iso(pub), "classification": classify_headline(title, pub)})
        if len(out["items"]) >= 12:
            break
    return out


def live_summary(p, live, has_local):
    items = live["items"]
    subject = ("Cyclone " + p["name"].title()) if p.get("name") else (p.get("hazard") or (p.get("category") or "disaster").replace("_", " "))
    where = f" in {p['place'].title()}" if p.get("place") else ""
    by = {k: [i for i in items if i["classification"] == k] for k in ("actual", "warning", "forecast", "historical", "unknown")}
    pubs = lambda k: len({policy.publisher_key(i["publisher"]) for i in by[k] if policy.publisher_key(i["publisher"])})
    if not live["ok"]:
        head = f"Live search for {subject}{where} could not be completed ({live['error']})."
    elif by["actual"]:
        head = (f"Live news: {len(by['actual'])} report(s) describing an actual {subject}{where} event from {pubs('actual')} independent "
                f"publisher(s) — external evidence, not yet verified by PRALAY.")
    elif by["warning"] or by["forecast"]:
        head = f"Only warnings/forecasts found for {subject}{where} — no confirmed occurrence in live news."
    elif by["historical"]:
        head = f"Only historical (older than 30 days) reports of {subject}{where} found."
    elif by["unknown"]:
        head = f"Related news found for {subject}{where}, but occurrence could not be established from the headlines and timestamps."
    else:
        head = f"No live news found for {subject}{where} after searching."
    lim = ["External evidence is not verified by PRALAY until it is ingested and corroborated."]
    if not has_local:
        lim.append("No matching incident in PRALAY's database — this does NOT mean nothing happened.")
    if by["warning"] or by["forecast"]:
        lim.append(f"{len(by['warning'])} warning(s) and {len(by['forecast'])} forecast(s) are listed separately and are not treated as confirmed events.")
    if pubs("actual") == 1:
        lim.append("Only one independent publisher — insufficient independent corroboration.")
    return {"headline": head, "counts": {k: len(v) for k, v in by.items()}, "independent_publishers_actual": pubs("actual"), "limitations": lim}


RULE_TEXT = {
    "R1_crowd_consensus": "3+ independent reports with consistent (medium/high-confidence) locations",
    "R2_crowd_plus_external": "2+ independent reports plus an independent news/official source",
    "R3_crowd_plus_official": "a report plus a matching official alert",
    "R4_two_independent_external": "2+ independent news/official sources",
    "R5_crowd_external_weather": "a report, an independent news/official source and supporting weather data",
}


def evidence_profile(inc, a):
    """Facts the confidence assessment is based on — read from the stored assessment, reports and evidence (no new scoring)."""
    reps = db.query("SELECT id, description, verification_status, override_status, location_confidence, is_duplicate_of, processing_state "
                    "FROM reports WHERE incident_id=?", (inc["id"],))
    done = [r for r in reps if r["processing_state"] in ("PROCESSED", "MAPPED")]
    eff = lambda r: r["override_status"] or r["verification_status"]
    indep = fusion.independent_reports(inc, done)
    strong = [r for r in indep if r["location_confidence"] in ("high", "medium")]
    contradicted = [r for r in done if eff(r) == "CONTRADICTED"]
    ids = [r["id"] for r in indep] or [-1]
    time_ok = db.one(f"SELECT COUNT(DISTINCT report_id) c FROM media_findings WHERE finding='TIME_CONSISTENT' AND report_id IN ({','.join('?' * len(ids))})", ids)["c"]
    cids = [r["id"] for r in contradicted] or [-1]
    strong_contra = db.one(f"SELECT COUNT(DISTINCT report_id) c FROM media_findings WHERE severity='contradiction' AND confidence IN ('high','medium') "
                           f"AND report_id IN ({','.join('?' * len(cids))})", cids)["c"]
    ev = db.query("SELECT * FROM evidence WHERE incident_id=?", (inc["id"],))
    sup = policy.supporting_items(inc, ev)
    groups = {e["independence_group"] for e in sup if e["independence_group"]}
    return {
        "reports_total": len(reps), "reports_processed": len(done), "independent_reports": len(indep),
        "reports_with_consistent_location": len(strong), "reports_time_consistent": time_ok,
        "duplicate_reports": sum(1 for r in done if r["is_duplicate_of"]), "contradicted_reports": len(contradicted),
        "contradicted_with_strong_findings": strong_contra,
        "supporting_items": len(sup), "independent_external_groups": sorted(g for g in groups if not g.startswith("weather:")),
        "official_support": any(g.startswith("official:") for g in groups), "weather_support": any(g.startswith("weather:") for g in groups),
        "contradicting_sources": sum(1 for e in ev if e["stance"] == "contradicts"),
        "sources_unavailable": sorted({e["source_name"] for e in ev if e["result_type"] == "unavailable" or e["stance"] == "unavailable"}),
        "rules_met": [RULE_TEXT.get(k, k) for k, v in (a.get("rules") or {}).items() if v],
    }


def assess_confidence(status, prof, fresh, overridden):
    """Qualitative confidence that the stated status is well supported. Deliberately NOT a percentage: PRALAY's numeric
    scores are uncalibrated heuristics, so any % would be arbitrary. Separate from status (CORROBORATED can be MEDIUM)."""
    why, level = [], "LOW"
    ext = len(prof["independent_external_groups"])
    crowd_ok = prof["reports_with_consistent_location"] >= 2
    if status == "CORROBORATED":
        kinds = int(crowd_ok) + int(ext >= 1 or prof["official_support"])
        if kinds == 2:
            level = "HIGH"
            why.append(f"two independent kinds of evidence agree: {prof['reports_with_consistent_location']} reports with consistent locations "
                       f"and {ext} independent external source group(s)")
        else:
            level = "MEDIUM"
            why.append("corroborated by one kind of evidence only (" + ("independent crowd reports; no independent news/official confirmation"
                       if crowd_ok else "external sources; few reports with consistent locations") + ")")
    elif status == "CONTRADICTED":
        if prof["contradicted_with_strong_findings"] >= 2 or prof["contradicting_sources"] >= 1:
            level = "HIGH"
            why.append(f"{prof['contradicted_with_strong_findings']} report(s) with strong contradicting findings"
                       + (f" and {prof['contradicting_sources']} contradicting source(s)" if prof["contradicting_sources"] else ""))
        elif prof["contradicted_with_strong_findings"] == 1:
            level = "MEDIUM"
            why.append("one report with a strong contradicting finding (e.g. recycled/old media or a GPS mismatch)")
        else:
            why.append("contradiction rests on weak findings")
    else:  # UNVERIFIED or unknown
        why.append("not enough independent evidence to confirm or rule out the event — UNVERIFIED does not mean false")
    if status in ("CORROBORATED", "CONTRADICTED") and fresh.get("state") != "fresh":
        level = {"HIGH": "MEDIUM", "MEDIUM": "LOW"}.get(level, "LOW")
        why.append(f"external evidence is {fresh.get('state', 'unknown').replace('_', ' ')} (lowered one level)")
    if status == "CORROBORATED" and prof["contradicted_reports"]:
        # A contradicted report (e.g. a recycled photo) discredits that media, not necessarily the event. Lower confidence
        # only when contradictions are a substantial share of the evidence; otherwise record them without lowering.
        if prof["contradicted_reports"] * 2 >= max(prof["independent_reports"], 1):
            level = {"HIGH": "MEDIUM", "MEDIUM": "LOW"}.get(level, "LOW")
            why.append(f"{prof['contradicted_reports']} contradicted vs {prof['independent_reports']} independent report(s) — substantial disagreement (lowered one level)")
        else:
            why.append(f"{prof['contradicted_reports']} associated report(s) contradicted (e.g. recycled/old media) — outweighed by "
                       f"{prof['independent_reports']} independent report(s), not lowered")
    if prof["duplicate_reports"]:
        why.append(f"{prof['duplicate_reports']} duplicate report(s) not counted as independent")
    if prof["sources_unavailable"]:
        why.append("unavailable sources: " + ", ".join(prof["sources_unavailable"]))
    if overridden:
        why.append("status was set by a human reviewer; confidence still reflects the recorded evidence")
    return {"level": level, "percentage": None,
            "percentage_note": "No percentage shown: PRALAY's numeric scores are uncalibrated heuristics, not probabilities.",
            "reasons": why}


def _inc_view(inc):
    a = db.jload(inc["assessment_json"], {})
    n = db.one("SELECT COUNT(*) c FROM reports WHERE incident_id=?", (inc["id"],))["c"]
    status = inc["override_status"] or inc["status"]
    fresh = external.freshness(inc)
    prof = evidence_profile(inc, a)
    return {"id": inc["id"], "title": inc["title"], "category": inc["category"], "status": status,
            "review_required": fusion.requires_evidence_review(inc),
            "confidence": inc["confidence_score"], "reports": n, "last_activity": inc["last_activity_at"],
            "grid_cell": inc["grid_cell_id"], "reason": a.get("reason"), "freshness": fresh,
            "is_demo": bool(inc["is_demo"]), "missing": a.get("missing", []), "evidence_profile": prof,
            "confidence_assessment": assess_confidence(status, prof, fresh, bool(inc["override_status"]))}


STATUS_RANK = {"CORROBORATED": 0, "UNVERIFIED": 1, "CONTRADICTED": 2}


def _freshness_overall(views):
    states = {v["freshness"]["state"] for v in views}
    return "stale" if "stale" in states else ("never_checked" if "never_checked" in states else ("fresh" if states else None))


def build_summary(p, resp):
    """Concise summary derived ONLY from the investigation result already in `resp` (incidents/sources/answer)."""
    views, intent = resp.get("incidents") or [], p["intent"]
    first_line = (resp.get("answer") or "").split("\n")[0]
    s = {"headline": first_line, "status": None, "confidence": None, "reports": None, "incidents": len(views),
         "freshness": _freshness_overall(views), "why": None, "limitations": [], "other_incidents": []}
    if intent not in ("investigate", "evidence") or not views:
        if intent == "investigate" and resp["provenance"].get("geocode") is None and not views:
            s["limitations"].append("The place could not be resolved, so nothing was investigated.")
        return s
    cat = (p.get("category") or (views[0]["category"])).replace("_", " ")
    place = (p.get("place") or "").title()
    hidden = resp["provenance"].get("no_supporting_evidence_incident")
    if intent == "investigate" and hidden and len(views) == 1 and not views[0]["reports"]:
        s.update(headline=f"No evidence of {cat} near {place} in PRALAY.", reports=0,
                 why="No crowdsourced reports exist and the external sources checked returned no supporting evidence in the time window.",
                 confidence={"level": "LOW", "percentage": None, "reasons": ["absence of evidence is not evidence of absence"],
                             "percentage_note": "No percentage shown: PRALAY's numeric scores are uncalibrated heuristics, not probabilities."})
        s["limitations"] = ["This does NOT prove there is no " + cat + "; PRALAY simply has no evidence for it yet."]
        un = views[0]["evidence_profile"]["sources_unavailable"]
        if un:
            s["limitations"].append("Unavailable sources: " + ", ".join(un))
        return s
    ordered = sorted(views, key=lambda v: (STATUS_RANK.get(v["status"], 3), -v["reports"]))
    pv = ordered[0]
    where = f" near {place}" if place else ""
    head = {"CORROBORATED": f"Yes — {cat}{where} is corroborated by independent evidence (incident #{pv['id']}).",
            "UNVERIFIED": f"Possibly — {cat}{where} has been reported, but there is not yet enough independent evidence to confirm it (incident #{pv['id']}).",
            "CONTRADICTED": f"Not supported — the available reports of {cat}{where} are contradicted by the evidence (incident #{pv['id']})."}
    if intent == "evidence":
        head = {k: f"Incident #{pv['id']} ({pv['title']}) is {k}." for k in STATUS_RANK}
    prof = pv["evidence_profile"]
    rules = prof["rules_met"]
    if pv["status"] == "CORROBORATED" and rules:
        why = "Corroborated because: " + "; ".join(rules) + "."
    else:
        why = pv["reason"] or ""
    s.update(headline=head.get(pv["status"], first_line), status=pv["status"], confidence=pv["confidence_assessment"],
             reports=sum(v["reports"] for v in views), why=why, primary_incident=pv["id"],
             other_incidents=[{"id": v["id"], "status": v["status"], "title": v["title"]} for v in ordered[1:]])
    lim = list(pv.get("missing") or [])
    if pv.get("review_required"):
        lim.insert(0, "Legacy status retained pending evidence/geography review; it has not been verified under the current occurrence policy.")
    if prof["sources_unavailable"]:
        lim.append("Unavailable sources: " + ", ".join(prof["sources_unavailable"]))
    if s["freshness"] != "fresh":
        lim.append(f"Some external evidence is {str(s['freshness']).replace('_', ' ')}.")
    if pv["status"] == "UNVERIFIED":
        lim.append("UNVERIFIED means insufficient independent evidence, not that the report is false.")
    s["limitations"] = lim
    return s


def _sources(iids, limit=12):
    if not iids:
        return []
    return db.query(f"SELECT incident_id, source_name, source_url, publisher, stance, summary, publication_time, retrieval_time "
                    f"FROM evidence WHERE incident_id IN ({','.join('?' * len(iids))}) "
                    f"ORDER BY CASE stance WHEN 'supports' THEN 0 WHEN 'contradicts' THEN 1 WHEN 'neutral' THEN 2 ELSE 3 END, id DESC LIMIT ?",
                    (*iids, limit))


def _lock(key):
    with _glock:
        return _locks.setdefault(key, threading.Lock())


def candidates(p, geo):
    """Match identity and globally validated place before considering proximity."""
    since = utils.utcnow() - timedelta(hours=max(p["window_h"], 72))
    cands = []
    if not p.get("category") or not geo or geo.get("country_code") != "in":
        return cands
    for inc in db.query("SELECT * FROM incidents WHERE category=?", (p["category"],)):
        if p.get("name") and (inc.get("event_name") or policy.storm_name(inc.get("title"))) != p["name"].lower():
            continue
        if (inc.get("place_name") or "").lower() != (p.get("place") or "").lower():
            resolved = utils.resolve_india(inc.get("place_name"))
            if not resolved.get("geo"):
                continue
            ig = resolved["geo"]
            if (inc.get("center_lat") is None or inc.get("center_lon") is None
                    or utils.haversine_km(ig["lat"], ig["lon"], inc["center_lat"], inc["center_lon"]) > 25):
                continue
        if inc.get("country_code") not in (None, "in"):
            continue
        la = utils.parse_time(inc["last_activity_at"])
        near = False
        if geo and inc["center_lat"] is not None:
            near = utils.haversine_km(geo["lat"], geo["lon"], inc["center_lat"], inc["center_lon"]) <= 25
        if near and la and la >= since:
            cands.append(inc)
    return cands


def outside_india(place):
    return utils.resolve_india(place)["state"] == "outside_india"


def investigate(p):
    """Flow B / F. Returns (incident_ids, provenance)."""
    if not p.get("category"):
        return [], {"geocode": None, "incidents": [], "category_required": True}
    resolved = utils.resolve_india(p.get("place"))
    if resolved["state"] == "outside_india":
        return [], {"geocode": None, "incidents": [], "out_of_scope": f"'{p['place'].title()}' is outside India — PRALAY covers India only."}
    geo = resolved.get("geo")
    prov = {"geocode": geo and geo["label"], "incidents": []}
    cands = candidates(p, geo)
    created = None
    if not cands and geo:
        # No shared intelligence: open an investigation-origin incident and query external sources.
        now = db.now_iso()
        created = db.execute(
            "INSERT INTO incidents(title,category,place_name,state_name,center_lat,center_lon,event_time_start,event_time_end,"
            "last_activity_at,origin,status,created_at,updated_at,event_name,hazard,country_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{('Cyclone ' + p['name'].title()) if p.get('name') else p['category'].replace('_', ' ').title()} query — {p['place'].title()}", p["category"], p["place"].title(), geo.get("state"),
             geo["lat"], geo["lon"], utils.iso(utils.utcnow() - timedelta(hours=p["window_h"])), now, now, "investigation", "UNVERIFIED", now, now,
             p.get("name"), p.get("hazard"), "in"))
        cands = [db.one("SELECT * FROM incidents WHERE id=?", (created,))]
    for inc in cands:
        g = external.gather(inc["id"])
        if g["action"] in ("refreshed", "investigated"):
            pipeline.cascade_incident(inc["id"], "chat investigation")
            label = "newly investigated" if g["action"] == "investigated" else (
                "updated with new evidence" if g.get("new_records") else "re-checked (no new evidence)")
        elif g["action"] == "reused":
            label = "reused from shared intelligence"
        else:
            label = "joined in-progress investigation"
        prov["incidents"].append({"incident_id": inc["id"], "action": label, "detail": g})
    if created:
        sup = db.one("SELECT COUNT(*) c FROM evidence WHERE incident_id=? AND stance='supports'", (created,))["c"]
        if not sup:
            # keep the evidence for audit but don't show an empty incident in the feed
            prov["no_supporting_evidence_incident"] = created
    return [i["id"] for i in cands], prov


def answer(q, context=None):
    p = parse(q)
    fresh = False
    if p["intent"] == "followup":
        ctx = context or {}
        if not (ctx.get("category") or ctx.get("place") or ctx.get("name") or ctx.get("hazard")):
            p["intent"] = "help"
        else:
            for k in ("category", "place", "name", "hazard", "historical"):
                p[k] = p.get(k) or ctx.get(k)
            p["intent"], fresh = "investigate", True
            p["norm_key"] = f"{policy.VERSION}|followup|{p['category']}|{p['place']}|{p['name']}|{p['hazard']}"
    with _lock(p["norm_key"]):  # request coalescing for identical simultaneous questions
        resp = _answer(p, fresh)
    resp["summary"] = build_summary(p, resp)
    if p["intent"] == "investigate" and not resp["provenance"].get("category_required"):
        local = bool(resp["incidents"]) and not (resp["provenance"].get("no_supporting_evidence_incident") and not resp["incidents"][0]["reports"])
        resp["live"] = live_search(p, fresh=fresh or not local)
        ls = live_summary(p, resp["live"], local)
        resp["live"]["summary"] = ls
        if not local:  # no shared-intelligence incident: lead with the live investigation, never "nothing happened"
            prior = resp["summary"]
            lims = ls["limitations"] + [x for x in prior.get("limitations", []) if x not in ls["limitations"]]
            if not resp["live"]["ok"]:  # live search failed: keep the evidence-based headline, disclose the failure
                lims.append(f"Live search unavailable: {resp['live']['error']}")
                prior.update(limitations=lims)
            else:
                prior.update(headline=ls["headline"], status=None, reports=0, why=None, limitations=lims)
            if not prior.get("confidence"):
                prior["confidence"] = {"level": "LOW", "percentage": None,
                                       "reasons": ["no PRALAY incident; live external evidence is unverified"],
                                       "percentage_note": "No percentage shown: PRALAY's numeric scores are uncalibrated heuristics, not probabilities."}
        resp["summary"]["live_counts"] = ls["counts"]
    resp["context"] = {k: p.get(k) for k in ("category", "place", "name", "hazard", "historical")}
    return resp


def _answer(p, fresh=False):
    now = utils.utcnow()
    intent = p["intent"]
    resp = {"parsed": p, "generated_at": db.now_iso(), "incidents": [], "sources": [], "items": [], "provenance": {}}
    if intent == "investigate":
        if not p.get("category"):
            resp["answer"] = "Please specify a disaster category before an occurrence can be investigated."
            resp["provenance"] = {"category_required": True, "geocode": None}
            return resp
        row = db.one("SELECT * FROM query_cache WHERE norm_key=?", (p["norm_key"],))
        if p.get("place") is None:  # named-storm / hazard-only query: live search only
            resp["provenance"] = {"query_cache": "not applicable", "incidents": []}
            resp["answer"] = "Searched live news (no specific place given)."
            return resp
        if row and utils.parse_time(row["expires_at"]) > now and not fresh:
            geo = utils.resolve_india(p["place"]).get("geo")
            valid = candidates(p, geo)
            valid_ids = {i["id"] for i in valid}
            iids = [i for i in db.jload(row["incident_ids"], []) if i in valid_ids]
            # Cheap DB re-check so incidents created since the cached answer are not missed (no external calls).
            new = [i["id"] for i in valid if i["id"] not in iids]
            iids = iids + new
            db.execute("UPDATE query_cache SET hit_count=hit_count+1, last_hit_at=? WHERE id=?", (db.now_iso(), row["id"]))
            resp["provenance"] = {"query_cache": "hit", "cached_at": row["refreshed_at"], "hit_count": row["hit_count"] + 1,
                                  "note": "Reused from shared intelligence; place and event identity revalidated. Incident evidence retrieval was not repeated.",
                                  **db.jload(row["result_json"], {}), "new_incidents_since_cache": new}
        else:
            iids, prov = investigate(p)
            resp["provenance"] = {"query_cache": "stale" if row else "miss", **prov}
            exp = utils.iso(now + timedelta(minutes=QUERY_CACHE_TTL_MIN))
            db.execute("INSERT INTO query_cache(norm_key,raw_query,category,place,window_h,result_json,incident_ids,created_at,refreshed_at,expires_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(norm_key) DO UPDATE SET raw_query=excluded.raw_query, result_json=excluded.result_json, "
                       "incident_ids=excluded.incident_ids, refreshed_at=excluded.refreshed_at, expires_at=excluded.expires_at",
                       (p["norm_key"], p["raw"], p["category"], p["place"], p["window_h"], db.jdump(prov), db.jdump(iids),
                        db.now_iso(), db.now_iso(), exp))
        incs = [db.one("SELECT * FROM incidents WHERE id=?", (i,)) for i in iids]
        incs = [i for i in incs if i]
        views = [_inc_view(i) for i in incs]
        resp["incidents"], resp["sources"] = views, _sources(iids)
        hidden = resp["provenance"].get("no_supporting_evidence_incident")
        cat = p["category"].replace("_", " ")
        if not views and resp["provenance"].get("out_of_scope"):
            resp["answer"] = resp["provenance"]["out_of_scope"] + " Not mapped on PRALAY's India grid; live news searched instead."
        elif not views:
            resp["answer"] = (f"I could not resolve '{p['place']}' to a location, so no investigation was run. "
                              "Try a more specific place name.")
        elif hidden and len(views) == 1 and not views[0]["reports"]:
            resp["answer"] = (f"No crowdsourced reports of {cat} near {p['place'].title()} exist in shared intelligence, and the external "
                              f"sources checked ({', '.join(sorted({s['source_name'] for s in resp['sources']}))}) returned no supporting "
                              f"evidence in the time window. This does NOT prove there is no {cat} — it means PRALAY has no evidence for it yet.")
        else:
            lines = [f"Found {len(views)} {cat} incident(s) near {p['place'].title()} in shared intelligence:"]
            for v in views:
                fr = v["freshness"]
                lines.append(f"• #{v['id']} {v['title']} — {v['status']} (evidence score {v['confidence']}/100 — heuristic, not a probability), "
                             f"{v['reports']} report(s), evidence last checked {fr['last_checked'] or 'never'} ({fr['state']}). {v['reason'] or ''}")
            lines.append("Status reflects available evidence only; UNVERIFIED means insufficient independent evidence, not false.")
            resp["answer"] = "\n".join(lines)
    elif intent == "list":
        geo = utils.resolve_india(p["place"]).get("geo") if p["place"] else None
        if p["place"] and not geo:
            resp["answer"] = "The requested place could not be validated inside India; no local incidents were matched."
            return resp
        rows = db.query("SELECT * FROM incidents WHERE origin='reports' OR id IN (SELECT incident_id FROM evidence WHERE stance='supports') "
                        "ORDER BY last_activity_at DESC")
        out = []
        for i in rows:
            if p["category"] and i["category"] != p["category"]:
                continue
            if "active" in p["normalized"] and not external.is_active(i):
                continue
            if geo and i["center_lat"] is not None and utils.haversine_km(geo["lat"], geo["lon"], i["center_lat"], i["center_lon"]) > 50:
                continue
            out.append(_inc_view(i))
        resp["incidents"] = out[:20]
        resp["sources"] = _sources([v["id"] for v in out[:5]], 8)
        where = f" within 50 km of {p['place'].title()}" if geo else ""
        resp["answer"] = (f"{len(out)} {'active ' if 'active' in p['normalized'] else ''}incident(s){where} in shared intelligence."
                          if out else f"No matching incidents{where} are stored in shared intelligence.")
        resp["provenance"] = {"query_cache": "not used (live database listing)", "geocode": geo and geo["label"]}
    elif intent == "evidence":
        iid = p["incident_id"]
        inc = db.one("SELECT * FROM incidents WHERE id=?", (iid,)) if iid else None
        if not inc:
            resp["answer"] = "Please name the incident, e.g. 'What evidence supports incident #3?'"
        else:
            a = db.jload(inc["assessment_json"], {})
            resp["incidents"], resp["sources"] = [_inc_view(inc)], _sources([iid], 20)
            resp["answer"] = "\n".join(
                [f"Incident #{iid} {inc['title']} is {inc['override_status'] or inc['status']}. {a.get('reason', '')}",
                 "Supporting: " + ("; ".join(a.get("supporting", [])[:8]) or "none recorded"),
                 "Contradicting: " + ("; ".join(a.get("contradicting", [])[:5]) or "none recorded"),
                 "Missing: " + ("; ".join(a.get("missing", [])) or "nothing flagged")])
    elif intent == "duplicates":
        rows = db.query("SELECT f.report_id, f.finding, f.related_report_id, f.method, f.created_at FROM media_findings f "
                        "WHERE f.finding IN ('EXACT_DUPLICATE','NEAR_DUPLICATE','PREVIOUSLY_KNOWN_RECYCLED') ORDER BY f.id DESC LIMIT 30")
        resp["items"] = rows
        resp["answer"] = (f"{len(rows)} duplicate/recycled media finding(s):\n" + "\n".join(
            f"• Report #{r['report_id']}: {r['finding'].replace('_', ' ').lower()}"
            + (f" of report #{r['related_report_id']}" if r["related_report_id"] else "") + f" — {r['method']}" for r in rows)
            if rows else "No duplicate or recycled media has been detected among stored reports.")
    elif intent in ("unverified", "contradicted"):
        st = intent.upper()
        rows = db.query("SELECT id, category, claimed_location_text, incident_id, report_confidence, assessment_json, upload_time FROM reports "
                        "WHERE COALESCE(override_status, verification_status)=? AND processing_state IN ('PROCESSED','MAPPED') ORDER BY id DESC LIMIT 30", (st,))
        resp["items"] = [{**{k: v for k, v in r.items() if k != "assessment_json"}, "reason": db.jload(r["assessment_json"], {}).get("reason")} for r in rows]
        resp["answer"] = (f"{len(rows)} {st} report(s):\n" + "\n".join(
            f"• #{r['id']} {r['category']} at {r['claimed_location_text'] or 'unknown'} — {db.jload(r['assessment_json'], {}).get('reason')}" for r in rows)
            if rows else f"No {st} reports among processed reports.")
    elif intent == "grid":
        from . import grid
        cells = [c for c in grid.cells({}) if c["mapped_verified"] or (p["category"] and p["category"] in c["categories"])]
        resp["items"] = cells
        resp["answer"] = (f"{len(cells)} grid cell(s) contain verified (mapped) reports [{grid.provider().label}]:\n" + "\n".join(
            f"• {c['cell_id']}: {c['mapped_verified']} verified / {c['reports']} total reports, incidents {c['incident_ids']}, "
            f"categories {c['categories']}" for c in cells) if cells else
            f"No grid cells contain verified reports yet [{grid.provider().label}]. Reports are mapped only after meeting the eligibility criteria.")
    else:
        resp["answer"] = ("I answer from PRALAY's shared intelligence database. Try: 'Is there flooding in Mohali?', "
                          "'Show active incidents near Chandigarh', 'What evidence supports incident #1?', 'Are there duplicate images?', "
                          "'Which reports remain unverified?', 'Which grid cells contain corroborated incidents?'")
    return resp
