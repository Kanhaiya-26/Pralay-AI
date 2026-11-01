"""Browser UI test (Playwright driving the installed Microsoft Edge) against an isolated, seeded server.
Exercises real UI interactions — file picker uploads, progress, live tracker, drawers, mini-map, grid filters
and reconfiguration, Sources & AI, chatbot, panel resizing, theme switching — and records console errors.

    .venv\\Scripts\\python.exe scripts\\run_tests.py --suite ui
"""
import io
import os
import struct
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
ART = Path(os.getenv("PRALAY_TEST_DATA_DIR", tempfile.gettempdir())) / "ui_artifacts"
ART.mkdir(parents=True, exist_ok=True)
NOW = datetime.now(timezone.utc)
results, console_errors = [], []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


tmp = Path(tempfile.mkdtemp(prefix="pralay_ui_files_"))
img_path = tmp / "citizen_mohali.jpg"
img_path.write_bytes(to_jpeg(scene(8080, "flood"), 30.7030, 76.7170, NOW - timedelta(minutes=40)))
big_path = tmp / "large_photo.jpg"  # ~8 MB noise image so upload progress is observable under throttling
Image.fromarray(np.random.default_rng(1).integers(0, 255, (2600, 2600, 3), dtype=np.uint8)).save(big_path, "JPEG", quality=97)
bad_path = tmp / "not_really.jpg"
bad_path.write_text("this is plain text pretending to be a photo")
import cv2  # noqa: E402
vid_path = tmp / "street.mp4"
w = cv2.VideoWriter(str(vid_path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
for i in range(30):
    f = np.full((240, 320, 3), (110, 95, 70), np.uint8)
    cv2.rectangle(f, (i * 6, 120), (i * 6 + 50, 220), (190, 170, 60), -1)
    w.write(f)
w.release()

import httpx  # noqa: E402
_t0 = time.time()
while time.time() - _t0 < 240:  # wait for the seeded reports to finish processing before driving the UI
    _s = httpx.get(API + "/api/summary", timeout=30).json()["reports"]
    if _s["awaiting"] == 0 and _s["processing"] == 0 and _s["processed"] >= 8:
        break
    time.sleep(2)

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
    page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e} | {(e.stack or '')[:500]}"))

    print("== Load + dashboard")
    page.goto(API + "/?dtab=overview#dashboard")
    page.wait_for_selector(".stat.kpi .v", timeout=20000)
    received = int(page.locator(".stat").filter(has_text="Total Reports").locator(".v").inner_text())
    check("application loads; dashboard shows database metrics", received >= 8, received)
    check("status strip shows measured processing + source health (System Health section)",
          page.get_by_text("Processing (measured)").count() == 1 and page.get_by_text("External sources & AI").count() == 1)
    page.get_by_role("tab", name="GIS Map").click()  # redesigned dashboard: feed + map + detail live on the GIS Map tab
    page.wait_for_function("document.querySelectorAll('.feed-item').length >= 3", timeout=20000)
    check("incident feed populated", page.locator(".feed-item").count() >= 3)
    check("map + demo-grid banner rendered", page.locator(".leaflet-container").count() >= 1 and page.get_by_text("NOT AN OFFICIAL IMD/NDMA GRID").count() >= 1)
    page.screenshot(path=str(ART / "01_dashboard.png"))

    print("== Panel resizing (drag splitter)")
    pane = page.locator(".split > .pane").first
    w0 = pane.bounding_box()["width"]
    sp = page.locator(".splitter").first.bounding_box()
    page.mouse.move(sp["x"] + sp["width"] / 2, sp["y"] + sp["height"] / 2)
    page.mouse.down()
    page.mouse.move(sp["x"] + 130, sp["y"] + sp["height"] / 2, steps=8)
    page.mouse.up()
    w1 = pane.bounding_box()["width"]
    check("dragging the divider widens the incident feed", w1 > w0 + 80, (w0, w1))
    page.reload()
    page.wait_for_selector(".split > .pane")
    w2 = page.locator(".split > .pane").first.bounding_box()["width"]
    check("panel width persisted across reload", abs(w2 - w1) < 5, (w1, w2))

    print("== Incident detail")
    page.locator(".feed-item").filter(has_text="Mohali").first.click()
    page.get_by_role("button", name="Open full review").click()
    page.wait_for_selector(".drawer >> text=External evidence", timeout=15000)
    check("incident review drawer shows evidence table with result types", page.locator(".drawer table").count() >= 2 and page.locator(".drawer").get_by_text("no match").count() + page.locator(".drawer").get_by_text("match").count() >= 1)
    check("separate incident & report confidence visible", page.locator(".drawer").get_by_text("Incident confidence").count() >= 1 and page.locator(".drawer").get_by_text("Report conf.").count() >= 1)
    page.screenshot(path=str(ART / "02_incident_drawer.png"))
    page.keyboard.press("Escape")

    print("== Report submission via file picker, progress, live tracker")
    page.goto(API + "/#submit")
    page.wait_for_selector("input[type=file]")
    expected_err_start = len(console_errors)
    page.set_input_files("input[type=file]", str(bad_path))
    page.get_by_placeholder("e.g. Phase 7, Mohali, Punjab").fill("Mohali, Punjab")
    page.locator("form button.btn.primary").click()
    page.wait_for_selector(".err", timeout=15000)
    check("disguised text file rejected with a per-file message", "File 1" in page.locator(".err").first.inner_text(), page.locator(".err").first.inner_text())
    # The browser logs the deliberate HTTP 422 of this step as a console error; only that expected entry is excused.
    expected_errs = [e for e in console_errors[expected_err_start:] if "422" in e]
    for e in expected_errs:
        console_errors.remove(e)

    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 20, "downloadThroughput": -1, "uploadThroughput": 600 * 1024})
    page.set_input_files("input[type=file]", [str(img_path), str(big_path)])
    page.wait_for_selector("text=✔ ready")
    check("file picker lists both files as ready with size/type", page.locator("text=✔ ready").count() == 2)
    page.get_by_label("Disaster category").select_option("flood")
    page.get_by_placeholder("e.g. Phase 7, Mohali, Punjab").fill("Mohali, Punjab")
    page.get_by_placeholder("What do you see? Water depth, damage, road status…").fill("UI test: water up to the knees near the bus stand")
    page.locator("form button.btn.primary").click()
    seen_progress = None
    t0 = time.time()
    while time.time() - t0 < 60:
        txt = page.locator("form").inner_text()
        if "Uploading…" in txt and "%" in txt:
            import re
            m = re.search(r"Uploading… (\d+)%", txt)
            if m and 0 < int(m.group(1)) < 100:
                seen_progress = int(m.group(1))
                break
        time.sleep(0.2)
    check("upload progress percentage visible during upload", seen_progress is not None, seen_progress)
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
    page.wait_for_selector("text=received", timeout=90000)
    check("submission confirmed with report id and stored-file details", page.get_by_text("file(s) stored").count() == 1)
    stage_seen = False
    t0 = time.time()
    while time.time() - t0 < 120:
        if page.locator(".stage.cur, .stage.done").count() > 0:
            stage_seen = True
        if page.get_by_text("processed in").count():
            break
        time.sleep(0.5)
    check("live tracker showed pipeline stage progress", stage_seen)
    check("tracker reached a final state with measured time", page.get_by_text("processed in").count() == 1)
    check("file picker cleared after successful submit", page.evaluate("document.querySelector('input[type=file]').files.length") == 0)
    page.screenshot(path=str(ART / "03_submit_tracker.png"))

    print("== Video upload via file picker")
    page.goto(API + "/#submit")
    page.wait_for_selector("input[type=file]")
    page.set_input_files("input[type=file]", str(vid_path))
    page.get_by_placeholder("e.g. Phase 7, Mohali, Punjab").fill("Zirakpur, Punjab")
    page.get_by_placeholder("What do you see? Water depth, damage, road status…").fill("UI test video")
    page.locator("form button.btn.primary").click()
    page.wait_for_selector("text=processed in", timeout=120000)
    check("video submitted through the UI and processed", "video" in page.locator("form").inner_text())
    page.get_by_role("button", name="Open full investigation →").click()
    page.locator(".drawer .tab", has_text="media").click()
    page.wait_for_selector(".drawer video", timeout=10000)
    check("video playable in investigation drawer + video metadata check listed", page.locator(".drawer video").count() >= 1 and page.locator(".drawer").get_by_text("VIDEO", exact=False).count() >= 1)
    page.keyboard.press("Escape")

    print("== Location comparison mini-map")
    page.goto(API + "/?open=report:6#dashboard")
    page.wait_for_selector(".drawer")
    page.locator(".drawer .tab", has_text="location").click()
    page.wait_for_selector(".drawer .leaflet-container", timeout=15000)
    try:  # OSM tiles come over the network: wait for them instead of a fixed sleep
        page.wait_for_selector(".drawer .leaflet-container img.leaflet-tile-loaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(500)
    # Leaflet uses the canvas renderer (preferCanvas), so markers are pixels on a <canvas>, not countable SVG paths.
    tiles = page.locator(".drawer .leaflet-container img.leaflet-tile-loaded").count()
    canv = page.locator(".drawer .leaflet-container canvas").count()
    painted = page.evaluate("""() => { const c = document.querySelector('.drawer .leaflet-container canvas'); if (!c) return 0;
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }""")
    check("mini-map renders: tiles loaded and markers/rings painted on the overlay canvas", tiles >= 1 and canv >= 1 and painted > 200, (tiles, canv, painted))
    check("claimed / extracted / investigated shown side by side", all(page.locator(".drawer").get_by_text(t).count() >= 1 for t in ("CLAIMED", "EXTRACTED (EXIF GPS)", "INVESTIGATED")))
    check("GPS contradiction distance shown", page.locator(".drawer").get_by_text("km from claim").count() >= 1)
    page.screenshot(path=str(ART / "04_location_compare.png"))
    page.keyboard.press("Escape")

    print("== Grid filters, cell inspection, reconfiguration")
    page.goto(API + "/?#grid")
    page.wait_for_selector("text=Grid cells (")
    n_all = page.locator(".pane table tbody tr.click").count()
    page.locator(".filters select").first.select_option("fire")
    page.wait_for_timeout(1500)
    n_fire = page.locator(".pane table tbody tr.click").count()
    fire_ok = all("Fire" in t for t in page.locator(".pane table tbody tr.click").all_inner_texts())
    check("type filter narrows the cell table to fire cells", n_fire < n_all and n_fire >= 1 and fire_ok, (n_all, n_fire))
    page.locator(".filters select").first.select_option("")
    page.wait_for_timeout(1000)
    page.locator(".pane table tbody tr.click").first.click()
    page.wait_for_selector("text=Reports in this cell", timeout=10000)
    check("cell inspection shows reports, incidents and mapping history", page.get_by_text("Mapping history").count() >= 1)
    page.get_by_placeholder("Resolution °").fill("0.5")
    page.get_by_role("button", name="Reconfigure grid").click()
    page.wait_for_selector("text=Grid reconfigured to 0.5", timeout=20000)
    check("grid reconfiguration through the UI (0.5°)", page.locator(".pane table tbody tr.click").first.inner_text().startswith("DEMO"))
    page.get_by_placeholder("Resolution °").fill("0.25")
    page.get_by_role("button", name="Reconfigure grid").click()
    page.wait_for_selector("text=Grid reconfigured to 0.25", timeout=20000)
    page.screenshot(path=str(ART / "05_grid.png"))

    print("== Sources & AI")
    page.goto(API + "/#sources")
    page.wait_for_selector("text=External evidence sources")
    page.wait_for_function("document.querySelectorAll('table tbody tr').length >= 6", timeout=15000)
    check("source health table lists all sources", all(page.get_by_text(t).count() >= 1 for t in ("NDMA SACHET (CAP alerts)", "Google News RSS", "Open-Meteo", "GDELT DOC 2.0")))
    check("visual AI shown NOT CONFIGURED (local provider disabled) with free/local note", page.get_by_text("NOT CONFIGURED").count() >= 1
          and page.get_by_text("Visual AI (local, free)").count() == 1 and page.get_by_text("never leave this machine", exact=False).count() >= 1)
    check("connectors listed with honest states", page.get_by_text("AVAILABLE MANUAL").count() >= 1 and page.get_by_text("X (Twitter)").count() == 1)
    page.screenshot(path=str(ART / "06_sources.png"))

    print("== Chatbot (grounded, reuse)")
    page.goto(API + "/#chat")
    page.get_by_role("button", name="Is there flooding in Mohali?").click()
    page.wait_for_selector(".msg.bot >> text=Mohali", timeout=120000)
    check("chatbot answers from stored incidents with sources", page.locator(".msg.bot").last.get_by_text("Sources").count() >= 1)
    page.get_by_role("button", name="Are there flood incidents near Mohali?").click()
    try:
        page.wait_for_selector("text=REUSED FROM SHARED INTELLIGENCE", timeout=60000)
    except Exception:
        pass
    check("equivalent question shows REUSED FROM SHARED INTELLIGENCE", page.get_by_text("REUSED FROM SHARED INTELLIGENCE").count() >= 1)
    page.screenshot(path=str(ART / "07_chat.png"))

    print("== Personalization")
    page.goto(API + "/#settings")
    page.get_by_text("Modern Glass").click()
    check("theme switch applies immediately", page.evaluate("document.documentElement.dataset.theme") == "modern")
    page.reload()
    check("theme persisted across reload", page.evaluate("document.documentElement.dataset.theme") == "modern")
    page.get_by_text("Cinematic Glass").click()

    browser.close()

important = [e for e in console_errors if "favicon" not in e.lower()]
print("console errors:", important[:10])
check("no browser console errors during the whole session", not important, important[:5])
ok = sum(1 for _, x in results if x)
print(f"\nscreenshots: {ART}")
print(f"{ok}/{len(results)} UI checks passed")
sys.exit(0 if ok == len(results) else 1)
