"""Security / privacy suite. Run by run_tests.py (--suite security) against a fresh server started WITHOUT test mode,
with MAX_UPLOAD_MB=2 and the vision endpoint set to a PUBLIC address (must be refused, nothing sent).
Includes a real-browser XSS check (Playwright + Edge)."""
import io
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
DATA = Path(os.getenv("PRALAY_TEST_DATA_DIR", "."))
c = httpx.Client(base_url=API, timeout=60)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def uploads():
    return sorted(p.name for p in (DATA / "uploads").glob("*"))


def post(data, files):
    return c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "sec", **data}, files=files)


print("== Path traversal")
leak = False
for path in ["/..%2f..%2fbackend%2fapp%2fconfig.py", "/files/uploads/..%2f..%2fpralay.db", "/files/uploads/../pralay.db",
             "/assets/..%2f..%2f..%2fbackend%2f.env.example", "/%2e%2e/%2e%2e/backend/app/config.py", "/..\\..\\backend\\app\\config.py"]:
    r = c.get(path)
    body = r.content[:4000]
    if b"load_dotenv" in body or b"SQLite format" in body or b"VISION_PROVIDER" in body:
        leak = True
        print("   leaked via", path)
check("no source, config or database file served via traversal paths", not leak)

print("== Upload hardening")
before = uploads()
big = Image.effect_noise((1800, 1800), 100).convert("RGB")
b = io.BytesIO(); big.save(b, "JPEG", quality=100)
r = post({}, [("files", ("big.jpg", b.getvalue(), "image/jpeg"))])
check(f"oversized file rejected (limit 2 MB, sent {len(b.getvalue()) // 1048576 + 1} MB)", r.status_code == 422 and "exceeds" in r.text, r.text[:120])
r = post({}, [("files", ("shell.jpg", b"<?php system($_GET['c']); ?>", "image/jpeg"))])
check("script disguised as .jpg rejected", r.status_code == 422)
r = post({}, [("files", ("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "image/svg+xml"))])
check("SVG (script-capable) rejected", r.status_code == 422)
r = post({}, [("files", ("page.html", b"<html><script>alert(1)</script></html>", "image/jpeg"))])
check("HTML disguised as image rejected", r.status_code == 422)
bomb = io.BytesIO(); Image.new("1", (22000, 22000)).save(bomb, "PNG")
r = post({}, [("files", ("bomb.png", bomb.getvalue(), "image/png"))])
check(f"decompression bomb rejected ({len(bomb.getvalue()) // 1024} KB file, 484 MP)", r.status_code == 422, r.text[:120])
r = post({}, [("files", (f"f{i}.jpg", to_jpeg(scene(i, "flood")), "image/jpeg")) for i in range(6)])
check("more than 5 files rejected", r.status_code == 422)
check("rejected uploads leave no orphan files", uploads() == before, (len(before), len(uploads())))
r = post({}, [("files", ("../../../evil.jpg", to_jpeg(scene(7, "flood")), "image/jpeg"))])
check("traversal filename accepted only as metadata; stored under a server-generated name", r.status_code == 201 and not (DATA.parent / "evil.jpg").exists()
      and all(len(n.split(".")[0]) == 32 for n in uploads()), r.text[:120])
rid = r.json()["id"]

print("== Input / URL handling")
check("javascript: source URL rejected", post({"source_url": "javascript:alert(1)"}, [("files", ("a.jpg", to_jpeg(scene(8, "flood")), "image/jpeg"))]).status_code == 422)
check("out-of-range coordinates rejected", post({"claimed_lat": "95", "claimed_lon": "10"}, []).status_code == 422)
r = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": ["flood"], "dry_run": True, "instance": "https://127.0.0.1"}).json()
check("SSRF: connector refuses a loopback instance", r["status"] == "failed" and "refused" in " ".join(r["errors"]), r)
r = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": ["flood"], "dry_run": True, "instance": "https://169.254.169.254"}).json()
check("SSRF: connector refuses the cloud-metadata address", r["status"] == "failed", r)

print("== Test hooks & CORS")
for ep, body in [("/api/test/simulate-failure", {"sources": ["google_news"]}), ("/api/test/stall", {"match": "abc", "stage": "VALIDATION", "seconds": 1}),
                 ("/api/test/vision-mode", {"mode": "inline"})]:
    check(f"{ep} disabled outside test mode (403)", c.post(ep, json=body).status_code == 403)
r = c.get("/api/summary", headers={"Origin": "https://evil.example"})
check("CORS: foreign origin not allowed", "access-control-allow-origin" not in {k.lower() for k in r.headers})

print("== Local-only AI")
st = c.post("/api/vision/validate").json()
check("public-IP vision endpoint refused (BLOCKED_NON_LOCAL), no paid provider", st["state"] == "BLOCKED_NON_LOCAL" and st["paid"] is False, st["state"])
t0 = time.time()
while time.time() - t0 < 90:
    rep = c.get(f"/api/reports/{rid}").json()
    if rep["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
        break
    time.sleep(1)
va = (rep["visual_analysis"] or [{}])[0]
check("analysis unavailable, media not sent, pipeline completed", va.get("status") == "unavailable" and "BLOCKED_NON_LOCAL" in (va.get("error") or "")
      and rep["processing_state"] in ("PROCESSED", "MAPPED"), va)

print("== Secrets / logs")
blob = c.get("/api/meta").text + c.get("/api/summary").text + c.get("/api/vision/status").text + c.get("/api/sources").text
check("no credential-like values in API responses", not any(k in blob for k in ("API_KEY=", "BEARER", "APP_PASSWORD", "secret")))
import re  # noqa: E402
logs = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in DATA.glob("server_*.log"))
check("server logs contain no image payloads or credentials", not re.search(r"/9j/[A-Za-z0-9+/]{200,}|API_KEY=\S+", logs))

print("== Stored XSS (real browser)")
payload = '<img src=x onerror="window.__pralay_xss=1">'
r = c.post("/api/reports", data={"category": "fire", "claimed_location": f"Ludhiana {payload}", "description": f"desc {payload}",
                                 "claimed_lat": "30.90", "claimed_lon": "75.85"},
           files=[("files", ("x.jpg", to_jpeg(scene(31, "fire")), "image/jpeg"))])
xid = r.json()["id"]
t0 = time.time()
while time.time() - t0 < 90 and c.get(f"/api/reports/{xid}").json()["processing_state"] not in ("PROCESSED", "MAPPED"):
    time.sleep(1)
from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as pw:
    br = pw.chromium.launch(channel="msedge", headless=True)
    pg = br.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(API + "/?dtab=map#dashboard")
    pg.wait_for_selector(".feed-item", timeout=30000)
    # Selecting an incident pans the map to it — but only on a selection CHANGE, so select another incident first.
    others = pg.locator(".feed-item").filter(has_not_text="Ludhiana")
    if others.count():
        others.first.click()
        pg.wait_for_timeout(800)
    pg.locator(".feed-item").filter(has_text="Ludhiana").first.click()
    pg.wait_for_timeout(1500)
    box = pg.locator(".map").first.bounding_box()
    hit = False
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2  # the selected incident is centred on the map
    for attempt in range(6):  # data refreshes re-render markers every few seconds: settle, hover, re-check
        pg.mouse.move(cx - 40, cy - 40)
        pg.mouse.move(cx, cy, steps=4)
        pg.wait_for_timeout(400)
        if pg.locator(".leaflet-tooltip").filter(has_text="Ludhiana").count():
            hit = True
            break
    if not hit:
        pg.screenshot(path=str(DATA / "xss_hover_debug.png"))
        print("   debug: tooltips present:", pg.locator(".leaflet-tooltip").all_inner_texts()[:3])
    tip = pg.locator(".leaflet-tooltip").filter(has_text="Ludhiana").first.inner_text() if hit else ""
    xss = pg.evaluate("window.__pralay_xss === 1")
    pg.get_by_role("button", name="Open full review").click()
    pg.wait_for_timeout(1500)
    xss = xss or pg.evaluate("window.__pralay_xss === 1")
    br.close()
check("incident tooltip opened in the browser", hit)
check("payload shown as literal text in the tooltip (escaped)", "<img" in tip, tip[:120])
check("payload did not execute anywhere in the UI", not xss)

ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} security checks passed")
sys.exit(0 if ok == len(results) else 1)
