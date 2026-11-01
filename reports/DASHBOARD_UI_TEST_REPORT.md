# Dashboard UI verification — 2026-10-31

## Actual results

| Check | Result |
|---|---|
| Production frontend build, installed Vite 5.4.21 | PASS, 51 modules |
| `node frontend/tests/hotspots.test.mjs` | 8/8 PASS, exit 0 |
| `backend/.venv/Scripts/python.exe -B frontend/tests/dashboard_ui.py` | 16/16 PASS, exit 0 |
| Existing `backend/scripts/test_evidence_correctness.py` | 39/39 PASS, 11.372 s, exit 0 |
| Existing local `/api/search`, GET only | Mohali: 1 incident/5 reports; flood: 3/6; numeric 1: 2/2; numeric 4: 1/1 |

The endpoint can return incidental substring matches alongside a numeric ID.
The UI filters explicit `incident #N` / `report #N` searches by exact ID and type.

## Browser coverage

Headless Microsoft Edge tests verify sidebar removal and retained branding/navigation;
location, type, numeric and typed-ID searches; incident/report drawer opening;
loading, empty, error and clear states; out-of-order successes/errors and clearing
an in-flight search; hotspot toggling and keyboard/pointer interaction; associated
report/incident inspection; duplicate exclusion; individual marker clicks;
grid rendering on/off independently of hotspots; mobile layout at 390 px;
desktop layout at 1700 px; sparse/missing coordinates; and report-fetch failure.
No browser runtime exceptions or API writes occurred in the successful run.

Browser tests serve the built frontend on an ephemeral localhost port. API
responses use the existing synthetic showcase snapshots, with isolated test
variants for failure/missing-coordinate cases. Map tiles are intercepted with
placeholder images, so the tests do not verify live tile availability. Screenshots
were visually inspected: `dashboard_ui/desktop.png`, `dashboard_ui/mobile.png`.

Testing caught an incident canvas intercepting hotspot clicks. Incident markers
now use SVG paths above the density layer, preserving marker interactions while
allowing hotspot clicks outside markers. The successful tests cover both.

## Changed files

* `frontend/src/App.jsx`: remove the sidebar footer without a replacement spacer.
* `frontend/src/components/GlobalSearch.jsx`: functional search and stale-response guards.
* `frontend/src/views/Dashboard.jsx`: search integration, density controls and inspection.
* `frontend/src/hotspots.js`: fixed 5 km neighborhoods anchored to stored report coordinates.
* `frontend/src/components/MapView.jsx`: clickable density layer, SVG incident markers,
  coordinate validation, fixed-size status markers and stale grid-request protection.
* `frontend/src/styles.css`: responsive search/map/sidebar styling.
* `frontend/tests/dashboard_ui.py`, `frontend/tests/hotspots.test.mjs`: focused tests.
* `frontend/dist/`: regenerated production UI bundle.
* This report and the desktop/mobile screenshots.

## Data semantics and scope

Density uses up to 500 latest reports from the existing endpoint, filtered to the
displayed incidents and report-time window. Duplicate report IDs and reports
marked `is_duplicate_of` do not add density. Missing/invalid coordinates and
contradicted/unresolved locations are excluded. A hotspot needs at least two
eligible nearby reports. Incident IDs remain unique inside each hotspot.
Density circles indicate report concentration, not severity, confirmed occurrence
or proof of independent sources. Existing incident statuses and freshness remain
visible through markers, tooltips and detail inspection.

No backend source, evidence logic, dependencies or configurations changed.
The application database was not reset or edited by this work. No application
server restart or deployment was performed. Build and Python/browser execution
required sandbox escalation; no packages were installed.
