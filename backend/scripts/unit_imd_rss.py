"""Unit tests for the IMD district-warning RSS parser (providers.rss_records). No network.
Fixture: tests_fixtures/imd_districtwisewarnings_20261031.xml, a REAL capture of
https://mausam.imd.gov.in/imd_latest/contents/districtwisewarnings.php (2026-10-31), unmodified.
Run:  .venv\\Scripts\\python.exe scripts\\unit_imd_rss.py"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PRALAY_DATA_DIR", tempfile.mkdtemp(prefix="pralay_unit_"))  # never touch the live DB
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import providers  # noqa: E402

FIX = Path(__file__).resolve().parent / "tests_fixtures" / "imd_districtwisewarnings_20261031.xml"
res = []


def check(n, cond, d=""):
    res.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}" + (f"  — {d}" if d and not cond else ""))


body = FIX.read_bytes()
recs = providers.rss_records(body, "imd", "https://mausam.imd.gov.in/imd_latest/contents/districtwisewarnings.php")
raw_items = body.count(b"<item")
check("real feed parsed; no 100-item cap", len(recs) > 0 and len(recs) == len({r["source_record_id"] for r in recs}), (len(recs), raw_items))
j = next(r for r in recs if r["title"] == "JAJAPUR")
check("'+5:30' sent time honoured (17:36:52 IST = 12:06:52 UTC)", j["published_at"].startswith("2026-10-31T12:06:52"), j["published_at"])
check("expiry 19:47 IST = 14:17 UTC", j["valid_until"].startswith("2026-10-31T14:17:00"), j["valid_until"])
check("onset kept separately (16:47 IST = 11:17 UTC), event_at not invented", j["data"]["onset"].startswith("2026-10-31T11:17:00") and j["event_at"] is None)
check("classified OFFICIAL_WARNING, not an incident; limitation stated", j["data"]["classification"] == "OFFICIAL_WARNING" and j["kind"] == "WARNING"
      and "not confirmation" in j["data"]["limitation"])
check("no invented geometry (district_unresolved until boundary match)", j["geometry"] is None and j["geo_precision"] == "district_unresolved")
check("warning expired relative to now (feed is from 2026-10-31 evening)",
      j["data"]["active_warning"] == (datetime.fromisoformat(j["valid_until"].replace("Z", "+00:00")) > datetime.now(timezone.utc)))
naive = b"<rss><channel><item><title>X</title><guid>g1</guid><pubDate>Sat, 31 Oct 2026 17:36:52 </pubDate></item></channel></rss>"
n = providers.rss_records(naive, "imd", "e")[0]
check("zone-less pubDate interpreted as IST, never UTC", n["published_at"].startswith("2026-10-31T12:06:52"), n["published_at"])
dup = b"<rss><channel>" + b"<item><title>A</title><guid>same</guid></item>" * 3 + b"</channel></rss>"
check("GUID de-duplication within a fetch", len(providers.rss_records(dup, "imd", "e")) == 1)
check("empty feed -> 0 records, no crash", providers.rss_records(b"<rss><channel></channel></rss>", "imd", "e") == [])
check("item with missing fields skipped/handled", providers.rss_records(b"<rss><channel><item><guid>z</guid></item></channel></rss>", "imd", "e") == [])
try:
    providers.rss_records(b"<rss><channel><item>", "imd", "e")
    check("malformed XML raises (caller records failure)", False)
except Exception as e:
    check("malformed XML raises a parse error (caller records failure, app continues)", "ParseError" in type(e).__name__, type(e).__name__)
print(f"\n{sum(res)}/{len(res)} IMD RSS unit checks passed")
sys.exit(0 if all(res) else 1)
