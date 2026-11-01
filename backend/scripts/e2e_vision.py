"""Local visual-AI adapter tests. Run by run_tests.py against a fresh PRALAY server configured with
VISION_PROVIDER=ollama, OLLAMA_URL=http://127.0.0.1:11555, OLLAMA_TIMEOUT_SEC=4, VISION_MODE=inline.

The Ollama side is scripts/fake_ollama.py — a PROTOCOL STUB, not a model. These checks prove PRALAY's adapter
behaviour (unavailable, missing model, timeout, malformed output, missing image, verdict stripping, success path,
deferred mode, privacy guard). They do NOT prove the quality of any real vision model: see e2e_vision_real.py.
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
STUB_PORT = 11555
STUB = f"http://127.0.0.1:{STUB_PORT}"
c = httpx.Client(base_url=API, timeout=120)
results = []
NOW = datetime.now(timezone.utc)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def start_stub():
    p = subprocess.Popen([sys.executable, str(HERE / "fake_ollama.py"), str(STUB_PORT), "qwen2.5vl:3b"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            httpx.get(STUB + "/api/tags", timeout=1)
            return p
        except httpx.HTTPError:
            time.sleep(0.25)
    raise RuntimeError("stub did not start")


def stub_mode(m):
    httpx.post(STUB + "/_mode", json={"mode": m}, timeout=5)
    c.post("/api/vision/validate")  # refresh PRALAY's cached provider status


def submit(desc, seed, video=None):
    files = [("files", ("v.mp4", video, "video/mp4"))] if video else \
        [("files", (f"{seed}.jpg", to_jpeg(scene(seed, "flood").resize((1600, 1066)), 30.703, 76.718, NOW - timedelta(minutes=30)), "image/jpeg"))]
    r = c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "description": desc, "is_demo": "true",
                                     "claimed_event_time": (NOW - timedelta(hours=1)).isoformat()}, files=files)
    r.raise_for_status()
    return r.json()["id"]


def done(rid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/reports/{rid}").json()
        if r["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
            return r
        time.sleep(0.7)
    return c.get(f"/api/reports/{rid}").json()


def va(r):
    return r["visual_analysis"][0] if r["visual_analysis"] else {}


c.post("/api/test/vision-mode", json={"mode": "inline"})

print("== Ollama unavailable (not running)")
st = c.post("/api/vision/validate").json()
check("status UNREACHABLE when Ollama is not running", st["state"] == "UNREACHABLE" and st["provider"] == "ollama", st["state"])
r = done(submit("vision-unavailable", 11))
check("analysis recorded 'unavailable' with reason; no output", va(r).get("status") == "unavailable" and not va(r).get("output") and "UNREACHABLE" in (va(r).get("error") or ""), va(r))
check("pipeline continued and finished the report", r["processing_state"] in ("PROCESSED", "MAPPED") and not r["error"])

stub = start_stub()
try:
    print("== Model not installed")
    stub_mode("no_model")
    st = c.get("/api/vision/status").json()
    check("status MODEL_NOT_INSTALLED with 'ollama pull' instruction", st["state"] == "MODEL_NOT_INSTALLED" and "ollama pull qwen2.5vl:3b" in st["detail"], st)
    r = done(submit("vision-no-model", 12))
    check("analysis unavailable, report still processed", va(r).get("status") == "unavailable" and r["processing_state"] in ("PROCESSED", "MAPPED"))

    print("== Successful analysis (stub)")
    stub_mode("ok")
    check("status READY when model installed", c.get("/api/vision/status").json()["state"] == "READY")
    r = done(submit("vision-ok", 13))
    v = va(r)
    out = v.get("output") or {}
    check("analysis completed with structured observations", v.get("status") == "completed" and out.get("description")
          and isinstance(out.get("disaster_indicators"), list) and "limitations" in out, v)
    last = httpx.get(STUB + "/_last").json()
    check("request carried the image, JSON schema, non-streaming, configured model", last.get("had_image") and last.get("has_schema")
          and last.get("stream") is False and last.get("model") == "qwen2.5vl:3b", last)
    check("image downscaled before sending (max edge ≤ 1024 px)", last.get("image_size") and max(last["image_size"]) <= 1024, last.get("image_size"))
    check("latency recorded in output metadata", (out.get("_meta") or {}).get("latency_ms") is not None)
    contrib = [x["factor"] for x in (r["assessment"] or {}).get("contributions", [])]
    check("observations feed the report assessment (vision:match factor)", "vision:match" in contrib, contrib)
    check("visual context concern recorded as low-confidence finding, never proof",
          any(f["finding"] == "VISUAL_CONTEXT_CONCERN" and f["confidence"] == "low" for f in r["findings"]))
    check("landmark check ran (no longer 'unavailable')", r["location_summary"] and not r["location_summary"]["landmark_check"].startswith("unavailable"),
          r["location_summary"] and r["location_summary"]["landmark_check"])

    print("== Model tries to declare authenticity → stripped")
    stub_mode("verdict")
    r = done(submit("vision-verdict", 14))
    out = va(r).get("output") or {}
    check("verdict keys removed from stored output", va(r).get("status") == "completed" and not any(k in out for k in ("is_fake", "verdict", "authenticity_score")), list(out))
    check("verdict sentence removed from description; sanitizer note recorded", "fake" not in out.get("description", "").lower()
          and any("authenticity" in n for n in (out.get("_meta") or {}).get("sanitizer_notes", [])), out.get("_meta"))
    check("model verdict did not make the report CONTRADICTED or 'manipulated'", r["effective_status"] != "CONTRADICTED" and r["media_classification"] != "POTENTIALLY_MANIPULATED")

    print("== Timeout")
    stub_mode("slow")
    t0 = time.time()
    r = done(submit("vision-timeout", 15))
    check("slow model → analysis failed 'timed out' (no output)", va(r).get("status") == "failed" and "timed out" in (va(r).get("error") or "") and not va(r).get("output"), va(r))
    check("pipeline continued after timeout", r["processing_state"] in ("PROCESSED", "MAPPED") and r["attempts"] == 1)

    print("== Malformed model response")
    stub_mode("malformed")
    r = done(submit("vision-malformed", 16))
    check("non-JSON model output → failed 'malformed', nothing stored", va(r).get("status") == "failed" and "malformed" in (va(r).get("error") or "") and not va(r).get("output"), va(r))
    stub_mode("error500")
    r = done(submit("vision-500", 17))
    check("Ollama HTTP 500 → failed, pipeline continues", va(r).get("status") == "failed" and r["processing_state"] in ("PROCESSED", "MAPPED"))

    print("== Missing image / privacy guard (adapter level, in-process)")
    os.environ.setdefault("PRALAY_DATA_DIR", os.getenv("PRALAY_TEST_DATA_DIR") or str(Path(os.getenv("TEMP", ".")) / "pralay_vision_unit"))
    from app.vision import OllamaProvider, sanitize  # noqa: E402
    stub_mode("ok")
    res = OllamaProvider(STUB, "qwen2.5vl:3b", 4).analyse(str(HERE / "does_not_exist.jpg"), "image/jpeg", "flood")
    check("missing image → failed 'image file not found' (no request sent)", res["status"] == "failed" and "not found" in res["error"])
    before = httpx.get(STUB + "/_last").json()["requests"]
    remote = OllamaProvider("https://vision.example.com", "qwen2.5vl:3b", 4)
    sample = HERE / "_vision_sample.jpg"
    sample.write_bytes(to_jpeg(scene(99, "flood")))
    res = remote.analyse(str(sample), "image/jpeg", "flood")
    sample.unlink()
    check("non-local endpoint refused: BLOCKED_NON_LOCAL, media not sent", remote.status()["state"] == "BLOCKED_NON_LOCAL" and res["status"] == "unavailable"
          and httpx.get(STUB + "/_last").json()["requests"] == before)
    check("sanitizer rejects schema-less output", sanitize({"foo": 1})[0] is None and sanitize("[]")[0] is None)
    real_contradiction = {"description": "Stylized urban scene.", "disaster_indicators": [
        {"category": "building_damage", "evidence": "The buildings appear to be intact with no visible signs of damage or collapse.", "strength": "moderate"},
        {"category": "flood", "evidence": "brown water covering the street", "strength": "strong"}],
        "objects_and_conditions": [], "visible_text": [], "landmarks": [], "context_inconsistencies": [], "limitations": []}
    cl, nt = sanitize(real_contradiction)  # verbatim pattern observed from qwen2.5vl:3b on 2026-10-31
    check("self-contradictory indicator (evidence denies it) dropped, valid one kept",
          [i["category"] for i in cl["disaster_indicators"]] == ["flood"] and any("self-contradictory" in n for n in nt), (cl["disaster_indicators"], nt))

    print("== Deferred mode (default for slow local models)")
    c.post("/api/test/vision-mode", json={"mode": "deferred"})
    stub_mode("ok")
    rid = submit("vision-deferred", 18)
    r = done(rid)
    check("report finishes without waiting for the model (analysis pending or already applied)",
          r["processing_state"] in ("PROCESSED", "MAPPED") and va(r).get("status") in ("pending", "running", "completed"), va(r).get("status"))
    t0 = time.time()
    # "applied" (not merely "completed") is the signal: the worker stores the result, then re-applies it to the report.
    while time.time() - t0 < 60 and not any(h["stage"] == "VISUAL_ANALYSIS_APPLIED" for h in c.get(f"/api/reports/{rid}").json()["status_history"]):
        time.sleep(1)
    r = c.get(f"/api/reports/{rid}").json()
    check("deferred analysis completed and was applied (history VISUAL_ANALYSIS_APPLIED)", va(r).get("status") == "completed"
          and any(h["stage"] == "VISUAL_ANALYSIS_APPLIED" for h in r["status_history"]))
    check("deferred observations reached the assessment", "vision:match" in [x["factor"] for x in r["assessment"]["contributions"]])
    import cv2  # noqa: E402
    import numpy as np  # noqa: E402
    vp = HERE / "_v.mp4"
    w = cv2.VideoWriter(str(vp), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for i in range(20):
        w.write(np.full((240, 320, 3), (100 + i, 90, 60), np.uint8))
    w.release()
    vid = submit("vision-video", 0, video=vp.read_bytes())
    vp.unlink()
    done(vid)
    t0 = time.time()
    while time.time() - t0 < 40 and va(c.get(f"/api/reports/{vid}").json()).get("status") != "completed":
        time.sleep(1)
    check("video: middle frame analysed", va(c.get(f"/api/reports/{vid}").json()).get("status") == "completed")

    print("== Free / local guarantees")
    st = c.get("/api/vision/status").json()
    meta = c.get("/api/meta").json()
    check("provider reports paid=false, media_leaves_machine=false, loopback endpoint", st["paid"] is False and st["media_leaves_machine"] is False
          and st["endpoint"].startswith("http://127.0.0.1"))
    probe = subprocess.run([sys.executable, "-c", "from app import config; print(config.VISION_PROVIDER)"], cwd=str(HERE.parent),
                           env={**os.environ, "VISION_PROVIDER": "gemini", "PRALAY_DATA_DIR": os.environ["PRALAY_DATA_DIR"]},
                           capture_output=True, text=True).stdout.strip()
    check("cloud provider names are not accepted (VISION_PROVIDER=gemini → 'none'); meta reports no paid services",
          probe == "none" and meta["capabilities"]["vision_paid_services"] is False, probe)
    check("dashboard summary exposes visual-AI state", c.get("/api/summary").json()["vision"]["state"] == "READY")
finally:
    stub.terminate()

ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} vision checks passed (adapter tested against a protocol stub — not a real model)")
sys.exit(0 if ok == len(results) else 1)
