"""Claimed-category vs image mismatch regression (isolated server + Ollama protocol STUB; synthetic fixtures only).
Run:  python scripts/run_tests.py --suite media-mismatch"""
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
STUB = "http://127.0.0.1:11555"
c = httpx.Client(base_url=API, timeout=120)
res, NOW = [], datetime.now(timezone.utc)


def check(n, cond, d=""):
    res.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  — {d}" if d and not cond else ""))


def mode(m):
    httpx.post(STUB + "/_mode", json={"mode": m}, timeout=5)
    c.post("/api/vision/validate")


def submit(seed, lat=30.703, lon=76.718, when=None, desc="mismatch test"):
    img = to_jpeg(scene(seed, "flood"), lat, lon, when or NOW - timedelta(minutes=30))
    r = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": desc, "is_demo": "true",
                                     "claimed_event_time": (NOW - timedelta(hours=1)).isoformat()}, files=[("files", ("x.jpg", img, "image/jpeg"))])
    rid = r.json()["id"]
    for _ in range(90):
        rep = c.get(f"/api/reports/{rid}").json()
        if rep["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
            return rep, img
        time.sleep(1)
    return c.get(f"/api/reports/{rid}").json(), img


stub = subprocess.Popen([sys.executable, str(HERE / "fake_ollama.py"), "11555", "qwen2.5vl:3b"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
c.post("/api/test/vision-mode", json={"mode": "inline"})
try:
    mode("helmet")
    hel = [submit(900 + i, 30.703 + i * 0.002)[0] for i in range(3)]
    f = hel[0]["assessment"]["fields"]
    check("helmet as flood: claimed=flood, AI detected none, relevance MISMATCH", f["user_claimed_category"] == "flood"
          and f["ai_detected_category"] == "none_detected" and f["visual_relevance"] == "MISMATCH", f)
    check("helmet as flood: UNVERIFIED + needs_review + mismatch explained", hel[0]["effective_status"] == "UNVERIFIED" and f["needs_review"]
          and any("Category mismatch" in x for x in hel[0]["assessment"]["concerns"]), hel[0]["assessment"]["reason"])
    inc = c.get(f"/api/incidents/{hel[0]['incident_id']}").json()
    check("3 GPS-consistent helmet 'flood' reports do NOT corroborate an incident", inc["effective_status"] != "CORROBORATED"
          and inc["assessment"]["independent_reports"] == 0, (inc["effective_status"], inc["assessment"].get("independent_reports")))
    mode("helmet_cyclone")
    hc = submit(940)[0]
    check("helmet with hallucinated 'strong cyclone' indicator as flood: RELATED_ONLY, UNVERIFIED, needs_review, no score boost",
          hc["assessment"]["fields"]["visual_relevance"] == "RELATED_ONLY" and hc["effective_status"] == "UNVERIFIED"
          and hc["assessment"]["fields"]["needs_review"] and "vision:match" not in [x["factor"] for x in hc["assessment"]["contributions"]], hc["assessment"]["fields"])
    mode("fire")
    fi = submit(950)[0]["assessment"]["fields"]
    check("fire image as flood: AI detected fire, MISMATCH", fi["ai_detected_category"] == "fire" and fi["visual_relevance"] == "MISMATCH", fi)
    mode("ok")
    fl = submit(960)[0]
    check("flood image as flood: MATCH, not flagged", fl["assessment"]["fields"]["visual_relevance"] == "MATCH" and not fl["assessment"]["fields"]["needs_review"])
    stub.terminate(); stub.wait(5); c.post("/api/vision/validate")
    un = submit(970)[0]["assessment"]
    check("vision unavailable: NOT_ANALYSED, listed as missing, never counted as support", un["fields"]["visual_relevance"] == "NOT_ANALYSED"
          and any("not analysed" in x for x in un["missing"]) and "vision:match" not in [x["factor"] for x in un["contributions"]])
    check("score basis explains 50 = no evidence, not 50% confidence", "not 50% confidence" in un["fields"]["score_basis"])
    stub = subprocess.Popen([sys.executable, str(HERE / "fake_ollama.py"), "11555", "qwen2.5vl:3b"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    mode("malformed")
    mal = submit(980)[0]["assessment"]["fields"]
    check("malformed model output: NOT_ANALYSED (failed), no fabricated relevance", mal["visual_relevance"] == "NOT_ANALYSED" and mal["analysis_status"] == "failed", mal)
    mode("ok")
    a, img = submit(990)
    d = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "dup", "is_demo": "true"},
               files=[("files", ("d.jpg", img, "image/jpeg"))]).json()["id"]
    time.sleep(8)
    check("duplicate image still detected", c.get(f"/api/reports/{d}").json()["is_duplicate_of"] == a["id"])
    old = submit(995, when=NOW - timedelta(days=60))[0]
    check("old image with current claim: CONTRADICTED (OLD_MEDIA) even though visually relevant",
          old["effective_status"] == "CONTRADICTED" and "OLD_MEDIA" in {x["finding"] for x in old["findings"]})
finally:
    stub.terminate()
print(f"\n{sum(res)}/{len(res)} media-mismatch checks passed")
sys.exit(0 if all(res) else 1)
