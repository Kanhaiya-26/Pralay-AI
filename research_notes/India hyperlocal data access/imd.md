# IMD public data access

## Which IMD APIs are documented and accessible?

### Takeaway
The requested 28 categories are present in the official API index, but the reference body publishes only 20 dataset URLs plus two station-mapping URLs. Every one of those 22 URLs, plus all 11 supplied legacy API candidates, returned HTTP 401 during the normal unauthenticated public GET tests. This is an authentication requirement, not evidence that the datasets do not exist.

### Cited Findings
- The official index lists the requested weather, nowcast, warning, rainfall, marine, cyclone, astronomy, highway, radar, lightning and agromet categories. Its actual reference body has 20 numbered dataset sections, with `cityforecast_mapping` and `aws_data_mapping` as additional mapping endpoints. The index also links a seasonal forecast webpage beyond the requested 28. — [Official API reference](https://api.imd.gov.in/public/api_reference.html)
- Public account registration/login links are visible in the API portal. No account, credentials, keys, or tokens were created or guessed. — [Official API portal](https://api.imd.gov.in/public/index.php)
- One-pass network results are in `reports/imd_probe.json`: 41 GET probes comprise 20 dataset APIs, two mappings, 11 legacy candidates and eight public pages/RSS. All 33 data/mapping/legacy API candidates returned 401; all eight public page/RSS requests returned 200. — [API reference](https://api.imd.gov.in/public/api_reference.html), [Mausam homepage](https://mausam.imd.gov.in/index_en.php?lang=en)
- The 28-category machine-readable catalogue explicitly gives `url: null` and `NO_PUBLIC_API_URL_PUBLISHED_IN_REFERENCE` for all-India bulletin, Mausamgram, fishermen warning, both highway products, radar, lightning and agromet. These were not assigned invented API paths. — [Official API reference](https://api.imd.gov.in/public/api_reference.html)

### Inferences
- API adapters can be implemented against documented schemas, but API live ingestion cannot be honestly marked authenticated or healthy without authorized access.
- `AUTH_REQUIRED` is more accurate than `HEALTHY` or an undifferentiated server error for the observed 401 responses.

### Gaps
- The public reference did not state a numeric rate limit for these APIs; probe metadata records this as undocumented.
- No authenticated endpoint was tested, and no published API URL was found for eight indexed categories.

## Can official public websites supply genuine station observations and geometry?

### Takeaway
Yes. The official rendered homepage and GIS pages openly fetch GeoServer WFS layers. Six exact public layers returned HTTP 200 GeoJSON with real point/polygon geometry and mixed current/stale data. These are separately published webpage feeds, not a retry or bypass of the 401 API routes.

### Cited Findings
- The homepage JavaScript explicitly fetches `imd:aws_data_layer`, `imd:synop_data_layer` and `imd:metar_data_layer` from `https://reactjs.imd.gov.in/geoserver/imd/ows`, using `service=WFS`, `version=1.0.0`, `request=GetFeature`, `outputFormat=application/json`. — [Official rendered homepage](https://mausam.imd.gov.in/index_en.php?lang=en)
- The official station/district nowcast pages fetch `imd:NowcastWarningStation` and `imd:NowcastWarningDistrict` using WFS 1.1.0 and EPSG:4326. Actual GeoJSON coordinates are longitude, latitude. — [Station nowcast page](https://mausam.imd.gov.in/responsive/stationWiseNowcastGIS.php), [District nowcast page](https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php)
- The official warning page fetches `imd:district_warnings_india` from `https://reactjs.imd.gov.in/geoserver/ows`. It contains actual MultiPolygon district geometry; separate `lat`/`lon` properties can be zero placeholders and must not replace that geometry. — [District warning page](https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php)
- Bounded public requests retrieved AWS 2,000 of 2,071 total features; SYNOP 466/466; METAR 145/145; station nowcasts 1,000/1,219; district nowcasts 50/764; district warnings 50/764. Raw responses and exact query URLs are preserved in `reports/imd_public_wfs_probe.json` and `reports/imd_payloads/`. These counts are fetched, not ingested, and the capped datasets are incomplete. — [Homepage publishing the observation layers](https://mausam.imd.gov.in/index_en.php?lang=en), [Station layer publisher](https://mausam.imd.gov.in/responsive/stationWiseNowcastGIS.php), [District layer publisher](https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php)
- Observation geometry provides genuine station coordinates. AWS properties include stable `id`, optional `call_sign`, station name, date/time, temperature, humidity, wind, rainfall and pressure. SYNOP uses `station_id`, `dat`, integer `utc`, `dbtemp`, `rh`, `windsp`, `winddir`, `24hrlyrain`, `mslp`. METAR uses station code/name, `dat`, `utc` as HH:MM, temperature and the corresponding measurement fields. — [Homepage rendering these fields](https://mausam.imd.gov.in/index_en.php?lang=en)
- Website-linked rainfall maps are ordinary images, and the seasonal page links georeferenced WMS forecasts; these must remain forecast/context rather than incident occurrence. — [Rainfall information](https://mausam.imd.gov.in/responsive/rainfallinformation.php), [Seasonal forecast](https://mausam.imd.gov.in/responsive/SeasonalForecast.php)

### Inferences
- The successful WFS point layers enable genuine station-resolution display without district-centroid fabrication, even while authenticated API mappings remain inaccessible.
- Bounded `maxFeatures` requests are suitable discovery probes; production ingestion must disclose coverage caps or implement bounded pagination rather than imply nationwide completeness.
- The GeoServer robots path returned 404, not an explicit automation prohibition. No authentication material or nonpublic endpoints were used.

### Gaps
- Some observation layers include stations outside the configured India bbox: AWS 1,998/2,000; SYNOP 410/466; METAR 144/145; station nowcast 978/1,000 were inside that bbox. A bbox is a coarse guard, not proof of country; preserve source identity and apply country/geometry validation.
- No cyclone track, cone, wind polygons, radar or lightning data was successfully acquired in this research. Their authenticated API routes returned 401 or lacked a published public API URL.

## Are the observations and warnings actually current?

### Takeaway
The public feeds mix current and very stale observations. At 15:38 UTC on 31 October 2026, the AWS/SYNOP/METAR samples contained hundreds of current measurements, while every fetched RSS nowcast had already expired. Fetch success alone must never determine a healthy/current status.

### Cited Findings
- Parsed 24-hour freshness counts from the actual responses: AWS 982 current and 1,018 stale; SYNOP 417 current and 49 stale; METAR 136 current and nine stale. Station nowcasts: 940 recently issued, six stale and 54 timestamp-unresolved. These are timestamp counts before intersecting geographic/measurement validity. Exact minimum/maximum dates and schema examples are in `reports/imd_validated_summary.json`. — [Observation publisher](https://mausam.imd.gov.in/index_en.php?lang=en), [Station nowcast publisher](https://mausam.imd.gov.in/responsive/stationWiseNowcastGIS.php)
- AWS exposes a `dat` date such as `2026-10-31Z`, but `time` is serialized with the placeholder date `1970-01-01T15:15:00Z`; the observation timestamp must combine the date from `dat` with the time of day. Stale rows include March 2026. SYNOP/METAR contain rows as old as 2024. — [Official homepage's current-date filtering](https://mausam.imd.gov.in/index_en.php?lang=en)
- Nowcast station/district pages explicitly label `Date` plus `toi` and `vupto` as issue/valid-until hours. The raw WFS `updated_at` values appear to serialize local clock time with a `Z` suffix; using those blindly can create false future observations. Prefer the explicitly displayed issue/validity fields with a documented IST assumption. — [Station nowcast page](https://mausam.imd.gov.in/responsive/stationWiseNowcastGIS.php), [District nowcast page](https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php)
- Public district nowcast RSS returned 100 items with original `guid`, district title, `sent`, `Onset`, `Expires`, `pubDate`, description and `category=NOWCAST`. All 100 expiry timestamps parsed, and zero were still active at the check time. `sent`/`Onset`/`Expires` use offsets such as `+5:30`, which should be normalized to `+05:30`; `pubDate` omits a zone. — [Official IMD RSS](https://mausam.imd.gov.in/imd_latest/contents/dist_nowcast_rss.php)
- Official webpage category mapping says `cat1` is “No Warning.” Other categories describe light/moderate/severe rain, snow, thunderstorms, dust and lightning probabilities. A station with only `cat1` is not an active warning. — [District nowcast page](https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php)

### Inferences
- Current observations can be accepted with their true station coordinates; stale rows should remain explicitly stale or be rejected from current layers, with the raw payload preserved for audit.
- Reject individual nonsensical measurements, e.g. AWS example `mslp=1433.8`, rather than relabeling every field or substituting values. Preserve the supplied units and quality flags.
- Rainfall observation, nowcast warning, forecast polygon and actual disaster occurrence require distinct classifications. These feeds do not independently prove flooding or a confirmed disaster.

### Gaps
- Provider-side timezone inconsistencies require explicit adapter notes and tests.
- Complete district forecast validity handling and active warning counts require interpretation of the day-specific issue date/forecast horizon; recent publication is not a substitute for valid event time.
