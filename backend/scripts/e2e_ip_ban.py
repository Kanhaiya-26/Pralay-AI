"""3-strike IP ban, end to end on an ISOLATED server (temp data dir) with the Ollama protocol STUB in 'helmet' mode
(the image genuinely shows no disaster -> real CLAIM MISMATCH via the normal pipeline; nothing is forced).
Run:  python scripts/run_tests.py --suite ip-ban"""
import io
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
HERE = Path(__file__).resolve().parent
c = httpx.Client(base_url=API, timeout=60)
res = []


def check(n, cond, d=""):
    res.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  — {d}" if d and not cond else ""))


FP = "fp_1a2b3c4d"


def submit(seed, fp=FP):
    b = io.BytesIO(); Image.new("RGB", (320, 240), (seed * 40 % 255, 90, 120)).save(b, "JPEG")
    return c.post("/api/reports", data={"category": "flood", "claimed_location": "Mohali, Punjab", "is_demo": "true", "description": "ip-ban test",
                                        "device_fingerprint": fp}, files=[("files", (f"x{seed}.jpg", b.getvalue(), "image/jpeg"))])


def dbq(sql, p=()):
    import os, sqlite3
    return sqlite3.connect(os.path.join(os.environ["PRALAY_TEST_DATA_DIR"], "pralay.db")).execute(sql, p).fetchall()


def wait(rid):
    for _ in range(90):
        r = c.get(f"/api/reports/{rid}").json()
        if r["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
            return r
        time.sleep(1)
    return r


stub = subprocess.Popen([sys.executable, str(HERE / "fake_ollama.py"), "11555", "qwen2.5vl:3b"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
try:
    c.post("/api/test/vision-mode", json={"mode": "inline"})
    httpx.post("http://127.0.0.1:11555/_mode", json={"mode": "ok"}); c.post("/api/vision/validate")
    ok = wait(submit(1, fp="<script>alert(1)</script>").json()["id"])
    check("matching image: no strike (needs_review false); IP and fingerprint not exposed by the API",
          not ok["assessment"]["fields"]["needs_review"] and "ip_address" not in ok and "device_fingerprint" not in ok, ok["assessment"]["fields"]["needs_review"])
    check("malformed fingerprint ignored (stored as NULL)", dbq("SELECT device_fingerprint FROM reports WHERE id=?", (ok["id"],))[0][0] is None)
    httpx.post("http://127.0.0.1:11555/_mode", json={"mode": "helmet"}); c.post("/api/vision/validate")
    for i in range(2, 5):
        r = submit(i)
        check(f"submission {i - 1} with mismatched image accepted (201)", r.status_code == 201, r.status_code)
        rr = wait(r.json()["id"])
        check(f"   -> CLAIM MISMATCH (needs_review) on report #{rr['id']}", rr["assessment"]["fields"]["needs_review"])
    check("fingerprint recorded on the mismatched reports", dbq("SELECT COUNT(*) FROM reports WHERE device_fingerprint=?", (FP,))[0][0] == 3)
    check("3 strikes -> device fingerprint AND IP both in their ban tables",
          dbq("SELECT COUNT(*) FROM banned_fingerprints WHERE fingerprint=?", (FP,))[0][0] == 1 and dbq("SELECT COUNT(*) FROM banned_ips")[0][0] == 1)
    r = submit(9)
    check("4th submission from the same device -> 403 device-ban message", r.status_code == 403 and "device has been blocked" in r.json().get("detail", ""),
          (r.status_code, r.text[:120]))
    r = submit(10, fp="fp_99999999")
    check("new/forged fingerprint from the same IP -> still 403 (IP fallback)", r.status_code == 403 and "IP has been blocked" in r.json().get("detail", ""),
          (r.status_code, r.text[:120]))
finally:
    stub.terminate()
print(f"\n{sum(res)}/{len(res)} ip-ban checks passed")
sys.exit(0 if all(res) else 1)
