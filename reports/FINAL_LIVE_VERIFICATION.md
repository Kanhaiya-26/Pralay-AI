# Controlled live verification — 31 October 2026

## Outcome

Live Microsoft Edge run: **17/18 checks passed**. One semantic assistant failure
remains. Ollama-stopped live Edge run: **16/16 checks passed**. These runs used
`http://127.0.0.1:8000` and its existing SQLite database, without mocked requests,
synthetic API substitutions, uploads, reviews or data resets. The database's
existing eight reports are themselves labelled synthetic demo data.

Browser-tab tooling reported its access host unavailable, including after the
user reconnected it. With explicit user authorization, verification switched to
the installed Playwright tooling and real, headless Microsoft Edge. Browser
screenshots and JSON responses are in `reports/live_demo/`.

## Server/build verification

The original PRALAY process tree was positively identified:
launcher 17476 → venv launcher 17920 → Uvicorn 10572, started at 19:28 IST.
That was older than the evidence fixes saved around 19:59 IST.

A consistent SQLite backup was created before the controlled restart:
`live_demo/pralay_before_20261031T150302Z.sqlite3`. The original logs and a baseline
of existing reports/incidents/evidence/assessments/review history were preserved.
The existing `stop.ps1 -Port 8000` and `start.ps1 -NoBrowser -Port 8000` were used.
No unrelated process was terminated; no packages or configurations changed.

The new launcher is 13308, with Uvicorn process 15984, started at 20:33 IST.
Startup confirmed the existing frontend build and current environment were used.
The server remains bound to localhost.

Served JS and CSS match the generated build byte-for-byte:

* `/assets/index-CWD_n7Ja.js`: SHA-256
  `1d01174025427641de3d37524acc06c99b7db3a966fff06f369351d49199b9f8`
* `/assets/index-kTQMLv01.css`: SHA-256
  `f45b70c4a5bb6a02bfadf1335e4416a1e286cb2eb4a3ed84eb9749847ae30c9e`

The new evidence validation schema is present. Actual chat responses use
`occurrence-v2` query keys and reject Nepal from India incident matching, confirming
the running backend has the correctness changes loaded.
Read-only inspection of the newly appended evidence also found `occurrence-v2`
validation decisions: new items for incidents 5 and 6 were neutral/unavailable,
with no new supporting items. Flood alerts attached to the cyclone query were
excluded with explicit hazard/geographic/named-event mismatch reasons.

## Verified in the actual browser

* Sidebar footer removed; PRALAY branding and seven navigation buttons retained.
* Search against stored records: Mohali, flood, incident #1 and report #4.
* Incident #1 opens its evidence details; report #4 opens its stored duplicate
  finding, `DUPLICATE OF #1`.
* Hotspot input coordinates and duplicate flags agree with direct read-only SQLite
  inspection. One hotspot contains reports **1, 2, 3 and 5**, all under incident
  **1**. Duplicate report **4** is excluded. Its anchor is the actual stored point
  **30.7046, 76.7179**.
* Clicking an exposed part of the hotspot circle opens inspection of those
  members; associated incident/report buttons open the correct stored records.
* Hotspot overlay toggles on/off. Report concentration is labelled separately
  from severity and independent-source corroboration. Report #5 is a contradicted
  submission included in density, not a confirmed independent incident.
* GIS grid visibly renders and toggles independently. Its custom/demo, nonofficial
  IMD/NDMA disclaimer remains intact. Actual OpenStreetMap tiles loaded.
* No JavaScript runtime exceptions were observed in the completed runs.

## Requested assistant sequence

| Question | Live browser result |
|---|---|
| Was there an avalanche in Nepal? | Investigates live news; rejects creation/matching of an Indian incident for Nepal. Returns unverified external headlines with actual/warning/forecast/unknown classifications. |
| Check again, I heard there was one. | Retains avalanche/Nepal context and performs another fresh news request. |
| Cyclone Arnab | Recognizes the name and cyclone category; searches live news without inventing a local location. |
| Was that an actual incident or just a warning? | **FAIL:** routes to `list`, returns seven stored incidents and loses the Arnab subject. It does not answer the occurrence-versus-warning question. |

The first three browser requests actually invoked Google News RSS, returned
HTTP 200, and reported `from_cache=false`. Corresponding outbound HTTP requests
are preserved in `live_demo/server_after_verification.err.log` at approximately
20:38:43, 20:38:44 and 20:38:45 IST. These verify live retrieval, not the factual
truth of the headlines or confirmed disaster occurrence. Some global geocoder
responses were reused from cache. Mastodon ingestion was not triggered.

## Ollama-stopped verification

Before stopping, the exact Ollama app/server executables and parent-child
relationship were checked. No models were loaded and no visual jobs were queued
or running. Only those identified Ollama processes were stopped.

With port 11434 no longer listening, PRALAY reported `UNREACHABLE` via its forced
vision-status check. In Edge, search, hotspots, record details, grid controls and
the Cyclone Arnab assistant search continued to work. Sources & AI displayed
`UNREACHABLE` without app failure. The browser run passed 16/16 checks.

Ollama was restored in a `finally` block; its listener and PRALAY `READY` state
were verified. This checks browsing/search/assistant fallback. New upload and
media-processing behavior while Ollama is stopped was not exercised.

## Data preservation

Immediately after restart, all original fields in the five snapshotted tables
were unchanged. At the final comparison:

* Reports: 8 → 8; all original fields unchanged.
* Incidents: 7 → 7; statuses, scores, identities and coordinates unchanged.
  Background refresh updated only `last_evidence_check` on incidents 5, 6 and 7.
* Evidence: 58 → 87; all 58 original rows unchanged. Background connector refresh
  appended retrieval snapshots under the new policy.
* Assessments: 31 → 31; all original rows unchanged.
* Review history: 0 → 0.
* SQLite integrity check: `ok`.

Normal query/cache/source-health updates occurred during the requested assistant
tests. No database reset, evidence overwrite, incident correction, deployment or
application source edit was performed during verification.

## Remaining limitations / demo issues

1. The actual-versus-warning conversational follow-up fails and needs a focused
   routing/context fix.
2. Legacy contaminated incidents **5 and 6 remain visible as CORROBORATED** in
   the live dashboard/search/map, pending the approved correction plan. Their old
   status/coordinates were intentionally not silently rewritten. Incident 7's
   hotel-versus-factory subject mismatch also remains pending review.
3. Headline classification is conservative text matching; successful retrieval
   does not independently verify occurrence, severity or source independence.
   In particular, Nepal's sole `actual` item was a scientific retrospective-sounding
   headline about a mountainside moving before an avalanche that killed 1,451.
   Its publication date was 20 October 2026; the event date was not established.
   This is a concrete publication-time versus event-time ambiguity: the live
   summary must not be presented as proof of a recent avalanche in Nepal.
4. No new citizen submission, upload processing, real-photo model inference or
   Mastodon ingestion was tested. The complete original test suites were not
   rerun as part of this live check.

This is a controlled live verification with a documented remaining failure,
not an all-clear declaration for every demo scenario.
