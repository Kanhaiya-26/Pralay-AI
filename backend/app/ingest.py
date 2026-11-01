"""Single ingestion path (Module 1) shared by the citizen API and authorised connectors.

Every report keeps: source type/platform/URL, external id (connector dedupe), reporter reference,
server submission time, claimed event time, original publication time, and per-file client
last-modified time when provided. Files are validated before anything is written to the database.
"""
from pathlib import Path

from . import db, pipeline, preprocessing, utils

CATEGORIES = ["flood", "fire", "smoke", "landslide", "building_damage", "road_blockage", "infrastructure_damage", "cyclone_storm", "other"]
SOURCE_TYPES = ["citizen", "social_post", "news", "rss", "demo"]


class IngestError(ValueError):
    pass


def create_report(*, category, description="", claimed_location="", claimed_lat=None, claimed_lon=None,
                  browser_lat=None, browser_lon=None, browser_accuracy_m=None, claimed_event_time="",
                  source_type="citizen", source_platform="", source_url="", original_publication_time="",
                  reporter_ref="", is_demo=False, files=(), ingest_connector=None, external_id=None,
                  require_location=True):
    """files: iterable of (bytes, original_name, client_last_modified_iso_or_None). Returns (report_id, priority, file_info)."""
    if category not in CATEGORIES:
        raise IngestError(f"category must be one of {CATEGORIES}")
    if source_type not in SOURCE_TYPES:
        raise IngestError(f"source_type must be one of {SOURCE_TYPES}")
    files = list(files)
    if not files and not (description or "").strip():
        raise IngestError("Provide at least one media file or a description")
    # The typed place name is required; GPS (EXIF / device / pin) is separate evidence and never substitutes for it.
    if require_location and not (claimed_location or "").strip():
        raise IngestError("Location is required to submit a report.")
    if len(files) > 5:
        raise IngestError("At most 5 files per report")
    for label, lat, lon in (("claimed pin", claimed_lat, claimed_lon), ("browser location", browser_lat, browser_lon)):
        if lat is None and lon is None:
            continue
        ok, why = preprocessing.validate_coords(lat, lon)
        if not ok:
            raise IngestError(f"Invalid {label}: {why}")
    if source_url and not source_url.startswith(("http://", "https://")):
        raise IngestError("source_url must be an http(s) URL")
    for name, val in (("claimed_event_time", claimed_event_time), ("original_publication_time", original_publication_time)):
        if val and utils.parse_time(val) is None:
            raise IngestError(f"{name} is not a valid timestamp")
    if claimed_event_time and utils.parse_time(claimed_event_time) > utils.utcnow() + __import__("datetime").timedelta(hours=1):
        raise IngestError("claimed_event_time is in the future")
    if external_id and db.one("SELECT id FROM reports WHERE ingest_connector=? AND external_id=?", (ingest_connector, external_id)):
        raise IngestError(f"already ingested ({ingest_connector}:{external_id})")

    stored, file_info = [], []
    try:
        for i, (data, name, client_mod) in enumerate(files):
            try:
                s = preprocessing.validate_and_store(data, name)
            except preprocessing.ValidationError as e:
                raise IngestError(f"File {i + 1} ({Path(name or 'upload').name}): {e}")
            s["client_last_modified"] = utils.iso(utils.parse_time(client_mod)) if client_mod else None
            stored.append(s)
            file_info.append({"name": s["original_filename"], "kind": s["kind"], "mime": s["mime"], "size_bytes": s["size_bytes"],
                              "sha256": s["sha256"]})
    except IngestError:
        for s in stored:
            Path(s["stored_path"]).unlink(missing_ok=True)
        raise

    now = db.now_iso()
    prio = pipeline.priority_for(category, claimed_lat if claimed_lat is not None else browser_lat,
                                 claimed_lon if claimed_lon is not None else browser_lon, claimed_location)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO reports(source_type,source_platform,source_url,reporter_ref,description,category,claimed_location_text,claimed_lat,claimed_lon,"
            "browser_lat,browser_lon,browser_accuracy_m,claimed_event_time,original_publication_time,upload_time,priority,is_demo,updated_at,"
            "processing_state,ingest_connector,external_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'RECEIVED',?,?)",
            (source_type, source_platform or None, source_url or None, (reporter_ref or "")[:80] or None, (description or "")[:4000], category,
             (claimed_location or "").strip()[:200] or None, claimed_lat, claimed_lon, browser_lat, browser_lon, browser_accuracy_m,
             utils.iso(utils.parse_time(claimed_event_time)), utils.iso(utils.parse_time(original_publication_time)), now, prio,
             1 if is_demo or source_type == "demo" else 0, now, ingest_connector, external_id))
        rid = cur.lastrowid
        for s in stored:
            c.execute("INSERT INTO media(report_id,kind,original_filename,stored_path,mime,size_bytes,sha256,client_last_modified,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, s["kind"], s["original_filename"], s["stored_path"], s["mime"], s["size_bytes"], s["sha256"],
                       s["client_last_modified"], now))
        c.execute("INSERT INTO status_history(report_id,state,stage,note,created_at) VALUES(?,?,?,?,?)",
                  (rid, "RECEIVED", "QUEUED", f"priority {prio}{' (near active incident)' if prio == 1 else ''}; {len(stored)} file(s)"
                   + (f"; via connector {ingest_connector}" if ingest_connector else ""), now))
    pipeline.bump()
    return rid, prio, file_info
