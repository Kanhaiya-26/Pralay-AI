# 20-minute integration sprint: report (2026-10-31)

## Credentials (presence only; values never read out)
NASA_FIRMS_MAP_KEY **missing** · NASA_EARTHDATA_TOKEN **missing** · OPENROUTER_API_KEY **missing**.
FIRMS, Earthdata/IMERG quantitative and OpenRouter were **skipped**. Nothing was faked for them.

## Implemented
- **Acquisition layer wired into the app** (it previously existed but was unused):
  - `GET /api/acquisition/sources` (per-source health; missing credentials are shown by name only)
  - `GET /api/acquisition/records`
  - `POST /api/acquisition/{provider}/run`
  - `GET /api/acquisition/assets/{sha}`
  - The `poll_loop` is started; polling is opt-in per provider via `PRALAY_<NAME>_POLL_MIN`.
- **IMD RSS parser fixes** (`providers.rss_records`):
  - Uses the IST-offset `sent` time (`+5:30`); a zone-less `pubDate` is read as IST, never UTC.
  - De-duplicates by GUID and drops the 100-item cap.
  - `active_warning` is computed from `Expires` (it used to be always true).
  - Stores `onset`; classified `OFFICIAL_WARNING`.
- **GIS layer "IMD warnings"**:
  - `GET /api/gis/imd-warnings` joins RSS warnings to the **official IMD district polygons** (IMD GeoServer `district_warnings_india`) by exact district name. Unmatched districts are listed, not drawn. No invented points.
  - On the map: orange = active, grey dashed = expired. A toggle and status line are added. Each tooltip shows the source, issue time, age, expiry, classification and "not confirmation".
- Also finished this session: the media category-mismatch fix (see `MEDIA_VERIFICATION_BUG_REPORT.md`).

## Fetched live and stored in `backend/data/pralay.db` (real, HTTP 200)
| Source | Retrieved | Stored | Rejected (reason) |
|---|---|---|---|
| IMD district nowcast RSS `dist_nowcast_rss.php` | 100 | 100 | 0. State STALE_DATA: the newest item is about 5.5 h old |
| NDMA SACHET alerts RSS | 100 | 100 | 0 |
| IMD GeoServer AWS/ARG | 2000 | 1980 | 20 outside India |
| IMD GeoServer SYNOP | 466 | 442 | 21 outside India, 3 no timestamp |
| IMD GeoServer METAR | 145 | 137 | 8 outside India |
| IMD GeoServer station nowcast | 1219 | 1104 | 63 no timestamp, 40 future-stamped, 12 outside India |
| IMD GeoServer district nowcast (polygons) | 764 | 734 | 8 no timestamp, 22 future-stamped |
| IMD GeoServer district warnings (polygons) | 764 | 756 | 8 no timestamp |
| **Total** | | **5353** | |

`districtwisewarnings.php` returned **HTTP 500, empty body** under PRALAY's honest User-Agent. I did not retry with a spoofed browser identity. A real capture of it from earlier today is the unit-test fixture.

## GIS layers showing real data (verified in Edge, headless)
- **IMD warnings:** 10 district polygons drawn for the last 24 h, all expired (0 active at verification time); 1 unmapped (LAKHIMPUR KHERI, a name mismatch). The toggle removes them; no page errors. Screenshot: `reports/gis_imd_warnings.png`.
- Crowd-report hotspots and incidents: existing layers, unchanged.
- The **AWS/SYNOP/METAR/SACHET records are stored but not yet drawn** on the map. There is no FIRMS or rainfall layer (no key).

## IMD 28 products (live probe today, 15:36 UTC; `reports/imd_probe.json`)
- **AUTH_REQUIRED (HTTP 401): 20.** These are #1–4, 7–18 and 20–23. The 2 mapping endpoints and 10 legacy URLs also returned 401. Nothing was bypassed.
- **NOT_AN_API: 8.** No API URL is published in the reference for #5 bulletin, #6 Mausamgram, #19 fishermen, #24–25 highway, #26 radar, #27 lightning and #28 agromet.
- **WORKING: 0** of the authenticated API products.
- Public alternatives that work, which are **not** the API itself: IMD GeoServer WFS (AWS, SYNOP, METAR, station/district nowcast, district warnings) and the district nowcast RSS.

## Tests
- IMD RSS unit tests (real-capture fixture + edge cases): **12/12**.
- Regression results are listed below (run after these changes).

## Remaining critical bugs / gaps
1. IMD GeoServer nowcasts include **future-stamped** records (62), probably because IMD stamps local time with a UTC marker. They are rejected, not shifted.
2. AWS/SYNOP/METAR/SACHET are stored but have no map layer yet. There is no source-health panel in the UI yet (the API exists).
3. Five RSS district names don't match the polygon names (spelling variants). No alias table yet.
4. The assistant's country-wide query and "warning vs incident" improvements were **not done** in this sprint.
5. The intermittent Leaflet `'save'` console error in `ui_test` is still open.

## User action needed
Put `NASA_FIRMS_MAP_KEY` (free) and, if wanted, `NASA_EARTHDATA_TOKEN` in `backend/.env`. To keep IMD/SACHET updating automatically, set `PRALAY_IMD_PUBLIC_POLL_MIN=30` and `PRALAY_SACHET_POLL_MIN=15`.

## Start
`.\start.ps1`, then open http://127.0.0.1:8000. A manual refresh is `POST /api/acquisition/imd_public/run`.
