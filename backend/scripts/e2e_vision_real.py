"""REAL local-model test: runs only if Ollama is running on this machine and OLLAMA_MODEL is installed.
Otherwise prints BLOCKED and exits 2 — it never reports success without a real model response.

    .venv\\Scripts\\python.exe scripts\\run_tests.py --suite vision-real
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
c = httpx.Client(base_url=API, timeout=900)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


st = c.post("/api/vision/validate").json()
print("provider status:", json.dumps({k: st.get(k) for k in ("state", "model", "endpoint", "detail", "installed_models")}))
if st["state"] != "READY":
    print(f"\nBLOCKED: real local model not available ({st['state']}). Install Ollama and run: ollama pull {st.get('model')}")
    sys.exit(2)

now = datetime.now(timezone.utc)
r = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": "real local model test", "is_demo": "true"},
           files=[("files", ("real.jpg", to_jpeg(scene(2468, "flood"), 30.703, 76.718, now - timedelta(minutes=30)), "image/jpeg"))]).json()
t0 = time.time()
rep = None
while time.time() - t0 < 900:
    rep = c.get(f"/api/reports/{r['id']}").json()
    if rep["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW") and rep["visual_analysis"] and rep["visual_analysis"][0]["status"] not in ("pending", "running"):
        break
    time.sleep(3)
v = rep["visual_analysis"][0]
out = v.get("output") or {}
print("model output:", json.dumps(out, ensure_ascii=False, indent=1)[:2500])
check("real model produced a completed analysis", v["status"] == "completed", v.get("error"))
check("output passed schema validation (description + typed lists)", bool(out.get("description")) and all(isinstance(out.get(k), list) for k in
      ("disaster_indicators", "objects_and_conditions", "visible_text", "landmarks", "context_inconsistencies", "limitations")))
check("no authenticity verdict stored", not any(k in out for k in ("is_fake", "verdict", "authentic", "real")))
check("latency measured", (out.get("_meta") or {}).get("latency_ms") is not None, out.get("_meta"))
check("pipeline completed", rep["processing_state"] in ("PROCESSED", "MAPPED"))
cats = [i["category"] for i in out.get("disaster_indicators", [])]
print(f"\nINFO (not asserted — model quality): indicators {cats}; latency {(out.get('_meta') or {}).get('latency_ms')} ms; "
      f"sanitizer notes {(out.get('_meta') or {}).get('sanitizer_notes')}")
ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} real local-model checks passed")
sys.exit(0 if ok == len(results) else 1)
