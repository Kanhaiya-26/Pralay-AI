"""Extended runtime tests (run by run_tests.py after e2e_test.py, against the same isolated server).
Covers video, citizen text-only, pixel-level duplicates, transformed near-duplicates, archive recycling,
GPS placeholders, external source failure handling, concurrency/duplicate-incident prevention, retries,
Gemini unconfigured state, the Mastodon public-API connector (live), and the GeoJSON grid provider.
Writes a snapshot for the post-restart persistence suite.
"""
import io
import json
import os
import struct
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seed_demo import scene, to_jpeg  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
DATA = os.getenv("PRALAY_TEST_DATA_DIR")
c = httpx.Client(base_url=API, timeout=180)
results = []
NOW = datetime.now(timezone.utc)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def wait_report(rid, timeout=150):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/reports/{rid}").json()
        if r["processing_state"] in ("PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"):
            return r
        time.sleep(1.5)
    return c.get(f"/api/reports/{rid}").json()


def wait_idle(timeout=200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get("/api/summary").json()["reports"]
        if s["awaiting"] == 0 and s["processing"] == 0:
            return True
        time.sleep(2)
    return False


def post(data, files=()):
    return c.post("/api/reports", data={"is_demo": "true", **data}, files=list(files))


def jpeg_bytes(img, q=88):
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


def strip_app1(jpeg):
    """Remove EXIF (APP1) segments WITHOUT re-encoding pixels, like many platforms do."""
    out, i = bytearray(jpeg[:2]), 2
    while i < len(jpeg):
        if jpeg[i] != 0xFF:
            out += jpeg[i:]
            break
        marker = jpeg[i + 1]
        if marker == 0xDA:  # start of scan: copy rest
            out += jpeg[i:]
            break
        seg_len = struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        if marker != 0xE1:
            out += jpeg[i:i + 2 + seg_len]
        i += 2 + seg_len
    return bytes(out)


def labels(r):
    return {f["finding"] for f in r["findings"]}


def finding(r, lab):
    return next((f for f in r["findings"] if f["finding"] == lab), None)


print("== R1 ingestion")
import cv2  # noqa: E402
import numpy as np  # noqa: E402
vp = os.path.join(tempfile.gettempdir(), "pralay_ext_test.mp4")
w = cv2.VideoWriter(vp, cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
for i in range(30):
    f = np.full((240, 320, 3), (120, 90, 60), np.uint8)
    cv2.rectangle(f, (i * 5, 100), (i * 5 + 60, 200), (200, 180, 50), -1)
    w.write(f)
w.release()
vb = bytearray(open(vp, "rb").read())
k = vb.find(b"mvhd")
old_ct = int((NOW - timedelta(days=40) - datetime(1904, 1, 1, tzinfo=timezone.utc)).total_seconds())
if k > 0 and vb[k + 4] == 0:  # version 0 → 32-bit creation_time right after version/flags
    vb[k + 8:k + 12] = struct.pack(">I", old_ct)
rv = post({"category": "flood", "claimed_location": "Zirakpur, Punjab", "description": "video: water on road",
           "claimed_event_time": (NOW - timedelta(hours=1)).isoformat()}, [("files", ("clip.mp4", bytes(vb), "video/mp4"))])
check("video upload accepted", rv.status_code == 201, rv.text)
v = wait_report(rv.json()["id"])
m = v["media"][0]
check("video frames extracted + per-frame hashes", len(m["frames"]) == 3 and len(m["frame_phashes"]) == 3)
check("MP4 container creation time parsed", m["container_time"] is not None and m["container_time"].startswith(str((NOW - timedelta(days=40)).year)), m["container_time"])
check("old container time flagged as concern (not proof)", "VIDEO_CREATED_BEFORE_EVENT" in labels(v)
      and finding(v, "VIDEO_CREATED_BEFORE_EVENT")["severity"] == "concern")

rt = post({"category": "road_blockage", "claimed_location": "Kharar, Punjab", "description": "Tree fallen across the highway near the flyover"})
check("citizen text-only report accepted", rt.status_code == 201)
t = wait_report(rt.json()["id"])
check("text-only report processed (media check reports NO_MEDIA)", t["processing_state"] in ("PROCESSED", "MAPPED") and t["media_classification"] == "NO_MEDIA")

bad = post({"category": "flood", "claimed_location": "Mohali", "description": "pin test", "claimed_lat": "0", "claimed_lon": "0"})
check("rejects (0,0) placeholder pin", bad.status_code == 422 and "0,0" in bad.text)
bad = post({"category": "flood", "claimed_location": "Mohali", "claimed_event_time": (NOW + timedelta(days=2)).isoformat()},
           [("files", ("a.jpg", jpeg_bytes(scene(1, "flood")), "image/jpeg"))])
check("rejects future event time", bad.status_code == 422)
bad = post({"category": "flood", "claimed_location": "Mohali"}, [("files", ("a.jpg", b"\xff\xd8\xff" + b"garbage" * 10, "image/jpeg"))])
check("rejects corrupt JPEG with per-file message", bad.status_code == 422 and "File 1" in bad.text, bad.text)
ok = post({"category": "flood", "claimed_location": "Mohali", "file_last_modified": (NOW - timedelta(hours=2)).isoformat()},
          [("files", ("a.jpg", jpeg_bytes(scene(4711, "flood")), "image/jpeg"))])
check("submission response includes file details + receipt time", ok.status_code == 201 and ok.json()["files"][0]["sha256"] and ok.json()["received_at"])
okr = wait_report(ok.json()["id"])
check("client file-modified time preserved as separate observation", any(o["source"] == "client_file_modified" for o in okr["observations"]))

print("== R2 old / fake / duplicate media")
r1 = c.get("/api/reports/1").json()
orig = c.get(r1["media"][0]["url"]).content
stripped = strip_app1(orig)
check("test copy really has different bytes", stripped != orig and len(stripped) < len(orig))
rs = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "same photo, EXIF stripped"},
          [("files", ("stripped.jpg", stripped, "image/jpeg"))])
s_ = wait_report(rs.json()["id"])
ex = finding(s_, "EXACT_DUPLICATE")
check("pixel-identical copy (EXIF stripped) detected as exact duplicate", ex is not None and "pixels" in json.dumps(ex["evidence"]), ex and ex["evidence"])
check("conclusion separated from evidence, with trigger + uncertainty", s_["media_conclusion"]["classification"] == "EXACT_DUPLICATE"
      and s_["media_conclusion"]["triggered_by"] and s_["media_conclusion"]["uncertainty"])

r2 = c.get("/api/reports/2").json()
img2 = Image.open(io.BytesIO(c.get(r2["media"][0]["url"]).content))
mir = ImageOps.mirror(img2).resize((800, 533))
rm = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "mirrored repost"}, [("files", ("mir.jpg", jpeg_bytes(mir, 80), "image/jpeg"))])
mm = wait_report(rm.json()["id"])
nd = finding(mm, "NEAR_DUPLICATE")
check("mirrored + resized + recompressed copy → near duplicate (transform recorded)", nd is not None and nd["evidence"]["matches"][0]["transform"] == "mirror", nd and nd["evidence"])
r3 = c.get("/api/reports/3").json()
img3 = Image.open(io.BytesIO(c.get(r3["media"][0]["url"]).content))
crop = img3.crop((int(img3.width * .1), int(img3.height * .1), int(img3.width * .9), int(img3.height * .9)))
rc = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "cropped repost"}, [("files", ("crop.jpg", jpeg_bytes(crop, 85), "image/jpeg"))])
cc = wait_report(rc.json()["id"])
check("80% centre crop of stored image → near duplicate", "NEAR_DUPLICATE" in labels(cc), [f["finding"] for f in cc["findings"]])
rot = img3.rotate(90, expand=True)
rr = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "rotated repost"}, [("files", ("rot.jpg", jpeg_bytes(rot, 85), "image/jpeg"))])
ro = wait_report(rr.json()["id"])
check("rotated copy → near duplicate", "NEAR_DUPLICATE" in labels(ro), [f["finding"] for f in ro["findings"]])
dup_ids = {r["is_duplicate_of"] for r in (s_, mm, cc, ro)}
check("duplicates link to original and are excluded from mapping", None not in dup_ids and not any(x["grid_mapped"] for x in (s_, mm, cc, ro)))

hist = scene(5150, "landslide")
ra = c.post("/api/archive", data={"source_name": "TEST archive record", "first_published": "2022-08-01T00:00:00+00:00", "is_demo": "true"},
            files=[("file", ("h.jpg", jpeg_bytes(hist), "image/jpeg"))])
check("archive registration via API", ra.status_code == 201)
rh = post({"category": "landslide", "claimed_location": "Mandi, Himachal Pradesh", "description": "landslide today",
           "claimed_event_time": (NOW - timedelta(hours=2)).isoformat()}, [("files", ("h2.jpg", jpeg_bytes(hist.resize((700, 466)), 70), "image/jpeg"))])
hh = wait_report(rh.json()["id"])
check("resized/recompressed archived image → previously known (recycled), contradicted", "PREVIOUSLY_KNOWN_RECYCLED" in labels(hh) and hh["effective_status"] == "CONTRADICTED")

gps_mis = to_jpeg(scene(808, "flood"), 30.70, 76.72, NOW - timedelta(hours=1))
im = Image.open(io.BytesIO(gps_mis))
ex_ = im.getexif()
g = ex_.get_ifd(0x8825)
g[29] = (NOW - timedelta(hours=7)).strftime("%Y:%m:%d")  # GPSDateStamp
from PIL import TiffImagePlugin  # noqa: E402
hh_, mm_, ss_ = (NOW - timedelta(hours=7)).strftime("%H %M %S").split()
g[7] = (TiffImagePlugin.IFDRational(int(hh_)), TiffImagePlugin.IFDRational(int(mm_)), TiffImagePlugin.IFDRational(int(ss_)))
b_ = io.BytesIO()
im.save(b_, "JPEG", exif=ex_.tobytes(), quality=88)
rg = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "clock mismatch test"}, [("files", ("g.jpg", b_.getvalue(), "image/jpeg"))])
gg = wait_report(rg.json()["id"])
check("camera clock vs GPS satellite clock mismatch → METADATA_INCONSISTENT (concern)", "METADATA_INCONSISTENT" in labels(gg)
      and finding(gg, "METADATA_INCONSISTENT")["severity"] == "concern", [f["finding"] for f in gg["findings"]])

print("== R3 location")
zero = to_jpeg(scene(909, "flood"), 0.0, 0.0, NOW - timedelta(hours=1))
rz = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "null-island gps"}, [("files", ("z.jpg", zero, "image/jpeg"))])
zz = wait_report(rz.json()["id"])
check("(0,0) EXIF GPS rejected; falls back to claim with low confidence", zz["exif_lat"] is None and zz["location_confidence"] in ("low", "medium"))
ls = r1.get("location_summary") or c.get("/api/reports/1").json()["location_summary"]
check("location summary has claimed / extracted / investigated separately", ls and ls["claimed"] and ls["extracted_gps"] and ls["investigated"]
      and "agree" in ls["thresholds_km"])
check("claimed-place extent recorded and thresholds scaled", ls["claimed"]["extent_km"] is not None and ls["thresholds_km"]["agree"] >= 15)
check("landmark check honestly unavailable without visual AI", "unavailable" in ls["landmark_check"].lower())

print("== External sources")
src = {s["source"]: s for s in c.get("/api/sources").json()["sources"]}
check("source health tracked (NDMA, Google News, Open-Meteo successes)", all((src[k].get("successes") or 0) >= 1 for k in ("ndma_sachet", "google_news", "open_meteo")),
      {k: (src[k].get("state"), src[k].get("successes")) for k in src})
inc1 = c.get("/api/incidents/1").json()
check("evidence carries result_type + retrieval time", all(e["result_type"] in ("match", "no_match", "unavailable") and e["retrieval_time"] for e in inc1["evidence"]))
check("'no match' distinguished from 'unavailable'", any(e["result_type"] == "no_match" for e in inc1["evidence"]) or any(e["result_type"] == "match" for e in inc1["evidence"]))
c.post("/api/test/simulate-failure", json={"sources": ["google_news", "open_meteo", "ndma_sachet", "gdelt"]})
rf = post({"category": "fire", "claimed_location": "Ludhiana, Punjab", "description": "factory fire (failure-handling test)"},
          [("files", ("f.jpg", to_jpeg(scene(3030, "fire"), 30.9010, 75.8573, NOW - timedelta(hours=1)), "image/jpeg"))])
ff = wait_report(rf.json()["id"])
check("all external sources failing → report still fully processed", ff["processing_state"] in ("PROCESSED", "MAPPED") and not ff["error"], ff["processing_state"])
inc_f = c.get(f"/api/incidents/{ff['incident_id']}").json()
by_src = {e["source_name"]: e for e in inc_f["evidence"]}
check("uncached failing sources recorded as 'unavailable' with reason (cached national feeds may still answer)",
      all(by_src[k]["result_type"] == "unavailable" and "simulated" in by_src[k]["summary"] for k in ("Google News RSS", "Open-Meteo")),
      {k: (v["result_type"], v["summary"][:60]) for k, v in by_src.items()})
src2 = {x["source"]: x for x in c.get("/api/sources").json()["sources"]}
check("failed sources show failures in health table", all((src2[k].get("consecutive_failures") or 0) >= 1 for k in ("google_news", "open_meteo")),
      {k: src2[k].get("consecutive_failures") for k in src2})
c.post("/api/test/simulate-failure", json={"sources": []})

print("== Concurrency, duplicate-incident prevention, retries")
def send(i, out):
    out[i] = post({"category": "flood", "claimed_location": "Rajpura, Punjab", "description": f"concurrent report {i}"},
                  [("files", (f"c{i}.jpg", to_jpeg(scene(7000 + i * 13, "flood"), 30.4840 + i * 0.001, 76.5940, NOW - timedelta(hours=1)), "image/jpeg"))])
outs = [None] * 8
ths = [threading.Thread(target=send, args=(i, outs)) for i in range(8)]
[t_.start() for t_ in ths]
[t_.join() for t_ in ths]
check("8 concurrent submissions accepted", all(o is not None and o.status_code == 201 for o in outs))
ids = [o.json()["id"] for o in outs]
wait_idle()
reps = [c.get(f"/api/reports/{i}").json() for i in ids]
check("all concurrent reports processed exactly once (attempts == 1)", all(r["attempts"] == 1 and r["processing_state"] in ("PROCESSED", "MAPPED") for r in reps),
      [(r["id"], r["attempts"], r["processing_state"]) for r in reps])
claims = [sum(1 for h in r["status_history"] if h["stage"] == "CLAIMED") for r in reps]
check("no report claimed twice", claims == [1] * 8, claims)
check("concurrent nearby reports → ONE incident (no duplicate incidents)", len({r["incident_id"] for r in reps}) == 1, {r["incident_id"] for r in reps})

rx = post({"category": "flood", "claimed_location": "Mohali, Punjab", "description": "retry test"}, [("files", ("x.jpg", jpeg_bytes(scene(6060, "flood")), "image/jpeg"))])
xid = rx.json()["id"]
wait_report(xid)
stored = c.get(f"/api/reports/{xid}").json()["media"][0]["url"].rsplit("/", 1)[-1]
if DATA:
    Path(DATA, "uploads", stored).unlink()
    c.post(f"/api/reports/{xid}/reprocess")
    t0 = time.time()
    xr = None
    while time.time() - t0 < 90:
        xr = c.get(f"/api/reports/{xid}").json()
        if xr["processing_state"] == "FAILED_NEEDS_REVIEW":
            break
        time.sleep(2)
    retries = sum(1 for h in xr["status_history"] if h["stage"] == "RETRY_SCHEDULED")
    check("processing failure retried with backoff, then FAILED_NEEDS_REVIEW", xr["processing_state"] == "FAILED_NEEDS_REVIEW" and retries >= 2,
          (xr["processing_state"], retries))
else:
    check("retry test (needs PRALAY_TEST_DATA_DIR)", False, "not run via run_tests.py")

print("== Dashboard / stats / visual AI (unconfigured)")
s = c.get("/api/summary").json()
check("summary exposes measured processing stats", s["processing"]["count"] > 0 and s["processing"]["median_ms"] > 0)
check("summary exposes source availability + visual-AI state", len(s["sources"]) >= 5 and s["vision"]["state"] == "NOT_CONFIGURED")
check("visual AI unconfigured → visual analysis 'unavailable', no fabricated output",
      all(va["status"] == "unavailable" and not va["output"] for va in c.get("/api/reports/1").json()["visual_analysis"]))
check("visual-AI validate while disabled stays NOT_CONFIGURED", c.post("/api/vision/validate").json()["state"] == "NOT_CONFIGURED")

print("== Grid")
cells = c.get("/api/grid/cells").json()
check("cells carry incident-status breakdown + polygon + label", all("incident_status" in x and x["polygon"] and "NOT AN OFFICIAL" in x["grid_label"] for x in cells["cells"]))
check("grid config reports provider + is_official false", cells["config"]["provider"] == "regular_latlon" and cells["config"]["is_official"] is False)
os.environ.setdefault("PRALAY_DATA_DIR", DATA or tempfile.mkdtemp())
from app.grid import GeoJSONGridProvider  # noqa: E402
gj = Path(tempfile.gettempdir()) / "pralay_test_grid.geojson"
gj.write_text(json.dumps({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"cell_id": "T1"}, "geometry": {"type": "Polygon", "coordinates": [[[76.6, 30.6], [76.8, 30.6], [76.8, 30.8], [76.6, 30.8], [76.6, 30.6]]]}},
    {"type": "Feature", "properties": {"cell_id": "T2"}, "geometry": {"type": "Polygon", "coordinates": [[[76.8, 30.6], [77.0, 30.6], [76.9, 30.8], [76.8, 30.6]]]}}]}))
gp = GeoJSONGridProvider(gj, 1)
check("GeoJSON grid provider: point-in-polygon assignment", gp.cell_for(30.70, 76.72) == "FILE1-T1" and gp.cell_for(30.65, 76.9) == "FILE1-T2" and gp.cell_for(30.79, 76.99) is None)
check("GeoJSON grid label never claims official", "NOT confirmed as an official" in gp.label)

print("== Connector (live Mastodon public API)")
from app.connectors import extract_place  # noqa: E402
check("place extraction rejects alert words ('in Flood Warning')", extract_place("Update: Flood Watch issued in Flood Warning area by NWS")[1] is None)
check("place extraction rejects non-Indian places ('in Valencia, Spain')", extract_place("Terrible flooding in Valencia, Spain today")[1] is None)
check("place extraction accepts Indian places ('in Guwahati, Assam')", (extract_place("Severe waterlogging in Guwahati, Assam today")[0] or "").startswith("Guwahati"))
from app.connectors import classify_disaster  # noqa: E402
check("relevance gate: non-disaster post naming a place is rejected", classify_disaster("Lovely sunset photo in Kerala #kerala #photography", ["kerala", "photography"]) is None)
check("relevance gate: disaster post classified", classify_disaster("Severe waterlogging in Guwahati after heavy rain", ["assam"]) == "flood"
      and classify_disaster("Road blocked by a landslide near Shimla", []) == "landslide")
cons = {x["name"]: x for x in c.get("/api/connectors").json()}
check("unconfigured connectors report NOT_CONFIGURED", all(cons[k]["state"] == "NOT_CONFIGURED" for k in ("x_api", "youtube_data_api", "bluesky")))
run = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": ["flood"], "limit": 20, "wait": True}).json()
check("Mastodon public tag API fetched real posts", run.get("fetched", 0) > 0, run)
check("connector run accounted for every post (ingested + skipped == fetched)", run.get("ingested", 0) + sum(run.get("skipped", {}).values()) == run.get("fetched", -1), run)
if run.get("report_ids"):
    sr = c.get(f"/api/reports/{run['report_ids'][0]}").json()
    wait_idle()
    labels_ = [c.get(f"/api/reports/{i}").json().get("claimed_geocode_label") or "" for i in run["report_ids"]]
    check("every ingested post geocodes inside India", all(l.endswith("India") for l in labels_), labels_)
    check("ingested post keeps platform, URL, author, publication time", sr["source_type"] == "social_post" and sr["source_url"] and sr["original_publication_time"] and sr["reporter_ref"].startswith("@"))
run2 = c.post("/api/connectors/mastodon_public_tags/run", json={"tags": ["flood"], "limit": 20, "wait": True}).json()
check("re-running connector does not re-ingest the same posts", run2.get("ingested", 0) == 0 or not set(run2.get("report_ids", [])) & set(run.get("report_ids", [])))
print(f"   (live run: fetched {run.get('fetched')}, ingested {run.get('ingested')}, skipped {run.get('skipped')})")

wait_idle()
snap = {"reports": c.get("/api/summary").json()["reports"]["received_total"],
        "evidence_inc1": len(c.get("/api/incidents/1").json()["evidence"]),
        "review_rows_r8": len(c.get("/api/reports/8").json()["review_history"]),
        "mapped": c.get("/api/summary").json()["reports"]["mapped"]}
if DATA:
    Path(DATA, "snapshot.json").write_text(json.dumps(snap))
ok = sum(1 for _, x in results if x)
print(f"\n{ok}/{len(results)} extended checks passed")
sys.exit(0 if ok == len(results) else 1)
