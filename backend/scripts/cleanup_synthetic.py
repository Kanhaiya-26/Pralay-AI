"""Remove ONLY records positively identified as synthetic seed data, with a verified backup and a full audit trail.

Selection (explicit provenance only):
  * reports:   is_demo=1 AND source_type='demo'            (written only by scripts/seed_demo.py)
  * incidents: is_demo=1 AND origin='reports' AND every linked report is a selected synthetic report
  * archive:   is_demo=1 AND source_name LIKE 'DEMO ARCHIVE%'
Never selected: investigation incidents, non-demo reports, external evidence not linked to a selected incident.

Every removed row is first copied verbatim into retired_records under a cleanup batch that references a verified backup.
Media files of removed reports are MOVED into the backup folder, not deleted.

    .venv\\Scripts\\python.exe scripts\\cleanup_synthetic.py            # dry run: prints the manifest, changes nothing
    .venv\\Scripts\\python.exe scripts\\cleanup_synthetic.py --apply    # backup -> verify -> retire -> delete -> verify
Stop the server first (.\\stop.ps1) so no worker writes during the cleanup.
"""
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DB_PATH, THUMB_DIR, UPLOAD_DIR  # noqa: E402
from app.db import init_db  # noqa: E402

APPLY = "--apply" in sys.argv


def rows(c, sql, p=()):
    return [dict(r) for r in c.execute(sql, p).fetchall()]


def ids_in(ids):
    return f"({','.join('?' * len(ids))})" if ids else "(NULL)"


def main():
    init_db()  # ensures retired_records / cleanup_batches exist (additive)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    rep = [r["id"] for r in rows(c, "SELECT id FROM reports WHERE is_demo=1 AND source_type='demo'")]
    inc_cand = [r["id"] for r in rows(c, "SELECT id FROM incidents WHERE is_demo=1 AND origin='reports'")]
    inc = [i for i in inc_cand if all(r["id"] in rep for r in rows(c, "SELECT id FROM reports WHERE incident_id=?", (i,)))]
    arch = [r["id"] for r in rows(c, "SELECT id FROM archive_media WHERE is_demo=1 AND source_name LIKE 'DEMO ARCHIVE%'")]
    keep_inc = rows(c, "SELECT id, origin, is_demo, title FROM incidents WHERE id NOT IN " + ids_in(inc), inc)
    keep_rep = rows(c, "SELECT id, source_type, is_demo FROM reports WHERE id NOT IN " + ids_in(rep), rep)
    rq, iq = ids_in(rep), ids_in(inc)
    plan = [  # (table, where, params) — dependants first, parents last
        ("media_findings", f"report_id IN {rq}", rep), ("observations", f"report_id IN {rq}", rep),
        ("visual_analyses", f"report_id IN {rq}", rep), ("status_history", f"report_id IN {rq}", rep),
        ("grid_mappings", f"report_id IN {rq} OR incident_id IN {iq}", rep + inc),
        ("assessments", f"(entity_type='report' AND entity_id IN {rq}) OR (entity_type='incident' AND entity_id IN {iq})", rep + inc),
        ("review_history", f"(entity_type='report' AND entity_id IN {rq}) OR (entity_type='incident' AND entity_id IN {iq})", rep + inc),
        ("evidence", f"incident_id IN {iq}", inc),  # external evidence fetched FOR synthetic incidents: retired (payload kept), not lost
        ("media", f"report_id IN {rq}", rep), ("reports", f"id IN {rq}", rep), ("incidents", f"id IN {iq}", inc),
        ("archive_media", f"id IN {ids_in(arch)}", arch),
    ]
    manifest = {"selected": {"reports": rep, "incidents": inc, "archive_media": arch},
                "preserved_incidents": keep_inc, "preserved_reports": keep_rep,
                "counts": {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE {w}", p).fetchone()[0] for t, w, p in plan},
                "query_cache_rows_cleared": c.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]}
    media_files = [m for m in rows(c, f"SELECT stored_path, thumb_path, frame_paths_json FROM media WHERE report_id IN {rq}", rep)]
    print(json.dumps(manifest, indent=1, ensure_ascii=False))
    if not APPLY:
        print("\nDRY RUN — nothing changed. Re-run with --apply (server stopped) to execute.")
        return
    # 1. backup + verify
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bdir = DB_PATH.parent / "backups" / f"pre_cleanup_{ts}"
    bdir.mkdir(parents=True)
    bpath = bdir / "pralay.db"
    with sqlite3.connect(bpath) as b:
        c.backup(b)
    with sqlite3.connect(bpath) as b:
        assert b.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "backup integrity failed"
        for t in ("reports", "incidents", "evidence", "media", "archive_media"):
            assert b.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], t
    print("backup verified:", bpath)
    # 2. retire + delete in one transaction
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c.execute("BEGIN IMMEDIATE")
    try:
        c.execute("INSERT INTO cleanup_batches(id, backup_path, manifest_json, created_at) VALUES(?,?,?,?)",
                  (ts, str(bpath), json.dumps(manifest, default=str), now))
        for t, w, p in plan:
            for r in rows(c, f"SELECT * FROM {t} WHERE {w}", p):
                c.execute("INSERT INTO retired_records(batch_id, table_name, original_id, payload_json, reason, retired_at) VALUES(?,?,?,?,?,?)",
                          (ts, t, r.get("id") or 0, json.dumps(r, default=str), "synthetic seed data (explicit is_demo/source_type provenance)", now))
            c.execute(f"DELETE FROM {t} WHERE {w}", p)
        c.execute("DELETE FROM query_cache")  # cached answers may reference removed incidents
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    # 3. move media files into the backup folder
    moved = 0
    for m in media_files:
        for f in [m["stored_path"], m["thumb_path"], *json.loads(m["frame_paths_json"] or "[]")]:
            if f and Path(f).exists():
                (bdir / "media").mkdir(exist_ok=True)
                shutil.move(f, bdir / "media" / Path(f).name)
                moved += 1
    # 4. verify
    ic = c.execute("PRAGMA integrity_check").fetchone()[0]
    fk = c.execute("PRAGMA foreign_key_check").fetchall()
    left = {t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE {w}", p).fetchone()[0] for t, w, p in plan}
    kept = [r["id"] for r in rows(c, "SELECT id FROM incidents")]
    print(f"integrity={ic} fk_violations={len(fk)} remaining_selected={left} media_files_moved={moved} incidents_now={kept}")
    assert ic == "ok" and not fk and not any(left.values()), "post-cleanup verification failed"
    assert all(i["id"] in kept for i in keep_inc), "a preserved incident went missing"
    (bdir / "manifest.json").write_text(json.dumps({**manifest, "batch": ts, "media_files_moved": moved}, indent=1, default=str), encoding="utf-8")
    print("cleanup complete; batch", ts)


if __name__ == "__main__":
    main()
