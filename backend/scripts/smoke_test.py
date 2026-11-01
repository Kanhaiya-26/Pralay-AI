"""Fast, offline smoke test (used by GitHub Actions CI and usable locally). Expects a server already running with
EXTERNAL_LOOKUPS=false and VISION_PROVIDER=none. Checks health, UI, upload -> processing -> dashboard, AI-disabled fallback.

    python scripts/smoke_test.py http://127.0.0.1:8000
"""
import io
import sys
import time

import httpx
from PIL import Image, ImageDraw

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=60)
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


for _ in range(60):
    try:
        if c.get("/api/health").json().get("ok"):
            break
    except httpx.HTTPError:
        time.sleep(1)
check("health endpoint", c.get("/api/health").json().get("ok") is True)
ui = c.get("/")
check("UI served", ui.status_code == 200 and 'id="root"' in ui.text, ui.status_code)
im = Image.new("RGB", (640, 420), (90, 110, 140))
ImageDraw.Draw(im).rectangle([0, 260, 640, 420], fill=(100, 92, 60))
b = io.BytesIO(); im.save(b, "JPEG")
r = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "claimed_lat": "30.70", "claimed_lon": "76.72",
                                 "description": "CI smoke test (synthetic)", "is_demo": "true"}, files=[("files", ("ci.jpg", b.getvalue(), "image/jpeg"))])
check("upload accepted", r.status_code == 201, r.text[:200])
rid = r.json().get("id")
rep = {}
for _ in range(90):
    rep = c.get(f"/api/reports/{rid}").json()
    if rep["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
        break
    time.sleep(1)
check("report processed automatically", rep.get("processing_state") in ("PROCESSED", "MAPPED"), rep.get("processing_state"))
check("duplicate checks ran", {"exact_duplicate", "near_duplicate"} <= {f["check_type"] for f in rep.get("findings", [])})
check("AI disabled -> analysis 'unavailable', pipeline unaffected", (rep.get("visual_analysis") or [{}])[0].get("status") == "unavailable")
check("dashboard summary counts the report", c.get("/api/summary").json()["reports"]["received_total"] >= 1)
check("invalid file rejected", c.post("/api/reports", data={"category": "flood", "claimed_location": "X", "description": "x"},
                                      files=[("files", ("x.jpg", b"not an image", "image/jpeg"))]).status_code == 422)
print(f"\n{'OK' if not fails else 'FAILED'}: {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
