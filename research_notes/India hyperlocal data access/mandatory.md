# Mandatory satellite and humanitarian providers

## Which provider contracts and authentication methods are supported?

### Takeaway
All requested credential variables are absent in the local backend environment.
FIRMS API, Sentinel Hub and ReliefWeb cannot truthfully be labelled authenticated
or live-data verified. A public IMERG imagery route does work without credentials.

### Cited Findings
- FIRMS Area API uses `GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAY_RANGE}[/{YYYY-MM-DD}]`. Supported source families include MODIS and VIIRS SNPP, NOAA20 and NOAA21, each NRT/SP. Day range is 1–5; no date means latest days, while a date means that date onward. A free MAP_KEY is required and documented limit is 5,000 transactions per ten minutes. — [FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/)
- Sentinel Hub uses backend OAuth client-credentials with token endpoint `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`. Supply grant_type, client_id, client_secret; cache the returned access_token until expires_in minus a margin rather than requesting one per data request. Secrets should never enter browser code. — [Official authentication](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/Authentication.html)
- The documented Catalog endpoint is **`POST https://sh.dataspace.copernicus.eu/catalog/v1/search`**. JSON accepts bbox in lon/lat WGS84, datetime interval, collections such as `sentinel-1-grd`, limit and optional next pagination. Features retain geometry and acquisition metadata. — [Catalog API](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Catalog.html)
- The documented Process endpoint is **`POST https://sh.dataspace.copernicus.eu/process/v1`**, not `/api/v1/process`. Supply input.bounds.bbox plus CRS84, input.data type and timeRange, output dimensions/format, and evalscript. Sentinel-1 examples use VV/VH radar bands and optional orthorectification; a radar rendering is not a derived flood extent. Sentinel-2 L2A examples use B04/B03/B02 for visual RGB. Preserve request processing parameters and imagery acquisition metadata. — [Sentinel-1 examples](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Process/Examples/S1GRD.html), [Sentinel-2 examples](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Process/Examples/S2L2A.html)
- IMERG provides Early, Late and Final run products in several formats. Near-real-time applications should use Early/Late; nominal native sampling is half-hourly. Units differ between rate and accumulated products and must be read explicitly. The official Early GIS directory requires PPS registration; it is not safe to assume an Earthdata username/password is automatically a PPS account. — [NASA IMERG access](https://gpm.nasa.gov/data/imerg), [NASA directory registration notes](https://gpm.nasa.gov/data/directory)
- ReliefWeb requires a **pre-approved appname on every request**, including read/search. Appname registration is a review/email process. Do not invent a default name or send credentials for publishing; the task requires read access only. — [ReliefWeb API documentation](https://apidoc.reliefweb.int/)
- ReliefWeb supports country/date/type filtering; use an AND filter with country.iso3 `ind`, date.created range, and optionally disaster_type.name. Preserve fields.date.original separately from fields.date.created; neither alone establishes occurrence date. Report entries are humanitarian context, not automatic incident confirmation. — [Official filtering](https://apidoc.reliefweb.int/read/filtering/)

### Inferences
- Missing NASA_FIRMS_MAP_KEY and either missing Copernicus OAuth variable should produce NOT_CONFIGURED with HTTP status null: documentation success is not data success.
- Missing approved RELIEFWEB_APPNAME should produce AUTH_REQUIRED/NOT_CONFIGURED; do not repeatedly submit anonymous data requests after the observed rejection.
- Derive internal deduplication hashes only as internal keys. FIRMS rows may not provide a unique detection ID; preserve the entire native row, acquisition time, satellite/instrument and native coordinates rather than pretending a hash is a provider-issued identifier.
- For Sentinel Hub, store returned PNG together with exact requested CRS84 bbox and dimensions as a GIS image footprint. Record evalscript, collection and acquisition interval. Never label it flood extent without a separate documented method.

### Gaps
- No authenticated FIRMS detection request, Sentinel Hub token, Catalog feature retrieval, Process imagery retrieval or ReliefWeb data request could be validated because all required credentials are absent.
- Quantitative IMERG grid parsing/subsetting is not established merely by downloading GIBS imagery. The application must distinguish quantitative data access from visualization.

## What did actual normal HTTP probes return?

### Takeaway
Public CMR metadata and a georeferenced IMERG India image were successfully fetched.
The image is real and nonempty, but this is visualization coverage, not station-level
coverage or quantitative rainfall ingestion. All raw probe metadata is in
`reports/mandatory_probe.json`.

### Cited Findings
- FIRMS Area documentation: HTTP 200 text/html; no detection records fetched and no key-bearing request made. — [Probed URL](https://firms.modaps.eosdis.nasa.gov/api/area/)
- Unauthenticated Sentinel Catalog: HTTP 401 application/json. Unauthenticated documented Process `/process/v1`: HTTP 401 application/json, requesting a valid bearer token. An initial legacy-style `/api/v1/process` candidate also returned 401; adapter implementation must use the documented path. — [Catalog endpoint](https://sh.dataspace.copernicus.eu/catalog/v1/search), [Process endpoint](https://sh.dataspace.copernicus.eu/process/v1)
- Unauthenticated ReliefWeb reports GET with India country filter but no appname: HTTP 400 application/json, `Missing appname parameter`. This is not a verified usable report feed. — [Probed URL](https://api.reliefweb.int/v2/reports?limit=1&filter%5Bfield%5D=country.iso3&filter%5Bvalue%5D=ind)
- IMERG Early GIS directory: HTTP 401. A CMR-advertised OPeNDAP `.dds` resource returned HTTP 302 to `/login/urs`; login was not followed or bypassed. — [GIS directory](https://jsimpsonhttps.pps.eosdis.nasa.gov/imerg/gis/early/), [CMR India metadata query](https://cmr.earthdata.nasa.gov/search/granules.json?short_name=GPM_3IMERGHHE&version=07&bounding_box=68,6,98,38&page_size=1&sort_key=-start_date)
- Public CMR returned one actual granule: `G4395899661-GES_DISC`, title `GPM_3IMERGHHE.07:3B-HHR-E.MS.MRG.3IMERG.20261031-S093000-E095959.0570.V07C.HDF5`, acquisition start 2026-10-31 09:30 UTC, end 09:59:59.999 UTC. It describes a global source granule; intersection with India does not mean this granule has already been geographically subset or its values downloaded. — [CMR query](https://cmr.earthdata.nasa.gov/search/granules.json?short_name=GPM_3IMERGHHE&version=07&bounding_box=68,6,98,38&page_size=1&sort_key=-start_date)
- Two suggested regional public CSV candidates returned 404; neither should become a configured healthy source. No further guessed-path attempts were made. — [VIIRS candidate](https://firms.modaps.eosdis.nasa.gov/data/active_fire/viirs-snpp-csv/South_Asia_24h.csv), [MODIS candidate](https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1-csv/South_Asia_24h.csv)

### Inferences
- Record HTTP 401 as AUTH_REQUIRED, not a generic connectivity failure. Preserve missing-appname HTTP 400 separately from that provider's semantic AUTH_REQUIRED state.
- Neither 200 documentation pages nor 200 CMR metadata establish DATA_INGESTED for precipitation values or GIS_DISPLAY_VERIFIED.
- The current observed IMERG acquisition time is about six hours behind the client's date/time during this run. Keep that latency visible rather than stamping retrieval time as observation time.

### Gaps
- No true new FIRMS fire detections, quantitative IMERG rainfall values, Sentinel imagery or ReliefWeb reports were imported by this research-only task.
- A successful regional fire CSV download was not found. Credentials remain the supported FIRMS API path.

## What public IMERG GIS path can be implemented now?

### Takeaway
NASA GIBS is an official public visualization service. Its WMS GetMap supports
small geographic bounding boxes and returns ready-to-display imagery. Implement
this access method independently from authenticated quantitative Earthdata access.

### Cited Findings
- GIBS exposes public WMTS/WMS/TWMS/XYZ imagery, including time dimensions; best/std/nrt are distinct service selections. — [NASA GIBS access documentation](https://nasa-gibs.github.io/gibs-api-docs/access-basics/)
- Actual EPSG:4326 capabilities response was HTTP 200 XML. It lists `IMERG_Precipitation_Rate_30min`, matrixset `2km`, with latest advertised time 2026-10-31T09:30:00Z. The separate daily layer `IMERG_Precipitation_Rate` advertises through 2026-10-30. It is a daily rate visualization, **not a daily accumulation layer**. — [Actual capabilities](https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml)
- Bounded public GET returned HTTP 200 image/png for WMS1.1.1, `LAYERS=IMERG_Precipitation_Rate_30min`, `SRS=EPSG:4326`, `BBOX=68,6,98,38`, width480,height512, time2026-10-31T09:30:00Z. Saved `reports/imerg_public_india.png`; Pillow validates480×512 pixels,20,753 nontransparent pixels and146 colors. — [Exact tested WMS request](https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS=IMERG_Precipitation_Rate_30min&STYLES=&FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:4326&BBOX=68,6,98,38&WIDTH=480&HEIGHT=512&TIME=2026-10-31T09:30:00Z)
- GIBS visualizations encode science values into display colors; imagery resolution may differ from sensor resolution. Geographic display resolution must not be misrepresented as newly acquired hyperlocal measurement accuracy. — [NASA visualization documentation](https://nasa-gibs.github.io/gibs-api-docs/available-visualizations/)

### Inferences
- Use Leaflet `imageOverlay` bounds `[[6,68],[38,98]]` for that tested WMS1.1.1 EPSG:4326 bbox, or fetch smaller configured bboxes. Do not use WMS1.3.0 axis order without adjusting it.
- Preserve product/layer name, exact acquisition time advertised by capabilities, request bbox, image dimensions, retrieval time, attribution, and native/product resolution separately.
- Do not reverse-engineer RGB as precise rainfall measurements or multiply a rate screenshot to fabricate accumulated rainfall. Until a quantitative access method succeeds, accumulation must be explicitly unavailable.
- The appropriate milestones now are: PUBLIC_METADATA_FETCH_VERIFIED and PUBLIC_IMAGERY_FETCH_VERIFIED. GIS_DISPLAY_VERIFIED requires the parent to actually render and test this layer; research has not done that.

### Gaps
- No authenticated, geographically subset quantitative Early/Late IMERG data request was tested. Earthdata/PPS registration and a numeric data decoder/subset path remain necessary for measurement-level rate/accumulation products.
