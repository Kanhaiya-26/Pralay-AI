# PRALAY AI — Final Test Report (release)

Date: 2026-10-31.

* **Machine:** i5-1135G7, 7.8 GB RAM, CPU-only, Windows 11.
* **Software:** Python 3.12.10, Node 24, Ollama 0.40.13 with `qwen2.5vl:3b`.
* **Isolation:** every suite ran on its own isolated server and temporary database (port 8001/8002/8090), except the release checks, which ran against the live local deployment on :8000 using synthetic uploads. The demo database was then reset and reseeded with the 8 SYNTHETIC DEMO reports.

## Summary

| Status | Checks |
|---|---|
| **PASS** | **283** |
| FAIL | 0 |
| BLOCKED | 0 (in this final run) |
| NOT TESTED | listed below (not counted) |

| Suite | Kind | Result |
|---|---|---|
| `e2e_test.py` — original flows A–F | automated, isolated | **39/39 PASS** |
| `e2e_extended.py` — media/location/sources/concurrency/retry/connector/grid | automated, isolated | **53/53 PASS** |
| `e2e_restart.py` — persistence after restart, AI-unreachable path, privacy | automated, isolated | **11/11 PASS** |
| `e2e_worker.py` — lease, heartbeat, stall recovery, stale-run protection, retry limit, concurrency, 429 cooldown, scheduled polling | automated, isolated | **20/20 PASS** |
| `e2e_vision.py` — local-AI adapter | **protocol STUB (not a model)** | **31/31 PASS** |
| `e2e_security.py` — traversal, uploads, SSRF, test hooks, CORS, local-only AI, secrets, stored XSS in a real browser | automated, production-like server | **24/24 PASS** |
| `ui_test.py` — real browser (Playwright + Edge) | browser | **31/31 PASS**, 0 console errors |
| `showcase_test.py` — GitHub Pages build served from `/PRALAY-AI/` | browser | **14/14 PASS** |
| `e2e_mastodon_real.py` — **live** public post, ingestion → dashboard | real-world | **16/16 PASS** |
| `e2e_vision_real.py` — **real local model** (`qwen2.5vl:3b` via Ollama) | real model | **5/5 PASS** |
| `smoke_test.py` — offline CI smoke | automated, isolated | **8/8 PASS** |
| `release_check.py` — live deployment on :8000 | release, live server | **19/19 PASS** |
| Ollama stopped on the live server | release, live server | **4/4 PASS** |
| `start.ps1` / `stop.ps1` behaviour | manual, executed | **PASS** (below) |

The original 103-check baseline is preserved: 39 + 53 + 11. Six Gemini-specific checks were replaced one-for-one by equivalent local-provider checks when Gemini was removed. No check was removed or weakened.

## Stub vs real model
* **Stub:** `e2e_vision.py` (31) talks to `scripts/fake_ollama.py`, a local HTTP stub of the Ollama API. It proves PRALAY's adapter behaviour: unavailable, missing model, timeout, malformed output, HTTP 500, missing image, verdict stripping, non-local endpoint refused, deferred mode, video frames. It **does not** prove model quality.
* **Real:** `e2e_vision_real.py` (5): the real `qwen2.5vl:3b` produced a schema-valid analysis in **125 s**; no authenticity verdict was stored, and the pipeline completed.
  * The deferred-mode real test (clean clone): 8 reports finished in 31 s without waiting for AI; the first real analysis completed after 108 s and was applied.
  * **Model quality on synthetic images was weak:** it labelled a synthetic flood scene as `building_damage`. This is recorded as information and not asserted.

## Real-world integration (live)
* **Mastodon:** 344 public posts fetched. Rejections: 259 older than 7 days, 82 with no Indian place, 1 sensitive, plus non-disaster posts. **1 real post ingested end to end:** @Mathrubhumi_English, 2026-10-27, Tamil Nadu heavy rain. Result UNVERIFIED (single source, state-level location). This ran in an isolated DB only.
* **NDMA SACHET, Google News RSS, Open-Meteo, OSM Nominatim:** live OK.
* **GDELT:** rate-limited (HTTP 429), so it was paused automatically. This is a real external limitation.

## Release verification on the live deployment (http://127.0.0.1:8000)
* **Server:** health OK; SQLite connected; UI served; `VISION_MODE=deferred`; listening on **127.0.0.1 only**.
* **Synthetic upload:** validated → duplicate checks → EXIF GPS → location consistent → incident → `DEMO1-…` grid cell → dashboard (verified in a real browser).
* **Bad inputs:** an exact duplicate is detected and not mapped; an invalid file is rejected with a per-file message.
* **Ollama stopped:** status UNREACHABLE; the report was still processed and mapped; AI recorded as "unavailable"; server healthy. Ollama was restarted afterwards.
* **Scripts:**
  * `start.ps1`: start, duplicate guard, port-conflict message, piped invocation, and a **clean-clone first run** from scratch (venv + pip + npm ci + build).
  * `stop.ps1`: stops the whole process tree; idempotent.

## Defects found and fixed during validation
1. Stored XSS in map tooltips.
2. SSRF in connector fetches.
3. Grid cells covering incident markers (hover/click lost).
4. The lease expired during long healthy stages (heartbeat added).
5. A stale run could write intermediate data (per-stage ownership check).
6. `start.ps1`: UTF-8 parse failure in PowerShell 5.1; a hang when output was piped; and a fresh-venv ImportError on stderr treated as a fatal error.
7. `stop.ps1` killed only the launcher, not the server child process.
8. UTF-8 mojibake in user-visible strings.
9. A self-contradictory model indicator is now dropped by the sanitizer.
10. The UI ignored URL hash changes after first load.
11. A favicon 404.
12. Personal path in the docs.

## NOT TESTED (honest list)
* **GitHub Actions workflows** (`ci.yml`, `pages.yml`). They can only run on GitHub; the same commands ran locally (smoke 8/8, showcase build + 14/14).
* **`gemma3:4b`.** Downloaded but never benchmarked; the benchmark is partial (see `MODEL_BENCHMARK.md`).
* **Killing the OS process mid-stage.** A hung worker and a full restart *are* tested.
* **Evidence refresh over a real 20-minute TTL**; Light/Dark **Auto** on an OS theme change.
* **Real (non-synthetic) photographs.** None were used anywhere.
