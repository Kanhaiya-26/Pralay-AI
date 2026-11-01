"""Controlled live Edge checks: no mocked requests, uploads, reviews or data resets."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import re
import sqlite3
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8000"


def expected_hotspots(reports, incidents):
    ids = {i["id"] for i in incidents}
    def valid(r):
        a,b = r.get("investigated_lat"),r.get("investigated_lon")
        return (isinstance(a,(float,int)) and isinstance(b,(float,int)) and -90<=a<=90 and -180<=b<=180
                and (a,b)!=(0,0) and r.get("location_confidence") not in ("contradicted","unresolved"))
    rest = sorted([r for r in reports if r["incident_id"] in ids and r.get("is_duplicate_of") is None and valid(r)],key=lambda r:r["id"])
    groups=[]
    while rest:
        first=rest[0]
        def near(r):
            a,b,x,y=map(math.radians,[first["investigated_lat"],first["investigated_lon"],r["investigated_lat"],r["investigated_lon"]])
            return 6371*2*math.asin(math.sqrt(min(1,math.sin((x-a)/2)**2+math.cos(a)*math.cos(x)*math.sin((y-b)/2)**2)))<=5
        members=[r for r in rest if near(r)];rest=[r for r in rest if not near(r)]
        if len(members)>=2:
            groups.append({"anchor":[first["investigated_lat"],first["investigated_lon"]],"report_ids":[r["id"] for r in members],
                           "incident_ids":sorted({r["incident_id"] for r in members})})
    return groups


async def main(off):
    results={"base":BASE,"ollama_off":off,"checks":[],"assistant":[],"errors":[]}
    filename="browser_ollama_off.json" if off else "browser_live.json"
    def save(): (OUT/filename).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    def record(name, passed, detail=None):
        results["checks"].append({"name":name,"passed":bool(passed),"detail":detail});save()
        print(("PASS " if passed else "FAIL ")+name+(" "+str(detail) if detail else ""),flush=True)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(channel="msedge",headless=True)
        context=await browser.new_context(viewport={"width":1700,"height":1100})
        page=await context.new_page();page.on("pageerror",lambda e:results["errors"].append(str(e)))
        try:
            await page.goto(BASE+"/#dashboard")
            await expect(page.get_by_role("searchbox",name="Global search")).to_be_visible()
            record("live frontend: sidebar footer removed and branding/navigation retained",
                   await page.locator(".sidebar-foot").count()==0 and await page.locator(".sidebar .nav-btn").count()==7
                   and "GRAVITON" not in await page.locator(".sidebar").inner_text())
            incidents=await (await page.request.get(BASE+"/api/incidents")).json()
            reports=await (await page.request.get(BASE+"/api/reports?limit=500")).json()
            results["stored_incident_ids"]=[i["id"] for i in incidents];results["stored_report_ids"]=[r["id"] for r in reports]
            search=page.get_by_role("searchbox",name="Global search");panel=page.locator("#dashboard-search-results")
            for q in ["Mohali","flood","incident #1","report #4"]:
                await search.fill(q);await expect(panel.locator(".search-row").first).to_be_visible()
                record("live search: "+q,True,(await panel.inner_text())[:350])
            await panel.get_by_role("button").filter(has_text="Report #4").click()
            await expect(page.locator(".drawer")).to_contain_text("DUPLICATE OF #1")
            record("search report #4 opens stored duplicate record",True)
            await page.get_by_role("button",name="Close ✕").click()
            await search.fill("incident #1");await panel.get_by_role("button").filter(has_text="Incident #1").click()
            await expect(page.locator(".drawer-head")).to_contain_text("Incident #1")
            await expect(page.locator(".drawer")).to_contain_text("External evidence")
            record("search incident #1 opens actual incident/evidence",True)
            await page.get_by_role("button",name="Close ✕").click()
            await page.get_by_role("button",name="Clear search").click()

            await page.get_by_role("tab",name="GIS Map",exact=True).click()
            groups=expected_hotspots(reports,incidents);results["hotspots"]=groups
            await expect(page.locator(".density-hotspot")).to_have_count(len(groups))
            # Independently compare fetched map data to the real SQLite records.
            conn=sqlite3.connect((ROOT/"backend/data/pralay.db").as_uri()+"?mode=ro",uri=True)
            conn.row_factory=sqlite3.Row
            points_match=all(tuple(conn.execute("SELECT investigated_lat,investigated_lon,is_duplicate_of FROM reports WHERE id=?",(r["id"],)).fetchone())
                             ==(r["investigated_lat"],r["investigated_lon"],r["is_duplicate_of"]) for r in reports)
            conn.close()
            record("hotspot inputs match actual database coordinates and duplicate flags",points_match,groups)
            if groups:
                path=page.locator(".density-hotspot").first
                await path.scroll_into_view_if_needed()
                # Choose a visible part of the density ring that is not covered
                # by a legitimate foreground incident marker in the live data.
                await page.wait_for_timeout(400)
                point=await path.evaluate("""el => {const r=el.getBoundingClientRect();
                  for(let n=0;n<24;n++){const a=n*Math.PI/12,x=r.x+r.width/2+Math.cos(a)*r.width*.38,
                    y=r.y+r.height/2+Math.sin(a)*r.height*.38;
                    if(document.elementFromPoint(x,y)===el)return {x,y};}return null;}""")
                if point is None:
                    raise AssertionError("No unobstructed visible hotspot click target")
                await page.mouse.click(point["x"],point["y"])
                inspector=page.get_by_role("region",name="Hotspot details");await expect(inspector).to_be_visible()
                actual_reports=[int(re.search(r"Report #(\d+)",s).group(1)) for s in await inspector.get_by_role("button").filter(has_text=re.compile(r"^Report #")).all_text_contents()]
                actual_incidents=[int(re.search(r"Incident #(\d+)",s).group(1)) for s in await inspector.get_by_role("button").filter(has_text=re.compile(r"^Incident #")).all_text_contents()]
                record("hotspot members equal stored non-duplicate reports and unique incidents",sorted(actual_reports)==groups[0]["report_ids"] and sorted(actual_incidents)==groups[0]["incident_ids"],{"reports":actual_reports,"incidents":actual_incidents})
                iid=actual_incidents[0];rid=actual_reports[0]
                await inspector.get_by_role("button").filter(has_text=re.compile(rf"^Incident #{iid}\b")).click()
                await expect(page.locator(".drawer-head")).to_contain_text(f"Incident #{iid}")
                await page.get_by_role("button",name="Close ✕").click()
                await inspector.get_by_role("button").filter(has_text=re.compile(rf"^Report #{rid}\b")).click()
                await expect(page.locator(".drawer-head")).to_contain_text(f"Report #{rid}")
                await page.get_by_role("button",name="Close ✕").click()
                record("live hotspot clicks open correct associated incident and report",True)
            toggle=page.get_by_role("checkbox",name="Report hotspots",exact=True)
            await toggle.uncheck();await expect(page.locator(".density-hotspot")).to_have_count(0)
            await toggle.check();await expect(page.locator(".density-hotspot")).to_have_count(len(groups))
            record("hotspot overlay toggles with live data",True)
            painted="""() => {const c=document.querySelector('.leaflet-pralay-cells-pane canvas');if(!c)return false;
            const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;for(let n=3;n<d.length;n+=4)if(d[n])return true;return false;}"""
            await page.wait_for_function(painted)
            grid=page.get_by_role("checkbox",name="Grid overlay",exact=True);await grid.uncheck()
            await page.wait_for_function("()=>!("+painted+")()")
            await grid.check();await page.wait_for_function(painted)
            await expect(page.locator(".gis-pane .banner")).to_contain_text("NOT AN OFFICIAL IMD/NDMA GRID")
            record("live grid renders/toggles; custom/demo disclaimer intact",True)
            await page.screenshot(path=str(OUT/("dashboard_ollama_off.png" if off else "dashboard_live.png")),full_page=True)

            await page.get_by_role("button",name=re.compile("Investigate")).click()
            questions=(["Cyclone Arnab"] if off else ["Was there an avalanche in Nepal?","Check again, I heard there was one.","Cyclone Arnab","Was that an actual incident or just a warning?"])
            for q in questions:
                async with page.expect_response(lambda r:r.url.endswith("/api/chat") and r.request.method=="POST",timeout=180000) as pending:
                    await page.locator(".chat form input").fill(q)
                    await page.get_by_role("button",name="Ask",exact=True).click()
                response=await pending.value;body=await response.json()
                await expect(page.locator(".msg.bot").last).to_contain_text(body.get("summary",{}).get("headline",body.get("answer",""))[:120],timeout=30000)
                results["assistant"].append({"query":q,"http_status":response.status,"response":body});save()
                expected=body.get("parsed",{}).get("intent")=="investigate"
                record("browser assistant: "+q,response.status==200 and expected,{"intent":body.get("parsed",{}).get("intent"),"headline":body.get("summary",{}).get("headline"),"live":{k:body.get("live",{}).get(k) for k in ["ok","from_cache","query","retrieved_at"]}})
                if q=="Was there an avalanche in Nepal?":
                    record("live backend uses occurrence-v2 and blocks Nepal from India incidents",body["parsed"]["norm_key"].startswith("occurrence-v2|") and not body["incidents"] and "outside India" in body["answer"])
            await page.locator(".msg.bot").last.locator(".inv-extended > summary").click()
            await page.screenshot(path=str(OUT/("assistant_ollama_off.png" if off else "assistant_live.png")),full_page=True)
            if off:
                validation=await (await page.request.post(BASE+"/api/vision/validate")).json()
                record("Ollama stopped: backend explicitly reports UNREACHABLE",validation.get("state")=="UNREACHABLE",validation)
                await page.get_by_role("button",name=re.compile("Sources & AI")).click()
                await expect(page.locator(".main")).to_contain_text("UNREACHABLE")
                record("Ollama stopped: live Sources & AI page shows unavailable without app failure",True)
                await page.screenshot(path=str(OUT/"sources_ollama_off.png"),full_page=True)
            record("no browser runtime errors",not results["errors"],results["errors"])
        except Exception as e:
            results["exception"]=str(e);save()
            await page.screenshot(path=str(OUT/"browser_failure.png"),full_page=True)
            raise
        finally:
            save();await browser.close()
    passed=sum(c["passed"] for c in results["checks"])
    print(f"FINAL {passed}/{len(results['checks'])} live browser checks passed",flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--ollama-off",action="store_true")
    asyncio.run(main(parser.parse_args().ollama_off))
