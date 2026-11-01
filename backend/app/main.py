"""PRALAY AI — FastAPI application (Data Collection Engine API + dashboard backend)."""
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import os
import threading

from . import acquisition, chatbot, connectors, db, external, grid, ingest, pipeline, preprocessing, utils, vision
from .config import BASE_DIR, EXTERNAL_LOOKUPS, MAX_UPLOAD_MB, THUMB_DIR, UPLOAD_DIR

TEST_MODE = os.getenv("PRALAY_TEST_MODE") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pralay")

CATEGORIES = ingest.CATEGORIES
STATUSES = ["UNVERIFIED", "CORROBORATED", "CONTRADICTED"]
SOURCE_TYPES = ingest.SOURCE_TYPES

app = FastAPI(title="PRALAY AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    db.init_db()
    grid.config()
    pipeline.start(workers=2)
    threading.Thread(target=connectors.poll_loop, args=(pipeline._stop,), daemon=True, name="pralay-connectors").start()
    # Hyperlocal acquisition: opt-in per provider via PRALAY_<NAME>_POLL_MIN (nothing polls by default).
    threading.Thread(target=acquisition.poll_loop, args=(pipeline._stop,), daemon=True, name="pralay-acquisition").start()
    log.info("PRALAY AI started. Visual AI: %s (%s, mode %s). External lookups: %s. Test mode: %s", vision.status()["state"], vision.status()["provider"], vision.mode(), EXTERNAL_LOOKUPS, TEST_MODE)


@app.on_event("shutdown")
def _shutdown():
    pipeline.stop()


@app.exception_handler(Exception)
async def _err(request, exc):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Internal error: {type(exc).__name__}: {exc}"})


def _url(path):
    if not path:
        return None
    p = Path(path)
    return f"/files/thumbs/{p.name}" if p.parent == THUMB_DIR else f"/files/uploads/{p.name}"


def _media_view(m):
    return {**{k: m[k] for k in ("id", "kind", "original_filename", "mime", "size_bytes", "sha256", "phash", "dhash", "width", "height", "created_at")},
            "url": _url(m["stored_path"]), "thumb_url": _url(m["thumb_path"]),
            "exif": db.jload(m["exif_json"]), "video_meta": db.jload(m["video_meta_json"]),
            "frames": [_url(f) for f in db.jload(m["frame_paths_json"], [])],
            "content_hash": m.get("content_hash"), "phash_variants": db.jload(m.get("phash_variants_json"), {}),
            "frame_phashes": db.jload(m.get("frame_phashes_json"), []), "container_time": m.get("container_time"),
            "client_last_modified": m.get("client_last_modified")}


def _report_view(r, full=False):
    v = dict(r)
    v.pop("ip_address", None)  # stored only for the anti-spam rule; never exposed through the API
    v.pop("device_fingerprint", None)
    v["effective_status"] = r["override_status"] or r["verification_status"]
    v["assessment"] = db.jload(v.pop("assessment_json"))
    v["grid_eligibility"] = db.jload(v.pop("grid_eligibility_json"))
    media = db.query("SELECT * FROM media WHERE report_id=?", (r["id"],))
    v["thumb_url"] = _url(media[0]["thumb_path"]) if media and media[0]["thumb_path"] else None
    v["media_count"] = len(media)
    if full:
        v["media"] = [_media_view(m) for m in media]
        v["findings"] = [{**f, "evidence": db.jload(f.pop("evidence_json"))} for f in db.query("SELECT * FROM media_findings WHERE report_id=? ORDER BY id", (r["id"],))]
        from .forensics import media_classification
        v["media_classification"] = media_classification(v["findings"]) if media else "NO_MEDIA"
        from .forensics import media_conclusion
        mf = [f for f in v["findings"] if f["check_type"] not in ("location_consistency", "time_consistency")]
        v["media_conclusion"] = media_conclusion(mf) if media else None
        lf = next((f for f in v["findings"] if f["check_type"] == "location_consistency"), None)
        v["location_summary"] = (lf["evidence"] or {}).get("summary") if lf else None
        v["location_finding"] = lf and {"finding": lf["finding"], "severity": lf["severity"], "notes": (lf["evidence"] or {}).get("notes"),
                                        "distance_km": (lf["evidence"] or {}).get("distance_km")}
        v["observations"] = db.query("SELECT * FROM observations WHERE report_id=? ORDER BY kind, id", (r["id"],))
        v["visual_analysis"] = [{**x, "output": db.jload(x.pop("output_json"))} for x in db.query("SELECT * FROM visual_analyses WHERE report_id=?", (r["id"],))]
        v["status_history"] = db.query("SELECT * FROM status_history WHERE report_id=? ORDER BY id", (r["id"],))
        v["assessment_history"] = db.query("SELECT * FROM assessments WHERE entity_type='report' AND entity_id=? ORDER BY id", (r["id"],))
        v["review_history"] = db.query("SELECT * FROM review_history WHERE entity_type='report' AND entity_id=? ORDER BY id", (r["id"],))
        v["grid_history"] = db.query("SELECT * FROM grid_mappings WHERE report_id=? ORDER BY id", (r["id"],))
    return v


def _incident_view(i, full=False):
    v = dict(i)
    v["effective_status"] = i["override_status"] or i["status"]
    v["assessment"] = db.jload(v.pop("assessment_json"))
    v["report_count"] = db.one("SELECT COUNT(*) c FROM reports WHERE incident_id=?", (i["id"],))["c"]
    v["mapped_count"] = db.one("SELECT COUNT(*) c FROM reports WHERE incident_id=? AND grid_mapped=1", (i["id"],))["c"]
    v["freshness"] = external.freshness(i)
    v["active"] = bool(external.is_active(i))
    th = db.one("SELECT m.thumb_path FROM media m JOIN reports r ON r.id=m.report_id WHERE r.incident_id=? AND m.thumb_path IS NOT NULL ORDER BY m.id LIMIT 1", (i["id"],))
    v["thumb_url"] = _url(th["thumb_path"]) if th else None
    if full:
        v["reports"] = [_report_view(r) for r in db.query("SELECT * FROM reports WHERE incident_id=? ORDER BY id", (i["id"],))]
        v["evidence"] = db.query("SELECT * FROM evidence WHERE incident_id=? ORDER BY CASE stance WHEN 'supports' THEN 0 WHEN 'contradicts' THEN 1 WHEN 'neutral' THEN 2 ELSE 3 END, id", (i["id"],))
        from .fusion import is_trusted
        for e in v["evidence"]:  # outlet reputation label only; does not imply this article supports the incident
            e["is_trusted"] = e["source_origin"] == "news" and is_trusted(e["publisher"])
        v["assessment_history"] = db.query("SELECT * FROM assessments WHERE entity_type='incident' AND entity_id=? ORDER BY id", (i["id"],))
        v["review_history"] = db.query("SELECT * FROM review_history WHERE entity_type='incident' AND entity_id=? ORDER BY id", (i["id"],))
    return v


# ------------------------------------------------------------------ meta
@app.get("/api/health")
def health():
    return {"ok": True, "db": str(db.DB_PATH), "time": db.now_iso()}


@app.get("/api/meta")
def meta():
    return {"categories": CATEGORIES, "statuses": STATUSES, "source_types": SOURCE_TYPES, "max_upload_mb": MAX_UPLOAD_MB,
            "vision": vision.status(), "connectors": [{k: c[k] for k in ("name", "platform", "state")} for c in connectors.registry()],
            "test_mode": TEST_MODE,
            "capabilities": {"vision_ai": vision.status()["state"], "vision_paid_services": False, "external_lookups": EXTERNAL_LOOKUPS, "database": "SQLite (fallback; PostgreSQL not available)",
                             "cache": "Database-backed (Redis not available)", "reverse_image_search": False,
                             "official_grid": False},
            "grid": grid.describe(), "pipeline_stages": pipeline.STAGES}


@app.get("/api/summary")
def summary():
    q = lambda sql, p=(): db.one(sql, p)["c"]
    incs = db.query("SELECT * FROM incidents WHERE origin='reports' OR id IN (SELECT incident_id FROM evidence WHERE stance='supports')")
    eff = lambda i: i["override_status"] or i["status"]
    return {
        "version": pipeline.version(),
        "reports": {
            "received_total": q("SELECT COUNT(*) c FROM reports"),
            "awaiting": q("SELECT COUNT(*) c FROM reports WHERE processing_state='RECEIVED'"),
            "processing": q("SELECT COUNT(*) c FROM reports WHERE processing_state='PROCESSING'"),
            "processed": q("SELECT COUNT(*) c FROM reports WHERE processing_state IN ('PROCESSED','MAPPED')"),
            "failed": q("SELECT COUNT(*) c FROM reports WHERE processing_state='FAILED_NEEDS_REVIEW'"),
            "unverified": q("SELECT COUNT(*) c FROM reports WHERE processing_state IN ('PROCESSED','MAPPED') AND COALESCE(override_status,verification_status)='UNVERIFIED'"),
            "corroborated": q("SELECT COUNT(*) c FROM reports WHERE processing_state IN ('PROCESSED','MAPPED') AND COALESCE(override_status,verification_status)='CORROBORATED'"),
            "contradicted": q("SELECT COUNT(*) c FROM reports WHERE processing_state IN ('PROCESSED','MAPPED') AND COALESCE(override_status,verification_status)='CONTRADICTED'"),
            "mapped": q("SELECT COUNT(*) c FROM reports WHERE grid_mapped=1"),
            "duplicates": q("SELECT COUNT(*) c FROM reports WHERE is_duplicate_of IS NOT NULL"),
            "demo": q("SELECT COUNT(*) c FROM reports WHERE is_demo=1"),
        },
        "incidents": {
            "total": len(incs), "active": sum(1 for i in incs if external.is_active(i)),
            "corroborated": sum(1 for i in incs if eff(i) == "CORROBORATED"),
            "unverified": sum(1 for i in incs if eff(i) == "UNVERIFIED"),
            "contradicted": sum(1 for i in incs if eff(i) == "CONTRADICTED"),
        },
        "grid": {"cells_with_reports": q("SELECT COUNT(DISTINCT grid_cell_id) c FROM reports WHERE grid_cell_id IS NOT NULL"),
                 "cells_verified": q("SELECT COUNT(DISTINCT grid_cell_id) c FROM reports WHERE grid_mapped=1"), "label": grid.provider().label},
        "recent_incidents": [_incident_view(i) for i in db.query("SELECT * FROM incidents WHERE origin='reports' ORDER BY updated_at DESC LIMIT 5")],
        "processing": pipeline.processing_stats(),
        "sources": _sources_view(),
        "vision": vision.status(),
        "generated_at": db.now_iso(),
    }


# ------------------------------------------------------------------ reports
@app.post("/api/reports", status_code=201)
async def submit_report(
    request: Request,
    category: str = Form(...), description: str = Form(""), claimed_location: str = Form(""),
    claimed_lat: Optional[float] = Form(None), claimed_lon: Optional[float] = Form(None),
    browser_lat: Optional[float] = Form(None), browser_lon: Optional[float] = Form(None), browser_accuracy_m: Optional[float] = Form(None),
    claimed_event_time: str = Form(""), source_type: str = Form("citizen"), source_platform: str = Form(""),
    source_url: str = Form(""), original_publication_time: str = Form(""), reporter_ref: str = Form(""),
    is_demo: bool = Form(False), files: List[UploadFile] = File(default=[]),
    file_last_modified: List[str] = Form(default=[]), device_fingerprint: str = Form(""),
):
    import re as _re
    from .fusion import ban_active
    ip = request.client.host if request.client else None
    fp = device_fingerprint.strip() if _re.fullmatch(r"fp_[0-9a-f]{1,16}", device_fingerprint.strip()) else None  # malformed -> none
    if ban_active("fp", fp):
        raise HTTPException(403, "This device has been blocked due to multiple fake reports.")
    if ban_active("ip", ip):
        raise HTTPException(403, "Your IP has been blocked due to multiple fake reports.")
    if (claimed_lat is None) != (claimed_lon is None) or (browser_lat is None) != (browser_lon is None):
        raise HTTPException(422, "Coordinates need both latitude and longitude")
    blobs = []
    for i, f in enumerate(files):
        data = await f.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
        blobs.append((data, f.filename, file_last_modified[i] if i < len(file_last_modified) and file_last_modified[i] else None))
    try:
        rid, prio, info = ingest.create_report(
            category=category, description=description, claimed_location=claimed_location, claimed_lat=claimed_lat, claimed_lon=claimed_lon,
            browser_lat=browser_lat, browser_lon=browser_lon, browser_accuracy_m=browser_accuracy_m, claimed_event_time=claimed_event_time,
            source_type=source_type, source_platform=source_platform, source_url=source_url,
            original_publication_time=original_publication_time, reporter_ref=reporter_ref, is_demo=is_demo, files=blobs)
    except ingest.IngestError as e:
        raise HTTPException(422, str(e))
    # recorded for the 3-strike anti-spam rule (fusion.strike); never returned by the API
    db.execute("UPDATE reports SET ip_address=?, device_fingerprint=? WHERE id=?", (ip, fp, rid))
    return {"id": rid, "processing_state": "RECEIVED", "priority": prio, "files": info, "received_at": db.now_iso()}


@app.get("/api/reports")
def list_reports(state: Optional[str] = None, status: Optional[str] = None, incident_id: Optional[int] = None,
                 category: Optional[str] = None, limit: int = Query(100, le=500)):
    where, p = ["1=1"], []
    if state:
        where.append("processing_state=?"); p.append(state)
    if status:
        where.append("COALESCE(override_status,verification_status)=?"); p.append(status)
    if incident_id:
        where.append("incident_id=?"); p.append(incident_id)
    if category:
        where.append("category=?"); p.append(category)
    return [_report_view(r) for r in db.query(f"SELECT * FROM reports WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*p, limit))]


@app.get("/api/reports/{rid}")
def get_report(rid: int):
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "Report not found")
    return _report_view(r, full=True)


class Review(BaseModel):
    status: Optional[str] = Field(None, description="UNVERIFIED|CORROBORATED|CONTRADICTED, or null to clear the override")
    reason: str = Field(..., min_length=5, max_length=1000)
    reviewer: str = Field("analyst", max_length=80)


def _review(entity, eid, body: Review):
    table = "reports" if entity == "report" else "incidents"
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, f"{entity} not found")
    if body.status is not None and body.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES} or null")
    prev = row["override_status"] or (row["verification_status"] if entity == "report" else row["status"])
    db.execute(f"UPDATE {table} SET override_status=?, override_reason=?, updated_at=? WHERE id=?", (body.status, body.reason, db.now_iso(), eid))
    db.execute("INSERT INTO review_history(entity_type,entity_id,previous_status,new_status,reason,reviewer,created_at) VALUES(?,?,?,?,?,?,?)",
               (entity, eid, prev, body.status or "(override cleared)", body.reason, body.reviewer, db.now_iso()))
    iid = row["incident_id"] if entity == "report" else eid
    if iid:
        pipeline.cascade_incident(iid, f"human review of {entity} #{eid}")
    elif entity == "report":
        grid.map_report(eid)
    pipeline.bump()
    return {"ok": True}


@app.post("/api/reports/{rid}/review")
def review_report(rid: int, body: Review):
    return _review("report", rid, body)


@app.get("/api/users")
def list_users():
    return db.query("SELECT reporter_name, credibility_score, reports_count FROM users ORDER BY credibility_score DESC, reporter_name")


@app.get("/api/users/{reporter_name}")
def user_profile(reporter_name: str):
    """Reporter profile: credibility (derived from report outcomes, see fusion.reporter_credibility) + report history."""
    ref = " ".join(reporter_name.lower().split())
    reps = db.query("SELECT id, category, claimed_location_text, COALESCE(override_status, verification_status) status, report_confidence, "
                    "json_extract(assessment_json,'$.fields.needs_review') needs_review, upload_time FROM reports "
                    "WHERE lower(trim(reporter_ref))=? ORDER BY id DESC", (ref,))
    if not ref or not reps:
        raise HTTPException(404, "No reports from this reporter")
    import hashlib
    u = db.one("SELECT * FROM users WHERE id=?", (hashlib.sha256(ref.encode()).hexdigest()[:16],))
    return {"reporter_name": (u or {}).get("reporter_name") or reporter_name.strip(),
            "credibility_score": u["credibility_score"] if u else None, "total_reports": len(reps),
            "reports": [{"id": x["id"], "category": x["category"], "location": x["claimed_location_text"], "status": x["status"],
                         "confidence": x["report_confidence"], "claim_mismatch": bool(x["needs_review"]), "submitted": x["upload_time"]} for x in reps]}


@app.post("/api/reports/{rid}/fetch-evidence")
def fetch_evidence(rid: int):
    """Fetch same-day Google News for this report, validate, store matches, re-score. Returns every article evaluated."""
    from . import fusion, news_ingestion
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "Report not found")
    before = r["report_confidence"]
    try:
        matched, evaluated = news_ingestion.refresh(rid)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    if r["incident_id"]:
        pipeline.cascade_incident(r["incident_id"], f"news evidence refresh for report #{rid}")
    else:
        fusion.assess_report(rid, f"news evidence refresh for report #{rid}")
    pipeline.bump()
    after = db.one("SELECT report_confidence, verification_status FROM reports WHERE id=?", (rid,))
    return {"report_id": rid, "confidence_before": before, "confidence_after": after["report_confidence"],
            "verification_status": after["verification_status"], "matched": matched, "evaluated": evaluated}


@app.post("/api/reports/{rid}/reprocess")
def reprocess(rid: int):
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "Report not found")
    if r["processing_state"] in ("PROCESSING", "RECEIVED"):
        raise HTTPException(409, "Report is already queued or being processed")
    with db.tx() as c:
        c.execute("UPDATE reports SET attempts=0, next_attempt_at=NULL WHERE id=?", (rid,))
        c.execute("DELETE FROM media_findings WHERE report_id=?", (rid,))
        c.execute("DELETE FROM observations WHERE report_id=?", (rid,))
        c.execute("DELETE FROM visual_analyses WHERE report_id=?", (rid,))
        c.execute("UPDATE reports SET processing_state='RECEIVED', error=NULL, is_duplicate_of=NULL, updated_at=? WHERE id=?", (db.now_iso(), rid))
        c.execute("INSERT INTO status_history(report_id,state,stage,note,created_at) VALUES(?,?,?,?,?)", (rid, "RECEIVED", "QUEUED", "manual reprocess", db.now_iso()))
    pipeline.bump()
    return {"ok": True}


# ------------------------------------------------------------------ incidents
@app.get("/api/incidents")
def list_incidents(category: Optional[str] = None, status: Optional[str] = None, active: Optional[bool] = None, since: Optional[str] = None):
    where, p = ["(origin='reports' OR id IN (SELECT incident_id FROM evidence WHERE stance='supports'))"], []
    if category:
        where.append("category=?"); p.append(category)
    if status:
        where.append("COALESCE(override_status,status)=?"); p.append(status)
    if since:
        where.append("last_activity_at>=?"); p.append(_ts(since))
    out = [_incident_view(i) for i in db.query(f"SELECT * FROM incidents WHERE {' AND '.join(where)} ORDER BY last_activity_at DESC", p)]
    if active is not None:
        out = [i for i in out if i["active"] == active]
    return out


@app.get("/api/incidents/{iid}")
def get_incident(iid: int):
    i = db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    if not i:
        raise HTTPException(404, "Incident not found")
    return _incident_view(i, full=True)


@app.post("/api/incidents/{iid}/review")
def review_incident(iid: int, body: Review):
    return _review("incident", iid, body)


@app.post("/api/incidents/{iid}/refresh")
def refresh_incident(iid: int):
    if not db.one("SELECT id FROM incidents WHERE id=?", (iid,)):
        raise HTTPException(404, "Incident not found")
    g = external.gather(iid, force=True)
    pipeline.cascade_incident(iid, "manual evidence refresh")
    return g


# ------------------------------------------------------------------ grid
@app.get("/api/grid/config")
def grid_config():
    return grid.describe()


class GridCfg(BaseModel):
    resolution_deg: float


@app.post("/api/grid/config")
def set_grid(body: GridCfg):
    try:
        grid.reconfigure(body.resolution_deg)
        cfg = grid.describe()
    except ValueError as e:
        raise HTTPException(422, str(e))
    pipeline.bump()
    return cfg


def _bbox(s):
    if not s:
        return None
    try:
        v = [float(x) for x in s.split(",")]
        assert len(v) == 4
        return v
    except (ValueError, AssertionError):
        raise HTTPException(422, "bbox must be lat_min,lon_min,lat_max,lon_max")


@app.get("/api/grid/cells")
def grid_cells(category: Optional[str] = None, status: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
               bbox: Optional[str] = None, mapped_only: bool = False):
    return {"label": grid.provider().label, "config": grid.describe(),
            "cells": grid.cells({"category": category, "status": status, "since": _ts(since), "until": _ts(until), "bbox": _bbox(bbox), "mapped_only": mapped_only})}


def _ts(v):
    """Normalise client timestamps to the stored UTC ISO format so string comparison is correct."""
    if not v:
        return None
    t = utils.parse_time(v)
    if t is None:
        raise HTTPException(422, f"Invalid timestamp: {v}")
    return utils.iso(t)


@app.get("/api/grid/cells/{cell_id}")
def grid_cell(cell_id: str):
    return grid.cell_detail(cell_id)


@app.get("/api/grid/lattice")
def grid_lattice(bbox: str):
    return grid.lattice(_bbox(bbox))


# ------------------------------------------------------------------ sources / AI / connectors / archive
EXPECTED_SOURCES = {"ndma_sachet": "NDMA SACHET (CAP alerts)", "google_news": "Google News RSS", "open_meteo": "Open-Meteo",
                    "gdelt": "GDELT DOC 2.0", "nominatim": "OSM Nominatim geocoder", "mastodon": "Mastodon public API"}


def _sources_view():
    rows = {r["source"]: r for r in db.query("SELECT * FROM source_status")}
    now = utils.utcnow()
    out = []
    for key, label in EXPECTED_SOURCES.items():
        r = rows.get(key)
        if not EXTERNAL_LOOKUPS:
            state = "DISABLED"
        elif not r:
            state = "NOT_USED_YET"
        elif r["cooldown_until"] and utils.parse_time(r["cooldown_until"]) > now:
            state = "COOLDOWN"
        elif r["consecutive_failures"]:
            state = "FAILING"
        else:
            state = "OK"
        out.append({"source": key, "label": label, "state": state, **({k: r[k] for k in r if k != "source"} if r else {})})
    return out


@app.get("/api/sources")
def sources():
    return {"external_lookups": EXTERNAL_LOOKUPS, "sources": _sources_view(), "simulated_failures": sorted(utils.SIMULATED_FAILURES)}


@app.get("/api/vision/status")
def vision_status():
    return vision.status()


@app.post("/api/vision/validate")
def vision_validate():
    """Re-check the local provider now (is Ollama running, is the model installed). Sends no media."""
    return vision.validate()


@app.get("/api/connectors")
def list_connectors():
    return connectors.registry()


class ConnectorRun(BaseModel):
    tags: Optional[List[str]] = None
    limit: int = Field(20, ge=1, le=40)
    wait: bool = Field(False, description="true = run synchronously and return the result (used by tests)")
    dry_run: bool = Field(False, description="apply all gates but download/create nothing; returns candidates")
    instance: Optional[str] = None


@app.post("/api/connectors/{name}/run")
def run_connector(name: str, body: ConnectorRun = ConnectorRun()):
    try:
        if name == "mastodon_public_tags":
            tags = [t.strip().lstrip("#").lower() for t in (body.tags or []) if t.strip()] or None
            if body.wait or body.dry_run:
                return connectors.run(name, tags=tags, limit=body.limit, dry_run=body.dry_run, instance=body.instance)
            # Geocoding is rate-limited (1 req/s, OSM policy), so UI runs happen in the background.
            def _bg():
                try:
                    connectors.run(name, tags=tags, limit=body.limit, instance=body.instance)
                finally:
                    pipeline.bump()
            threading.Thread(target=_bg, daemon=True, name="pralay-connector-run").start()
            return {"status": "started", "detail": "Running in the background; see the runs table for progress."}
        return connectors.run(name)
    except KeyError:
        raise HTTPException(404, "Unknown connector")


@app.get("/api/acquisition/sources")
def acquisition_sources():
    """Per-source health. Credentials are reported only as missing names, never values."""
    return acquisition.registry()


@app.get("/api/acquisition/records")
def acquisition_records(provider: Optional[str] = None, kind: Optional[str] = None, district: Optional[str] = None,
                        locality: Optional[str] = None, limit: int = 500):
    return acquisition.records({"provider": provider, "kind": kind, "district": district, "locality": locality}, limit)


@app.post("/api/analyze-image")
def analyze_image_ep(file: UploadFile = File(...), claimed_category: Optional[str] = Form(None), claimed_description: Optional[str] = Form(None)):
    """Quick image check (nothing is stored). Verdict is a signal, never a statement of authenticity."""
    import io, tempfile, uuid
    from PIL import Image as _Img, UnidentifiedImageError
    from .image_analysis import analyze_image
    data = file.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Image larger than {MAX_UPLOAD_MB} MB")
    try:
        with _Img.open(io.BytesIO(data)) as im:
            im.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise HTTPException(422, "Not a readable image")
    ext = (Path(file.filename or "").suffix.lower() or ".img")[:6]
    tmp = Path(tempfile.gettempdir()) / f"pralay_analyze_{uuid.uuid4().hex}{ext}"
    tmp.write_bytes(data)
    try:
        return analyze_image(str(tmp), (claimed_category or "").strip() or None, (claimed_description or "").strip() or None)
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/api/gis/imd-warnings")
def gis_imd_warnings(hours: int = 24):
    return acquisition.imd_warning_layer(max(1, min(hours, 168)))


@app.post("/api/acquisition/{name}/run")
def acquisition_run(name: str, dataset: Optional[str] = None):
    try:
        return acquisition.run(name, dataset)
    except KeyError:
        raise HTTPException(404, "Unknown provider/dataset")


@app.get("/api/acquisition/assets/{fname}")
def acquisition_asset(fname: str):
    import re as _re
    from fastapi.responses import FileResponse
    from .config import DATA_DIR
    if not _re.fullmatch(r"[0-9a-f]{64}\.(png|tif)", fname):
        raise HTTPException(404, "Not found")
    f = DATA_DIR / "provider_assets" / fname
    if not f.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(f)


@app.get("/api/connectors/runs")
def connector_runs():
    return db.query("SELECT * FROM connector_runs ORDER BY id DESC LIMIT 30")


@app.get("/api/processing/stats")
def processing_stats():
    return pipeline.processing_stats()


@app.post("/api/archive", status_code=201)
async def add_archive(file: UploadFile = File(...), source_name: str = Form(...), first_published: str = Form(...),
                      source_url: str = Form(""), description: str = Form(""), is_demo: bool = Form(False)):
    """Register known historical media (e.g. a viral photo from a past event) for the recycled-media check.
    Only hashes are kept; the file itself is not stored."""
    if utils.parse_time(first_published) is None:
        raise HTTPException(422, "first_published must be a valid timestamp")
    if source_url and not source_url.startswith(("http://", "https://")):
        raise HTTPException(422, "source_url must be an http(s) URL")
    data = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    import hashlib
    import io
    from PIL import Image, ImageOps
    if preprocessing.sniff_mime(data[:16]) not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(422, "Archive entries must be images (JPEG/PNG/WEBP)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            up = ImageOps.exif_transpose(im)
            ph, ch = preprocessing.phash(up), preprocessing.content_hash(up)
    except Exception as e:
        raise HTTPException(422, f"Image could not be decoded: {e}")
    aid = db.execute("INSERT INTO archive_media(sha256,phash,content_hash,source_name,source_url,first_published,description,is_demo,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (hashlib.sha256(data).hexdigest(), ph, ch, source_name[:120], source_url or None,
                                                    utils.iso(utils.parse_time(first_published)), description[:500], 1 if is_demo else 0, db.now_iso()))
    return {"id": aid, "phash": ph}


@app.get("/api/archive")
def list_archive():
    return db.query("SELECT id, source_name, source_url, first_published, description, is_demo, created_at FROM archive_media ORDER BY id DESC")


class SimFail(BaseModel):
    sources: List[str] = []


class Stall(BaseModel):
    match: str = Field(..., min_length=3)
    stage: str
    seconds: int = Field(..., ge=1, le=120)
    times: int = Field(1, ge=1, le=5)


@app.post("/api/test/stall")
def test_stall(body: Stall):
    """TEST MODE ONLY: make the worker hang at `stage` for reports whose description contains `match`."""
    if not TEST_MODE:
        raise HTTPException(403, "Only available when PRALAY_TEST_MODE=1")
    if body.stage not in pipeline.STAGES:
        raise HTTPException(422, f"stage must be one of {pipeline.STAGES}")
    pipeline.TEST_STALL.clear()
    pipeline.TEST_STALL.update(match=body.match, stage=body.stage, seconds=body.seconds, remaining=body.times)
    return {"stall": pipeline.TEST_STALL, "lease_sec": pipeline.LEASE_SEC, "lease_check_sec": pipeline.LEASE_CHECK_SEC}


class VisionMode(BaseModel):
    mode: str


@app.post("/api/test/vision-mode")
def test_vision_mode(body: VisionMode):
    """TEST MODE ONLY: switch inline/deferred visual analysis at runtime."""
    if not TEST_MODE:
        raise HTTPException(403, "Only available when PRALAY_TEST_MODE=1")
    try:
        vision.set_mode(body.mode)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"mode": vision.mode()}


@app.post("/api/test/simulate-failure")
def simulate_failure(body: SimFail):
    """TEST MODE ONLY: force named sources to fail so failure handling can be verified. Disabled in normal runs."""
    if not TEST_MODE:
        raise HTTPException(403, "Only available when PRALAY_TEST_MODE=1")
    utils.SIMULATED_FAILURES.clear()
    utils.SIMULATED_FAILURES.update(body.sources)
    return {"simulated_failures": sorted(utils.SIMULATED_FAILURES)}


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=200)):
    g = utils.geocode(q)
    if not g:
        raise HTTPException(404, "Place not found (or geocoder unreachable)")
    return g


# ------------------------------------------------------------------ chat / cache / pipeline
class Chat(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    context: Optional[dict] = None


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=100)):
    """Global dashboard search over stored incidents and reports (id, place, type, status, text)."""
    s = q.strip().lower().lstrip("#")
    like = f"%{s}%"
    num = int(s) if s.isdigit() else -1
    incs = db.query("SELECT * FROM incidents WHERE (origin='reports' OR id IN (SELECT incident_id FROM evidence WHERE stance='supports')) AND "
                    "(id=? OR lower(title) LIKE ? OR lower(COALESCE(place_name,'')) LIKE ? OR lower(COALESCE(state_name,'')) LIKE ? OR "
                    "lower(category) LIKE ? OR lower(COALESCE(override_status,status)) LIKE ?) ORDER BY last_activity_at DESC LIMIT 20",
                    (num, like, like, like, like, like))
    reps = db.query("SELECT * FROM reports WHERE id=? OR lower(COALESCE(claimed_location_text,'')) LIKE ? OR lower(category) LIKE ? OR "
                    "lower(COALESCE(description,'')) LIKE ? OR lower(COALESCE(override_status,verification_status)) LIKE ? ORDER BY id DESC LIMIT 20",
                    (num, like, like, like, like))
    return {"query": q, "incidents": [_incident_view(i) for i in incs], "reports": [_report_view(r) for r in reps]}


@app.post("/api/chat")
def chat(body: Chat):
    resp = chatbot.answer(body.query, body.context)
    from . import news_ingestion, openrouter
    news = []
    if isinstance(resp, dict) and resp.get("summary"):
        try:  # live external context (free Google News RSS, last 24 h); failure leaves the database-only path intact
            news = news_ingestion.fetch_articles(body.query.strip().rstrip("?"), max_results=3)
        except Exception as e:
            log.warning("chat news fetch failed: %s", e)
        resp["external_news"] = news
    ai = openrouter.phrase(body.query, resp, news) if isinstance(resp, dict) and resp.get("summary") else None
    if ai:  # reworded from PRALAY's own context only; template answer kept for audit
        resp["summary"] = {**resp["summary"], "template_headline": resp["summary"].get("headline"),
                           "headline": ai["text"], "ai_phrased_by": f"OpenRouter {ai['model']} (free model; may include the model's general knowledge; not evidence)"}
    return resp


@app.get("/api/cache/stats")
def cache_stats():
    return {
        "query_cache": db.query("SELECT norm_key, raw_query, hit_count, refreshed_at, expires_at, last_hit_at FROM query_cache ORDER BY refreshed_at DESC LIMIT 30"),
        "http_cache": db.one("SELECT COUNT(*) entries, COALESCE(SUM(hit_count),0) hits FROM http_cache"),
        "geocode_cache": db.one("SELECT COUNT(*) entries FROM geocode_cache"),
        "incident_freshness": [{"id": i["id"], "title": i["title"], **external.freshness(i)} for i in db.query("SELECT * FROM incidents ORDER BY id DESC LIMIT 30")],
    }


@app.get("/api/pipeline")
def pipeline_status():
    return {"stages": pipeline.STAGES,
            "queue": db.query("SELECT id, category, priority, processing_state, pipeline_stage, upload_time FROM reports WHERE processing_state IN ('RECEIVED','PROCESSING') ORDER BY priority, id"),
            "recent": db.query("SELECT h.*, r.category FROM status_history h JOIN reports r ON r.id=h.report_id ORDER BY h.id DESC LIMIT 60"),
            "events": db.query("SELECT * FROM system_events ORDER BY id DESC LIMIT 20")}


# ------------------------------------------------------------------ files + SPA
app.mount("/files/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/files/thumbs", StaticFiles(directory=THUMB_DIR), name="thumbs")

DIST = BASE_DIR.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
