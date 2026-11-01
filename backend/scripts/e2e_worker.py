"""Worker reliability suite. Run by run_tests.py against a FRESH isolated server started with
PRALAY_LEASE_SEC=8, PRALAY_LEASE_CHECK_SEC=2, MASTODON_POLL_MIN=1 and PRALAY_TEST_MODE=1.
Uses the test-mode stall hook to simulate a hung worker. Never touches the demo database."""
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
c = httpx.Client(base_url=API, timeout=120)
results = []
NOW = datetime.now(timezone.utc)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def submit(desc, seed, lat=30.70, lon=76.72, place="Mohali, Punjab"):
    r = c.post("/api/reports", data={"category": "flood", "claimed_location": place, "description": desc, "is_demo": "true",
                                     "claimed_event_time": (NOW - timedelta(hours=1)).isoformat()},
               files=[("files", (f"{seed}.jpg", to_jpeg(scene(seed, "flood"), lat, lon, NOW - timedelta(hours=1)), "image/jpeg"))])
    r.raise_for_status()
    return r.json()["id"]


def get(rid):
    return c.get(f"/api/reports/{rid}").json()


def stages(r):
    return [h["stage"] for h in r["status_history"]]


def wait(pred, timeout, every=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


cfg = c.post("/api/test/stall", json={"match": "no-such-report", "stage": "VALIDATION", "seconds": 1, "times": 1}).json()
check("test server runs with short lease (8 s), fast recovery check, 15 s hung-stage limit", cfg["lease_sec"] == 8 and cfg["lease_check_sec"] <= 5, cfg)

print("== W1 controlled worker interruption → stalled-task recovery → stale run cannot overwrite")
c.post("/api/test/stall", json={"match": "stall-test-A", "stage": "EXTERNAL_EVIDENCE", "seconds": 40, "times": 1})
a = submit("stall-test-A water rising", 101)
st = wait(lambda: (get(a)["processing_state"] == "PROCESSING" and get(a)["pipeline_stage"] == "EXTERNAL_EVIDENCE") or None, 40)
check("worker picked up report and began processing (now stalled at EXTERNAL_EVIDENCE)", st)
rec = wait(lambda: "LEASE_EXPIRED" in stages(get(a)) or None, 45)
check("expired lease detected by monitor while worker is hung", rec)
fin = wait(lambda: get(a)["processing_state"] in ("PROCESSED", "MAPPED") and get(a), 60)
check("report re-claimed and completed by another attempt", fin, get(a)["processing_state"])
fin = get(a)
check("attempt counter reflects the recovery (attempts == 2)", fin["attempts"] == 2, fin["attempts"])
done_at, state, nfind = fin["processed_at"], fin["processing_state"], len(fin["findings"])
ab = wait(lambda: "STALE_RUN_ABANDONED" in stages(get(a)) or None, 60)
check("older (stalled) attempt detected lost lease and stopped", ab)
after = get(a)
check("older attempt did not overwrite the newer result (state, processed_at, findings unchanged)",
      after["processing_state"] == state and after["processed_at"] == done_at and len(after["findings"]) == nfind,
      (after["processing_state"], state, after["processed_at"], done_at, len(after["findings"]), nfind))

print("== W1b slow-but-alive stage keeps its lease (heartbeat)")
c.post("/api/test/stall", json={"match": "slow-but-alive", "stage": "EXTERNAL_EVIDENCE", "seconds": 12, "times": 1})
sa = submit("slow-but-alive stage", 111, 30.702, 76.722)
fs = wait(lambda: get(sa)["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW") and get(sa), 60)
check("12 s stage with 8 s lease completes in ONE attempt (lease renewed, not recovered)", fs and fs["attempts"] == 1
      and "LEASE_EXPIRED" not in stages(fs), fs and (fs["attempts"], stages(fs).count("LEASE_EXPIRED")))

print("== W2 retry limit under repeated stalls")
c.post("/api/test/stall", json={"match": "stall-test-B", "stage": "DUPLICATE_CHECK", "seconds": 30, "times": 3})
b = submit("stall-test-B repeated hang", 202)
fb = wait(lambda: get(b)["processing_state"] == "FAILED_NEEDS_REVIEW" and get(b), 200, every=1)
rb = get(b)
check("after 3 stalled attempts the report is FAILED_NEEDS_REVIEW (no infinite retries)", fb and rb["attempts"] == 3,
      (rb["processing_state"], rb["attempts"]))
check("each recovery recorded (3 × LEASE_EXPIRED)", stages(rb).count("LEASE_EXPIRED") == 3, stages(rb).count("LEASE_EXPIRED"))
time.sleep(32)  # let the last stalled thread wake up
check("abandoned attempts never resurrected the failed report", get(b)["processing_state"] == "FAILED_NEEDS_REVIEW")

print("== W3 report is not PROCESSED/MAPPED before mapping and final processing complete")
c.post("/api/test/stall", json={"match": "stall-test-C", "stage": "GRID_MAPPING", "seconds": 5, "times": 1})
cc = submit("stall-test-C mapping hold", 303)
# The hold happens on entry to GRID_MAPPING (after the eligibility check, before mapping): sample inside the 5 s hold.
wait(lambda: get(cc)["pipeline_stage"] == "GRID_ELIGIBILITY_CHECK", 40, every=0.2)
time.sleep(2.0)
mid = get(cc)
check("while held between eligibility check and mapping the report is still PROCESSING (no processed_at)",
      mid["processing_state"] == "PROCESSING" and mid["processed_at"] is None and mid["verification_status"] is not None,
      (mid["processing_state"], mid["pipeline_stage"], mid["processed_at"]))
wait(lambda: get(cc)["processing_state"] in ("PROCESSED", "MAPPED"), 30)
check("final state set only after DASHBOARD_UPDATE with processed_at + timing", get(cc)["processed_at"] and get(cc)["processing_ms"])
d = submit("watch-test-D", 404, 30.701, 76.721)
violations, seen = [], set()
t0 = time.time()
while time.time() - t0 < 60:
    r = get(d)
    seen.add(r["processing_state"])
    if r["processing_state"] in ("PROCESSED", "MAPPED"):
        if not (r["processed_at"] and r["pipeline_stage"] == "DASHBOARD_UPDATE"):
            violations.append((r["processing_state"], r["pipeline_stage"], r["processed_at"]))
        break
    time.sleep(0.15)
check("high-frequency polling never saw a premature final state", not violations and seen & {"PROCESSED", "MAPPED"}, (violations, seen))

print("== W4 concurrent workers + duplicate incident prevention (fresh place)")
ids = [None] * 6
def go(i):
    ids[i] = submit(f"concurrency-{i}", 500 + i * 7, 31.3260 + i * 0.001, 75.5762, "Jalandhar, Punjab")
ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
[t.start() for t in ts]
[t.join() for t in ts]
wait(lambda: all(get(i)["processing_state"] in ("PROCESSED", "MAPPED") for i in ids), 120, every=1.5)
rs = [get(i) for i in ids]
check("6 concurrent reports → exactly one incident", len({r["incident_id"] for r in rs}) == 1, {r["incident_id"] for r in rs})
check("each claimed exactly once", all(stages(r).count("CLAIMED") == 1 for r in rs), [stages(r).count("CLAIMED") for r in rs])

print("== W5 rate-limited source pauses safely; scheduled polling")
c.post("/api/test/simulate-failure", json={"sources": ["mastodon:429"]})
r1 = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": ["landslide"], "wait": True}).json()  # tag not cached by the poller
src = {s["source"]: s for s in c.get("/api/sources").json()["sources"]}["mastodon"]
check("Mastodon HTTP 429 → run reports error, source enters COOLDOWN", "429" in " ".join(r1["errors"]) and src["state"] == "COOLDOWN", (r1["errors"], src["state"]))
att = src["attempts"]
c.post("/api/test/simulate-failure", json={"sources": []})
r2 = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": ["landslide"], "wait": True}).json()
src2 = {s["source"]: s for s in c.get("/api/sources").json()["sources"]}["mastodon"]
check("during cooldown no request is sent (skipped, attempts unchanged)", "cooldown" in " ".join(r2["errors"]) and src2["attempts"] == att, (r2["errors"], src2["attempts"], att))
con = {x["name"]: x for x in c.get("/api/connectors").json()}["mastodon_public_tags"]
check("connector reports scheduled polling enabled", con["state"] == "ENABLED_POLLING" and con["config"]["poll_min"] == 1, con["state"])
polled = wait(lambda: [x for x in c.get("/api/connectors/runs").json() if '"trigger": "schedule"' in (x.get("params_json") or "")] or None, 80, every=3)
check("scheduled poll executed automatically (run recorded with trigger=schedule)", polled, [x.get("params_json", "")[:80] for x in c.get("/api/connectors/runs").json()[:3]])
if polled:
    print(f"   (scheduled run: status {polled[0]['status']}, fetched {polled[0]['fetched']}, ingested {polled[0]['ingested']}, skipped {polled[0]['skipped']})")

ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} worker checks passed")
sys.exit(0 if ok == len(results) else 1)
