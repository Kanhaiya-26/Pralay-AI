"""REAL-WORLD end-to-end validation of the Mastodon connector, against a fresh isolated server.
Uses only Mastodon's documented public API. If no qualifying public post (disaster-related, names a place in
India, has media, ≤ MASTODON_MAX_AGE_DAYS old) exists right now, the result is BLOCKED (exit code 2) — never faked.

    .venv\\Scripts\\python.exe scripts\\run_tests.py --suite mastodon-real
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
OUT = Path(os.getenv("PRALAY_TEST_DATA_DIR", ".")) / "mastodon_real_result.json"
TAGS = ["flood", "floods", "flooding", "landslide", "cloudburst", "cyclone", "waterlogging", "heavyrain", "indiafloods", "monsoon"]
c = httpx.Client(base_url=API, timeout=600)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


print("== 1-4 discovery + relevance + geographic + India gates (dry run: nothing downloaded)")
dry = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": TAGS, "limit": 40, "dry_run": True}).json()
print(f"   fetched {dry['fetched']} public posts; skipped: {json.dumps(dry['skipped'])}; errors: {dry['errors'][:3]}")
check("public API returned posts", dry["fetched"] > 0)
check("every fetched post has a recorded gate decision (skipped reasons + candidates == fetched)",
      sum(dry["skipped"].values()) == dry["fetched"], (sum(dry["skipped"].values()), dry["fetched"]))
cands = dry["candidates"]
for x in cands[:5]:
    print("   candidate:", json.dumps({k: x[k] for k in ("url", "created_at", "category", "place_phrase", "geocoded_as")}, ensure_ascii=False))
if not cands:
    print("\nBLOCKED: no qualifying public post (disaster-related + Indian place + media + recent) is available right now.")
    OUT.write_text(json.dumps({"result": "BLOCKED", "dry_run": dry}, default=str))
    sys.exit(2)

print("== 5-8 attribution, timestamps, media download, report creation (real run)")
before = c.get("/api/summary").json()["reports"]["received_total"]
real = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": TAGS, "limit": 40, "wait": True}).json()
print(f"   real run: fetched {real['fetched']}, ingested {real['ingested']}, skipped {json.dumps(real['skipped'])}")
check("qualifying post(s) ingested as reports", real["ingested"] >= 1 and real["report_ids"], real)
if not real["report_ids"]:
    OUT.write_text(json.dumps({"result": "FAILED_TO_INGEST", "dry_run": dry, "real": real}, default=str))
    sys.exit(1)
t0 = time.time()
while time.time() - t0 < 300:
    s = c.get("/api/summary").json()["reports"]
    if s["awaiting"] == 0 and s["processing"] == 0:
        break
    time.sleep(3)
rid = real["report_ids"][0]
r = c.get(f"/api/reports/{rid}").json()
cand = next((x for x in cands if x["url"] == r["source_url"]), None)
check("source attribution: platform, post URL, author handle", r["source_type"] == "social_post" and "Mastodon" in (r["source_platform"] or "")
      and r["source_url"].startswith("https://") and (r["reporter_ref"] or "").startswith("@"))
check("original publication time preserved (post created_at)", cand and r["original_publication_time"] and r["original_publication_time"][:16] == cand["created_at"][:16],
      (r["original_publication_time"], cand and cand["created_at"]))
check("server submission time recorded separately", r["upload_time"] and r["upload_time"] != r["original_publication_time"])
check("media downloaded from the post and hashed", r["media_count"] >= 1 and all(m["sha256"] and (m["phash"] or m["frame_phashes"]) for m in r["media"]))
check("report flagged as real (not demo)", r["is_demo"] == 0)

print("== 9-13 pipeline: duplicates, location, external evidence, incident, dashboard")
check("processed by the automatic pipeline", r["processing_state"] in ("PROCESSED", "MAPPED"), r["processing_state"])
check("duplicate checks ran", {"exact_duplicate", "near_duplicate", "recycled"} <= {f["check_type"] for f in r["findings"]})
check("location investigated (claim geocoded inside India)", (r["claimed_geocode_label"] or "").endswith("India") and r["location_confidence"], r["claimed_geocode_label"])
inc = c.get(f"/api/incidents/{r['incident_id']}").json() if r["incident_id"] else None
check("associated with an incident", inc is not None)
check("external evidence investigated for that incident", inc and len(inc["evidence"]) >= 3 and {e["result_type"] for e in inc["evidence"]})
after = c.get("/api/summary").json()
check("dashboard summary reflects the new report(s)", after["reports"]["received_total"] == before + real["ingested"])
check("incident visible in the dashboard incident feed", any(i["id"] == r["incident_id"] for i in c.get("/api/incidents").json()))
again = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": TAGS, "limit": 40, "wait": True}).json()
check("re-run does not ingest the same post twice", again["ingested"] == 0 or not set(again["report_ids"]) & set(real["report_ids"]))

summary = {"result": "PASS" if all(x for _, x in results) else "FAIL", "report_id": rid, "post_url": r["source_url"], "author": r["reporter_ref"],
           "published": r["original_publication_time"], "category": r["category"], "claimed_location": r["claimed_location_text"],
           "geocoded_as": r["claimed_geocode_label"], "location_confidence": r["location_confidence"],
           "verification_status": r["effective_status"], "media_classification": r["media_classification"],
           "incident_id": r["incident_id"], "evidence": [(e["source_name"], e["stance"], e["result_type"]) for e in inc["evidence"]] if inc else [],
           "processing_ms": r["processing_ms"], "ingested_total": real["ingested"]}
print("\nRESULT:", json.dumps(summary, indent=1, ensure_ascii=False))
OUT.write_text(json.dumps(summary, default=str, ensure_ascii=False))
ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} real-world Mastodon checks passed")
sys.exit(0 if ok == len(results) else 1)
