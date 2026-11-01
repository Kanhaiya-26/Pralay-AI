# PRALAY AI — Crowdsourced Disaster Intelligence & Verification

**GRAVITON 2026 · Problem Statement #06 — Crowdsourced Extreme Weather Verification & GIS De-Duplicator · SDG 13**

PRALAY AI ingests crowdsourced disaster reports: photos, videos, text, forwarded social posts and posts from an authorised public social API. It checks each report for old, fake and duplicate media and verifies location and time from several sources. Related reports are grouped into incidents and checked against independent public sources. Reports that meet documented criteria are mapped onto a disaster control grid. Every report goes through all of this automatically, with retries, and the dashboard updates live.

> Decision-support only. Statuses describe the available evidence and its uncertainty. AI cannot guarantee authenticity. UNVERIFIED means *insufficient evidence*, not *false*.

---

## 1. Quick start (Windows, PowerShell)

Requirements: Windows 10/11, **Python 3.11+**, **Node.js 18+** (to build the UI), internet on first run. Optional: **Ollama** for free local visual AI.

```powershell
.\start.ps1        # first run sets everything up: backend\.venv, packages, UI build, backend\.env (VISION_MODE=deferred)
                   # then starts the server on http://127.0.0.1:8000 (localhost only) and opens the dashboard
.\stop.ps1         # stop it (data in backend\data is kept)
```
Options: `-NoBrowser`, `-Port 8010`, `-Foreground`, `-Rebuild`. If scripts are blocked: `powershell -ExecutionPolicy Bypass -File .\start.ps1`.
`start.ps1` refuses duplicate servers, explains port conflicts, and the app works with Ollama stopped (AI shows "unavailable").

Optional visual AI: install Ollama → `ollama pull qwen2.5vl:3b` → restart. Analysis runs **in the background** (deferred) so reports never wait for it.

Deployment targets (local, GitHub, GitHub Pages static showcase, future cloud / hybrid): see **[docs/DEPLOYMENT.md](DEPLOYMENT.md)**.

**Reload the 8 SYNTHETIC DEMO reports** (in a second terminal, server running):

```powershell
# optional clean slate: stop the server, delete backend\data, start again
cd backend
.\.venv\Scripts\python.exe scripts\seed_demo.py
```

**Tests.** Every suite runs against an isolated temporary server on port 8001 and **never touches the demo database**:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt      # once: Playwright for the browser tests
.\.venv\Scripts\python.exe scripts\run_tests.py --suite all            # 154 automated checks (~8 min): 39+53+11+20 + 31 vision-stub
.\.venv\Scripts\python.exe scripts\run_tests.py --suite ui_test.py     # 31 real-browser checks (drives installed Edge)
.\.venv\Scripts\python.exe scripts\run_tests.py --suite mastodon-real  # 16 live real-world checks; exit 2 = BLOCKED
```

| Suite | Checks | What it does |
|---|---|---|
| `e2e_test.py` | 39 | Original end-to-end flows A–F |
| `e2e_extended.py` | 53 | Media/location/source-failure/concurrency/retry/connector/grid checks |
| `e2e_restart.py` | 11 | Server restarted on the same data with visual AI pointed at a dead local port: persistence, real UNREACHABLE path, media stays local, no image data in logs |
| `e2e_vision.py` | 31 | Local visual-AI adapter against an **Ollama protocol stub** (not a model): unavailable, model missing, success, timeout, malformed output, HTTP 500, missing image, verdict stripping, non-local endpoint refused, deferred mode, video frames |
| `e2e_worker.py` | 20 | Fresh server with an 8 s lease: controlled worker hang → recovery, stale run can't overwrite, heartbeat keeps slow stages alive, retry limit, no premature PROCESSED, no duplicate incidents, Mastodon 429 cooldown, scheduled polling |
| `e2e_security.py` (`--suite security`) | 24 | Production-like server: traversal, upload hardening, SSRF, test-hook lockout, CORS, local-only AI, secrets/logs, stored XSS in a real browser |
| `showcase_test.py` | 14 | GitHub Pages static showcase served from `/PRALAY-AI/`: renders, banner, filters, actions refused, no backend requests |
| `e2e_vision_real.py` (`--suite vision-real`) | 5 | **Real** local model via Ollama (exit 2 = BLOCKED if Ollama/model missing) |
| `smoke_test.py` / `release_check.py` | 8 / 19 | Offline CI smoke test / verification of the running local deployment |
| `ui_test.py` | 31 | Playwright + Edge: file-picker image/video upload, upload progress, live tracker, drawers, mini-map, grid filters and reconfiguration, Sources & AI, chatbot, divider drag, theme persistence, console errors |
| `e2e_mastodon_real.py` | 16 | Finds a real qualifying public post (dry run, nothing downloaded) and ingests it end to end. Reports BLOCKED if none exists. |

Some checks call live services (Open-Meteo, Google News, NDMA SACHET, Nominatim, Mastodon). `--suite vision-real` runs the real local model through the machine's Ollama (exit 2 = BLOCKED if Ollama/model missing). Test-only hooks (`/api/test/*`) exist only when `PRALAY_TEST_MODE=1`, which the runner sets for its own servers.

Development with hot reload: `cd frontend; npm run dev` → http://localhost:5173 (proxies `/api` to :8000).

Demo deep links: `?theme=modern&mode=light&accent=blue` · `?open=report:6&tab=location_time` · `?open=incident:1` · `?ask=Is%20there%20flooding%20in%20Mohali%3F#chat` · `#sources`.

---

## 2. Architecture

```
frontend/ (React + Vite + Leaflet)                 backend/ (Python FastAPI, SQLite WAL)
  Dashboard · Submit · Grid · Processing   ──►     app/main.py          REST API, file serving, SPA
  Investigate (chat) · Sources & AI · Settings     app/ingest.py        single validated ingestion path
  polls /api/summary every 3 s and refetches       app/connectors.py    social connectors (Mastodon public API; others NOT_CONFIGURED)
  when the backend change counter moves            app/pipeline.py      DB-backed queue, leases, retries, monitor, timing
                                                   app/preprocessing.py validation, EXIF/GPS/clock, MP4 mvhd time, hashes, frames
                                                   app/forensics.py     exact/pixel/near/recycled duplicates, manipulation, metadata, conclusions
                                                   app/vision.py        provider-independent visual AI: local Ollama (free) | none
                                                   app/location_time.py location & time verification (extent-scaled thresholds)
                                                   app/clustering.py    incident association
                                                   app/external.py      NDMA SACHET, Google News RSS, GDELT, Open-Meteo
                                                   app/utils.py         cached HTTP, source health + cooldown, geocoding
                                                   app/fusion.py        incident vs report credibility
                                                   app/grid.py          grid providers (DEMO lattice | GeoJSON file)
                                                   app/chatbot.py       grounded assistant + query/incident cache
                                                   app/db.py            schema + in-place migrations
```

### How the five original requirements are implemented

| # | Requirement | Implementation |
|---|---|---|
| 1 | Multimedia ingestion | `POST /api/reports` → `ingest.create_report`: magic-byte type check, size limit, decode check, per-file error messages, coordinate validation (rejects out-of-range and the (0,0) placeholder), future-time rejection. It stores source type, platform, URL, publication time, submission time and per-file browser last-modified time. **Connectors:** Mastodon's documented public tag-timeline API (no credentials). A post is ingested only if it is public, not marked sensitive, ≤ `MASTODON_MAX_AGE_DAYS` (7) old, has media, describes a physical disaster (figurative uses such as "a flood of emails" are stripped first) *and* names a place that geocodes to an Indian settlement or administrative area. All of these gates run **before** any media is downloaded. A dry-run mode previews candidates without downloading. Health and cooldown are tracked per instance. X, YouTube and Bluesky are registered as NOT_CONFIGURED. |
| 2 | Old / fake / duplicate filtering | **Exact:** SHA-256 of file bytes, plus SHA-256 of decoded pixels (catches metadata-stripped copies). **Near:** pHash of the original plus rotated/mirrored/80%-cropped variants and every video frame, with dHash confirmation. **Recycled:** matches against the known-media archive (register entries via the API/UI) and earlier reports, dated by upload time or EXIF capture time. **Metadata:** camera clock vs GPS satellite clock, capture after upload, MP4 container time vs claimed event. **Manipulation indicators:** editing-software tag, EXIF dimension mismatch, modify/capture gap, Error Level Analysis. Each report gets a **conclusion kept separate from evidence**, with the triggering finding, unavailable checks and explicit uncertainty. |
| 3 | Location verification | Separate observations (claimed text, geocode with place extent, pin, EXIF GPS with DOP/altitude, device, landmark). Agreement/contradiction thresholds scale with the size of the claimed place (agree ≤ max(15 km, extent+3), contradict > max(50 km, 2·extent+20)). Invalid or (0,0) GPS is rejected with the reason recorded. The reviewer view shows claimed / extracted / investigated side by side on a mini-map. Landmark/signboard clues come from the local vision model; without it the check shows "unavailable". |
| 4 | Disaster control grid | `GridProvider` interface. Default: **CUSTOM / DEMO GRID — NOT AN OFFICIAL IMD/NDMA GRID** (regular lat/lon, default 0.25°, versioned, re-mappable). Alternative: a `GeoJSON` polygon provider for an operator-supplied cell file (never auto-labelled official). Per-cell report counts and statuses, incident-status breakdown, categories, polygon, mapping history. Filters: type, status, time, place, verified-only. |
| 5 | Timely processing | Persistent DB queue, 2 workers, atomic claim with a lease (300 s) renewed by a **heartbeat** while the current stage is younger than `PRALAY_STAGE_MAX_SEC` (600 s). Slow-but-alive stages keep their lease; a hung stage stops being renewed and is recovered. Priority 1 near active incidents. Idempotent re-processing. Every stage checks lease ownership, so an older attempt stops before writing anything. **Automatic retry with backoff** (3 attempts) → FAILED_NEEDS_REVIEW. Expired leases are recovered by the monitor. A global lock prevents duplicate incidents from concurrent workers. External failures never fail a report. Per-stage durations and total processing time are **measured** and shown. |

### Processing workflow

```
RECEIVED → PROCESSING [CLAIMED → VALIDATION → METADATA_EXTRACTION → DUPLICATE_CHECK → VISUAL_ANALYSIS → MEDIA_AUTHENTICITY
           → LOCATION_TIME_INVESTIGATION → INCIDENT_ASSOCIATION → EXTERNAL_EVIDENCE → CREDIBILITY_ASSESSMENT
           → GRID_ELIGIBILITY_CHECK → GRID_MAPPING → DASHBOARD_UPDATE] → PROCESSED | MAPPED
           (on error: RETRY_SCHEDULED → RECEIVED, up to 3 attempts → FAILED_NEEDS_REVIEW)
```

* Workflow state (`processing_state`) and verification status (`UNVERIFIED / CORROBORATED / CONTRADICTED`) are separate. A report stays `PROCESSING` until every stage has finished.
* A run checks that it still owns the report at every stage boundary and before committing. An older, stalled attempt aborts (`STALE_RUN_ABANDONED`) without writing results. This was verified with a controlled worker hang in `e2e_worker.py`.
* **Measured timing** (demo seed on this machine, 2026-10-31): median 8.2 s, p90 20.6 s. Isolated test runs ranged from 2 to 26 s. External evidence dominates (average ≈6–9 s; GDELT is throttled to 1 request / 5 s). These are measurements, not guarantees.

### External integrations & health
Each source records attempts, successes, consecutive failures, last error and cooldown (`source_status`). Rate limiting (HTTP 429) or repeated failures open a cooldown, and stale cached copies are labelled `[STALE …]`. Every evidence record has a `result_type`:
* `match`: a relevant item was found
* `no_match`: the source answered, but had nothing relevant
* `unavailable`: the request failed or was skipped

`retrieval_time` is the real fetch time, even for cached copies. Syndicated headlines collapse into one independent item.

---

### Technology stack (as actually used in this repository)

| Layer | Technology | Purpose |
|---|---|---|
| API / server | Python 3.12, **FastAPI**, **Uvicorn** | REST API, background threads, serves the built UI |
| Uploads | **python-multipart** | Multipart form parsing for media uploads |
| Database | **SQLite** (WAL mode) via the standard library | Shared intelligence DB, persistent work queue, caches (19 tables). *Not* PostgreSQL/PostGIS. |
| Images | **Pillow**, **NumPy** | Decode/validate, EXIF/GPS, thumbnails, DCT pHash/dHash, pixel hash, Error Level Analysis |
| Video | **opencv-python-headless** | Video metadata and frame extraction (MP4 `mvhd` time parsed in pure Python) |
| HTTP | **httpx** | External sources, Ollama (local), connectors — with DB-backed cache, cooldown and SSRF guard |
| Config | **python-dotenv** | `backend/.env` settings |
| Visual AI | **Ollama** (local, free) + `qwen2.5vl:3b` (default) / `gemma3:4b` | Structured observations only; loopback-only; no cloud/paid provider exists |
| Frontend | **React 18**, **Vite 5**, **Leaflet 1.9** + OpenStreetMap tiles | Dashboard, map, grid overlay, forms |
| Tests (dev only) | **Playwright** (drives installed Microsoft Edge), **psutil** | Browser tests; benchmark resource sampling |

Not used (no evidence in the repo): PostgreSQL, PostGIS, Redis, Celery, FAISS, any paid AI API.

### Data flow
```
Citizen upload / Mastodon public API ──► ingest.create_report (validate, store under UUID name) ──► reports (RECEIVED)
   ──► worker claims (lease + heartbeat) ──► metadata/hashes ──► duplicate & recycled checks ──► visual AI (inline, or queued
   for deferred local analysis) ──► authenticity indicators ──► location/time investigation (Nominatim) ──► incident association
   ──► external evidence (NDMA SACHET, Google News, Open-Meteo, GDELT; cached, health-tracked) ──► report + incident assessment
   ──► grid eligibility ──► CUSTOM/DEMO grid mapping ──► PROCESSED/MAPPED ──► dashboard (polls /api/summary every 3 s)
Deferred visual AI: vision worker ──► local Ollama ──► sanitize (schema, verdicts stripped) ──► re-apply observations ──► reassess
```

### Database design (SQLite, `backend/app/db.py`)
`reports` (workflow + verification state, locations, lease/retry fields) · `media` (hashes, EXIF, frames) · `media_findings`
(every check with evidence/method/confidence/limitation) · `observations` (location/time sources) · `visual_analyses`
(local AI observations, status) · `incidents` · `evidence` (external sources, stance, result type) · `assessments`,
`status_history`, `review_history`, `grid_mappings` (audit trails) · `grid_config` · `archive_media` (known old media) ·
`query_cache`, `http_cache`, `geocode_cache` (shared intelligence caches) · `source_status` (integration health) ·
`connector_runs` · `system_events`. Schema upgrades are additive and applied in place at startup.

## 3. Credibility rules

### A. Incident confidence (`fusion.assess_incident`)
*Independent reports* = processed, not contradicted, not duplicates, location not unresolved. *Strong* = medium/high location confidence. *External groups* = distinct independence groups among supporting evidence.

CORROBORATED if any rule holds and no source contradicts:
* **R1** ≥3 strong independent reports
* **R2** ≥2 independent reports + ≥1 non-weather external group
* **R3** ≥1 report + an official alert
* **R4** ≥2 independent non-weather external groups
* **R5** ≥1 report + ≥1 non-weather group + weather support

CONTRADICTED if every report is contradicted and nothing supports the incident. Otherwise UNVERIFIED.
Score = `25 + 10·min(reports,4) + 5·min(strong,3) + 15·min(ext,3) + 8·weather − 10·contradicted − 15·contradicting_sources`, clipped to 0–100.

### B. Report confidence (`fusion.assess_report`)
* CONTRADICTED on any `contradiction`-severity finding: recycled media, OLD_MEDIA, published before the event, or EXIF GPS beyond the contradiction threshold.
* CORROBORATED if the incident is corroborated, location confidence is medium/high, and the media is not POTENTIALLY_MANIPULATED.
* Otherwise UNVERIFIED.
* Score = 50 + documented additive weights.

**Scores are transparent heuristics, not calibrated probabilities.** Missing evidence never counts as contradiction.

### Grid eligibility (`grid.eligibility`)
A report is mapped as verified only if all of these hold:
* status CORROBORATED
* location confidence medium/high
* not a duplicate
* has coordinates
* inside the grid extent

Every located report still gets a display cell. A report whose location is contradicted is shown in the cell it *claims*.

---

## 4. Disaster control grid — CUSTOM / DEMO GRID, NOT AN OFFICIAL IMD/NDMA GRID
No official IMD/NDMA grid-cell specification was available.
* **Default grid:** a configurable regular lattice over 6–38°N, 68–98°E, with cell IDs `DEMO<version>-R<row>C<col>`. Change the resolution from the Grid page or with `POST /api/grid/config`; this bumps the version and re-maps every report.
* **Official grid later:** set `GRID_PROVIDER=geojson` and `GRID_GEOJSON_PATH=<file>` (a FeatureCollection of Polygons with a `cell_id` property). PRALAY then labels the grid *"GRID FROM FILE … — provenance not verified by PRALAY; NOT confirmed as an official IMD/NDMA grid"*.

---

## 5. Six-minute live demonstration

**Data provenance.** Reports #1–#8 are **SYNTHETIC DEMO** data: generated images stamped "SYNTHETIC DEMO IMAGE", with a purple DEMO DATA badge. The single archive record is synthetic too. **External evidence is live**, fetched from NDMA SACHET, Google News RSS, Open-Meteo and GDELT, with real retrieval times. A report submitted during the demo is a real submission and carries no DEMO badge.

**Before the demo** (at least 20 minutes ahead: after a reseed, the 8 deferred local-AI analyses take ~16 min on a CPU-only laptop — or set `VISION_PROVIDER=none` in `backend\.env` for a lighter demo)
1. Stop the server, delete `backend\data`, run `.\start.ps1`, then `scripts\seed_demo.py`.
2. Wait until *Awaiting / processing* shows 0 (about 1–2 min).
3. Have a JPEG with GPS EXIF taken near Mohali ready, or any photo (it will end up UNVERIFIED).
4. Open http://127.0.0.1:8000.

| Time | Step | Expected result | If it differs |
|---|---|---|---|
| 0:00–0:40 | **1. Dashboard & source health** | 8 reports (8 demo), 4 corroborated / 3 contradicted / 1 unverified reports, 3 mapped. Measured median/p90 processing time. Source chips: NDMA, Google News, Open-Meteo, Nominatim **OK**; GDELT often **COOLDOWN/FAILING**; Visual AI (local) **READY** or **UNREACHABLE**. The "NOT AN OFFICIAL IMD/NDMA GRID" banner is visible. | GDELT red or amber is expected: it rate-limits this network (HTTP 429) and the app pauses it. If all sources are red, the venue has no internet. Evidence is then recorded as "unavailable"; the pipeline still works. |
| 0:40–1:50 | **2. Citizen submission** (Submit report) | Pick the photo, type "Mohali, Punjab" and submit. A progress bar appears, then the tracker steps through 12 stages. Priority badge "near an active incident". Typically finishes in a few seconds to ~25 s: GPS near Mohali gives **CORROBORATED → MAPPED**; no GPS gives **UNVERIFIED** (location claim-only). | A slow finish means external sources (GDELT throttle) are slow; the stage pills show where. "FAILED / NEEDS REVIEW" would show a retry note: open the drawer's History tab. |
| 1:50–2:40 | **3. Duplicate & recycled** (Processing → #4, #5) | #4 Media tab: conclusion **EXACT DUPLICATE** of #1 (identical file), separated from the evidence, with uncertainty; not mapped. #5: **PREVIOUSLY KNOWN / RECYCLED** (matches the synthetic 2023 archive record) → CONTRADICTED. | — |
| 2:40–3:20 | **4. GPS contradiction** (#6 → location / time) | Claimed *Sector 17, Chandigarh* vs EXIF GPS in Mumbai, **1,355 km** apart; dashed line on the mini-map; **LOCATION CONTRADICTION**. Landmark check: "unavailable" or "pending" (local AI). | If the map tiles are blank there's no internet for OSM tiles; the three cards still show the data. |
| 3:20–3:40 | **5. Missing metadata** (#8, plus #7) | #8: METADATA_UNAVAILABLE → "unavailable evidence, NOT a sign of fakery", stays UNVERIFIED (not contradicted). #7: OLD_MEDIA (captured 60 days before the claim). | — |
| 3:40–4:30 | **6. Incident review & override** (Dashboard → #1 → Open full review) | Incident confidence (60–70) shown separately from each report's confidence; rule **R1 crowd consensus** met. Evidence rows show stance + result type + retrieval time. Apply an override with a reason → audit trail row; then clear it. | A reason under 5 characters is rejected by design. |
| 4:30–5:15 | **7. Grid** (Grid intelligence) | Type filter "Fire" leaves only the Chandigarh cell. Click the Mohali cell: reports, incidents by status, mapping history. Optionally reconfigure 0.5° → all re-mapped → back to 0.25°. | — |
| 5:15–5:40 | **8. Shared intelligence** (Investigate) | "Is there flooding in Mohali?" lists incidents with sources and timestamps. "Are there flood incidents near Mohali?" shows **REUSED FROM SHARED INTELLIGENCE**. | The first question may take ~10–20 s if evidence is stale (live re-check); the second is instant. |
| 5:40–6:00 | **9. Sources & AI** | Live source table; Visual AI (local, free) READY or UNREACHABLE — either is fine; reports never wait for it; connectors: Mastodon AVAILABLE, X/YouTube/Bluesky NOT CONFIGURED. Mention the real Mastodon end-to-end test (16/16, see AUDIT.md). | Avoid clicking *Run now* live: geocoding is rate-limited and a run can take minutes. |

## 6. Configuration
All settings live in `backend/.env` (template: `backend/.env.example`). Secrets are read server-side only and are never returned by the API or written to logs (both are covered by the restart test suite).

* **Visual AI (local, free):** install Ollama from ollama.com, run `ollama pull qwen2.5vl:3b` (or `gemma3:4b`), keep `VISION_PROVIDER=ollama`, then **Sources & AI → Check local model now**. States: `READY`, `MODEL_NOT_INSTALLED`, `UNREACHABLE`, `BLOCKED_NON_LOCAL`, `NOT_CONFIGURED`. `VISION_MODE=deferred` (default) lets reports finish first and adds AI observations afterwards.
* **Mastodon connector:** works without credentials. `MASTODON_TAGS` and `MASTODON_INSTANCE` choose what to read, and `MASTODON_POLL_MIN>0` enables polling.
* **X / YouTube / Bluesky:** credentials can be set, but adapters are not implemented. They show `CONFIGURED_NOT_IMPLEMENTED` and fetch nothing.
* **Grid:** `GRID_PROVIDER`, `GRID_RESOLUTION_DEG`, `GRID_GEOJSON_PATH`.

## 7. Known limitations
* **Visual AI**: local only (Ollama, `qwen2.5vl:3b` default). Real-model test passed (5/5) but on this CPU-only laptop one image takes ~2 minutes, so analysis is deferred; observation quality on synthetic images was weak; `gemma3:4b` not benchmarked (see reports/MODEL_BENCHMARK.md). Gemini and all cloud AI were removed.
* **Social media**:
  * The Mastodon public-API connector is real and was **validated end to end with a real post** on 2026-10-31: @Mathrubhumi_English, 2026-10-27, Tamil Nadu heavy rain. It went from discovery through media download, pipeline, incident, external evidence and dashboard; result UNVERIFIED (single source, state-level location). This ran in an isolated test DB; the demo DB contains no real social posts.
  * Gates are keyword/geocoder heuristics. News *commentary* about a disaster (not an event report) can pass. Category comes from the post's own hashtags first (that real post was tagged #Cyclone and became `cyclone_storm`, although the article is about heavy rain/flooding).
  * Most real candidates found were news-publisher posts, not citizen media.
  * X / YouTube / Bluesky are not implemented (no authorised access; Bluesky's public search returned HTTP 403).
  * Nothing is scraped.
* **Reverse image search**: not integrated (no legitimate free service). Recycled detection covers only PRALAY's own archive and reports.
* **Official IMD/NDMA grid**: not available, so a custom demo grid is used. The GeoJSON provider is ready for an operator-supplied file.
* **Geocoding** uses OSM Nominatim (1 request/s policy), which makes connector runs take minutes. Place-extent radii come from bounding boxes and are approximate.
* **GDELT** frequently returns HTTP 429 from this network. It is recorded as unavailable or in cooldown.
* **Open-Meteo** is modelled data (~11 km). Low modelled rain is never treated as disproof.
* **Video**: the container creation time is read from MP4/MOV `mvhd` and can be rewritten by editing apps (concern, not proof). WEBM has no equivalent field.
* **Manipulation indicators** (ELA, EXIF heuristics) are weak signals affected by platform recompression.
* **Infrastructure**: SQLite instead of PostgreSQL/PostGIS, a DB-backed cache instead of Redis, threads instead of Celery, linear hash scan instead of FAISS. All access goes through `db.py`, `utils.cached_get` and `pipeline.py`, which keeps a later migration contained.
* The **chatbot** uses deterministic intent rules (guaranteed grounding). Unusual phrasings fall back to help text.
* Map markers are incident centroids, not affected-area boundaries.

See `AUDIT.md` for the full audit.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `http://127.0.0.1:8000` does not load | Server not running → `.\start.ps1`. "UI not built" warning → `cd frontend; npm install; npm run build`. |
| Dashboard shows "Backend unreachable" | The API process stopped; restart it. Data in `backend\data` persists. |
| Visual AI shows `UNREACHABLE` | Ollama is not running: start the Ollama app (or `ollama serve`). |
| Visual AI shows `MODEL_NOT_INSTALLED` | `ollama pull qwen2.5vl:3b` (or set `OLLAMA_MODEL`). |
| Visual AI shows `BLOCKED_NON_LOCAL` | `OLLAMA_URL` points off this machine; PRALAY refuses to send media there by design. |
| AI analysis stays "pending" | Deferred mode analyses one image at a time on CPU (tens of seconds each); see §2 benchmark. |
| GDELT shows COOLDOWN / FAILING | GDELT rate-limits (HTTP 429); PRALAY pauses it automatically. Not an app fault. |
| All sources red, blank map | No internet: evidence is recorded as "unavailable", processing continues; map tiles need internet. |
| Reports stuck in "Awaiting" | Check the server log; stalled work is recovered automatically after the lease (default 300 s). |
| Port 8001 busy when testing | Another test server is still running; wait or stop it. Tests never use the demo DB on :8000. |
