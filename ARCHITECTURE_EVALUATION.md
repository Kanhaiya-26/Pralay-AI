# PRALAY AI: Architecture Evaluation

Snapshot: 2026-11-01. Facts are from the code and the live database (`backend/data/pralay.db`), read-only.

## 1. System architecture

| Layer | Stack | Notes |
|---|---|---|
| Frontend | React + Vite, Leaflet | Static build served by the backend. No icon or Markdown libraries. |
| Backend | Python 3.12, FastAPI, Uvicorn | Bound to `127.0.0.1:8000` only. |
| Database | SQLite in WAL mode (`backend/data/pralay.db`) | 26 tables; additive migrations in `db.init_db()`. |
| Local AI | Ollama at `127.0.0.1:11434`, `qwen2.5vl:3b` | CPU only. Images never leave the machine; the endpoint must be loopback. |
| Optional cloud AI | OpenRouter, `:free` models only | Only rewords chat answers. Never treated as evidence. |
| External data | Google News RSS, GDELT, NDMA SACHET, Open-Meteo, IMD GeoServer and RSS, NASA FIRMS (key works, not wired into the map) | All free or public. Every fetch is cached and health-tracked. |

**Communication**
- REST/JSON only; there are no WebSockets.
- The UI polls `GET /api/summary`. Its `version` counter goes up through `pipeline.bump()` whenever data changes, and views refetch when they see a new value.

**Background threads** (in `main.py` startup):
- 2 report workers, using a database-backed queue with leases and heartbeats.
- `vision_loop`, which runs the deferred image analysis.
- The connector poll loop.
- The acquisition poll loop, which only polls providers that are explicitly enabled.

## 2. The five core engines

### Vision engine
| Part | Location | What it does |
|---|---|---|
| Ollama client | `backend/app/vision.py`: `OllamaProvider.analyse` | Sends the stored image re-encoded as JPEG (longest side 1024 px, raw base64), constrains the reply to a JSON schema, and strips any authenticity verdicts the model offers (`sanitize`). |
| Deferred processing | `backend/app/pipeline.py`: `process_report` → `vision_loop` → `apply_visual` | The report is queued as `pending`. The worker finishes the rest of the processing first (3–20 s). Analysis takes about 2–3 min, then the report is reassessed. |
| Mismatch detection | `vision.category_match` + `fusion.visual_state` | Produces `MATCH`, `RELATED_ONLY` (only an associated hazard is seen), `MISMATCH` or `NOT_ANALYSED`. A mismatch sets `needs_review`, keeps the report UNVERIFIED and excludes it from incident corroboration (`fusion.independent_reports`). |
| Quick image check | `backend/app/image_analysis.py`: `POST /api/analyze-image` | A separate tool, not used by the pipeline. Combines a semantic prompt, a pHash match and EXIF/ELA, and returns one of 4 verdicts. It never says "authentic". |

### Fusion and news engine
| Part | Location | What it does |
|---|---|---|
| Incident evidence | `external.py`, validated by `evidence_policy.py` (`evaluate`, `supporting_items`) | An article counts only if it passes all of: actual occurrence, hazard, place (25 km), named event, time. Each publisher counts once. |
| Incident scoring | `fusion.assess_incident` | Rules R1–R5 decide CORROBORATED. +15 per independent source; +30 if the outlet is in `TRUSTED_OUTLETS` (exact publisher match). |
| Report news | `news_ingestion.py` (`fetch_articles`, `check_evidence`, `refresh`) | Same-day Google News, validated on hazard + most specific place word + within 24 h + actual occurrence. Stored as `observations(kind='news')`. |
| Report scoring | `fusion.assess_report` | Additive heuristic starting at 50: +15 per validated article, +25 more for a trusted outlet, at most 3 articles. **Withheld when the image does not match the claim.** |

### Trust and safety engine
| Part | Location | What it does |
|---|---|---|
| Reporter credibility | `fusion.reporter_credibility` | 50 base, +5 per corroborated report, −10 per contradicted or mismatched report, clipped 0–100. **Recomputed** from history each time, never incremented. |
| Credibility effect on reports | same | Uses the reporter's *other* reports: above 80 adds +10; below 30 subtracts 10 and flags manual review. |
| Credibility storage | `users` table | Keyed by sha256 of the normalised `reporter_ref`. Anonymous reports are not scored. |
| 3-strike IP ban | `fusion.strike_ip`, `fusion.ip_ban_active`, `main.submit_report` | The submitter's IP (`request.client.host`) goes in `reports.ip_address`. 3 claim mismatches put the IP in `banned_ips`, and further submissions get **403**. |
| Ban settings | env vars, `scripts/unban_ip.py` | Expires after `IP_BAN_HOURS` (default 24). `IP_BAN_STRIKES` sets the count; 0 disables. |
| IP privacy | `_report_view` | IPs are never returned by the API. |

### Location provenance (`location_time.investigate_location`)
- **Typed place name:** mandatory. Enforced in `ingest.create_report`, which returns 422 "Location is required to submit a report." It is geocoded and treated as a claim.
- **EXIF GPS:** independent evidence, compared against the claim:

  | EXIF GPS vs. claim | Location confidence |
  |---|---|
  | Within the place's extent + 3 km | **high** |
  | Beyond 2×extent + 20 km | **contradicted**, and the report is CONTRADICTED |
  | In between | **low** |
  | EXIF GPS only, no claim | **medium** |

- **Device GPS** (browser, captured automatically when a file is chosen, with permission):
  - Raises a claim-only location to **medium** if within the agreement distance.
  - Is the fallback when nothing else exists: medium, or low if accuracy is worse than 1 km.
  - It records where the device was at upload, never where the photo was taken.
- **Landmarks:** visible text and landmarks read by the vision model are geocoded as supporting clues.

### GIS mapping
| Part | Location | What it does |
|---|---|---|
| Incident clustering | `clustering.find_or_create_incident` | Groups reports by geographic **and** time proximity (`CLUSTER_RADIUS_KM`, `CLUSTER_TIME_WINDOW_H`), with storm-name and text-token matching. |
| Grid | `grid.py` (`map_report`, `cells`, `cell_detail`) | A **custom 0.25° demo lattice, not an official IMD/NDMA grid**. Only eligible verified reports are mapped. A GeoJSON grid is supported. |
| Map rendering | `frontend/src/components/MapView.jsx` | Separate panes for incidents (status ring), grid cells, report hotspots and IMD district warnings. |
| Hotspots | `frontend/src/hotspots.js` | Clustering done in the browser. |
| IMD warning layer | `acquisition.imd_warning_layer` | Drawn on official IMD district polygons only; districts that don't match are listed, never placed on invented points. |

## 3. Database schema and relationships

**Declared foreign keys (2):**
- `reports.incident_id → incidents.id`
- `media.report_id → reports.id`

**Logical references, enforced in application code (no FK constraint):**

| Table | Primary key | References |
|---|---|---|
| `reports` | `id` | `incident_id`; `ip_address` (anti-spam); `reporter_ref` (→ users) |
| `incidents` | `id` | n/a |
| `media` | `id` | `report_id` (declared FK) |
| `media_findings` | `id` | `report_id`, `media_id` |
| `visual_analyses` | `id` | `report_id`, `media_id` |
| `observations` | `id` | `report_id`; holds location, time and **news** observations |
| `status_history` | `id` | `report_id` (workflow log) |
| `evidence` | `id` | `incident_id` (external sources, append-only) |
| `assessments`, `review_history` | `id` | `(entity_type, entity_id)`, polymorphic: report or incident |
| `grid_mappings`, `grid_config` | `id` | `report_id`, `incident_id`, cell id |
| `users` | `id` = sha256(normalised reporter_ref)[:16] | linked to reports by the normalised `reporter_ref` |
| `banned_ips` | `ip_address` | matched against `reports.ip_address` |
| `provider_records` | `id`; unique `(provider, dataset, source_record_id, version_hash)` | `fetch_id` → `acquisition_fetches` |
| `acquisition_fetches`, `acquisition_health` | `id`; `(provider, dataset)` | raw payloads and source health |
| `retired_records`, `cleanup_batches` | `id` | audit trail of every cleanup (full row copied before delete) |

The remaining tables are caches and logs:
- `http_cache` (`cache_key`) and `geocode_cache` (`query`).
- `query_cache`, `source_status` (`source`), `connector_runs`, `system_events`.
- `archive_media`: the known-media archive used for recycled-media checks.

**Why SQLite:**
- A single local file with zero configuration, which fits a laptop or field deployment.
- WAL mode: readers don't block the writer, so 2 workers, the vision thread and the API can run concurrently.
- Transactions are short; `db.tx()` uses `BEGIN IMMEDIATE`.
- Backups use the online backup API, followed by `PRAGMA integrity_check`.

## 4. End-to-end data flow: the life of a report
1. **Submit** (`frontend/src/views/Submit.jsx` → `POST /api/reports`):
   - The form requires a category and a place name.
   - Device GPS is captured, with permission, when a file is chosen.
   - Banned IPs are refused with 403.
2. **Ingest** (`ingest.create_report`):
   - Files are validated by magic bytes, size limit and decompression-bomb guard.
   - They are stored under server-generated names.
   - The report row is created, along with its priority and IP.
   - It is queued in state `RECEIVED`.
3. **Process** (`pipeline.process_report`, a leased worker), in stage order:
   - **Metadata:** EXIF, MP4 time, SHA-256 and perceptual hashes.
   - **Duplicates:** checked against stored reports and the archive.
   - **Vision:** queued as `pending` (deferred).
   - **Manipulation checks:** forensics, including ELA.
   - **Location and time investigation.**
   - **Incident association.**
   - **External evidence:** Google News, GDELT, SACHET, Open-Meteo.
   - **Report news:** same-day validated articles.
   - **Assessment:** `fusion.assess_report` / `cascade_incident`.
   - **Grid eligibility and mapping.**
   - **State:** `PROCESSED` or `MAPPED`.
4. **Deferred vision** (`vision_loop` → `apply_visual`):
   - About 2–3 min later, the model's observations re-run the manipulation, location and landmark checks.
   - The report and its incident are reassessed. This step sets `visual_relevance` and `needs_review`, and can add a strike against the submitting IP.
5. **Fuse:** report confidence is assembled from the factors listed in `assessment.contributions`; incident status comes from rules R1–R5.
6. **Display:**
   - `bump()` advances the version, and the open dashboard refetches.
   - The badges are CLAIM MISMATCH and AI CHECK PENDING.
   - The drawer shows each finding, location provenance, reporter credibility and the visual verdict.

## 5. Known shortcuts and technical debt
**Scoring and AI**
- **Scores are heuristics, not calibrated probabilities.** Report confidence is an additive score from base 50; the factors are shown, but there's no ground-truth calibration.
- **The vision model is small and slow.** qwen2.5vl:3b runs on CPU, 2–3 min per image, one at a time. It has hallucinated: a helmet photo got a "strong cyclone" indicator. That's why a related hazard is not counted as a match.
- **OpenRouter is unreliable.** Free models often return 403, 429 or an empty answer; the chat then falls back to the template answer. In testing, it once invented a URL (links are now filtered to supplied URLs only), conflated two events and overstated certainty.
- **News validation is regex-based.** "exchange of fire" (gunfire) passes as a fire. Google News RSS is a public feed, not a contracted API. Only the last 24 h counts for reports.

**Trust and safety**
- **Credibility is unauthenticated.** `reporter_ref` is free text, so anyone can claim any name.
- **One user row was inserted by hand for the demo:** "Demo Reporter A", score 15 with 0 reports. It is not earned.
- **IP banning is blunt.**
  - **Shared IPs:** every local user is `127.0.0.1`, and people behind a shared network (NAT) share one IP.
  - **Evasion:** changing IP gets around it.
  - **Wrong signal:** a claim mismatch means the photo doesn't show the claim, not proven fakery.
  - `request.client.host` is used directly; forwarded-for headers are not trusted, so they can't spoof it.
- **No authentication or authorisation on any API.** The safeguard is binding to `127.0.0.1`; it is not designed to face the internet.

**Data**
- **Only 2 foreign keys are declared.** Other references depend on application code; cleanup tooling (`cleanup_synthetic.py`) handles dependants explicitly.
- **Raw HTML/XML is stored on purpose** in `acquisition_fetches.raw_payload` and `http_cache.body` (provenance and re-parsing). Display fields go through `utils.clean_html`.
- **IMD:** all 20 authenticated IMD API products return 401 without credentials, and 8 have no published URL. Only the public IMD GeoServer and RSS feeds are used.
- **NASA:** the FIRMS key is valid but its data isn't shown on the map. The Earthdata token is unused.
- **The grid is a demo lattice**, not an official grid.

**Engineering**
- **No git repository**, so there is no version history.
- **A known UI bug:** a Leaflet "save" console error appears on some map teardowns.
- **Mocks exist only in tests:** `scripts/fake_ollama.py`, a protocol stub. The live app uses the real Ollama.
- **Test suites** (isolated servers, synthetic fixtures), as last run:

  | Suite | Result |
  |---|---|
  | Full | 154/154 |
  | Security | 24/24 |
  | Media mismatch | 11/11 |
  | IP ban | 8/8 |
  | Core end-to-end | 39/39 |
  | IMD RSS unit | 12/12 |
  | Confidence | 11/12 (Leaflet console error) |

  A full rerun after the mandatory-location change was still running when this file was written.
