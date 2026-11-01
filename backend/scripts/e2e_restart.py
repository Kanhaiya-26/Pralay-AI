"""Post-restart suite: run by run_tests.py after the server is restarted on the SAME data dir, this time with an
visual AI pointed at a local port where nothing listens. Verifies persistence and the configured-but-unavailable visual-AI path."""
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
DATA = os.getenv("PRALAY_TEST_DATA_DIR")
c = httpx.Client(base_url=API, timeout=120)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


snap = json.loads(Path(DATA, "snapshot.json").read_text())
s = c.get("/api/summary").json()
print("== Persistence after restart")
check("report count persisted", s["reports"]["received_total"] == snap["reports"], (s["reports"]["received_total"], snap["reports"]))
check("mapped reports persisted", s["reports"]["mapped"] == snap["mapped"])
check("incident evidence persisted", len(c.get("/api/incidents/1").json()["evidence"]) >= snap["evidence_inc1"])
check("review audit history persisted", len(c.get("/api/reports/8").json()["review_history"]) == snap["review_rows_r8"])
check("no report left stuck in PROCESSING", s["reports"]["processing"] == 0)

print("== Visual AI configured but provider unreachable (local Ollama endpoint with nothing listening)")
st = c.get("/api/vision/status").json()
check("visual AI reported as configured (local Ollama provider)", st["configured"] is True and st["provider"] == "ollama")
v = c.post("/api/vision/validate").json()
check("real validation call → UNREACHABLE (connection to the local endpoint refused)", v["state"] == "UNREACHABLE", v)
now = datetime.now(timezone.utc)
r = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "vision path test", "is_demo": "true"},
           files=[("files", ("g.jpg", to_jpeg(scene(4321, "flood"), 30.705, 76.718, now - timedelta(hours=1)), "image/jpeg"))]).json()
t0 = time.time()
rep = None
while time.time() - t0 < 120:
    rep = c.get(f"/api/reports/{r['id']}").json()
    if rep["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
        break
    time.sleep(1.5)
va = rep["visual_analysis"][0]
check("visual analysis attempted and recorded as unavailable (no fabricated output)", va["status"] == "unavailable" and not va["output"], va)
check("pipeline continued despite visual-AI failure", rep["processing_state"] in ("PROCESSED", "MAPPED"))
check("media can only go to this machine (loopback endpoint, media_leaves_machine=false, no paid services)",
      st["endpoint"].startswith("http://127.0.0.1") and st["media_leaves_machine"] is False and st["paid"] is False)
logs = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in Path(DATA).glob("server_*.log"))
import re  # noqa: E402
check("server logs contain no image payloads (no base64 JPEG data)", not re.search(r"/9j/[A-Za-z0-9+/]{200,}", logs))

ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} restart checks passed")
sys.exit(0 if ok == len(results) else 1)
