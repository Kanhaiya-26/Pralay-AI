"""Bounded one-pass public IMD access probes. No app/database imports."""
import concurrent.futures
import datetime
import hashlib
import html
import json
import pathlib
import re
import time
import xml.etree.ElementTree as ET

import httpx

ROOT = pathlib.Path(__file__).resolve().parent
DOC = "https://api.imd.gov.in/public/api_reference.html"
CATALOGUE = [
 ("City Weather Forecast (7 Days)", "cityforecast", "FORECAST"),
 ("City Weather Forecast with Latitude & Longitude", "cityforecastloc", "FORECAST"),
 ("Subdivision Rainfall Forecast (7 Days)", "subdivision_rainfall_forecast", "FORECAST"),
 ("State District Rainfall Forecast (5 Days)", "state_district_rainfall_forecast", "FORECAST"),
 ("All India Weather Forecast Bulletin", None, "FORECAST"),
 ("Weather at your location (Mausamgram)", None, "FORECAST"),
 ("Current Weather API", "current_wx", "OBSERVATION"),
 ("District-wise Nowcast", "districtnowcast", "WARNING"),
 ("Station-wise Nowcast", "stationnowcast", "WARNING"),
 ("AWS/ARG Data", "aws_data", "OBSERVATION"),
 ("District-wise Warnings", "districtwarning", "WARNING"),
 ("Subdivision-wise Warnings", "subdivisionwarning", "WARNING"),
 ("District-wise Rainfall", "districtrainfall", "OBSERVATION"),
 ("State-wise Rainfall", "staterainfall", "OBSERVATION"),
 ("River Basin QPF", "basinqpf", "FORECAST"),
 ("Port Warning", "portwarning", "WARNING"),
 ("Sea Area Bulletin", "seabulletin", "FORECAST"),
 ("Coastal Bulletin", "coastalbulletin", "FORECAST"),
 ("Fishermen Warning", None, "WARNING"),
 ("Cyclone Track", "cyclone_track", "FORECAST"),
 ("Cyclone Wind Warning", "cyclone_wind", "WARNING"),
 ("Cyclone Cone of Uncertainty", "cyclone_cou", "FORECAST"),
 ("Sun Moon Rise/Set", "sunmoon", "CONTEXT"),
 ("Highway Nowcast Warning", None, "WARNING"),
 ("Highway Warning 5 Days", None, "WARNING"),
 ("Radar Image", None, "OBSERVATION"),
 ("Lightning Data", None, "OBSERVATION"),
 ("Agromet Advisory", None, "FORECAST"),
]
LEGACY = [
 "https://city.imd.gov.in/api/cityweather.php", "https://city.imd.gov.in/api/cityweather_loc.php",
 "https://mausam.imd.gov.in/api/current_wx_api.php", "https://mausam.imd.gov.in/api/nowcast_district_api.php",
 "https://mausam.imd.gov.in/api/districtwise_rainfall_api.php", "https://mausam.imd.gov.in/api/warnings_district_api.php",
 "https://mausam.imd.gov.in/api/nowcastapi.php", "https://mausam.imd.gov.in/api/statewise_rainfall_api.php",
 "https://city.imd.gov.in/api/aws_data_api.php", "https://mausam.imd.gov.in/api/basin_qpf_api.php",
 "https://mausam.imd.gov.in/api/port_wx_api.php",
]
PUBLIC = ["https://api.imd.gov.in/public/index.php", "https://mausam.imd.gov.in/index_en.php?lang=en",
 "https://mausam.imd.gov.in/imd_latest/contents/dist_nowcast_rss.php",
 "https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php",
 "https://mausam.imd.gov.in/responsive/stationWiseNowcastGIS.php",
 "https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php",
 "https://mausam.imd.gov.in/responsive/rainfallinformation.php",
 "https://mausam.imd.gov.in/responsive/SeasonalForecast.php"]


def summarize(body, content_type):
    summary = {"structure": "text", "record_count": None, "field_names": [], "sample_timestamps": {}, "coordinate_fields": []}
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
        summary["structure"] = type(data).__name__
        if isinstance(data, dict):
            summary["top_level_keys"] = list(data)[:40]
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        if isinstance(data, list):
            summary["record_count"] = len(data)
            first = next((r for r in data if isinstance(r, dict)), {})
            summary["field_names"] = list(first)[:100]
            summary["sample_timestamps"] = {k: str(v)[:150] for r in data[:3] if isinstance(r, dict) for k,v in r.items() if re.search(r"date|time|valid|issued|updated", k, re.I)}
            summary["coordinate_fields"] = [k for k in first if re.search(r"lat|lon|coord|geometry", k, re.I)]
            summary["sample_records"] = data[:2]
            return summary
    except (ValueError, TypeError):
        pass
    try:
        root = ET.fromstring(text)
        summary["structure"] = "XML:" + root.tag
        items = list(root.iter("item"))
        summary["record_count"] = len(items)
        if items:
            summary["field_names"] = [e.tag for e in items[0]]
            summary["sample_records"] = [{e.tag:(e.text or "")[:300] for e in r} for r in items[:2]]
            summary["sample_timestamps"] = {e.tag:e.text for r in items[:3] for e in r if re.search(r"date|time|pub", e.tag,re.I)}
        return summary
    except ET.ParseError:
        pass
    summary["structure"] = "HTML" if "<html" in text.lower() or "<!doctype html" in text.lower() else "text"
    summary["sample_text"] = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", text))[:450]
    return summary


def probe(item):
    name,url = item
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out = {"name": name, "url": url, "attempted_at": now, "http_status": None, "content_type": None,
           "status": "SOURCE_UNAVAILABLE", "auth_required": None, "rate_limit": "No rate limit documented in the public reference", "documentation": DOC}
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent":"PRALAY-AI/0.1 public-data-access-check"}) as client:
            with client.stream("GET",url) as r:
                out.update(http_status=r.status_code,content_type=r.headers.get("content-type"),final_url=str(r.url),retry_after=r.headers.get("retry-after"))
                body=b""
                for chunk in r.iter_bytes():
                    body+=chunk
                    if len(body)>2*1024*1024:
                        out["truncated"]=True;body=body[:2*1024*1024];break
                out["bytes"]=len(body)
                out.update(summarize(body,out["content_type"]))
                out["status"] = ("AUTH_REQUIRED" if r.status_code==401 else "ACCESS_RESTRICTED" if r.status_code==403 else
                                 "RATE_LIMITED" if r.status_code==429 else "FETCHED_UNVALIDATED" if r.is_success else "SOURCE_UNAVAILABLE")
                out["auth_required"]=r.status_code in (401,403)
                if r.is_success:
                    folder=ROOT/"imd_payloads";folder.mkdir(exist_ok=True)
                    filename=hashlib.sha256(url.encode()).hexdigest()[:16]+".payload"
                    (folder/filename).write_bytes(body);out["raw_payload_path"]="reports/imd_payloads/"+filename
    except Exception as e:
        out["error"]=type(e).__name__+": "+str(e)[:250]
    print(out["http_status"],out["status"],url,flush=True)
    return out


if __name__ == "__main__":
    catalogue=[{"index":i+1,"name":n,"classification":k,"url":"https://api.imd.gov.in/api/v1/"+p if p else None,
                "status":"DOCUMENTED_CANDIDATE" if p else "NO_PUBLIC_API_URL_PUBLISHED_IN_REFERENCE"} for i,(n,p,k) in enumerate(CATALOGUE)]
    endpoints=[(x["name"],x["url"]) for x in catalogue if x["url"]]
    endpoints += [("AWS station mapping","https://api.imd.gov.in/api/v1/aws_data_mapping"),("City forecast station mapping","https://api.imd.gov.in/api/v1/cityforecast_mapping")]
    endpoints += [("legacy candidate",u) for u in LEGACY]+[("public page or RSS",u) for u in PUBLIC]
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(probe,endpoints):
            results.append(result)
            (ROOT/"imd_probe.json").write_text(json.dumps({"catalogue":catalogue,"probes":results},ensure_ascii=False,indent=2),encoding="utf-8")
    print("Finished",len(results),"public GET probes",flush=True)
