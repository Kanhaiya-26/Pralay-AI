"""Active Disaster Processing (automatic pipeline) + Background Monitoring (Module 10).

The queue is persistent: reports in state RECEIVED in the database ARE the queue, ordered by
priority (reports near active incidents first) then arrival. Worker threads claim reports
atomically, so a restart never loses work (stuck PROCESSING reports are re-queued at startup).

Workflow (processing_state) is tracked separately from verification_status:
  RECEIVED -> PROCESSING -> PROCESSED (verification UNVERIFIED/CORROBORATED/CONTRADICTED)
                         -> MAPPED (eligible verified report placed on the grid)
                         -> FAILED_NEEDS_REVIEW
Stage order note: location/time investigation runs BEFORE incident association because
association requires a location; the spec's other stage order is preserved.
"""
import logging
import threading
import time
import traceback
from pathlib import Path

from . import clustering, db, external, forensics, fusion, grid, location_time, preprocessing, utils, vision
from .config import MONITOR_INTERVAL_SEC, WORKER_POLL_SEC

log = logging.getLogger("pralay.pipeline")
STAGES = ["VALIDATION", "METADATA_EXTRACTION", "DUPLICATE_CHECK", "VISUAL_ANALYSIS", "MEDIA_AUTHENTICITY",
          "LOCATION_TIME_INVESTIGATION", "INCIDENT_ASSOCIATION", "EXTERNAL_EVIDENCE", "CREDIBILITY_ASSESSMENT",
          "GRID_ELIGIBILITY_CHECK", "GRID_MAPPING", "DASHBOARD_UPDATE"]

_version = {"v": 0}
_vlock = threading.Lock()
_stop = threading.Event()
_incident_lock = threading.Lock()   # serialises find-or-create so racing workers cannot create duplicate incidents
MAX_ATTEMPTS = 3
LEASE_SEC = int(__import__("os").getenv("PRALAY_LEASE_SEC", "300"))
LEASE_CHECK_SEC = int(__import__("os").getenv("PRALAY_LEASE_CHECK_SEC", "30"))
# A heartbeat renews the lease while the current stage is younger than STAGE_MAX_SEC. A slow-but-alive stage
# (e.g. external sources behind a rate limit) keeps its lease; a stage hung longer than this stops being renewed,
# its lease expires and the monitor re-queues the report.
STAGE_MAX_SEC = int(__import__("os").getenv("PRALAY_STAGE_MAX_SEC", "600"))
_progress = {}  # owner -> monotonic time the current stage started
TEST_MODE = __import__("os").getenv("PRALAY_TEST_MODE") == "1"
TEST_STALL = {}  # test mode only: {"match": substring of description, "stage": STAGE, "seconds": n, "remaining": k}


class LeaseLost(Exception):
    """Raised inside a run that no longer owns its report (lease expired and the report was re-queued/re-claimed)."""
_worker_id = f"{__import__('os').getpid()}"


def bump():
    with _vlock:
        _version["v"] += 1


def version():
    return _version["v"]


def _hist(rid, state, stage=None, note=None, duration_ms=None):
    db.execute("INSERT INTO status_history(report_id,state,stage,note,duration_ms,created_at) VALUES(?,?,?,?,?,?)",
               (rid, state, stage, note, duration_ms, db.now_iso()))


_stage_t = threading.local()


def _stage(rid, stage, note=None):
    """Enter a stage: records the previous stage's duration and renews the processing lease."""
    if TEST_MODE and TEST_STALL.get("remaining", 0) > 0 and TEST_STALL.get("stage") == stage:
        d = (db.one("SELECT description FROM reports WHERE id=?", (rid,)) or {}).get("description") or ""
        if TEST_STALL.get("match") and TEST_STALL["match"] in d:
            TEST_STALL["remaining"] -= 1
            log.warning("TEST MODE: stalling report %s at %s for %ss", rid, stage, TEST_STALL["seconds"])
            time.sleep(TEST_STALL["seconds"])
    owner = getattr(_stage_t, "owner", None)
    if owner:
        cur = db.one("SELECT lease_owner FROM reports WHERE id=?", (rid,))
        if not cur or cur["lease_owner"] != owner:
            raise LeaseLost(f"report {rid}: lease no longer held at stage {stage}")
    now = time.time()
    if owner:
        _progress[owner] = time.monotonic()
    prev = getattr(_stage_t, "cur", None)
    if prev and prev[0] == rid:
        db.execute("UPDATE status_history SET duration_ms=? WHERE id=?", (int((now - prev[2]) * 1000), prev[1]))
    lease = utils.iso(utils.utcnow() + __import__("datetime").timedelta(seconds=LEASE_SEC))
    db.execute("UPDATE reports SET pipeline_stage=?, lease_expires_at=?, updated_at=? WHERE id=?", (stage, lease, db.now_iso(), rid))
    hid = db.execute("INSERT INTO status_history(report_id,state,stage,note,created_at) VALUES(?,?,?,?,?)",
                     (rid, "PROCESSING", stage, note, db.now_iso()))
    _stage_t.cur = (rid, hid, now)
    bump()


def _reset_partial(rid):
    """Make (re)processing idempotent: remove results of any earlier, interrupted or failed attempt."""
    with db.tx() as c:
        c.execute("DELETE FROM media_findings WHERE report_id=?", (rid,))
        c.execute("DELETE FROM observations WHERE report_id=?", (rid,))
        c.execute("DELETE FROM visual_analyses WHERE report_id=?", (rid,))
        c.execute("UPDATE reports SET is_duplicate_of=NULL WHERE id=?", (rid,))


def _save_findings(rid, mid, findings):
    now = db.now_iso()
    with db.tx() as c:
        for f in findings:
            c.execute("INSERT INTO media_findings(report_id,media_id,check_type,finding,severity,evidence_json,method,confidence,limitation,"
                      "status,related_media_id,related_report_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, mid, f["check_type"], f["finding"], f["severity"], db.jdump(f["evidence"]), f["method"], f["confidence"],
                       f["limitation"], f["status"], f["related_media_id"], f["related_report_id"], now))


def _save_obs(rid, obs):
    now = db.now_iso()
    with db.tx() as c:
        for o in obs:
            c.execute("INSERT INTO observations(report_id,kind,source,lat,lon,time_value,label,limitation,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, o["kind"], o["source"], o.get("lat"), o.get("lon"), o.get("time_value"), o.get("label"), o.get("limitation"), now))


def cascade_incident(iid, trigger, include=None):
    """Re-assess an incident and every processed report in it (plus `include`, the report currently being
    processed), then re-check grid eligibility. The in-flight report's workflow state is finalised by its worker."""
    if fusion.requires_evidence_review(db.one("SELECT * FROM incidents WHERE id=?", (iid,))):
        return  # preserve legacy geometry, scores, statuses and report/grid audit history
    sel = "SELECT id FROM reports WHERE incident_id=? AND (processing_state IN ('PROCESSED','MAPPED') OR id=?)"
    inc_id = include or -1
    clustering.refresh_incident_geometry(iid)
    fusion.assess_incident(iid, trigger, include)
    for r in db.query(sel, (iid, inc_id)):
        fusion.assess_report(r["id"], trigger)
    # second pass: incident score depends on report statuses
    _, changed = fusion.assess_incident(iid, trigger, include)
    if changed:
        for r in db.query(sel, (iid, inc_id)):
            fusion.assess_report(r["id"], trigger)
    for r in db.query(sel, (iid, inc_id)):
        m = grid.map_report(r["id"])
        if r["id"] != include:
            db.execute("UPDATE reports SET processing_state=? WHERE id=? AND processing_state IN ('PROCESSED','MAPPED')",
                       ("MAPPED" if m["mapped"] else "PROCESSED", r["id"]))
    bump()


def process_report(rid, owner=None):
    t_start = time.time()
    _stage_t.owner = owner
    _reset_partial(rid)
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    _stage(rid, "VALIDATION")
    media = db.query("SELECT * FROM media WHERE report_id=?", (rid,))
    for m in media:
        if not Path(m["stored_path"]).exists():
            raise RuntimeError(f"Stored media file missing for media #{m['id']}")
    if r["claimed_lat"] is not None:
        ok, why = preprocessing.validate_coords(r["claimed_lat"], r["claimed_lon"])
        if not ok:
            db.execute("UPDATE reports SET claimed_lat=NULL, claimed_lon=NULL WHERE id=?", (rid,))
            _hist(rid, "PROCESSING", "VALIDATION", f"claimed pin discarded: {why}")
            r = db.one("SELECT * FROM reports WHERE id=?", (rid,))

    _stage(rid, "METADATA_EXTRACTION")
    exif_main, phashes = None, []
    for m in media:
        stem = Path(m["stored_path"]).stem
        info = preprocessing.process_image(m["stored_path"], stem) if m["kind"] == "image" else preprocessing.process_video(m["stored_path"], stem)
        db.execute("UPDATE media SET phash=?, dhash=?, width=?, height=?, exif_json=?, video_meta_json=?, thumb_path=?, frame_paths_json=?, "
                   "content_hash=?, phash_variants_json=?, frame_phashes_json=?, container_time=? WHERE id=?",
                   (info.get("phash"), info.get("dhash"), info.get("width"), info.get("height"), db.jdump(info.get("exif")),
                    db.jdump(info.get("video_meta")), info.get("thumb_path"), db.jdump(info.get("frames")),
                    info.get("content_hash"), db.jdump(info.get("phash_variants")), db.jdump(info.get("frame_phashes")),
                    info.get("container_time"), m["id"]))
        m.update(phash=info.get("phash"), dhash=info.get("dhash"), width=info.get("width"), height=info.get("height"),
                 exif=info.get("exif"), video_meta=info.get("video_meta"), frames=info.get("frames"),
                 content_hash=info.get("content_hash"), phash_variants=info.get("phash_variants"),
                 frame_phashes=info.get("frame_phashes"), container_time=info.get("container_time"))
        if info.get("phash"):
            phashes.append(info["phash"])
        if exif_main is None and (info.get("exif") or {}).get("present"):
            exif_main = info["exif"]
    if exif_main:
        gps = exif_main.get("gps") or {}
        db.execute("UPDATE reports SET exif_lat=?, exif_lon=? WHERE id=?", (gps.get("lat"), gps.get("lon"), rid))

    _stage(rid, "DUPLICATE_CHECK")
    dup_of = None
    for m in media:
        f = forensics.check_exact_duplicates(r, m) + forensics.check_near_duplicates(r, m) + forensics.check_recycled(r, m)
        _save_findings(rid, m["id"], f)
        for x in f:
            if x["finding"] in ("EXACT_DUPLICATE", "NEAR_DUPLICATE") and x["related_report_id"] and x["related_report_id"] < rid:
                dup_of = min(dup_of or x["related_report_id"], x["related_report_id"])
    if dup_of:
        db.execute("UPDATE reports SET is_duplicate_of=? WHERE id=?", (dup_of, rid))

    _stage(rid, "VISUAL_ANALYSIS")
    visual = None
    tgt = _vision_target(media)
    if tgt:
        path, mime, mid = tgt
        vst = vision.status()
        if vision.mode() == "deferred" and vst["state"] == "READY":
            # Local CPU models are slow: queue the image and finish the report now; observations are applied later.
            visual = {"provider": f"{vst['provider']}:{vst['model']}", "status": "pending",
                      "error": "pending — queued for deferred local AI analysis; results are applied when ready"}
            log.info("Queued visual analysis for report %s", rid)
        else:
            visual = vision.analyse_image(path, mime, r["category"])
        db.execute("INSERT INTO visual_analyses(report_id,media_id,provider,status,description,output_json,error,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (rid, mid, visual["provider"], visual["status"], visual.get("description"),
                    db.jdump(visual.get("output")), visual.get("error"), db.now_iso()))

    _stage(rid, "MEDIA_AUTHENTICITY")
    for m in media:
        _save_findings(rid, m["id"], forensics.check_manipulation(r, m, m.get("exif"), visual) + forensics.check_metadata(r, m, m.get("exif")))

    _stage(rid, "LOCATION_TIME_INVESTIGATION")
    loc = location_time.investigate_location(r, exif_main, visual)
    tm = location_time.investigate_time(r, exif_main, container_time=next((m.get("container_time") for m in media if m.get("container_time")), None),
                                        client_modified=next((m.get("client_last_modified") for m in media if m.get("client_last_modified")), None))
    _save_obs(rid, loc["observations"] + tm["observations"])
    _save_findings(rid, None, loc["findings"] + tm["findings"])
    claim, inv = loc["claim"], loc["investigated"]
    db.execute("UPDATE reports SET claimed_lat=COALESCE(?, claimed_lat), claimed_lon=COALESCE(?, claimed_lon), claimed_geocode_label=?, claimed_state=?, "
               "claimed_radius_km=?, investigated_lat=?, investigated_lon=?, location_method=?, location_confidence=?, location_assessment=?, "
               "exif_capture_time=?, time_assessment=? WHERE id=?",
               (claim and claim["lat"], claim and claim["lon"], claim and claim.get("label"), claim and claim.get("state"), loc.get("radius_km"),
                inv and inv["lat"], inv and inv["lon"], loc["method"], loc["confidence"], loc["assessment"],
                tm["exif_time"], tm["assessment"], rid))

    _stage(rid, "INCIDENT_ASSOCIATION")
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    with _incident_lock:
        iid, assoc = clustering.find_or_create_incident(r, phashes)
        if iid:
            db.execute("UPDATE reports SET incident_id=? WHERE id=?", (iid, rid))
            clustering.refresh_incident_geometry(iid)
    _hist(rid, "PROCESSING", "INCIDENT_ASSOCIATION", db.jdump(assoc))

    _stage(rid, "EXTERNAL_EVIDENCE")
    if iid:
        try:
            g = external.gather(iid)
        except Exception as e:  # external evidence must never stop the pipeline
            log.exception("external evidence failed for incident %s", iid)
            g = {"action": "failed", "error": f"{type(e).__name__}: {e}"}
        _hist(rid, "PROCESSING", "EXTERNAL_EVIDENCE", db.jdump(g))

    _stage(rid, "CREDIBILITY_ASSESSMENT")
    try:  # same-day news for this report (free Google News RSS, cached 15 min); failure never blocks processing
        from . import news_ingestion
        matched, evaluated = news_ingestion.refresh(rid)
        log.info("news evidence for report %s: %d matched of %d evaluated", rid, len(matched), len(evaluated))
    except Exception as e:
        log.warning("news evidence for report %s unavailable: %s", rid, e)
    fusion.assess_report(rid)
    _stage(rid, "GRID_ELIGIBILITY_CHECK")
    if iid:
        cascade_incident(iid, f"new report #{rid}", include=rid)
    else:
        fusion.assess_report(rid)
        grid.map_report(rid)
    _stage(rid, "GRID_MAPPING")
    rr = db.one("SELECT grid_mapped, grid_cell_id, verification_status FROM reports WHERE id=?", (rid,))
    final = "MAPPED" if rr["grid_mapped"] else "PROCESSED"
    _stage(rid, "DASHBOARD_UPDATE")
    ms = int((time.time() - t_start) * 1000)
    with db.tx() as c:
        cur = c.execute("UPDATE reports SET processing_state=?, pipeline_stage='DASHBOARD_UPDATE', processed_at=?, processing_ms=?, "
                        "lease_owner=NULL, lease_expires_at=NULL, error=NULL, updated_at=? WHERE id=? AND (lease_owner IS ? OR ? IS NULL)",
                        (final, db.now_iso(), ms, db.now_iso(), rid, owner, owner))
        owned = cur.rowcount == 1
    prev = getattr(_stage_t, "cur", None)
    if prev and prev[0] == rid:
        db.execute("UPDATE status_history SET duration_ms=? WHERE id=?", (int((time.time() - prev[2]) * 1000), prev[1]))
    _stage_t.cur = None
    if not owned:
        _hist(rid, "PROCESSING", "DASHBOARD_UPDATE", "lease lost (report was re-queued or re-claimed by another run) — this run's final state discarded")
        return
    _hist(rid, final, "DASHBOARD_UPDATE", f"verification={rr['verification_status']} cell={rr['grid_cell_id']} total={ms} ms", ms)
    bump()


def _vision_target(media):
    """(path, mime, media_id) of the image to analyse: first image, else the middle frame of the first video."""
    img = next((m for m in media if m["kind"] == "image"), None)
    if img:
        return img["stored_path"], img["mime"], img["id"]
    for m in media:
        frames = m.get("frames") if m.get("frames") is not None else db.jload(m.get("frame_paths_json"), [])
        if frames:
            return frames[len(frames) // 2], "image/jpeg", m["id"]
    return None


def apply_visual(rid, visual, trigger="deferred visual analysis"):
    """Re-apply visual observations to an already-processed report: visual-context finding, landmark-aware location
    investigation, then reassessment and grid re-check. Incident association is not changed."""
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    media = db.query("SELECT * FROM media WHERE report_id=?", (rid,))
    for m in media:
        m["exif"] = db.jload(m["exif_json"], {})
        m["video_meta"] = db.jload(m["video_meta_json"], {})
    exif_main = next((m["exif"] for m in media if (m["exif"] or {}).get("present")), None)
    with db.tx() as c:
        c.execute("DELETE FROM media_findings WHERE report_id=? AND check_type IN ('manipulation','visual_context','location_consistency')", (rid,))
        c.execute("DELETE FROM observations WHERE report_id=? AND kind='location'", (rid,))
    for m in media:
        _save_findings(rid, m["id"], forensics.check_manipulation(r, m, m["exif"], visual))
    loc = location_time.investigate_location(r, exif_main, visual)
    _save_obs(rid, loc["observations"])
    _save_findings(rid, None, loc["findings"])
    inv = loc["investigated"]
    db.execute("UPDATE reports SET investigated_lat=?, investigated_lon=?, location_method=?, location_confidence=?, location_assessment=?, "
               "updated_at=? WHERE id=?", (inv and inv["lat"], inv and inv["lon"], loc["method"], loc["confidence"], loc["assessment"],
                                          db.now_iso(), rid))
    if r["incident_id"]:
        cascade_incident(r["incident_id"], trigger)
    else:
        fusion.assess_report(rid, trigger)
        m_ = grid.map_report(rid)
        db.execute("UPDATE reports SET processing_state=? WHERE id=? AND processing_state IN ('PROCESSED','MAPPED')",
                   ("MAPPED" if m_["mapped"] else "PROCESSED", rid))
    st = db.one("SELECT processing_state, verification_status FROM reports WHERE id=?", (rid,))
    _hist(rid, st["processing_state"], "VISUAL_ANALYSIS_APPLIED", f"{visual['status']} via {visual['provider']}; verification={st['verification_status']}")
    bump()


def vision_loop():
    """Deferred local AI analysis: one image at a time, only after the report finished its main pipeline."""
    while not _stop.wait(2):
        try:
            row = db.one("SELECT v.* FROM visual_analyses v JOIN reports r ON r.id=v.report_id WHERE v.status='pending' "
                         "AND r.processing_state IN ('PROCESSED','MAPPED') ORDER BY v.id LIMIT 1")
            if not row:
                continue
            if not vision.enough_memory():
                if not getattr(vision_loop, "_warned", False):
                    log.warning("deferred visual AI postponed: available RAM %s MB < VISION_MIN_FREE_MB", vision.LOW_MEMORY["available_mb"])
                    vision_loop._warned = True
                _stop.wait(15)
                continue
            vision_loop._warned = False
            with db.tx() as c:
                if c.execute("UPDATE visual_analyses SET status='running' WHERE id=? AND status='pending'", (row["id"],)).rowcount != 1:
                    continue
            r = db.one("SELECT category FROM reports WHERE id=?", (row["report_id"],))
            log.info("STARTING VISUAL ANALYSIS FOR REPORT %s", row["report_id"])
            tgt = _vision_target(db.query("SELECT * FROM media WHERE report_id=?", (row["report_id"],)))
            visual = vision.analyse_image(tgt[0], tgt[1], r["category"]) if tgt else {"provider": row["provider"], "status": "failed", "error": "no image to analyse"}
            db.execute("UPDATE visual_analyses SET provider=?, status=?, description=?, output_json=?, error=?, created_at=? WHERE id=?",
                       (visual["provider"], visual["status"], visual.get("description"), db.jdump(visual.get("output")), visual.get("error"),
                        db.now_iso(), row["id"]))
            apply_visual(row["report_id"], visual)
            fields = (db.jload((db.one("SELECT assessment_json FROM reports WHERE id=?", (row["report_id"],)) or {}).get("assessment_json"), {}) or {}).get("fields", {})
            log.info("Visual analysis completed for report %s. Relevance: %s (status %s, needs_review=%s)", row["report_id"],
                     fields.get("visual_relevance"), visual["status"], fields.get("needs_review"))
        except Exception:
            log.exception("deferred vision analysis failed")


def _claim_next():
    """Atomically claim the highest-priority due report and take a time-limited lease on it."""
    now = db.now_iso()
    with db.tx() as c:
        row = c.execute("SELECT id FROM reports WHERE processing_state='RECEIVED' AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                        "ORDER BY priority, id LIMIT 1", (now,)).fetchone()
        if not row:
            return None, None
        owner = f"{_worker_id}:{threading.current_thread().name}:{time.time():.3f}"
        lease = utils.iso(utils.utcnow() + __import__("datetime").timedelta(seconds=LEASE_SEC))
        cur = c.execute("UPDATE reports SET processing_state='PROCESSING', attempts=attempts+1, lease_owner=?, lease_expires_at=?, "
                        "processing_started_at=?, next_attempt_at=NULL, updated_at=? WHERE id=? AND processing_state='RECEIVED'",
                        (owner, lease, now, now, row["id"]))
        if cur.rowcount != 1:
            return None, None  # someone else claimed it first
        return row["id"], owner


def _fail(rid, owner, err):
    """Retry with exponential backoff up to MAX_ATTEMPTS, then FAILED_NEEDS_REVIEW."""
    r = db.one("SELECT attempts, lease_owner FROM reports WHERE id=?", (rid,))
    if owner and r["lease_owner"] != owner:
        return  # lease was taken over; the other run owns the outcome
    if r["attempts"] < MAX_ATTEMPTS:
        delay = 5 * 2 ** (r["attempts"] - 1)
        nxt = utils.iso(utils.utcnow() + __import__("datetime").timedelta(seconds=delay))
        db.execute("UPDATE reports SET processing_state='RECEIVED', next_attempt_at=?, error=?, lease_owner=NULL, lease_expires_at=NULL, "
                   "updated_at=? WHERE id=?", (nxt, err, db.now_iso(), rid))
        _hist(rid, "RECEIVED", "RETRY_SCHEDULED", f"attempt {r['attempts']}/{MAX_ATTEMPTS} failed: {err}; retry in {delay}s")
    else:
        db.execute("UPDATE reports SET processing_state='FAILED_NEEDS_REVIEW', error=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                   (err, db.now_iso(), rid))
        _hist(rid, "FAILED_NEEDS_REVIEW", None, f"failed after {r['attempts']} attempt(s): {err}")
    bump()


def worker_loop(n):
    while not _stop.is_set():
        try:
            rid, owner = _claim_next()
        except Exception:
            log.exception("queue claim failed")
            rid, owner = None, None
        if rid is None:
            _stop.wait(WORKER_POLL_SEC)
            continue
        _hist(rid, "PROCESSING", "CLAIMED", f"claimed by worker {n} (lease {LEASE_SEC}s)")
        t0 = time.time()
        _progress[owner] = time.monotonic()
        hb_stop = threading.Event()
        threading.Thread(target=_heartbeat, args=(rid, owner, hb_stop), daemon=True, name=f"pralay-heartbeat-{rid}").start()
        try:
            process_report(rid, owner)
            log.info("report %s processed in %.1fs", rid, time.time() - t0)
        except LeaseLost as e:
            _stage_t.cur = None
            log.warning("abandoning stale run: %s", e)
            _hist(rid, "PROCESSING", "STALE_RUN_ABANDONED", f"{e} — this older attempt stopped without writing results")
        except Exception as e:
            log.error("report %s failed: %s", rid, traceback.format_exc())
            _stage_t.cur = None
            _fail(rid, owner, f"{type(e).__name__}: {e}")
        finally:
            hb_stop.set()
            _progress.pop(owner, None)


def _heartbeat(rid, owner, stop):
    interval = max(1.0, LEASE_SEC / 4)
    while not stop.wait(interval):
        started = _progress.get(owner)
        if started is None or time.monotonic() - started > STAGE_MAX_SEC:
            continue  # stage looks hung: stop renewing so the lease can expire and the monitor can recover it
        lease = utils.iso(utils.utcnow() + __import__("datetime").timedelta(seconds=LEASE_SEC))
        try:
            db.execute("UPDATE reports SET lease_expires_at=? WHERE id=? AND lease_owner=? AND processing_state='PROCESSING'", (lease, rid, owner))
        except Exception:
            log.exception("lease heartbeat failed for report %s", rid)


def recover_expired_leases():
    """Reports whose worker died or hung (lease expired) are re-queued (or failed after MAX_ATTEMPTS)."""
    for r in db.query("SELECT id, attempts FROM reports WHERE processing_state='PROCESSING' AND lease_expires_at IS NOT NULL AND lease_expires_at<?",
                      (db.now_iso(),)):
        _hist(r["id"], "PROCESSING", "LEASE_EXPIRED", "processing lease expired — recovering")
        _fail(r["id"], None, "processing lease expired (worker stalled or crashed)")


def monitor_loop():
    """Background refresh: active incidents with stale evidence are re-investigated, then reassessed.
    Also recovers expired processing leases."""
    last_refresh = time.time()
    while not _stop.wait(min(MONITOR_INTERVAL_SEC, LEASE_CHECK_SEC)):
        try:
            recover_expired_leases()
        except Exception:
            log.exception("lease recovery failed")
        if time.time() - last_refresh < MONITOR_INTERVAL_SEC:
            continue
        last_refresh = time.time()
        try:
            for inc in db.query("SELECT * FROM incidents ORDER BY last_activity_at DESC"):
                if not external.is_active(inc):
                    continue
                if external.freshness(inc)["state"] == "stale":
                    g = external.gather(inc["id"])
                    cascade_incident(inc["id"], "background refresh")
                    db.log_event("monitor", f"incident #{inc['id']} refreshed: {g.get('action')}")
        except Exception:
            log.exception("monitor iteration failed")


def start(workers=2):
    # Single-process deployment: anything PROCESSING at startup belonged to a dead process.
    for r in db.query("SELECT id FROM reports WHERE processing_state='PROCESSING'"):
        _hist(r["id"], "RECEIVED", "RECOVERED_AT_STARTUP", "server restarted during processing; re-queued")
    db.execute("UPDATE reports SET processing_state='RECEIVED', lease_owner=NULL, lease_expires_at=NULL WHERE processing_state='PROCESSING'")
    db.execute("UPDATE visual_analyses SET status='pending' WHERE status='running'")  # interrupted deferred analyses
    threading.Thread(target=vision_loop, daemon=True, name="pralay-vision").start()
    for i in range(workers):
        threading.Thread(target=worker_loop, args=(i,), daemon=True, name=f"pralay-worker-{i}").start()
    threading.Thread(target=monitor_loop, daemon=True, name="pralay-monitor").start()


def stop():
    _stop.set()


def processing_stats():
    """Measured processing durations (not promises)."""
    rows = [r["processing_ms"] for r in db.query("SELECT processing_ms FROM reports WHERE processing_ms IS NOT NULL ORDER BY processing_ms")]
    if not rows:
        return {"count": 0}
    pick = lambda q: rows[min(len(rows) - 1, int(q * len(rows)))]
    stages = db.query("SELECT stage, COUNT(*) n, AVG(duration_ms) avg_ms, MAX(duration_ms) max_ms FROM status_history "
                      "WHERE duration_ms IS NOT NULL AND state='PROCESSING' GROUP BY stage ORDER BY avg_ms DESC")
    return {"count": len(rows), "median_ms": pick(0.5), "p90_ms": pick(0.9), "max_ms": rows[-1], "min_ms": rows[0],
            "stages": [{**s, "avg_ms": round(s["avg_ms"] or 0)} for s in stages],
            "retries": db.one("SELECT COUNT(*) c FROM status_history WHERE stage='RETRY_SCHEDULED'")["c"],
            "lease_recoveries": db.one("SELECT COUNT(*) c FROM status_history WHERE stage IN ('LEASE_EXPIRED','RECOVERED_AT_STARTUP')")["c"]}


def priority_for(category, lat, lon, place_text):
    """Reports likely belonging to ACTIVE incidents are processed first (priority 1)."""
    for inc in db.query("SELECT * FROM incidents"):
        if not external.is_active(inc) or clustering.group(inc["category"]) != clustering.group(category):
            continue
        if lat is not None and inc["center_lat"] is not None and utils.haversine_km(lat, lon, inc["center_lat"], inc["center_lon"]) <= 25:
            return 1
        if place_text and inc["place_name"] and inc["place_name"].split(",")[0].lower() in place_text.lower():
            return 1
    return 5
