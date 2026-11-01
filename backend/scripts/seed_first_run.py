"""First-run demo data for a fresh clone: 3 SYNTHETIC reports submitted through the real API by "Demo Reporter A".

Images are generated here and labelled SYNTHETIC DEMO (real user photos are never shipped in the repository).
Every report is flagged is_demo and goes through the full pipeline, so the outcome is computed, not written:
  1. a synthetic fire scene claimed as FIRE                -> expected to match the claim
  2. plain still-life shapes claimed as FLOOD              -> expected CLAIM MISMATCH (no disaster in the image)
  3. plain "room" shapes claimed as CYCLONE                -> expected CLAIM MISMATCH
Reporter credibility for "Demo Reporter A" is then derived from these outcomes (2 mismatches -> 30/100).
The mismatch results need the local vision model (Ollama + qwen2.5vl:3b); they appear about 2-3 minutes per image later.

Usage (backend running on http://127.0.0.1:8000):
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\seed_first_run.py
Remove later: the reports are is_demo=1; see backend\\scripts\\cleanup_synthetic.py (dry run first).
"""
import io
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import scene  # noqa: E402  (synthetic scene generator shared with the test seed)

API = "http://127.0.0.1:8000"
REPORTER = "Demo Reporter A"


def jpeg(im):
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 260, 24), fill=(0, 0, 0)); d.text((6, 6), "SYNTHETIC DEMO - PRALAY AI", fill=(255, 255, 255))
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=90)
    return b.getvalue()


def still_life():
    im = Image.new("RGB", (800, 600), (235, 225, 200)); d = ImageDraw.Draw(im)
    d.rectangle((250, 200, 550, 420), fill=(120, 70, 40)); d.ellipse((330, 120, 470, 230), fill=(200, 160, 120))
    return im


def room():
    im = Image.new("RGB", (800, 600), (210, 205, 200)); d = ImageDraw.Draw(im)
    d.rectangle((0, 420, 800, 600), fill=(150, 150, 155)); d.rectangle((480, 260, 780, 440), fill=(225, 225, 230))
    d.ellipse((230, 230, 400, 290), fill=(245, 245, 245)); d.rectangle((300, 290, 330, 430), fill=(30, 30, 30))
    return im


REPORTS = [
    ("fire", "Sector 17, Chandigarh", "SYNTHETIC DEMO: smoke and flames from a showroom", lambda: scene(3, "fire")),
    ("flood", "Mumbai", "SYNTHETIC DEMO: claimed flood (image shows no disaster)", still_life),
    ("cyclone_storm", "Chennai", "SYNTHETIC DEMO: claimed cyclone (image shows an indoor room)", room),
]


def main():
    try:
        httpx.get(API + "/api/health", timeout=5).raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"Backend not reachable at {API}. Start it first:  .\\start.ps1")
    print(f"Seeding 3 SYNTHETIC demo reports as '{REPORTER}' via {API}/api/reports ...")
    for cat, loc, desc, make in REPORTS:
        r = httpx.post(API + "/api/reports", timeout=60,
                       data={"category": cat, "claimed_location": loc, "description": desc, "reporter_ref": REPORTER,
                             "source_type": "demo", "is_demo": "true"},
                       files=[("files", (f"demo_{cat}.jpg", jpeg(make()), "image/jpeg"))])
        if r.status_code == 201:
            print(f"  OK   report #{r.json()['id']:<4} {cat:<14} {loc:<24} {desc}")
        else:
            print(f"  FAIL {cat:<14} HTTP {r.status_code}: {r.text[:160]}")
    print("\nDone. Reports are processed automatically. With Ollama running, the image check marks reports 2 and 3 as\n"
          "CLAIM MISMATCH after ~2-3 minutes each, and the Dashboard's Reporter Credibility panel shows Demo Reporter A.\n"
          "All three are labelled DEMO in the UI.")


if __name__ == "__main__":
    main()
