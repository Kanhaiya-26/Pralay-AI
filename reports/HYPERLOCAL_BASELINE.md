# Hyperlocal baseline — 31 October 2026

Read-only inspection before application changes: the local server is healthy on
127.0.0.1:8000. SQLite contains 8 reports, 7 incidents, 8 media, 134 evidence,
1 archive item, 31 assessments and 144 status-history entries (counts can increase
through the existing background evidence monitor).

All eight reports explicitly carry BOTH `source_type=demo` and `is_demo=1`.
Four report-origin incidents are marked demo. The one archive record explicitly
describes itself as synthetic. No genuine citizen submissions are present at this
baseline. Three investigation-origin incidents and real external evidence must
be retained; the Bhutan/cyclone contamination remains a separate review issue.

`seed_demo.py` generates synthetic images and submits them to the live API by
default. Test runners also seed isolated databases. `export_showcase.py` and the
showcase snapshot contain synthetic examples. Normal `start.ps1` does not seed.
The cleanup must archive exact removed records first, preserve audit tables and
all external evidence, and refuse ambiguous/genuine report deletion.

Actual source health history: NDMA SACHET, Google News, Open-Meteo and Nominatim
have successful fetches. GDELT has successes but is rate limited. Mastodon has an
implemented adapter, but zero connector runs in the current database. Bluesky,
X and YouTube are placeholders. None of the newly requested acquisition providers
has an implemented station/observation store at baseline.

The assistant mixes local listing/investigation and treats India as a point;
country-wide acquisition, explicit historical/warning intents and the warning
follow-up need work. Grid Intelligence aggregates report cells on a regular
0.25-degree lattice; it has no station, sensor, raster or provider-coverage model.

Detailed baseline: `HYPERLOCAL_BASELINE.json`. Existing constraints and historical
verification: `FINAL_LIVE_VERIFICATION.md`, `EVIDENCE_CORRECTION_PLAN.md`.
