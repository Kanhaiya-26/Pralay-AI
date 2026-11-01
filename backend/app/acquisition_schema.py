"""Additive storage for raw fetches, immutable observation versions and cleanup audit."""
SQL = """
CREATE TABLE IF NOT EXISTS acquisition_fetches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, dataset TEXT NOT NULL,
 endpoint TEXT NOT NULL, retrieved_at TEXT NOT NULL, http_status INTEGER, content_type TEXT,
 raw_payload TEXT, error TEXT, response_sha256 TEXT);
CREATE TABLE IF NOT EXISTS provider_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, dataset TEXT NOT NULL,
 source_record_id TEXT NOT NULL, version_hash TEXT NOT NULL, fetch_id INTEGER,
 kind TEXT NOT NULL, title TEXT, source_url TEXT, retrieved_at TEXT NOT NULL,
 published_at TEXT, observed_at TEXT, event_at TEXT, valid_until TEXT,
 geometry_json TEXT, geo_precision TEXT NOT NULL, resolution_km REAL,
 country_code TEXT, state_name TEXT, district_name TEXT, locality TEXT,
 hazard TEXT, data_json TEXT NOT NULL, independence_key TEXT,
 UNIQUE(provider,dataset,source_record_id,version_hash));
CREATE INDEX IF NOT EXISTS ix_provider_records_source ON provider_records(provider,dataset,source_record_id,id);
CREATE TABLE IF NOT EXISTS acquisition_health (
 provider TEXT NOT NULL, dataset TEXT NOT NULL, state TEXT NOT NULL,
 attempted_at TEXT, successful_at TEXT, observation_at TEXT, http_status INTEGER,
 retrieved INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0,
 rejected INTEGER NOT NULL DEFAULT 0, reasons_json TEXT, error TEXT, next_retry_at TEXT,
 failures INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(provider,dataset));
CREATE TABLE IF NOT EXISTS retired_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, table_name TEXT NOT NULL,
 original_id INTEGER NOT NULL, payload_json TEXT NOT NULL, reason TEXT NOT NULL, retired_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cleanup_batches (
 id TEXT PRIMARY KEY, backup_path TEXT NOT NULL, manifest_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""
