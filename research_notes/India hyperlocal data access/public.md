# Additional public geospatial sources

## Which sources actually returned usable public data?

### Takeaway
USGS and SACHET returned genuine current records with original identifiers and timestamps. SACHET supplies current warning content, but the sampled CAP alerts have no geometry. WorldPop returned historical population metadata, not live weather/disaster observations.

### Cited Findings
- The USGS custom FDSN query for 24–31 October 2026 in the India bounding rectangle returned HTTP 200, JSON FeatureCollection with 17 events. Fields: feature `id`, `properties.time` Unix milliseconds, `properties.updated`, `properties.place`, `properties.mag`, `properties.url`, and `geometry.coordinates=[longitude,latitude,depth_km]`. The documentation recommends realtime GeoJSON feeds for automated displays where feasible. — [USGS query](https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=2026-10-24&endtime=2026-11-01&minlatitude=6&maxlatitude=38&minlongitude=68&maxlongitude=98&limit=100&orderby=time), [official API documentation](https://earthquake.usgs.gov/fdsnws/event/1/)
- The USGS weekly feed returned HTTP 200 and 1,811 events in a 1.29 MB payload, with generated timestamp 1793460930000. — [official weekly GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson)
- SACHET India RSS returned HTTP 200, application/xml, 100 items, with current 31 October 2026 publication timestamps. Items provide title/link/pubDate/category/author, and real public CAP links. — [SACHET RSS](https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml), [official portal](https://sachet.ndma.gov.in/)
- Three CAP downloads returned HTTP 200 text/xml. Example identifier `IN-1793455917187017_17`, sent `2026-10-31T19:47:37+05:30`, event Thunderstorm with Lightning, certainty Likely, urgency Expected, expires `2026-10-31T22:35:00+05:30`. — [actual CAP download](https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier=1793455917187017)
- WorldPop country catalogue returned HTTP 200 with 21 metadata records spanning population years 2000–2020. Fields include id/title/popyear/date/iso3/files/desc/license/url_summary; files are WGS84 GeoTIFF population counts around 100 m, units people per pixel. This is historical exposure context. — [WorldPop India metadata](https://www.worldpop.org/rest/data/pop/wpgp?iso3=IND), [official API guide](https://www.worldpop.org/sdi/introapi/)
- Mastodon public `india` tag timeline returned HTTP 200 with five posts. This only verifies fetching; those posts were not accepted as disasters or ingested by this research. — [tested timeline](https://mastodon.social/api/v1/timelines/tag/india?limit=5), [official timeline method](https://docs.joinmastodon.org/methods/timelines/#tag)
- Google News RSS returned HTTP 200 with 77 national-query items, including irrelevant foreign material. A country term in the search request is not country verification. — [tested RSS query](https://news.google.com/rss/search?q=India%20disaster%20when:7d&hl=en-IN&gl=IN&ceid=IN:en)

### Inferences
- Use SACHET as warnings, USGS as earthquake observations, and WorldPop as population context; never fuse these into a single undifferentiated confirmed-disaster stream.
- Current SACHET alert validity is stronger evidence of freshness than retrieval time alone, while historical WorldPop product years must remain visible.

### Gaps
- No station-level weather observations were within this research scope; the IMD researcher owns those.
- No new posts or observations were ingested into the application database by this research.

## How precise and geographically trustworthy are these records?

### Takeaway
Preserve original geometries and reject ambiguous country attribution. A nearest-place label or an India bounding box is insufficient to label an offshore or border point an Indian land incident.

### Cited Findings
- USGS event `us7000tty7` has coordinates `[91.6593,13.3785,10]`, event timestamp `1793263782448`, and place text “218 km NNW of Bamboo Flat, India.” These are original source fields. The location may be offshore; the nearest named Indian settlement does not establish land jurisdiction. — [USGS original event](https://earthquake.usgs.gov/earthquakes/eventpage/us7000tty7), [tested bounded query](https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=2026-10-24&endtime=2026-11-01&minlatitude=6&maxlatitude=38&minlongitude=68&maxlongitude=98&limit=100&orderby=time)
- A simple substring check for `india` on the global feed also matches “western Indian-Antarctic Ridge”, at `[139.4087,-50.2688,10]`; this is plainly outside the India query rectangle. — [USGS weekly feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson)
- All three downloaded SACHET CAP samples omitted polygon and circle. One specifies `areaDesc=Jhargram district of West Bengal`, geocode `{valueName:LGD District Code,value:703}`; other lightning alerts specify mandal names without a geocode. Do not place these at a district centroid. — [Jhargram CAP](https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier=1793455917187017), [Andhra CAP](https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier=1793460089801008)
- The CAP `status=Actual` field occurs on alerts with Expected urgency and Possible certainty. It distinguishes an actual alert from a test; it does not mean disaster occurrence is confirmed. — [actual alert payload](https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier=1793460089801008)
- WorldPop's stats API supports an explicit GeoJSON and product year, allowing geographic subsetting rather than a global download. — [official advanced API guide](https://www.worldpop.org/sdi/advancedapi/)
- Gap-closing boundary fetch: geoBoundaries' public GitHub IND ADM0 directory returned HTTP 200. The raw GitHub URL supplied a Git LFS pointer; the corresponding public LFS media download returned HTTP 200 with a 1,550,512-byte, 394-part MultiPolygon. Its SHA-256 exactly matches the published pointer: `f110789a0983447fc76aebdf901ee784698cedca86ca0157b42ce736f3206871`. Saved as `reports/provider_samples/india_boundary.raw`. — [public repository listing](https://api.github.com/repos/wmgeolab/geoBoundaries/contents/releaseData/gbOpen/IND/ADM0), [actual geometry](https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/IND/ADM0/geoBoundaries-IND-ADM0_simplified.geojson)
- Boundary metadata attributes DataMeet India community/Election Commission of India, source year 2011, build February 2024, and CC BY 2.5 India licensing. Upstream attributes are inconsistent: the ADM0 download's feature says Puducherry/IN-PY and metadata says boundaryType ADM1, despite its geometry covering the country. These issues must be recorded, not hidden. — [source metadata](https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/IND/ADM0/geoBoundaries-IND-ADM0-metaData.json)

### Inferences
- Country-boundary or reverse-geocoding validation is needed before USGS bbox results are accepted as India-specific; unknown/offshore points should remain explicitly regional/unresolved, not silently relocated.
- If a genuine district polygon is resolved from a public administrative boundary source, label that source and district precision. Such a polygon is not an observed hazard footprint.
- Never create geometry from SACHET area names alone without a separately verified mapping.
- Independent point-in-polygon checks against the downloaded geometry accept Delhi, Chennai and Port Blair; reject Kathmandu, Lahore and Dhaka. The offshore USGS point is also outside this land geometry and must not be accepted as an Indian land incident. This is useful operational filtering, not a claim of authoritative legal boundaries or street-level precision.

### Gaps
- No authentic district polygon was downloaded in this research. Existing CAP XML samples cannot support polygon GIS display by themselves.
- The downloaded country geometry is approximate, historical and has inconsistent metadata. Reject ambiguous boundary-edge cases; do not present it as an official IMD/NDMA or current legal boundary.

## Which sources failed or provide context only?

### Takeaway
Bluesky was access-restricted, GDELT rate-limited, and the primary Overpass request timed out. The EMS public page was accessible but did not itself provide normalized event geometries. These results must not be described as healthy data ingestion.

### Cited Findings
- Bluesky public search returned HTTP 403 with HTML; no further attempts or credential guessing were made. — [tested public search](https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=India%20flood&limit=10), [official HTTP API reference](https://docs.bsky.app/docs/api/app-bsky-feed-search-posts)
- GDELT DOC query returned HTTP 429 with a request to limit traffic to one request per five seconds. No retry was made. — [tested DOC query](https://api.gdeltproject.org/api/v2/doc/doc?query=India%20flood&mode=artlist&format=json&maxrecords=5&timespan=1d)
- A bounded Overpass hospital-context query on `overpass-api.de` timed out after 25 seconds. A single documented public-instance fallback at `overpass.kumi.systems` also timed out after 22 seconds. These are geographic context queries, not disaster observations. — [Overpass public API documentation](https://wiki.openstreetmap.org/wiki/Overpass_API)
- Copernicus EMS activations page returned HTTP 200 HTML. The official portal describes public on-demand mapping products and restricts activation requests to authorized users. Reading the page is not imagery or geometry ingestion. — [EMS activations](https://mapping.emergency.copernicus.eu/activations/), [official EMS mapping portal](https://mapping.emergency.copernicus.eu/)

### Inferences
- Health should distinguish ACCESS_RESTRICTED, RATE_LIMITED and SOURCE_UNAVAILABLE from implementation status. HTTP 200 HTML catalogue access must not count as normalized geospatial data success.
- No social API access restriction was bypassed. Public context datasets remain separate from occurrence corroboration.

### Gaps
- Public EMS machine-readable product discovery was not verified, so this research cannot supply an operational EMS download adapter contract.
- Bounded raw payloads and exact response statuses are saved in `reports/public_probe.json` and `reports/provider_samples/`; they are research evidence, not application ingestion milestones.
