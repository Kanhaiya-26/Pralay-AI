"""Shared Intelligence Database (Module 9).

SQLite fallback (PostgreSQL/PostGIS not available in this environment). All access goes through
this module so it can be swapped for PostgreSQL later. One connection per unit of work; WAL mode
lets the background worker and API threads share the file safely.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

_init_lock = threading.Lock()

SCHEMA = """
-- Reporter credibility (additive). id = sha256 of the normalised reporter reference; the score is RECOMPUTED from the
-- reporter's report outcomes on every assessment (never incremented), so repeated re-assessment cannot compound it.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    reporter_name TEXT NOT NULL,
    credibility_score INTEGER NOT NULL DEFAULT 50,
    reports_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Anti-spam: an IP is blocked after 3 claim-mismatch reports. Bans expire after IP_BAN_HOURS (0 = permanent).
CREATE TABLE IF NOT EXISTS banned_ips (
    ip_address TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    banned_at TEXT NOT NULL
);

-- Device fingerprint bans (primary key for the 3-strike rule; IP bans stay as a fallback).
CREATE TABLE IF NOT EXISTS banned_fingerprints (
    fingerprint TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    banned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL DEFAULT 'citizen',      -- citizen | social_post | news | rss | demo
    source_platform TEXT,
    source_url TEXT,
    reporter_ref TEXT,
    description TEXT,
    category TEXT NOT NULL,
    claimed_location_text TEXT,
    claimed_lat REAL, claimed_lon REAL, claimed_geocode_label TEXT, claimed_state TEXT,
    browser_lat REAL, browser_lon REAL, browser_accuracy_m REAL,
    exif_lat REAL, exif_lon REAL,
    investigated_lat REAL, investigated_lon REAL,
    location_method TEXT, location_confidence TEXT, location_assessment TEXT,
    claimed_event_time TEXT, exif_capture_time TEXT, original_publication_time TEXT,
    upload_time TEXT NOT NULL,
    time_assessment TEXT,
    incident_id INTEGER REFERENCES incidents(id),
    processing_state TEXT NOT NULL DEFAULT 'RECEIVED',
    pipeline_stage TEXT,
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    report_confidence REAL,
    assessment_json TEXT,
    override_status TEXT, override_reason TEXT,
    grid_cell_id TEXT, grid_mapped INTEGER NOT NULL DEFAULT 0, grid_mapped_at TEXT, grid_eligibility_json TEXT,
    is_duplicate_of INTEGER,
    priority INTEGER NOT NULL DEFAULT 5,
    is_demo INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reports_state ON reports(processing_state, priority);
CREATE INDEX IF NOT EXISTS ix_reports_incident ON reports(incident_id);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    kind TEXT NOT NULL,                                -- image | video
    original_filename TEXT, stored_path TEXT NOT NULL, mime TEXT, size_bytes INTEGER,
    sha256 TEXT, phash TEXT, dhash TEXT, width INTEGER, height INTEGER,
    exif_json TEXT, video_meta_json TEXT, thumb_path TEXT, frame_paths_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_media_sha ON media(sha256);

CREATE TABLE IF NOT EXISTS archive_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT, phash TEXT,
    source_name TEXT, source_url TEXT, first_published TEXT, description TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL, media_id INTEGER,
    check_type TEXT NOT NULL,     -- exact_duplicate | near_duplicate | recycled | manipulation | metadata | location_consistency | time_consistency | visual_context
    finding TEXT NOT NULL,        -- classification label
    severity TEXT NOT NULL,       -- contradiction | concern | info | clear
    evidence_json TEXT, method TEXT, confidence TEXT, limitation TEXT,
    status TEXT NOT NULL,         -- completed | unavailable | inconclusive
    related_media_id INTEGER, related_report_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_report ON media_findings(report_id);

CREATE TABLE IF NOT EXISTS visual_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL, media_id INTEGER,
    provider TEXT, status TEXT NOT NULL, description TEXT, output_json TEXT, error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    kind TEXT NOT NULL,           -- location | time
    source TEXT NOT NULL,         -- claimed_text | geocode | exif_gps | browser | landmark | exif_time | claimed_time | publication | upload
    lat REAL, lon REAL, time_value TEXT, label TEXT, limitation TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, category TEXT NOT NULL,
    place_name TEXT, state_name TEXT,
    center_lat REAL, center_lon REAL,
    event_time_start TEXT, event_time_end TEXT,
    last_activity_at TEXT,
    origin TEXT NOT NULL DEFAULT 'reports',      -- reports | investigation
    status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    confidence_score REAL, assessment_json TEXT,
    override_status TEXT, override_reason TEXT,
    grid_cell_id TEXT,
    last_evidence_check TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER, report_id INTEGER,
    source_name TEXT NOT NULL, source_url TEXT, source_origin TEXT NOT NULL,  -- official | news | weather | crowd | gdelt
    publisher TEXT, publication_time TEXT, event_time TEXT, retrieval_time TEXT NOT NULL,
    summary TEXT, stance TEXT NOT NULL,          -- supports | contradicts | neutral | unavailable
    independence_group TEXT, dedupe_key TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidence_incident ON evidence(incident_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_dedupe ON evidence(incident_id, dedupe_key);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
    status TEXT NOT NULL, score REAL, reasons_json TEXT, trigger TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assess_entity ON assessments(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL, state TEXT NOT NULL, stage TEXT, note TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
    previous_status TEXT, new_status TEXT, reason TEXT NOT NULL, reviewer TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    norm_key TEXT NOT NULL UNIQUE, raw_query TEXT, category TEXT, place TEXT,
    lat REAL, lon REAL, window_h REAL,
    result_json TEXT, incident_ids TEXT,
    created_at TEXT NOT NULL, refreshed_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0, last_hit_at TEXT
);

CREATE TABLE IF NOT EXISTS http_cache (
    cache_key TEXT PRIMARY KEY, url TEXT, fetched_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    status_code INTEGER, body TEXT, error TEXT, hit_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query TEXT PRIMARY KEY, ok INTEGER NOT NULL, lat REAL, lon REAL, label TEXT, state TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grid_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL, resolution_deg REAL NOT NULL,
    lat_min REAL, lon_min REAL, lat_max REAL, lon_max REAL,
    label TEXT NOT NULL, is_official INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grid_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL, incident_id INTEGER, cell_id TEXT NOT NULL, grid_version INTEGER NOT NULL,
    action TEXT NOT NULL,        -- mapped | unmapped
    criteria_json TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, message TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT, last_success_at TEXT, last_result TEXT, last_error TEXT,
    cooldown_until TEXT
);

CREATE TABLE IF NOT EXISTS connector_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, fetched INTEGER DEFAULT 0, ingested INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
    error TEXT, params_json TEXT
);
"""

# Additive, idempotent migrations (ALTER TABLE ADD COLUMN) so existing databases upgrade in place.
MIGRATIONS = [
    ("reports", "ip_address", "TEXT"), ("reports", "device_fingerprint", "TEXT"),
    ("media", "content_hash", "TEXT"), ("media", "phash_variants_json", "TEXT"), ("media", "frame_phashes_json", "TEXT"),
    ("media", "client_last_modified", "TEXT"), ("media", "container_time", "TEXT"),
    ("archive_media", "content_hash", "TEXT"),
    ("reports", "attempts", "INTEGER NOT NULL DEFAULT 0"), ("reports", "next_attempt_at", "TEXT"),
    ("reports", "processing_started_at", "TEXT"), ("reports", "processed_at", "TEXT"), ("reports", "processing_ms", "INTEGER"),
    ("reports", "lease_owner", "TEXT"), ("reports", "lease_expires_at", "TEXT"),
    ("reports", "ingest_connector", "TEXT"), ("reports", "external_id", "TEXT"), ("reports", "claimed_radius_km", "REAL"),
    ("evidence", "result_type", "TEXT"),
    ("evidence", "validation_json", "TEXT"),
    ("incidents", "event_name", "TEXT"), ("incidents", "hazard", "TEXT"),
    ("incidents", "country_code", "TEXT"),
    ("geocode_cache", "radius_km", "REAL"), ("geocode_cache", "place_type", "TEXT"),
    ("status_history", "duration_ms", "INTEGER"),
]
POST_MIGRATION_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_reports_external ON reports(ingest_connector, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_media_content ON media(content_hash);
UPDATE evidence SET result_type = CASE WHEN stance='unavailable' THEN 'unavailable'
    WHEN dedupe_key LIKE '%:none' THEN 'no_match' ELSE 'match' END WHERE result_type IS NULL;
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx():
    """Transaction scope. Commits on success, rolls back on error."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def query(sql, params=()):
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def init_db():
    with _init_lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            for table, col, typ in MIGRATIONS:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            conn.executescript(POST_MIGRATION_SQL)
            from .acquisition_schema import SQL as ACQUISITION_SCHEMA
            conn.executescript(ACQUISITION_SCHEMA)
        finally:
            conn.close()


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def jload(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def log_event(kind, message):
    execute("INSERT INTO system_events(kind, message, created_at) VALUES (?,?,?)", (kind, message, now_iso()))
