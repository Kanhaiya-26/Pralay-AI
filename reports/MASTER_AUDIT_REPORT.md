# PRALAY AI — Master Audit (Phase 1)

Audit date: 2026-10-31 ~21:30 IST. Method: code inspection, import checks, a live probe of every catalogued source into a **throwaway** database (`reports/audit_live_probe.json`), and the existing regression suites on isolated servers. **No changes were made to the live database during this audit.**

## 1. The interrupted implementation — what actually exists

The interrupted agent touched more than the five files listed:
* Code: `acquisition.py`, `acquisition_schema.py`, `providers.py`, `imd_catalogue.py` and `db.py`, plus changes to `external.py`, `clustering.py`, `chatbot.py` and `utils.py` between 19:55 and 20:00.
* Docs: `docs/DATA_PROVIDER_SETUP.md`.
* Research notes: 3 files.
* Probe dumps: `reports/imd_probe.json` (10 MB), `imd_public_wfs_probe.json` (9.5 MB), `imd_payloads/` (~5.7 MB), and the boundary download. These are large and should be excluded from git.

| Component | State | Evidence |
|---|---|---|
| `acquisition.py` (normalize, validate, ingest, health, backoff, poll loop) | **IMPLEMENTED, not wired** | Imports OK. **Not used by `main.py`**: no API routes, `poll_loop` never started, no frontend. |
| `acquisition_schema.py` (5 tables) | Implemented | Created by `db.init_db()`. **Not yet present in the live DB** (server started before the change). |
| `providers.py` (21 source specs + fetch/parse) | Implemented | Live results below. |
| `imd_catalogue.py` (28 IMD categories) | Catalogued | 20 with documented URLs, 8 with `url: null` (no published API path; correctly not invented). |
| Research notes | Good quality, cited | IMD, mandatory providers, public sources. |
| `external.py` geographic validation, `utils.resolve_india` | Implemented | Not separately tested. |

## 2. Live provider results (throwaway DB, 2026-10-31 ~21:20 IST)

| Result | Sources |
|---|---|
| **HEALTHY (data accepted)** | IMD district-nowcast RSS (100/100), NDMA SACHET (100/100), GIBS IMERG rate imagery (1/1), Google News (61/61) |
| **Fetched but rejected by a bug** | IMD AWS 0/2000, METAR 0/145, SYNOP 54/466, station nowcast 20/1219, USGS 0/14. All are `foreign_or_unresolved_country`. **Root cause:** `providers.in_india()` requires `<data dir>/india_boundary.geojson`, which was never installed. The boundary was downloaded to `reports/provider_samples/india_boundary.raw` (DataMeet/ECI, CC BY 2.5 IN) but never placed there, so the gate fails closed. |
| **Fetch bug** | IMD district nowcast and district warnings: "Response exceeds bounded download limit" (8 MB cap vs ~1.8 MB × pages). The research also says district warnings live at `/geoserver/ows`, while the adapter uses `/geoserver/imd/ows`. |
| **AUTH_REQUIRED (HTTP 401)** | All 20 documented IMD API URLs. No credentials exist; nothing was bypassed. |
| **No published API URL** | IMD all-India bulletin, Mausamgram, fishermen warning, highway nowcast, highway 5-day, radar, lightning, agromet (8) |
| **NOT_CONFIGURED / AUTH_REQUIRED (missing credentials)** | FIRMS, Copernicus S1, Copernicus S2, IMERG quantitative, ReliefWeb |
| **ACCESS_RESTRICTED** | Bluesky public search (HTTP 403) |
| **RATE_LIMITED** | GDELT (HTTP 429) |
| **Other** | WorldPop HTTP 301 (redirect not followed). Overpass needs a ≤0.25° bbox, so it is not a national feed. Copernicus EMS: page fetched, no validated India product. |

**Credentials:** none of `NASA_FIRMS_MAP_KEY`, `COPERNICUS_CLIENT_ID/SECRET`, `NASA_EARTHDATA_USERNAME/PASSWORD`, `RELIEFWEB_APPNAME` or `XAI_API_KEY` is set in `backend/.env` or the environment. **No provider can honestly reach AUTHENTICATED or LIVE_FETCH_VERIFIED for those until you add them.**

## 3. Regressions found (existing suites, isolated servers)

| Suite | Result | Cause |
|---|---|---|
| Frontend build | PASS | — |
| `e2e_test.py` (original 39) | **38/39** | Exact-query-cache reuse broken: the new hazard term in the cache key splits equivalent questions ("flooding" vs "flood"). Introduced in the previous session. |
| `e2e_extended` 53, `e2e_restart` 11, `e2e_worker` 20, `e2e_vision` 31 | PASS | — |
| `e2e_security.py` | **FAIL (browser XSS step timeout)** | Test targets the old dashboard; the feed/map moved to the GIS Map tab. Test needs updating, not weakening. |
| `e2e_chat_summary.py` | **FAIL + crash** | Known: the live-search summary override drops the confidence level and changes the no-evidence headline; a later step then dereferences `None`. |
| `ui_test`, `showcase_test`, `release_check` | NOT RUN | Expected to fail for the same dashboard-selector reason. |

## 4. Live database (read-only inspection)

* `integrity_check`: ok; `foreign_key_check`: 0 violations; 20 tables. Acquisition tables are absent until restart.
* reports: 8, **all `is_demo=1`**.
* incidents: 8. **#1–#4 are demo** (`is_demo=1`, origin=reports). **#5 Orissa, #6 Bhutan, #7 Delhi, #8 Jammu are investigation incidents (`is_demo=0`) to preserve.** The brief said three; there are now four.
* archive_media: 1, `is_demo=1`.
* Incident #6 "Flood query — Bhutan" is the known mis-geocoded incident (Bhutan news attached to a J&K hamlet). It is genuine user-query data, so it is preserved but should be flagged for review, not deleted.

## 5. Policy conflicts needing your decision
1. **xAI Grok is a paid, metered API.** Earlier you required PRALAY to stay completely free with no paid AI APIs. **I have not implemented Grok.** Please confirm whether the free-only rule is lifted for an *optional* Grok layer that is off without a key.
2. **Copernicus EMS:** the adapter fetches an HTML activations page. To respect your no-scraping rule it should use a documented API/feed, or stay disabled.

## 6. Answers to the audit questions
1. **Implemented:** acquisition core, 21 source adapters, IMD catalogue, health states and backoff.
2. **Partial:** wiring (API, poll loop, GIS layers), the country boundary file, district polygon pagination, the GeoServer path, the WorldPop redirect, IMERG quantitative access, Grok (not started).
3. **Imports and integrations:** all modules import; the acquisition layer is not integrated into the running app.
4. **DB changes:** additive schema only (5 new tables, applied on next start). No data was modified.
5. **Regressions:** yes, three (section 3).
6. **Build/start:** frontend builds; backend imports and runs (suites start isolated servers successfully).
7. **Valid live data today:** IMD nowcast RSS, NDMA SACHET, Google News, GIBS IMERG imagery. IMD station layers are reachable and will become valid once the boundary file is installed.
