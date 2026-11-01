# PRALAY AI — Release Audit

GRAVITON 2026 · Problem Statement #06 (Crowdsourced Extreme Weather Verification & GIS De-Duplicator) · 2026-10-31

Detailed evidence:
* [reports/FINAL_TEST_REPORT.md](reports/FINAL_TEST_REPORT.md): **283 checks PASS, 0 FAIL**
* [reports/SECURITY_AUDIT.md](reports/SECURITY_AUDIT.md): 24/24
* [reports/MODEL_BENCHMARK.md](reports/MODEL_BENCHMARK.md): **partial**
* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## Release status

| Target | Status | Evidence |
|---|---|---|
| **Local deployment (Windows)** | **READY** | `.\start.ps1` executed and verified: health, UI, SQLite, 127.0.0.1-only binding, deferred AI, works with Ollama stopped. Release checks 19/19 + 4/4; clean-clone first-run setup verified. |
| **GitHub repository** | **READY to publish — not pushed** | `.gitignore` verified; 178 files (~1 MB); secret/personal-data scan clean; no file over 1 MB. Git is not installed and no remote exists. **Pushing needs your approval and sign-in.** |
| **GitHub Pages** | **READY (static showcase) — not deployed** | `npm run build:showcase` verified; browser test 14/14 from a `/PRALAY-AI/` subpath; no backend URL; workflow prepared. Not executed on GitHub. **Pages hosts only a read-only synthetic snapshot**, not FastAPI, SQLite, workers or Ollama. |

## Five original requirements

| # | Requirement | Status | Evidence | Limitation |
|---|---|---|---|---|
| 1 | Crowdsourced multimedia ingestion | **IMPLEMENTED AND TESTED** | Image, video and text upload (API and real browser); per-file validation; a **live Mastodon post ingested end to end** | X needs a paid API (excluded by the free-only policy); YouTube and Bluesky adapters not built |
| 2 | Old / fake / duplicate filtering | **IMPLEMENTED AND TESTED** | Exact, pixel-level, transform-robust (mirror/rotate/crop/resize), recycled-archive, OLD_MEDIA, camera-vs-GPS clock, video container time; conclusion kept separate from evidence | Heuristic manipulation indicators; no reverse image search (no free legitimate service) |
| 3 | Location verification | **IMPLEMENTED AND TESTED** | Agreement, contradiction (1,355 km case), claim-only, (0,0) rejection; extent-scaled thresholds; mini-map comparison | Landmark clues depend on the local vision model (slow on CPU) |
| 4 | IMD/NDMA disaster control grid mapping | **IMPLEMENTED AND TESTED on a CUSTOM / DEMO GRID — NOT AN OFFICIAL IMD/NDMA GRID** | Eligibility-gated mapping, per-cell aggregation, filters, inspection, reconfiguration, GeoJSON provider for a future official file | **No official IMD/NDMA grid specification exists in the project.** Official integration: NOT IMPLEMENTED |
| 5 | Timely processing during active disasters | **IMPLEMENTED AND TESTED** | Automatic queue, priority, heartbeat leases, stalled-task recovery, stale-run protection, retries, no duplicate incidents; reports never wait for AI | Typical processing 2–26 s, dominated by external sources; not guaranteed |

## AI architecture (free, local-only)
* **Gemini was removed. There is no cloud or paid AI code path.** `VISION_PROVIDER` accepts only `ollama` or `none`; any other value becomes `none`.
* **Local only:** media is sent only to a **loopback** Ollama endpoint. A LAN host needs the explicit `VISION_ALLOW_LAN` setting; public hosts are refused (`BLOCKED_NON_LOCAL`, tested).
* **Observations, not verdicts:** the model returns structured observations validated against a schema. Authenticity verdicts and self-contradictory indicators are stripped. The model never decides genuine or fake.
* **Deferred by default:** reports finish without waiting. One image at a time, postponed while RAM is below 600 MB, model unloaded 60 s after use. If the AI fails, is missing or times out, the analysis is recorded as unavailable or failed and nothing else is affected.
* **Model:** `qwen2.5vl:3b` (3.2 GB) is the provisional default.
  * On this laptop: about **108–142 s per image** (CPU-only), with 6/6 and 5/5 schema-valid outputs.
  * Observation quality on synthetic images was weak.
  * `gemma3:4b` is installed but **not benchmarked**.

## Status by category

**IMPLEMENTED AND TESTED:**
* All five requirements (requirement 4 on the demo grid)
* Local deployment scripts
* Security controls: XSS escaping, SSRF guard, upload validation, traversal, test-hook lockout, CORS, local-only AI, no secrets or image data in logs
* Worker reliability
* Deferred local AI with a real model
* AI-disabled and AI-unavailable fallback
* Live Mastodon ingestion
* Showcase build
* Browser UI

**IMPLEMENTED BUT PARTIALLY TESTED:**
* GitHub Actions workflows (commands verified locally, not run on GitHub)
* GeoJSON grid provider (unit-level only)
* Model quality (synthetic images only; benchmark partial)
* Evidence refresh over a real TTL
* OS-theme "Auto" mode

**CONFIGURED BUT UNAVAILABLE:**
* GDELT (HTTP 429 rate limiting)
* YouTube and Bluesky connectors (no credentials; adapters not built)

**PROPOSED BUT NOT IMPLEMENTED:**
* Authentication and roles for any non-local deployment
* PostgreSQL/PostGIS migration
* Hybrid "vision agent" (see `docs/DEPLOYMENT.md`)
* Official IMD/NDMA grid
* Reverse image search

**NOT SUPPORTED (by design):**
* Paid or cloud AI
* X API (paid)
* Scraping
* Exposing the backend or Ollama to public networks
* GitHub Pages running the backend

## Technology actually used
* **Backend:** Python 3.12 · FastAPI/Uvicorn · **SQLite** (WAL) · Pillow · NumPy · OpenCV-headless · httpx · python-dotenv · psutil
* **Local AI:** Ollama
* **Frontend:** React 18 · Vite 5 · Leaflet + OpenStreetMap
* **Testing:** Playwright (driving Edge)

**Not used:** PostgreSQL, PostGIS, Redis, Celery, FAISS, any paid API.

## Known risks / remaining blockers
* **No authentication.** That's safe only because the server binds to 127.0.0.1. Do not expose it publicly as-is.
* **Demo laptop:** about 1 GB of free RAM. The 8 deferred AI analyses take ~16 minutes after a reseed. **Reseed at least 20 minutes before the demo**, or set `VISION_PROVIDER=none` for a lighter demo.
* **Venue internet:** external evidence and map tiles need internet. Offline, evidence shows "unavailable" and processing still works.
* **Publishing:** GitHub publishing and Pages need **your** git install, sign-in, repository creation, visibility choice and license choice.
