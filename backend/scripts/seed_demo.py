"""Seed SYNTHETIC DEMO data through the real API so every report flows through the full pipeline.

All images are generated programmatically, stamped "SYNTHETIC DEMO", and submitted with
source_type=demo. The one archive entry is a labelled demo record (no fabricated URL).

Usage (server running on :8000):
    .venv\\Scripts\\python.exe scripts\\seed_demo.py
"""
import io
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, TiffImagePlugin

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
IST = timezone(timedelta(hours=5, minutes=30))


def scene(seed, kind="flood"):
    rnd = random.Random(seed)
    w, h = 960, 640
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    sky = (rnd.randint(90, 140), rnd.randint(100, 150), rnd.randint(120, 170)) if kind != "fire" else (90, 60, 50)
    for y in range(h):
        f = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c * (1 - 0.4 * f)) for c in sky))
    x = 0
    while x < w:
        bw, bh = rnd.randint(50, 140), rnd.randint(120, 380)
        col = tuple(rnd.randint(70, 190) for _ in range(3))
        d.rectangle([x, h * 0.62 - bh, x + bw, h * 0.75], fill=col)
        for wy in range(int(h * 0.62 - bh) + 12, int(h * 0.7), 28):
            for wx in range(x + 8, x + bw - 12, 22):
                if rnd.random() < 0.7:
                    d.rectangle([wx, wy, wx + 10, wy + 14], fill=(230, 220, 160) if rnd.random() < 0.3 else (40, 50, 60))
        x += bw + rnd.randint(4, 30)
    if kind == "flood":
        for y in range(int(h * 0.6), h):
            d.line([(0, y), (w, y)], fill=(rnd.randint(95, 110), rnd.randint(85, 95), 60))
        for _ in range(60):
            yy = rnd.randint(int(h * 0.62), h)
            xx = rnd.randint(0, w)
            d.line([(xx, yy), (xx + rnd.randint(20, 90), yy)], fill=(150, 140, 110), width=2)
    elif kind == "fire":
        for _ in range(40):
            cx, cy = rnd.randint(0, w), rnd.randint(int(h * 0.3), int(h * 0.7))
            r = rnd.randint(20, 80)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, rnd.randint(80, 180), 0))
        d.rectangle([0, int(h * 0.75), w, h], fill=(60, 55, 50))
    else:  # landslide
        d.polygon([(0, h * 0.4), (w * 0.6, h * 0.55), (w, h), (0, h)], fill=(120, 90, 60))
        for _ in range(80):
            cx, cy = rnd.randint(0, w), rnd.randint(int(h * 0.6), h)
            d.ellipse([cx - 9, cy - 6, cx + 9, cy + 6], fill=(90, 80, 70))
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    d = ImageDraw.Draw(im)
    d.rectangle([12, 12, 330, 46], fill=(0, 0, 0))
    d.text((22, 22), "SYNTHETIC DEMO IMAGE - PRALAY AI", fill=(255, 220, 0))
    return im


def _rat(v):
    return TiffImagePlugin.IFDRational(v)


def to_jpeg(im, lat=None, lon=None, when=None, make="DemoCam", model="Synthetic-1"):
    ex = Image.Exif()
    if make is None:  # simulate a platform-stripped image (no EXIF at all)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    ex[0x010F], ex[0x0110] = make, model
    if when:
        s = when.astimezone(IST).strftime("%Y:%m:%d %H:%M:%S")
        ex[0x0132] = s
        sub = ex.get_ifd(0x8769)
        sub[36867] = s
        sub[36881] = "+05:30"
    if lat is not None:
        def dms(x):
            x = abs(x)
            dd = int(x); mm = int((x - dd) * 60); ss = round(((x - dd) * 60 - mm) * 60, 2)
            return (_rat(dd), _rat(mm), _rat(ss))
        g = ex.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = ("N" if lat >= 0 else "S"), dms(lat), ("E" if lon >= 0 else "W"), dms(lon)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88, exif=ex.tobytes())
    return buf.getvalue()


def submit(client, img_bytes, name, **fields):
    files = [("files", (name, img_bytes, "image/jpeg"))] if img_bytes else []
    data = {"source_type": "demo", "is_demo": "true", **{k: str(v) for k, v in fields.items() if v is not None}}
    r = client.post(f"{API}/api/reports", data=data, files=files)
    r.raise_for_status()
    print(f"  submitted report #{r.json()['id']}: {fields.get('description', '')[:60]}")
    return r.json()["id"]


def main():
    now = datetime.now(timezone.utc)
    c = httpx.Client(timeout=60)
    # Synthetic seeding is for isolated TEST servers only. The real deployment must never receive fake disaster records.
    if not c.get(f"{API}/api/meta").json().get("test_mode") and "--allow-synthetic" not in sys.argv:
        sys.exit("Refusing to seed SYNTHETIC data into a non-test server. Seeding is only allowed against isolated test servers "
                 "(PRALAY_TEST_MODE=1) or with an explicit --allow-synthetic flag.")
    print("Seeding SYNTHETIC DEMO data via", API)

    # Demo archive: an image "already published" long ago (for the recycled-media check). Registered via the API
    # so it lands in whichever server/database is being seeded.
    old_scene = scene(9001, "flood")
    if not any(a["is_demo"] for a in c.get(f"{API}/api/archive").json()):
        buf = io.BytesIO()
        old_scene.save(buf, "JPEG", quality=90)
        r = c.post(f"{API}/api/archive", data={"source_name": "DEMO ARCHIVE (synthetic record)", "first_published": "2023-07-10T09:00:00+00:00",
                                              "description": "Synthetic stand-in for a widely shared 2023 flood photo", "is_demo": "true"},
                   files=[("file", ("archive.jpg", buf.getvalue(), "image/jpeg"))])
        r.raise_for_status()
        print("  added 1 demo archive record (synthetic)")

    mohali = [(30.7046, 76.7179), (30.7120, 76.7090), (30.6985, 76.7260)]
    first = None
    for i, (lat, lon) in enumerate(mohali):
        b = to_jpeg(scene(100 + i * 37, "flood"), lat, lon, now - timedelta(hours=3 - i * 0.5))
        rid = submit(c, b, f"mohali_flood_{i}.jpg", category="flood", claimed_location="Mohali, Punjab",
                     description=["Knee-deep water on the main road near Phase 7 market, cars stalled",
                                  "Waterlogging in residential sector, water entering ground floors",
                                  "Underpass flooded after heavy rain, traffic diverted"][i],
                     claimed_event_time=(now - timedelta(hours=3)).isoformat(), reporter_ref=f"demo-citizen-{i + 1}")
        if i == 0:
            first = b
    submit(c, first, "forwarded.jpg", category="flood", claimed_location="Mohali",
           description="Forwarded on WhatsApp: Mohali flooding now", reporter_ref="demo-citizen-9",
           claimed_event_time=(now - timedelta(hours=2)).isoformat())
    submit(c, to_jpeg(old_scene.resize((900, 600))), "viral_flood.jpg", category="flood", claimed_location="Mohali, Punjab",
           description="Massive flood in Mohali right now!!", reporter_ref="demo-social-1", source_platform="demo-social",
           claimed_event_time=(now - timedelta(hours=1)).isoformat())
    submit(c, to_jpeg(scene(4242, "fire"), 19.0760, 72.8777, now - timedelta(hours=5)), "fire.jpg", category="fire",
           claimed_location="Sector 17, Chandigarh", description="Fire in a showroom, thick smoke", reporter_ref="demo-citizen-5",
           claimed_event_time=(now - timedelta(hours=4)).isoformat())
    submit(c, to_jpeg(scene(777, "flood"), 30.6942, 76.8606, now - timedelta(days=60)), "panchkula.jpg", category="flood",
           claimed_location="Panchkula, Haryana", description="Ghaggar overflowing near the bridge", reporter_ref="demo-citizen-6",
           claimed_event_time=(now - timedelta(hours=2)).isoformat())
    submit(c, to_jpeg(scene(31337, "landslide"), make=None), "shimla.jpg", category="landslide", claimed_location="Shimla, Himachal Pradesh",
           description="Debris on the highway after landslide, road blocked", reporter_ref="demo-citizen-7",
           claimed_event_time=(now - timedelta(hours=6)).isoformat())
    print("Done. Reports are processed automatically by the background workers.")


if __name__ == "__main__":
    main()
