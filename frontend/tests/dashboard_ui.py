"""Headless Edge UI checks against built frontend and intercepted snapshot APIs.
No application backend is started and no database is opened or changed.
Run from repo root: backend/.venv/Scripts/python.exe -B frontend/tests/dashboard_ui.py
"""
import asyncio
import base64
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit, parse_qs

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
SNAP = ROOT / "frontend/showcase-public/snapshot"
ART = ROOT / "reports/dashboard_ui"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def snapshot(name):
    return json.loads((SNAP / (name + ".json")).read_text(encoding="utf-8"))


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(ROOT / "frontend/dist")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    incidents, reports = snapshot("incidents"), snapshot("reports")
    cells = snapshot("grid__cells%40category%3D%26status%3D%26mapped_only%3D")
    mode = {"missing": False, "reports_error": False, "sparse": False}
    started = asyncio.Event()
    counts = []
    errors, writes = [], []

    async def check(name, action):
        try:
            await action()
        except Exception:
            ART.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(ART / "failure.png"), full_page=True)
            raise
        counts.append(name)
        print("PASS", name, flush=True)

    async def route_api(route):
        request = route.request
        if request.method != "GET":
            writes.append(request.url)
            return await route.fulfill(status=405, json={"detail": "Test refuses writes"})
        parsed = urlsplit(request.url)
        path, query = parsed.path.removeprefix("/api/"), parse_qs(parsed.query)
        if path == "search":
            q = query.get("q", [""])[0].lower().lstrip("#")
            if q in ("slow", "slowerror"):
                started.set()
                await asyncio.sleep(1.4)
                if q == "slowerror":
                    return await route.fulfill(status=503, json={"detail": "Old error"})
                data = {"incidents": [{**incidents[0], "title": "STALE RESULT"}], "reports": []}
            elif q == "error":
                return await route.fulfill(status=503, json={"detail": "Fixture source unavailable"})
            else:
                numeric = int(q) if q.isdigit() else -1
                data = {"incidents": [i for i in incidents if i["id"] == numeric or q in " ".join(str(i.get(k) or "") for k in ("title", "place_name", "state_name", "category", "effective_status")).lower()],
                        "reports": [r for r in reports if r["id"] == numeric or q in " ".join(str(r.get(k) or "") for k in ("claimed_location_text", "category", "description", "effective_status")).lower()]}
        elif path == "incidents":
            data = copy.deepcopy(incidents)
            if mode["missing"]:
                for i in data:
                    i["center_lon"] = None
            for key, value in query.items():
                if key in ("category", "active"):
                    data = [i for i in data if str(i.get(key)).lower() == value[0]]
                if key == "status":
                    data = [i for i in data if i["effective_status"] == value[0]]
        elif path == "reports":
            if mode["reports_error"]:
                return await route.fulfill(status=503, json={"detail": "Report locations unavailable"})
            data = copy.deepcopy(reports)
            if mode["missing"]:
                for r in data:
                    r["investigated_lon"] = None
            if mode["sparse"]:
                data = [next(r for r in data if r["id"] == 1)]
            for key, value in query.items():
                if key == "category":
                    data = [r for r in data if r["category"] == value[0]]
                if key == "status":
                    data = [r for r in data if r["effective_status"] == value[0]]
        elif path == "grid/cells":
            data = cells
        elif path == "grid/lattice":
            data = {"lat_lines": [30.5, 30.75, 31], "lon_lines": [76.5, 76.75, 77]}
        else:
            name = path.replace("/", "__")
            try:
                data = snapshot(name)
            except FileNotFoundError:
                return await route.fulfill(status=404, json={"detail": f"No fixture: {path}"})
        await route.fulfill(json=data)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel="msedge", headless=True)
            context = await browser.new_context(viewport={"width": 1700, "height": 1100})
            await context.route("**/api/**", route_api)
            await context.route("**/*.tile.openstreetmap.org/**", lambda route: route.fulfill(content_type="image/png", body=PNG))
            await context.route("**/snapshot/files/**", lambda route: route.fulfill(content_type="image/png", body=PNG))
            page = await context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(url + "/#dashboard")
            await expect(page.get_by_role("tab", name="Overview", exact=True)).to_be_visible()
            search = page.get_by_role("searchbox", name="Global search")
            results = page.locator("#dashboard-search-results")

            async def sidebar():
                await expect(page.locator(".sidebar .brand-title")).to_have_text("PRALAY AI")
                await expect(page.locator(".sidebar .nav-btn")).to_have_count(7)
                await expect(page.locator(".sidebar-foot")).to_have_count(0)
                text = await page.locator(".sidebar").inner_text()
                assert all(s not in text for s in ("GRAVITON", "SDG 13", "Decision-support", "PS #06"))
                await expect(search).to_have_attribute("placeholder", "Search disasters, locations, incident IDs, reports...")
            await check("sidebar footer removed; brand/navigation/search retained", sidebar)

            for query, expected in [("Mohali", "Incident #1"), ("flood", "Report #1"), ("incident #1", "Incident #1"), ("report #4", "Report #4"), ("#1", "Incident #1")]:
                async def search_case(q=query, text=expected):
                    await search.fill(q)
                    await expect(results.locator(".search-row").filter(has_text=text).first).to_be_visible()
                    if q.startswith("incident"):
                        await expect(results.get_by_text("Report #1", exact=True)).to_have_count(0)
                    if q.startswith("report"):
                        await expect(results.get_by_text("Incident #4", exact=True)).to_have_count(0)
                await check(f"search: {query}", search_case)

            async def open_results():
                await search.fill("incident #1")
                await results.get_by_role("button").filter(has_text="Incident #1").click()
                await expect(page.locator(".drawer-head")).to_contain_text("Incident #1")
                await expect(page.locator(".drawer")).to_contain_text("External evidence")
                await page.get_by_role("button", name="Close ✕").click()
                await search.fill("report #4")
                await results.get_by_role("button").filter(has_text="Report #4").click()
                await expect(page.locator(".drawer-head")).to_contain_text("Report #4")
                await expect(page.locator(".drawer")).to_contain_text("DUPLICATE OF #1")
                await page.get_by_role("button", name="Close ✕").click()
            await check("search opens incident evidence and report detail drawers", open_results)

            async def states():
                await search.fill("not-a-real-location-xyz")
                await expect(results).to_contain_text("No incidents or reports match")
                await search.fill("error")
                await expect(results.get_by_role("alert")).to_contain_text("Fixture source unavailable")
                await page.get_by_role("button", name="Clear search").click()
                await expect(results).to_have_count(0)
                await expect(search).to_be_focused()
            await check("empty/error/clear search states", states)

            async def race():
                for old in ("slow", "slowerror"):
                    started.clear()
                    await search.fill(old)
                    await expect(results.get_by_role("status")).to_have_text("Searching…")
                    await asyncio.wait_for(started.wait(), timeout=5)
                    await search.fill("Mohali")
                    await expect(results.locator(".search-row").first).to_be_visible()
                    await asyncio.sleep(1.5)
                    assert "STALE RESULT" not in await results.inner_text()
                    await expect(results.get_by_role("alert")).to_have_count(0)
                started.clear()
                await search.fill("slow")
                await asyncio.wait_for(started.wait(), timeout=5)
                await page.get_by_role("button", name="Clear search").click()
                await asyncio.sleep(1.5)
                await expect(results).to_have_count(0)
            await check("stale successes/errors and cleared in-flight search cannot overwrite newer state", race)

            async def map_setup():
                await page.get_by_role("tab", name="GIS Map", exact=True).click()
                await expect(page.locator(".density-hotspot").first).to_be_visible()
                await expect(page.locator(".gis-pane")).to_contain_text("duplicates excluded")
                await expect(page.locator(".gis-pane")).to_contain_text("not disaster severity")
                await expect(page.locator(".gis-pane .banner")).to_contain_text("NOT AN OFFICIAL IMD/NDMA GRID")
            await check("GIS renders actual snapshot hotspot data and density/grid labels", map_setup)

            async def hotspots():
                toggle = page.get_by_role("checkbox", name="Report hotspots", exact=True)
                await toggle.uncheck()
                await expect(page.locator(".density-hotspot")).to_have_count(0)
                await expect(page.locator(".leaflet-pralay-incidents-pane path")).to_have_count(len(incidents))
                await toggle.check()
                path = page.locator(".density-hotspot").first
                await path.focus()
                await page.keyboard.press("Enter")
                inspector = page.get_by_role("region", name="Hotspot details")
                await expect(inspector).to_be_visible()
                await expect(inspector).to_contain_text("not a severity rating")
                await expect(inspector.get_by_role("button").filter(has_text="Report #4")).to_have_count(0)
                await expect(inspector.get_by_role("button").filter(has_text="Incident #1")).to_have_count(1)
                await inspector.get_by_role("button").filter(has_text="Incident #1").click()
                await expect(page.locator(".drawer-head")).to_contain_text("Incident #1")
                await page.get_by_role("button", name="Close ✕").click()
                await inspector.get_by_role("button").filter(has_text="Report #1").click()
                await expect(page.locator(".drawer-head")).to_contain_text("Report #1")
                await page.get_by_role("button", name="Close ✕").click()
                await page.get_by_role("button", name="Close hotspot details").click()
                # Click the circle's visible outer ring, away from the incident marker.
                bounds = await path.bounding_box()
                await path.click(position={"x": bounds["width"] / 2, "y": 6})
                await expect(inspector).to_be_visible()
            await check("hotspot toggle, keyboard/pointer click, deduplication and associated detail inspection", hotspots)

            async def grid():
                grid = page.get_by_role("checkbox", name="Grid overlay", exact=True)
                painted = """() => { const c = document.querySelector('.leaflet-pralay-cells-pane canvas');
                  if (!c) return false; const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                  for(let n=3;n<d.length;n+=4) if(d[n]) return true; return false; }"""
                await page.wait_for_function(painted)
                await grid.uncheck()
                await page.wait_for_function("() => !(" + painted + ")()")
                await expect(page.locator(".density-hotspot").first).to_be_visible()
                await grid.check()
                await page.wait_for_function(painted)
                await expect(page.locator(".gis-pane .banner")).to_contain_text("NOT AN OFFICIAL")
            await check("grid toggles independently of hotspots and retains disclaimer", grid)

            async def incident_marker():
                await page.locator(".leaflet-pralay-incidents-pane path").first.click()
                await expect(page.locator(".dashboard-map-split > .pane").last).to_contain_text("#4 Landslide")
                await page.get_by_role("button", name="Open full review").click()
                await expect(page.locator(".drawer-head")).to_contain_text("Incident #4")
                await page.get_by_role("button", name="Close ✕").click()
            await check("individual incident markers remain clickable above hotspots and grid", incident_marker)

            async def mobile():
                await page.set_viewport_size({"width": 390, "height": 844})
                await search.fill("Mohali")
                await expect(results.locator(".search-row").first).to_be_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert await page.locator(".gis-pane .map").evaluate("e => e.getBoundingClientRect().width <= innerWidth")
                await expect(page.locator(".sidebar .brand-title")).to_be_visible()
                await page.get_by_role("button", name="Clear search").click()
                ART.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(ART / "mobile.png"), full_page=True)
                await page.set_viewport_size({"width": 1700, "height": 1100})
                await page.screenshot(path=str(ART / "desktop.png"), full_page=True)
            await check("mobile search/map fit viewport; desktop and mobile screenshots captured", mobile)

            async def fallback():
                mode["sparse"] = True
                await page.reload()
                await expect(page.locator(".density-status")).to_contain_text("Insufficient nearby report coordinates")
                await expect(page.locator(".density-hotspot")).to_have_count(0)
                mode["missing"] = True
                await page.reload()
                await expect(page.locator(".density-status")).to_contain_text("0 located non-duplicate reports")
                await expect(page.locator(".density-hotspot")).to_have_count(0)
                await expect(page.locator(".leaflet-pralay-incidents-pane path")).to_have_count(0)
                await expect(page.locator(".gis-pane .leaflet-container")).to_have_count(1)
                mode["missing"] = False
                mode["reports_error"] = True
                await page.reload()
                await expect(page.locator(".density-status")).to_contain_text("Report density unavailable")
                await expect(page.locator(".density-hotspot")).to_have_count(0)
            await check("sparse/missing coordinates and failed report fetch produce no fabricated hotspots", fallback)

            assert not errors, errors
            assert not writes, writes
            print("PASS no browser runtime errors; no API writes", flush=True)
            counts.append("no browser errors or writes")
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(f"\n{len(counts)}/{len(counts)} dashboard browser checks passed")


if __name__ == "__main__":
    asyncio.run(main())
