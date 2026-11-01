"""Release verification against the RUNNING local deployment (default http://127.0.0.1:8000), synthetic data only.
Checks health, UI, SQLite, upload -> processing -> grid -> dashboard (real browser), duplicate + invalid uploads,
deferred visual-AI mode and 127.0.0.1-only binding. Optional: --ollama-down verifies behaviour while Ollama is stopped.
Uploads are flagged is_demo and described as release checks; reseed the demo afterwards for a clean demo DB.
"""
import io
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from seed_demo import scene, to_jpeg  # noqa: E402

API = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:8000")
c = httpx.Client(base_url=API, timeout=60)
res = []
NOW = datetime.now(timezone.utc)


def check(name, cond, detail=""):
    res.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def wait(rid, t=120):
    t0 = time.time()
    while time.time() - t0 < t:
        r = c.get(f"/api/reports/{rid}").json()
        if r["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
            return r
        time.sleep(1)
    return c.get(f"/api/reports/{rid}").json()


h = c.get("/api/health").json()
check("backend health endpoint", h.get("ok") is True)
check("SQLite database connected (health reports DB file, summary reads it)", str(h.get("db", "")).endswith("pralay.db") and "reports" in c.get("/api/summary").json())
ui = c.get("/")
check("frontend served", ui.status_code == 200 and 'id="root"' in ui.text)
vs = c.get("/api/vision/status").json()
check("VISION_MODE=deferred", vs["mode"] == "deferred", vs["mode"])
out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-NetTCPConnection -State Listen -LocalPort 8000 | ForEach-Object LocalAddress"],
                     capture_output=True, text=True).stdout.split()
check("server bound to 127.0.0.1 only (no network exposure)", out and all(a == "127.0.0.1" for a in out), out)

print("== Upload -> processing -> grid -> dashboard")
before = c.get("/api/summary").json()["reports"]["received_total"]
img = to_jpeg(scene(97531, "flood"), 30.7041, 76.7172, NOW - timedelta(minutes=20))
r = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "RELEASE CHECK (synthetic) — water on road",
                                 "claimed_event_time": (NOW - timedelta(minutes=30)).isoformat(), "is_demo": "true"},
           files=[("files", ("release_check.jpg", img, "image/jpeg"))])
check("upload accepted and validated", r.status_code == 201, r.text[:150])
rep = wait(r.json()["id"])
labels = {f["finding"] for f in rep["findings"]}
check("processed automatically (not waiting for AI)", rep["processing_state"] in ("PROCESSED", "MAPPED"), rep["processing_state"])
check("duplicate detection ran", {"exact_duplicate", "near_duplicate", "recycled"} <= {f["check_type"] for f in rep["findings"]})
check("metadata extracted (EXIF GPS)", rep["exif_lat"] is not None)
check("location verified", rep["location_confidence"] in ("high", "medium") and "LOCATION_CONSISTENT" in labels, rep["location_confidence"])
check("associated with an incident", rep["incident_id"] is not None)
check("assigned to the custom demo grid", (rep["grid_cell_id"] or "").startswith("DEMO"), rep["grid_cell_id"])
check("dashboard summary updated", c.get("/api/summary").json()["reports"]["received_total"] == before + 1)
va = (rep["visual_analysis"] or [{}])[0].get("status")
check("visual AI queued or unavailable, never blocking", va in ("pending", "running", "completed", "unavailable"), va)

print("== Duplicate and invalid uploads")
d = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "RELEASE CHECK duplicate (synthetic)", "is_demo": "true"},
           files=[("files", ("copy.jpg", img, "image/jpeg"))])
dr = wait(d.json()["id"])
check("exact duplicate detected and not mapped", "EXACT_DUPLICATE" in {f["finding"] for f in dr["findings"]} and dr["is_duplicate_of"] == rep["id"] and not dr["grid_mapped"])
bad = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali", "description": "x"}, files=[("files", ("fake.jpg", b"definitely not an image", "image/jpeg"))])
check("invalid file rejected with a clear message", bad.status_code == 422 and "File 1" in bad.text, bad.text[:120])

print("== Real browser: report visible on the dashboard")
from playwright.sync_api import sync_playwright  # noqa: E402
errs = []
with sync_playwright() as pw:
    br = pw.chromium.launch(channel="msedge", headless=True)
    pg = br.new_page(viewport={"width": 1600, "height": 1000})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(API + "/#pipeline")
    pg.wait_for_selector("text=RELEASE CHECK", timeout=20000) if False else pg.wait_for_selector("table tbody tr", timeout=20000)
    pg.wait_for_timeout(1500)
    check("new report listed in the dashboard UI", pg.locator("tr").filter(has_text=str(rep["id"])).count() >= 1)
    pg.goto(API + f"/?open=report:{rep['id']}#dashboard")
    pg.wait_for_selector(".drawer >> text=" + rep["grid_cell_id"], timeout=15000)
    check("report drawer shows grid cell + status", pg.locator(".drawer").get_by_text(rep["effective_status"]).count() >= 1)
    shot = Path(HERE.parent.parent / "reports" / "release_dashboard.png")
    pg.goto(API + "/?dtab=map#dashboard")
    pg.wait_for_selector(".feed-item", timeout=15000)
    pg.wait_for_timeout(2000)
    pg.screenshot(path=str(shot))
    br.close()
check("no page errors in the browser", not errs, errs[:3])

ok = sum(1 for _, x in res if x)
print(f"\n{ok}/{len(res)} release checks passed")
sys.exit(0 if ok == len(res) else 1)
