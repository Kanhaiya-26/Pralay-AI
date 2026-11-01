"""Browser test of the GitHub Pages STATIC SHOWCASE build (frontend/dist-showcase), served from a /PRALAY-AI/
subpath exactly like a GitHub Pages project site. Verifies: loads, banner, snapshot data, filters, drawers, actions
refused, and NO request to any /api backend or to localhost:8000.

    cd frontend; npm run build:showcase; cd ..\\backend
    .venv\\Scripts\\python.exe scripts\\showcase_test.py
"""
import functools
import http.server
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent.parent
DIST = ROOT / "frontend" / "dist-showcase"
results, console_errors, requests = [], [], []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


if not (DIST / "index.html").exists():
    sys.exit("build first: cd frontend; npm run build:showcase")
site = Path(tempfile.mkdtemp(prefix="pralay_pages_"))
shutil.copytree(DIST, site / "PRALAY-AI")
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


handler = functools.partial(Quiet, directory=str(site))
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8090), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8090/PRALAY-AI/"

try:
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("console", lambda m: m.type == "error" and console_errors.append(m.text + (f" @ {m.location.get('url', '')}:{m.location.get('lineNumber')}" if m.location else "")))
        pg.on("pageerror", lambda e: console_errors.append(f"{e} | {(e.stack or '')[:400]}"))
        pg.on("request", lambda r: requests.append(r.url))
        pg.goto(BASE + "?dtab=map#dashboard")
        pg.wait_for_selector(".feed-item", timeout=20000)
        check("showcase loads from a /PRALAY-AI/ subpath", pg.locator(".feed-item").count() >= 3)
        check("permanent STATIC SHOWCASE / SYNTHETIC banner", pg.get_by_text("STATIC SHOWCASE").count() >= 1 and pg.get_by_text("SYNTHETIC DEMO data").count() >= 1)
        check("demo grid labelled non-official", pg.get_by_text("NOT AN OFFICIAL IMD/NDMA GRID").count() >= 1)
        pg.get_by_role("tab", name="Overview").click()
        pg.wait_for_selector(".stat.kpi", timeout=10000)
        rec = int(pg.locator(".stat").filter(has_text="Total Reports").locator(".v").inner_text())
        pg.get_by_role("tab", name="GIS Map").click()
        pg.wait_for_selector(".feed-item", timeout=10000)
        check("snapshot metrics shown (8 synthetic reports)", rec == 8, rec)
        check("all incidents labelled DEMO DATA", pg.locator(".feed-item").count() == pg.locator(".feed-item").filter(has_text="DEMO DATA").count())
        pg.locator(".feed-item").first.click()
        pg.get_by_role("button", name="Open full review").click()
        pg.wait_for_selector(".drawer >> text=External evidence", timeout=10000)
        check("incident drawer renders from snapshot", pg.locator(".drawer table").count() >= 2)
        pg.keyboard.press("Escape")
        pg.goto(BASE + "?open=report:6&tab=location_time#dashboard")
        pg.wait_for_selector(".drawer >> text=CLAIMED", timeout=10000)
        check("report drawer + location comparison render", pg.locator(".drawer").get_by_text("km from claim").count() >= 1)
        check("synthetic thumbnail loads from the snapshot", pg.evaluate("[...document.querySelectorAll('.drawer img')].some(i => i.complete && i.naturalWidth > 0)"))
        pg.goto(BASE + "#grid")
        pg.wait_for_selector("text=Grid cells (", timeout=10000)
        n_all = pg.locator(".pane table tbody tr.click").count()
        pg.locator(".filters select").first.select_option("fire")
        pg.wait_for_timeout(800)
        n_fire = pg.locator(".pane table tbody tr.click").count()
        check("grid type filter works in the showcase", 1 <= n_fire < n_all, (n_all, n_fire))
        pg.goto(BASE + "#submit")
        pg.wait_for_selector("input[type=file]")
        pg.get_by_placeholder("e.g. Phase 7, Mohali, Punjab").fill("Mohali")
        pg.get_by_placeholder("What do you see? Water depth, damage, road status…").fill("x")
        pg.locator("form button.btn.primary").click()
        pg.wait_for_selector(".err", timeout=5000)
        check("submission refused with an explanation (no backend)", "Static showcase" in pg.locator(".err").first.inner_text())
        pg.goto(BASE + "#chat")
        pg.get_by_role("button", name="Is there flooding in Mohali?").click()
        pg.wait_for_timeout(1000)
        check("chat refused with an explanation", pg.locator(".msg.bot").filter(has_text="Static showcase").count() >= 1)
        br.close()
finally:
    srv.shutdown()
    shutil.rmtree(site, ignore_errors=True)

bad = [u for u in requests if "/api/" in u or ":8000" in u]
check("no request to any backend API or localhost:8000", not bad, bad[:5])
external = sorted({u.split("/")[2] for u in requests if not u.startswith("http://127.0.0.1:8090")})
print("external hosts contacted:", external)
check("only map tiles are fetched from third parties (OpenStreetMap)", all(h.endswith("tile.openstreetmap.org") for h in external), external)
check("no console errors", not console_errors, console_errors[:5])
ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} showcase checks passed")
sys.exit(0 if ok == len(results) else 1)
