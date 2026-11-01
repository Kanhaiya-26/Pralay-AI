"""Incident Detection & Clustering Engine (Module 3).

A report joins an existing incident only when BOTH geographic proximity and time proximity hold
(keywords alone never merge reports). Text and visual similarity raise the association score.
"""
from . import db, utils, fusion, evidence_policy as policy
from .config import CLUSTER_RADIUS_KM, CLUSTER_TIME_WINDOW_H
from .preprocessing import hamming

CATEGORY_GROUP = {"fire": "fire", "smoke": "fire"}
LABELS = {"flood": "Flooding", "fire": "Fire", "smoke": "Fire / smoke", "landslide": "Landslide",
          "building_damage": "Building damage", "road_blockage": "Road blockage",
          "infrastructure_damage": "Infrastructure damage", "cyclone_storm": "Cyclone / storm", "other": "Disaster event"}


def group(cat):
    return CATEGORY_GROUP.get(cat, cat)


def _report_time(r):
    return utils.parse_time(r.get("claimed_event_time")) or utils.parse_time(r.get("exif_capture_time")) \
        or utils.parse_time(r.get("original_publication_time")) or utils.parse_time(r["upload_time"])


def association_point(r):
    """The location a report *claims* to describe (claim geocode), else investigated location."""
    if r.get("claimed_lat") is not None:
        return r["claimed_lat"], r["claimed_lon"]
    if r.get("investigated_lat") is not None:
        return r["investigated_lat"], r["investigated_lon"]
    return None


def find_or_create_incident(report, phashes):
    pt = association_point(report)
    if not pt:
        return None, {"reason": "No location available — report not associated with an incident."}
    t = _report_time(report)
    best, best_score, best_detail = None, 0.0, None
    toks = utils.tokens(report.get("description"))
    name = policy.storm_name(report.get("description")) if report["category"] == "cyclone_storm" else None
    for inc in db.query("SELECT * FROM incidents WHERE center_lat IS NOT NULL"):
        if fusion.requires_evidence_review(inc):
            continue  # do not attach new submissions to unreviewed legacy incidents
        inc_name = inc.get("event_name") or policy.storm_name(inc.get("title"))
        if (name or inc_name) and name != inc_name:
            continue
        if group(inc["category"]) != group(report["category"]):
            continue
        d = utils.haversine_km(pt[0], pt[1], inc["center_lat"], inc["center_lon"])
        if d > CLUSTER_RADIUS_KM:
            continue
        start = utils.parse_time(inc["event_time_start"]) or utils.parse_time(inc["created_at"])
        end = utils.parse_time(inc["event_time_end"]) or start
        dt = 0 if start <= t <= end else min(utils.hours_between(t, start), utils.hours_between(t, end))
        if dt > CLUSTER_TIME_WINDOW_H:
            continue
        others = db.query("SELECT description FROM reports WHERE incident_id=?", (inc["id"],))
        txt = max([utils.jaccard(toks, utils.tokens(o["description"])) for o in others] + [0])
        vis = 0.0
        if phashes:
            for m in db.query("SELECT m.phash FROM media m JOIN reports r ON r.id=m.report_id WHERE r.incident_id=? AND m.phash IS NOT NULL", (inc["id"],)):
                if any(hamming(p, m["phash"]) is not None and hamming(p, m["phash"]) <= 10 for p in phashes):
                    vis = 1.0
                    break
        score = 0.5 * (1 - d / CLUSTER_RADIUS_KM) + 0.3 * (1 - dt / CLUSTER_TIME_WINDOW_H) + 0.1 * txt + 0.1 * vis
        if score > best_score:
            best, best_score = inc, score
            best_detail = {"distance_km": round(d, 2), "time_gap_h": round(dt, 1), "text_similarity": round(txt, 2),
                           "visual_similarity": vis, "score": round(score, 3)}
    if best:
        return best["id"], {"action": "associated", "incident_id": best["id"], **best_detail,
                            "rule": f"same category group, within {CLUSTER_RADIUS_KM} km and {CLUSTER_TIME_WINDOW_H} h"}
    place = report.get("claimed_location_text")
    state = report.get("claimed_state")
    if not place:
        rv = utils.reverse_state(pt[0], pt[1]) or {}
        place, state = rv.get("place") or f"{pt[0]:.3f}, {pt[1]:.3f}", rv.get("state")
    now = db.now_iso()
    iid = db.execute(
        "INSERT INTO incidents(title,category,place_name,state_name,center_lat,center_lon,event_time_start,event_time_end,"
        "last_activity_at,origin,status,is_demo,created_at,updated_at,event_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"{LABELS.get(report['category'], 'Event')} — {place}", report["category"], place, state, pt[0], pt[1],
         utils.iso(t), utils.iso(t), now, "reports", "UNVERIFIED", report.get("is_demo", 0), now, now, name))
    return iid, {"action": "created", "incident_id": iid, "reason": "No existing incident within the space/time window."}


def refresh_incident_geometry(iid):
    rows = db.query("SELECT * FROM reports WHERE incident_id=?", (iid,))
    pts = [association_point(r) for r in rows if r["verification_status"] != "CONTRADICTED"]
    pts = [p for p in pts if p]
    times = [_report_time(r) for r in rows]
    now = db.now_iso()
    sets, params = ["last_activity_at=?", "updated_at=?"], [now, now]
    if pts:
        sets += ["center_lat=?", "center_lon=?"]
        params += [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]
    if times:
        sets += ["event_time_start=?", "event_time_end=?"]
        params += [utils.iso(min(times)), utils.iso(max(times))]
    db.execute(f"UPDATE incidents SET {', '.join(sets)} WHERE id=?", (*params, iid))
