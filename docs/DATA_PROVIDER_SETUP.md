# Data provider setup

PRALAY keeps provider configuration in `backend/.env`. Use placeholder entries in
`backend/.env.example` as a reference, but never commit actual credentials. Restart
the local backend after changing environment variables. No provider is healthy
merely because a variable exists: inspect the last HTTP result, accepted count,
source timestamp, rejection reasons and retry time.

## Environment variables

```dotenv
# Optional credentialed access methods. Leave empty until legitimately registered.
NASA_FIRMS_MAP_KEY=
COPERNICUS_CLIENT_ID=
COPERNICUS_CLIENT_SECRET=
NASA_EARTHDATA_USERNAME=
NASA_EARTHDATA_PASSWORD=
IMERG_SUBSET_URL=
RELIEFWEB_APPNAME=
IMD_API_TOKEN=

# External lookups must be enabled to fetch data.
EXTERNAL_LOOKUPS=true

# Polling is disabled by default. Zero means manual operation.
PRALAY_IMD_PUBLIC_POLL_MIN=0
PRALAY_IMD_POLL_MIN=0
PRALAY_USGS_POLL_MIN=0
PRALAY_SACHET_POLL_MIN=0
PRALAY_FIRMS_POLL_MIN=0
PRALAY_COPERNICUS_POLL_MIN=0
PRALAY_IMERG_POLL_MIN=0
PRALAY_RELIEFWEB_POLL_MIN=0
PRALAY_WORLDPOP_POLL_MIN=0
PRALAY_OVERPASS_POLL_MIN=0
PRALAY_EMS_POLL_MIN=0
PRALAY_BLUESKY_PUBLIC_POLL_MIN=0
PRALAY_GOOGLE_NEWS_POLL_MIN=0
PRALAY_GDELT_POLL_MIN=0
```

Provider polling uses **`PRALAY_{PROVIDER_NAME_IN_UPPERCASE}_POLL_MIN`**, preserving
underscores. `imd_public` and `imd` are different access methods. An interval
enables scheduled fetching; provider failure/backoff can postpone an attempt.
Start with manual tests before enabling polling, and choose intervals consistent
with provider limits and source update frequency. The existing Mastodon media
ingestion path separately uses `MASTODON_POLL_MIN` and its existing configuration.

## IMD: public website feeds and authorized catalogue APIs

The official catalogue is at
[api.imd.gov.in](https://api.imd.gov.in/public/api_reference.html); register or
request access through the [official portal](https://api.imd.gov.in/public/index.php).
Configure `IMD_API_TOKEN` only when IMD supplies an authorized token. The adapter
sends it as a backend bearer token. A supplied token is not proof that the account
has permission for every dataset.

All **33 tested catalogue/mapping/legacy URLs returned 401** during the access
audit. The requested 28-category index includes eight categories without a public
API URL in the reference; those entries remain catalogued rather than invented
endpoints. See `reports/imd_probe.json` for actual per-URL results.

The separate `imd_public` provider uses exact GeoServer WFS requests published by
the [official Mausam website](https://mausam.imd.gov.in/index_en.php?lang=en) and its
nowcast/warning pages. Its datasets are `aws`, `synop`, `metar`, `station_nowcast`,
`district_nowcast`, `district_warning` and `rss`. These public feeds do not require
the catalogue token. They provide source station coordinates or original district
geometry; station names without usable coordinates must not become centroid pins.
Verify source dates and expiry because the feeds include stale records. Website
warnings and forecasts must remain separate from occurrence confirmation.

## NASA FIRMS: VIIRS and MODIS thermal detections

Request a free MAP_KEY from the [official FIRMS API page](https://firms.modaps.eosdis.nasa.gov/api/area/)
and set `NASA_FIRMS_MAP_KEY` locally. The key is part of the provider's request path;
do not copy request URLs into shared screenshots, logs or browser code. The
acquisition layer redacts known credential values and avoids HTTP URL logging.

Run provider `firms`, dataset `active_fire`. Optional fetch settings are `sensor`,
`days`, `date` and `bbox`. A bbox uses **west, south, east, north**. The adapter
supports its allowlisted MODIS/VIIRS sources; the provider API permits 1–5 days
per Area request, so PRALAY caps that window at five days. The documented limit
is 5,000 transactions per ten minutes; large requests can consume more than one
transaction. ([FIRMS documentation](https://firms.modaps.eosdis.nasa.gov/api/area/))

Check acquisition date/time, native coordinates, satellite/instrument, confidence
and FRP in the retained source fields. Records are `SATELLITE_FIRE_DETECTION`.
A thermal anomaly is not automatically a wildfire or a corroborated incident.
Missing key yields `NOT_CONFIGURED`; a successful documentation page is not a
successful detection fetch. Empty results can legitimately mean no matching data,
but must not be presented as live coverage without a validated response.

## Copernicus Sentinel Hub: Catalog and Process imagery

Register in [Copernicus Data Space](https://dataspace.copernicus.eu/), then create a
backend OAuth client through its dashboard and set `COPERNICUS_CLIENT_ID` and
`COPERNICUS_CLIENT_SECRET`. Follow the [official client-credentials instructions](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/Authentication.html).
Keep the client secret and temporary access tokens server-side.

Provider `copernicus` has datasets `sentinel-1-grd` and `sentinel-2-l2a`. The backend
uses the documented token endpoint, caches temporary tokens with an expiry margin,
and sends bearer authorization to Catalog and Process requests. The documented
data URLs are `/catalog/v1/search` and `/process/v1` on
`https://sh.dataspace.copernicus.eu`. A Catalog 401 clears the cached token for the
next authorized attempt; source backoff remains in effect.

Fetch options include `bbox`, `days`, and `imagery`. Catalog search preserves scene
geometry and acquisition time. When imagery is requested after successful scene
discovery, the adapter submits a bounded 512-pixel rendering request with its
processing parameters. Sentinel-1 is a radar-backscatter visualization and
Sentinel-2 an optical composite; neither is a derived flood-extent product.
Clouds, radar viewing geometry, acquisition dates, mosaicking and processing
parameters matter. Inspect the returned footprint and attribution before use.
([Catalog](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Catalog.html),
[radar examples](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Process/Examples/S1GRD.html),
[optical examples](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Process/Examples/S2L2A.html))

Missing OAuth fields yield `NOT_CONFIGURED`. A 401 indicates authentication or
authorization failure; 429 indicates provider limits. Do not regenerate a token
for every request or print provider response tokens while troubleshooting.

## NASA GPM IMERG: public visualization versus numeric subsets

Provider `imerg`, dataset `public_rate_30min`, uses public NASA GIBS capabilities
and a bounded WMS request. **It needs no Earthdata credentials.** Acquisition time
comes from the advertised source time dimension, not retrieval time. This route
can return a geographically bounded rainfall-rate image; it does not return
numeric precipitation measurements. A smaller requested map image does not make
the source's native 0.1-degree product street-level data. The daily public rate
layer must not be relabelled an accumulation layer. ([NASA IMERG](https://gpm.nasa.gov/data/imerg),
[GIBS access](https://nasa-gibs.github.io/gibs-api-docs/access-basics/))

For quantitative data, register through [NASA Earthdata](https://urs.earthdata.nasa.gov/users/new)
and follow the access instructions for the **selected GES DISC/PPS service**.
`NASA_EARTHDATA_USERNAME` and `NASA_EARTHDATA_PASSWORD` alone are not guaranteed to
unlock a separate PPS account. The tested Early GIS directory required
authentication, and the metadata-advertised OPeNDAP route redirected to login.
No verified authenticated quantitative path was established during the access audit.
([NASA directory](https://gpm.nasa.gov/data/directory))

The implemented `quantitative_subset` access method additionally requires
`IMERG_SUBSET_URL`: an explicitly configured HTTPS `.ascii` OPeNDAP hyperslab on
the allowlisted `gpm1.gesdisc.eosdis.nasa.gov` host, including a geographic query.
Do not put an entire HDF archive URL there. This narrowly scoped legacy-compatible
adapter is **not proof that the current Earthdata authentication/session flow
works**. A redirect to login is not bypassed. Configure it only after validating
an authorized small subset against the selected service's own documentation.
The current access method also needs a granule observation time in its fetch
options; a response without a source observation/publication time is rejected.

Keep units, run type, native grid, source time, retrieval time and processing
metadata. Rate and accumulation are separate quantities. Accumulation requires
actual numeric data plus a specified integration interval/method; never derive it
from display colors. Missing required quantitative configuration yields
`AUTH_REQUIRED` while public visualization remains independently available.

## ReliefWeb: approved application identity

Request a pre-approved application name using the registration link in the
[official ReliefWeb documentation](https://apidoc.reliefweb.int/) and configure
`RELIEFWEB_APPNAME` only after approval. Every applicable URL includes that name.
Do not substitute a random string or reuse an unapproved example name. No
publishing API key is needed for this read-only integration.

Run provider `reliefweb`, dataset `reports`. The adapter requests recent
India-specific reports and supports `days`, `hazard` and `location` fetch filters.
Country validation uses the report's country metadata; country centroids are not
rendered as local disaster pins. Publication time is preserved separately from
unknown event time, and source organizations/original fields remain available.
Reports are humanitarian context, not automatic occurrence confirmation.

The unauthenticated audit request returned HTTP 400 with `Missing appname
parameter`. Missing approved configuration yields `AUTH_REQUIRED`; approval and
successful data fetch are still separate milestones. ([Filtering](https://apidoc.reliefweb.int/read/filtering/))

## Test a provider and inspect the result

With dependencies already installed, the acquisition runner can be exercised
directly from the repository root. It uses the configured local data directory;
successful calls intentionally add provenance and normalized provider records.
Do not use this command against a database you intended to keep read-only.

```powershell
# Example: public station observations, no API token needed.
backend\.venv\Scripts\python.exe -B -c "from app import db, acquisition; db.init_db(); print(acquisition.run('imd_public', 'aws'))"
```

The example requires Python's module search path to include `backend`. The safer
working-directory form is:

```powershell
Push-Location backend
try {
  .venv\Scripts\python.exe -B -c "from app import db, acquisition; db.init_db(); print(acquisition.run('imd_public', 'aws'))"
  .venv\Scripts\python.exe -B -c "from app import acquisition; print(acquisition.registry())"
} finally {
  Pop-Location
}
```

Substitute the provider/dataset names described above. Use `imerg` /
`public_rate_30min` for a permitted public imagery test, or `usgs` / `earthquakes`
and `sachet` / `alerts` for observations/official warnings. Test a small bbox
before requesting larger data where supported. Overpass requires an explicit
context bbox no larger than 0.25 degrees per side; population metadata and EMS
catalogue access do not establish a live disaster layer.

The registry reports implementation status separately from authentication,
successful fetch and ingested records. `gis_display_verified` is deliberately not
promoted by configuration or ingestion alone. Inspect health fields
`attempted_at`, `successful_at`, `observation_at`, `http_status`, `retrieved`,
`accepted`, `rejected`, `rejection_reasons`, `error` and `next_retry_at`. A record's
source time and freshness must agree with the intended use.

## Troubleshoot without bypassing access controls

| State | What to inspect |
|---|---|
| `NOT_CONFIGURED` | Missing variables or no documented endpoint; no successful fetch is implied. |
| `AUTH_REQUIRED` | Required service registration/approval, 401, or a login redirect. Do not guess credentials. |
| `ACCESS_RESTRICTED` | HTTP 403 or an explicit source restriction. Stop that access method. |
| `RATE_LIMITED` | HTTP 429, Retry-After and `next_retry_at`; reduce polling. |
| `SOURCE_UNAVAILABLE` | Timeout, DNS/network issue, invalid response or unsupported product retrieval. |
| `STALE_DATA` | The source observation/publication time is too old for that dataset, even if HTTP succeeded. |
| `DEGRADED` | Empty or partially rejected data; inspect rejection reasons and unresolved geometry. |
| `HEALTHY` | A validated fetch accepted records within the source freshness rule; it is not a disaster-confirmation verdict. |

Each provider has independent health/backoff. Manual reruns do not bypass an
active `next_retry_at`; wait rather than repeatedly hammering the source.
Configuration changes take effect after restart. Check health again with a small
authorized request. Never paste credentials, token responses or key-bearing URLs
into a public issue, frontend bundle or shared report.
