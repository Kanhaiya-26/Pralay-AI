"""Regression: report confidence must be shown for VERIFIED-cell / CORROBORATED reports exactly as for other statuses.
Isolated server + synthetic seed only.   Run:  python scripts/run_tests.py --suite confidence-verified"""
import sqlite3
import os
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
DATA = os.environ["PRALAY_TEST_DATA_DIR"]
c = httpx.Client(base_url=API, timeout=60)
res = []


def check(n, cond, d=""):
    res.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  — {d}" if d and not cond else ""))


for _ in range(180):  # wait for the background workers to finish the seeded scenario
    reps = c.get("/api/reports").json()
    if reps and not any(r["processing_state"] in ("RECEIVED", "PROCESSING") for r in reps):
        break
    time.sleep(1)
cor = next(r for r in reps if r["effective_status"] == "CORROBORATED" and r["grid_mapped"])
unv = next(r for r in reps if r["effective_status"] == "UNVERIFIED" and r["grid_cell_id"])
cells = {x["cell_id"]: x["cell_status"] for x in c.get("/api/grid/cells").json()["cells"]}
check("seed has a VERIFIED cell holding a CORROBORATED report", cells.get(cor["grid_cell_id"]) == "VERIFIED", cells)
cell = c.get(f"/api/grid/cells/{cor['grid_cell_id']}").json()
row = next(x for x in cell["reports"] if x["id"] == cor["id"])
check("1. VERIFIED-cell report: API returns its calculated confidence (same value as report API)",
      row.get("report_confidence") == cor["report_confidence"] is not None and row["confidence_unavailable_reason"] is None, row)
urow = next(x for x in c.get(f"/api/grid/cells/{unv['grid_cell_id']}").json()["reports"] if x["id"] == unv["id"])
check("3. UNVERIFIED report: confidence returned", urow["report_confidence"] == unv["report_confidence"] is not None, urow)
for bad in ("CONFIRMED", "VERIFIED"):
    r = c.post(f"/api/reports/{unv['id']}/review", json={"status": bad, "reason": "status enum probe"})
    check(f"4. '{bad}' is not a separate report status (review API rejects it, 422)", r.status_code == 422, r.status_code)
before = unv["report_confidence"]
c.post(f"/api/reports/{unv['id']}/review", json={"status": "CORROBORATED", "reason": "analyst confirmed on site (test)"}).raise_for_status()
time.sleep(1)
after = c.get(f"/api/reports/{unv['id']}").json()
check("5. UNVERIFIED -> CORROBORATED by review: status changes, confidence kept (not invented, not wiped)",
      after["effective_status"] == "CORROBORATED" and after["report_confidence"] is not None and after["report_confidence"] == before,
      (after["effective_status"], before, after["report_confidence"]))
again = c.get(f"/api/reports/{unv['id']}").json()
check("6. reload after status update: confidence still present", again["report_confidence"] == after["report_confidence"])
# 2. missing confidence: simulate a legacy row (isolated test DB only) and require an explicit reason, never a number
con = sqlite3.connect(os.path.join(DATA, "pralay.db")); con.execute("UPDATE reports SET report_confidence=NULL WHERE id=?", (cor["id"],)); con.commit(); con.close()
miss = next(x for x in c.get(f"/api/grid/cells/{cor['grid_cell_id']}").json()["reports"] if x["id"] == cor["id"])
check("2. VERIFIED-cell report with missing confidence: null + explicit reason, no arbitrary score",
      miss["report_confidence"] is None and miss["confidence_unavailable_reason"], miss)

with sync_playwright() as pw:
    br = pw.chromium.launch(channel="msedge", headless=True); pg = br.new_page(viewport={"width": 1500, "height": 950})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(API + "/#grid"); pg.wait_for_timeout(2000)
    pg.locator("tr.click", has_text=cor["grid_cell_id"]).first.click(); pg.wait_for_timeout(1500)
    txt = pg.locator("h4:has-text('Reports in this cell') + table").inner_text()
    check("browser: VERIFIED cell panel shows missing-confidence reason for the nulled report", "confidence unavailable" in txt, txt[:300])
    other = next(x for x in cell["reports"] if x["id"] != cor["id"] and x["report_confidence"] is not None)
    check("browser: VERIFIED cell panel shows numeric confidence for the other reports", str(round(other["report_confidence"])) in txt, txt[:300])
    pg.goto(API + "/#pipeline"); pg.wait_for_timeout(1500)
    rows = pg.locator("tbody tr.click")
    next(rows.nth(i) for i in range(rows.count()) if rows.nth(i).locator("td").nth(1).inner_text().split("\n")[0].strip() == str(unv["id"])).click()
    pg.wait_for_timeout(1500)
    kv = pg.locator(".kv").first.inner_text()
    check("browser: report drawer for the newly CORROBORATED report shows its confidence", str(round(after["report_confidence"])) in kv, kv[-120:])
    check("browser: no page errors", not errs, errs)
    br.close()
print(f"\n{sum(res)}/{len(res)} confidence checks passed")
sys.exit(0 if all(res) else 1)
