"""End-to-end runtime test of PRALAY AI flows A-F against a running server (seed_demo.py first).

    .venv\\Scripts\\python.exe scripts\\e2e_test.py
Prints PASS/FAIL per check and exits non-zero on any failure.
"""
import io
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=120)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def wait_idle(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get("/api/summary").json()["reports"]
        if s["awaiting"] == 0 and s["processing"] == 0:
            return True
        time.sleep(2)
    return False


def rep(i):
    return c.get(f"/api/reports/{i}").json()


def labels(r):
    return {f["finding"] for f in r["findings"]}


print("== Startup")
check("backend health", c.get("/api/health").json()["ok"])
meta = c.get("/api/meta").json()
check("grid labelled non-official", "NOT AN OFFICIAL" in meta["grid"]["label"] and meta["capabilities"]["official_grid"] is False)
check("automatic processing drains queue", wait_idle())

print("== Seeded scenario (Flow A / C / E)")
r1, r4, r5, r6, r7, r8 = rep(1), rep(4), rep(5), rep(6), rep(7), rep(8)
check("Mohali reports 1-3 corroborated & mapped",
      all(rep(i)["effective_status"] == "CORROBORATED" and rep(i)["grid_mapped"] == 1 for i in (1, 2, 3)))
check("EXIF GPS extracted", r1["exif_lat"] is not None and r1["location_confidence"] == "high")
check("report 4 exact duplicate, not mapped", "EXACT_DUPLICATE" in labels(r4) and r4["is_duplicate_of"] == 1 and r4["grid_mapped"] == 0)
check("report 5 recycled media contradicted", "PREVIOUSLY_KNOWN_RECYCLED" in labels(r5) and r5["effective_status"] == "CONTRADICTED")
check("report 6 location contradiction", "LOCATION_CONTRADICTION" in labels(r6) and r6["effective_status"] == "CONTRADICTED")
check("report 6 shown in claimed (Chandigarh) cell", 30 < (r6["claimed_lat"] or 0) < 31 and r6["grid_cell_id"].split("-R")[1].startswith("09"), r6["grid_cell_id"])
check("report 7 old media contradicted", "OLD_MEDIA" in labels(r7) and r7["effective_status"] == "CONTRADICTED")
check("report 8 unverified, claim-only location", r8["effective_status"] == "UNVERIFIED" and r8["location_confidence"] == "low")
check("missing EXIF = unavailable evidence, not fake", "METADATA_UNAVAILABLE" in labels(r8) and r8["effective_status"] != "CONTRADICTED")
check("visual AI honest fallback", all(v["status"] in ("unavailable", "completed", "failed") for v in r1["visual_analysis"]))
stages = {h["stage"] for h in r1["status_history"]}
check("all pipeline stages recorded", set(meta["pipeline_stages"]) <= stages, str(set(meta["pipeline_stages"]) - stages))
inc1 = c.get("/api/incidents/1").json()
check("incident 1 has 4 reports (incl. duplicate) + external evidence", inc1["report_count"] >= 4 and len(inc1["evidence"]) >= 3)
check("separate incident vs report confidence", inc1["confidence_score"] is not None and r1["report_confidence"] is not None)
check("syndication-aware independence groups stored", all("independence_group" in e for e in inc1["evidence"]))

print("== Validation (Flow A/C)")
bad = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali"}, files=[("files", ("x.jpg", b"not an image", "image/jpeg"))])
check("rejects non-image disguised as jpg", bad.status_code == 422)
bad2 = c.post("/api/reports", data={"category": "volcano", "claimed_location": "Mohali", "description": "x"})
check("rejects unknown category", bad2.status_code == 422)
for label, data in (("missing", {}), ("whitespace-only", {"claimed_location": "   "}),
                    ("device GPS only (GPS does not replace the typed location)", {"browser_lat": "30.70", "browser_lon": "76.72", "browser_accuracy_m": "20"})):
    nl = c.post("/api/reports", data={"category": "flood", "description": "no location", **data})
    check(f"rejects {label} location with the required-location message", nl.status_code == 422 and nl.json().get("detail") == "Location is required to submit a report.", nl.text[:120])
png = io.BytesIO(); scene(55, "flood").save(png, "PNG")
rr = c.post("/api/reports", data={"category": "flood", "claimed_location": "Kharar, Punjab", "description": "Street flooding"},
            files=[("files", ("a.png", png.getvalue(), "image/png"))])
check("accepts PNG upload", rr.status_code == 201)

print("== Active disaster processing (priority + auto mapping)")
now = datetime.now(timezone.utc)
before_v = c.get("/api/summary").json()["version"]
nr = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "Water rising near bus stand",
                                  "claimed_event_time": (now - timedelta(hours=1)).isoformat()},
            files=[("files", ("new.jpg", to_jpeg(scene(2024, "flood"), 30.7010, 76.7150, now - timedelta(minutes=50)), "image/jpeg"))]).json()
check("new report near active incident gets priority 1", nr["priority"] == 1)
wait_idle()
n = rep(nr["id"])
check("new report auto-processed → CORROBORATED → MAPPED", n["processing_state"] == "MAPPED" and n["effective_status"] == "CORROBORATED", n["processing_state"])
check("dashboard change counter advanced", c.get("/api/summary").json()["version"] > before_v)

print("== Investigation + shared intelligence reuse (Flow B / F)")
a1 = c.post("/api/chat", json={"query": "Is there flooding in Mohali?"}).json()
check("chat finds Mohali incident", any(i["id"] == 1 for i in a1["incidents"]), a1.get("answer"))
check("chat returns sources with timestamps", a1["sources"] and all(s["retrieval_time"] for s in a1["sources"]))
a2 = c.post("/api/chat", json={"query": "Are there flood incidents near Mohali?"}).json()
check("equivalent question reuses exact query cache", a2["provenance"].get("query_cache") == "hit", a2["provenance"])
a3 = c.post("/api/chat", json={"query": "any flooding in Mohali this week"}).json()
acts = [x["action"] for x in a3["provenance"].get("incidents", [])]
check("different window reuses incident evidence cache", a3["provenance"].get("query_cache") == "miss" and "reused from shared intelligence" in acts, acts)
d = c.post("/api/chat", json={"query": "Are there duplicate images?"}).json()
check("chat lists duplicates", any(x["report_id"] == 4 for x in d["items"]))
g = c.post("/api/chat", json={"query": "Which grid cells contain corroborated incidents?"}).json()
check("chat lists verified grid cells", len(g["items"]) >= 1)

print("== Incident review / override (Flow D)")
o = c.post("/api/reports/8/review", json={"status": "CORROBORATED", "reason": "Confirmed by HP SDMA control room call", "reviewer": "e2e"})
r8b = rep(8)
check("override applied with audit trail", o.status_code == 200 and r8b["effective_status"] == "CORROBORATED" and r8b["review_history"])
check("override without reason rejected", c.post("/api/reports/8/review", json={"status": "CONTRADICTED", "reason": ""}).status_code == 422)
c.post("/api/reports/8/review", json={"status": None, "reason": "Clearing test override", "reviewer": "e2e"})
check("override cleared", rep(8)["effective_status"] == "UNVERIFIED")

print("== Grid (Flow E)")
cells = c.get("/api/grid/cells").json()["cells"]
mohali = next(x for x in cells if x["cell_id"] == r1["grid_cell_id"])
check("Mohali cell aggregates verified reports", mohali["mapped_verified"] >= 4 and mohali["cell_status"] == "VERIFIED", mohali)
check("category filter", all(set(x["categories"]) == {"fire"} for x in c.get("/api/grid/cells", params={"category": "fire"}).json()["cells"]))
check("status filter", all(x["CONTRADICTED"] == x["reports"] for x in c.get("/api/grid/cells", params={"status": "CONTRADICTED"}).json()["cells"]))
check("time filter (future 'since' → empty)", c.get("/api/grid/cells", params={"since": (now + timedelta(days=1)).isoformat()}).json()["cells"] == [])
check("location (bbox) filter", all(x["cell_id"] for x in c.get("/api/grid/cells", params={"bbox": "30.5,76.5,30.9,76.9"}).json()["cells"]))
det = c.get(f"/api/grid/cells/{r1['grid_cell_id']}").json()
check("cell inspection lists reports & incidents", det["reports"] and det["incidents"] and det["mapping_history"])
cfg = c.post("/api/grid/config", json={"resolution_deg": 0.5}).json()
check("grid reconfigurable (0.5°) and remaps", cfg["resolution_deg"] == 0.5 and rep(1)["grid_cell_id"].startswith(f"DEMO{cfg['version']}-"))
c.post("/api/grid/config", json={"resolution_deg": 0.25})

wait_idle()
ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} checks passed")
sys.exit(0 if ok == len(results) else 1)
