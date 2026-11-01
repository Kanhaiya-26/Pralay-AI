"""Investigate (AI) page: Investigation Summary + Extended Information + confidence assessment.
Run via:  .venv\\Scripts\\python.exe scripts\\run_tests.py --suite e2e_chat_summary.py   (isolated, seeded server)
Checks that the summary is derived from the real investigation result (status, report counts, freshness, confidence
rules), that confidence is qualitative (no fabricated %), and that the browser shows the summary with a collapsed
accordion that reveals the complete original details."""
import sys
import time

import httpx

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
c = httpx.Client(base_url=API, timeout=180)
res = []


def check(name, cond, detail=""):
    res.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def ask(q):
    r = c.post("/api/chat", json={"query": q})
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


t0 = time.time()
while time.time() - t0 < 240:
    s = c.get("/api/summary").json()["reports"]
    if s["awaiting"] == 0 and s["processing"] == 0 and s["processed"] >= 8:
        break
    time.sleep(2)


def consistent(r, label):
    s, incs = r["summary"], r["incidents"]
    prim = next(i for i in incs if i["id"] == s["primary_incident"])
    check(f"{label}: summary status = primary incident status", s["status"] == prim["status"], (s["status"], prim["status"]))
    check(f"{label}: report count = sum of incident report counts", s["reports"] == sum(i["reports"] for i in incs), (s["reports"], [i["reports"] for i in incs]))
    check(f"{label}: no fabricated percentage", s["confidence"]["percentage"] is None and "uncalibrated" in s["confidence"]["percentage_note"])
    check(f"{label}: confidence has reasons", len(s["confidence"]["reasons"]) >= 1)
    return prim


print("== CORROBORATED")
_, r = ask("Is there flooding in Mohali?")
prim = consistent(r, "corroborated")
check("status CORROBORATED", r["summary"]["status"] == "CORROBORATED")
ext = len(prim["evidence_profile"]["independent_external_groups"]) + int(prim["evidence_profile"]["official_support"])
check("CORROBORATED is not automatically HIGH (HIGH only with crowd + independent external evidence, fresh, no contradictions)",
      r["summary"]["confidence"]["level"] != "HIGH" if ext == 0 else True, (r["summary"]["confidence"], ext))
check("duplicate report not counted as independent", prim["evidence_profile"]["duplicate_reports"] >= 1
      and prim["evidence_profile"]["independent_reports"] < prim["reports"], prim["evidence_profile"])
check("basis explains the corroboration rule in plain language", "independent reports" in (r["summary"]["why"] or ""), r["summary"]["why"])

print("== UNVERIFIED")
_, r = ask("Is there a landslide in Shimla?")
consistent(r, "unverified")
check("status UNVERIFIED with LOW confidence", r["summary"]["status"] == "UNVERIFIED" and r["summary"]["confidence"]["level"] == "LOW")
check("states that UNVERIFIED is not false", any("not that the report is false" in x or "does not mean false" in x
                                                  for x in r["summary"]["limitations"] + r["summary"]["confidence"]["reasons"]))

print("== CONTRADICTED")
_, r = ask("Is there a fire in Chandigarh?")
prim = consistent(r, "contradicted")
check("status CONTRADICTED", r["summary"]["status"] == "CONTRADICTED")
check("contradiction confidence follows the strong-finding count",
      r["summary"]["confidence"]["level"] in ({"MEDIUM", "LOW"} if prim["evidence_profile"]["contradicted_with_strong_findings"] <= 1 else {"HIGH", "MEDIUM"}),
      (r["summary"]["confidence"], prim["evidence_profile"]))

print("== Many sources (evidence intent)")
_, r = ask("What evidence supports incident #1?")
check("evidence query has a summary + full original answer kept", r["summary"]["status"] and "Supporting:" in r["answer"])
check("sources preserved in the response", len(r["sources"]) >= 3, len(r["sources"]))

print("== No available sources")
c.post("/api/test/simulate-failure", json={"sources": ["google_news", "open_meteo", "ndma_sachet", "gdelt"]})
_, r = ask("Is there a cyclone in Jodhpur?")
c.post("/api/test/simulate-failure", json={"sources": []})
s = r["summary"]
check("no-evidence summary: says no evidence, does not claim absence", "No evidence" in s["headline"] and any("does NOT prove" in x for x in s["limitations"]), s)
check("unavailable sources disclosed", any("Unavailable sources" in x for x in s["limitations"]), s["limitations"])
check("confidence LOW, no percentage", s["confidence"]["level"] == "LOW" and s["confidence"]["percentage"] is None)

print("== Failed / empty / unrelated queries")
code, _ = ask("")
check("empty query rejected (422)", code == 422)
code, r = ask("hello there")
check("unrelated query → help text, no status/confidence invented", code == 200 and r["summary"]["status"] is None and r["summary"]["confidence"] is None)

print("== Browser (real Edge)")
from playwright.sync_api import sync_playwright  # noqa: E402
errs = []
with sync_playwright() as pw:
    br = pw.chromium.launch(channel="msedge", headless=True)
    pg = br.new_page(viewport={"width": 1400, "height": 950})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(API + "/#chat")
    pg.get_by_role("button", name="Is there flooding in Mohali?").click()
    pg.wait_for_selector(".inv-summary", timeout=120000)
    msg = pg.locator(".msg.bot.inv").last
    check("summary visible by default with headline, status and confidence",
          msg.locator(".inv-headline").is_visible() and msg.get_by_text("CORROBORATED").first.is_visible() and msg.get_by_text("Confidence").first.is_visible())
    det = msg.locator("details.inv-extended")
    check("Extended Information collapsed by default", det.count() == 1 and det.evaluate("d => !d.open"))
    check("collapsed: details (sources list) not visible", not msg.locator(".inv-ext-body").is_visible())
    h_collapsed = msg.bounding_box()["height"]
    msg.screenshot(path=str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent / "reports" / "chat_summary_collapsed.png"))
    det.locator(":scope > summary").click()  # direct child (a nested Diagnostics <details> also has a summary)
    pg.wait_for_timeout(300)
    check("expanding reveals the complete original answer text", msg.locator(".inv-answer").is_visible()
          and "Status reflects available evidence only" in msg.locator(".inv-answer").inner_text())
    check("expanded: incident table, per-incident confidence reasoning and sources shown",
          msg.locator(".inv-ext-body table").count() >= 1 and msg.locator(".inv-ext-body .finding").filter(has_text="confidence").count() >= 1
          and msg.locator(".inv-ext-body").get_by_text("Sources (").count() == 1)
    msg.screenshot(path=str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent / "reports" / "chat_summary_expanded.png"))
    links = msg.locator(".inv-ext-body a[target=_blank]")
    check("source links clickable (href present)", links.count() == 0 or (links.first.get_attribute("href") or "").startswith("http"))
    check("collapsed view is compact (smaller than expanded)", h_collapsed < msg.bounding_box()["height"], (h_collapsed, msg.bounding_box()["height"]))
    pg.get_by_role("button", name="Show contradicted reports").click()
    pg.wait_for_timeout(1500)
    check("non-investigation answer still renders (summary headline, items under Extended)", pg.locator(".msg.bot.inv").last.locator(".inv-headline").is_visible())
    pg.get_by_placeholder("e.g. Is there flooding in Mohali?").fill("x" * 600)
    pg.get_by_role("button", name="Ask").click()
    pg.wait_for_timeout(1500)
    check("failed query shows an error message (no fake summary)", pg.locator(".msg.bot").last.inner_text().startswith("⚠"))
    pg.set_viewport_size({"width": 420, "height": 900})
    pg.wait_for_timeout(300)
    check("responsive: summary fits a narrow screen", pg.locator(".inv-summary").first.bounding_box()["width"] <= 420)
    pg.get_by_role("button", name="Clear").click()
    check("Clear button still works", pg.locator(".msg").count() == 0)
    br.close()
check("no page errors", not errs, errs[:3])

ok = sum(1 for _, x in res if x)
print(f"\n{ok}/{len(res)} investigation-summary checks passed")
sys.exit(0 if ok == len(res) else 1)
