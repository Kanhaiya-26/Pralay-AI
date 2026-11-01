"""Shared public-data acquisition. Observations/context never auto-create corroborated incidents."""
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

from . import db, utils
from .config import DATA_DIR, EXTERNAL_LOOKUPS, USER_AGENT

INDIA_BBOX = [68.0, 6.0, 98.0, 38.0]
_locks = {}
_guard = threading.Lock()
SECRET_NAMES = ("NASA_FIRMS_MAP_KEY", "COPERNICUS_CLIENT_ID", "COPERNICUS_CLIENT_SECRET", "NASA_EARTHDATA_USERNAME", "NASA_EARTHDATA_PASSWORD", "IMD_API_TOKEN")


def redact(value):
    text = str(value)
    for name in SECRET_NAMES:
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "[REDACTED]").replace(urllib.parse.quote(secret, safe=""), "[REDACTED]")
    return text


def request(url, *, method="GET", body=None, headers=None, max_bytes=8_000_000, timeout=15):
    """No httpx URL logger: FIRMS embeds credentials in its path. No redirect credential leakage."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                return {"status": r.status, "content_type": r.headers.get("Content-Type", ""), "body": b"", "error": "Response exceeds bounded download limit"}
            return {"status": r.status, "content_type": r.headers.get("Content-Type", ""), "body": data,
                    "retry_after": r.headers.get("Retry-After"), "error": None}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "content_type": e.headers.get("Content-Type", ""), "body": e.read(4096),
                "retry_after": e.headers.get("Retry-After"), "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"status": None, "content_type": "", "body": b"", "error": type(e).__name__}


def catalogue():
    from .providers import specs
    from .imd_catalogue import IMD_DATASETS
    return specs() + [{"name": "imd", "dataset": x["id"], "label": x["label"], "endpoint": x.get("url"),
                       "kind": x["kind"], "implemented": bool(x.get("url")), "poll_min": 30, "ttl_h": x.get("ttl_h", 24)} for x in IMD_DATASETS]


def registry():
    out = []
    for spec in catalogue():
        h = db.one("SELECT * FROM acquisition_health WHERE provider=? AND dataset=?", (spec["name"], spec["dataset"])) or {}
        missing = [name for name in spec.get("credentials", []) if not os.getenv(name)]
        state = h.get("state") or ("NOT_CONFIGURED" if missing or not spec.get("endpoint") else "SOURCE_UNAVAILABLE")
        if missing:
            state = "AUTH_REQUIRED" if spec.get("auth_required") else "NOT_CONFIGURED"
        if h.get("observation_at") and state in ("HEALTHY", "DEGRADED"):
            age = (utils.utcnow() - utils.parse_time(h["observation_at"])).total_seconds()/3600
            if age > spec.get("ttl_h", 24): state = "STALE_DATA"
        out.append({**{k:v for k,v in spec.items() if k != "credentials"}, **h, "state": state,
                    "missing_configuration": missing, "implementation_status": "IMPLEMENTED" if spec.get("implemented") else "CATALOGUED_ONLY",
                    "authenticated": bool(spec.get("credentials")) and not missing and bool(h.get("successful_at")),
                    "live_fetch_verified": bool(h.get("successful_at")),
                    "data_ingested": bool(db.one("SELECT count(*) n FROM provider_records WHERE provider=? AND dataset=?", (spec["name"],spec["dataset"]))["n"]),
                    "gis_display_verified": False, "rejection_reasons": db.jload(h.get("reasons_json"), {})})
    return out


def point(lon, lat):
    try:
        lon, lat = float(lon), float(lat)
    except (ValueError, TypeError): return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180) or (lat==0 and lon==0): return None
    return {"type":"Point", "coordinates":[lon,lat]}


def geometry_valid(g):
    if not isinstance(g, dict): return False
    if g.get("type")=="Point": return point(*g.get("coordinates", [None,None])[:2]) is not None
    if g.get("type") not in ("Polygon","MultiPolygon","LineString","MultiLineString"): return False
    def valid(coords):
        if not isinstance(coords, list) or not coords: return False
        if isinstance(coords[0], (int,float)): return len(coords)>=2 and point(*coords[:2]) is not None
        return all(valid(c) for c in coords)
    return valid(g.get("coordinates"))


def normalize_record(r, spec):
    r = dict(r)
    if not r.get("source_record_id"): raise ValueError("missing_source_id")
    if r.get("country_code") != "in": raise ValueError("foreign_or_unresolved_country")
    if r.get("geometry") and not geometry_valid(r["geometry"]): raise ValueError("invalid_geometry")
    if (r.get("geometry") or {}).get("type")=="Point":
        lon,lat=r["geometry"]["coordinates"][:2]
        if not (68<=lon<=98 and 6<=lat<=38): raise ValueError("outside_india_extent")
    for key in ("published_at","observed_at","event_at","valid_until"):
        if r.get(key):
            stamp=utils.parse_time(r[key])
            if not stamp: raise ValueError("invalid_"+key)
            if key != "valid_until" and stamp > utils.utcnow()+timedelta(minutes=10): raise ValueError("future_"+key)
            r[key]=utils.iso(stamp)
    if not (r.get("observed_at") or r.get("published_at")): raise ValueError("missing_source_timestamp")
    r.setdefault("kind",spec["kind"]);r.setdefault("geo_precision","unresolved" if not r.get("geometry") else "source_coordinates")
    return r


def ingest(spec, records, fetch_id):
    accepted=0;reasons={};latest=None
    for raw in records:
        try: r=normalize_record(raw,spec)
        except (ValueError,TypeError,IndexError) as e:
            key=str(e);reasons[key]=reasons.get(key,0)+1;continue
        stamp=r.get("observed_at") or r.get("published_at")
        latest=max(latest or "",stamp)
        version=hashlib.sha256(db.jdump(r).encode()).hexdigest()
        keys=["source_record_id","kind","title","source_url","published_at","observed_at","event_at","valid_until","geo_precision","resolution_km","country_code","state_name","district_name","locality","hazard","independence_key"]
        cols="provider,dataset,version_hash,fetch_id,retrieved_at,geometry_json,data_json,"+",".join(keys)
        values=[spec["name"],spec["dataset"],version,fetch_id,db.now_iso(),db.jdump(r.get("geometry")),db.jdump(r.get("data",{}))]+[r.get(k) for k in keys]
        with db.tx() as c:
            c.execute(f"INSERT INTO provider_records({cols}) VALUES({','.join('?' for _ in values)}) ON CONFLICT(provider,dataset,source_record_id,version_hash) DO NOTHING",values)
        accepted+=1
    return accepted,reasons,latest


def run(name, dataset=None, options=None, force=False):
    from . import providers
    matches=[s for s in catalogue() if s["name"]==name and (not dataset or s["dataset"]==dataset)]
    if not matches: raise KeyError("Unknown provider/dataset")
    results=[]
    for spec in matches:
        key=(name,spec["dataset"])
        with _guard: lock=_locks.setdefault(key,threading.Lock())
        if not lock.acquire(False):
            results.append({"dataset":key[1],"state":"DEGRADED","error":"Already running"});continue
        try:
            h=db.one("SELECT * FROM acquisition_health WHERE provider=? AND dataset=?",key) or {}
            retry=utils.parse_time(h.get("next_retry_at"))
            if retry and retry>utils.utcnow():
                results.append({**h,"skipped":"backoff"});continue
            now=db.now_iso();missing=[x for x in spec.get("credentials",[]) if not os.getenv(x)]
            state=None;error=None;payloads=[];records=[];status=None
            if missing: state="AUTH_REQUIRED" if spec.get("auth_required") else "NOT_CONFIGURED";error="Missing configuration: "+", ".join(missing)
            elif not spec.get("endpoint"): state="NOT_CONFIGURED";error="No documented public endpoint configured"
            elif not EXTERNAL_LOOKUPS: state="SOURCE_UNAVAILABLE";error="External lookups disabled"
            else:
                try: records,payloads=providers.fetch(spec,options or {})
                except Exception as e: state="SOURCE_UNAVAILABLE";error=redact(type(e).__name__+": "+str(e))[:400]
            fetch_id=None
            for p in payloads:
                status=p.get("status")
                raw=p.get("body",b"")
                stored=redact(raw.decode("utf-8",errors="replace")) if not p.get("binary") else db.jdump({"binary_sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)})
                fetch_id=db.execute("INSERT INTO acquisition_fetches(provider,dataset,endpoint,retrieved_at,http_status,content_type,raw_payload,error,response_sha256) VALUES(?,?,?,?,?,?,?,?,?)",
                    (name,key[1],redact(p.get("endpoint") or spec["endpoint"]),now,status,p.get("content_type"),stored,redact(p.get("error") or ""),hashlib.sha256(raw).hexdigest()))
                if p.get("error") or status not in range(200,300):
                    state={401:"AUTH_REQUIRED",403:"ACCESS_RESTRICTED",429:"RATE_LIMITED"}.get(status,"SOURCE_UNAVAILABLE");error=p.get("error") or f"HTTP {status}"
            accepted,reasons,latest=ingest(spec,records,fetch_id)
            if not state:
                age=(utils.utcnow()-utils.parse_time(latest)).total_seconds()/3600 if latest else None
                state="STALE_DATA" if age is not None and age>spec.get("ttl_h",24) else ("HEALTHY" if accepted and not reasons else "DEGRADED")
                if not accepted:error="Source answered but no acceptable timestamped India records were returned"
            failures=0 if state in ("HEALTHY","STALE_DATA") else (h.get("failures") or 0)+1
            retry_seconds=min(3600,60*2**min(failures,5)) if failures else int(os.getenv("PRALAY_"+name.upper()+"_POLL_MIN",spec.get("poll_min",30)))*60
            if state=="RATE_LIMITED":
                try:retry_seconds=max(retry_seconds,min(86400,int(payloads[-1].get("retry_after") or 0)))
                except ValueError:pass
            success=now if accepted and status and 200<=status<300 else h.get("successful_at")
            db.execute("INSERT INTO acquisition_health(provider,dataset,state,attempted_at,successful_at,observation_at,http_status,retrieved,accepted,rejected,reasons_json,error,next_retry_at,failures) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,dataset) DO UPDATE SET state=excluded.state,attempted_at=excluded.attempted_at,successful_at=excluded.successful_at,observation_at=COALESCE(excluded.observation_at,observation_at),http_status=excluded.http_status,retrieved=excluded.retrieved,accepted=excluded.accepted,rejected=excluded.rejected,reasons_json=excluded.reasons_json,error=excluded.error,next_retry_at=excluded.next_retry_at,failures=excluded.failures",
                (name,key[1],state,now,success,latest,status,len(records),accepted,sum(reasons.values()),db.jdump(reasons),redact(error or ""),utils.iso(utils.utcnow()+timedelta(seconds=retry_seconds)),failures))
            results.append({"provider":name,"dataset":key[1],"state":state,"http_status":status,"retrieved":len(records),"accepted":accepted,"rejected":sum(reasons.values()),"reasons":reasons,"error":error})
        finally:lock.release()
    from . import pipeline
    pipeline.bump()
    return results


def records(filters=None,limit=1000):
    f=filters or {};params=[];where=["p.id IN (SELECT MAX(id) FROM provider_records GROUP BY provider,dataset,source_record_id)"]
    for key,col in (("provider","provider"),("kind","kind"),("state","state_name"),("district","district_name"),("hazard","hazard")):
        if f.get(key):where.append(f"p.{col}=?");params.append(f[key])
    if f.get("locality"):where.append("lower(COALESCE(p.locality,p.title,'')) LIKE ?");params.append("%"+f["locality"].lower()+"%")
    rows=db.query("SELECT p.* FROM provider_records p WHERE "+" AND ".join(where)+" ORDER BY COALESCE(observed_at,published_at) DESC LIMIT ?",[*params,min(5000,limit)])
    for r in rows:
        r["geometry"]=db.jload(r.pop("geometry_json"));r["data"]=db.jload(r.pop("data_json"),{})
        stamp=utils.parse_time(r["observed_at"] or r["published_at"])
        spec=next((s for s in catalogue() if s["name"]==r["provider"] and s["dataset"]==r["dataset"]),{})
        r["freshness"]="stale" if not stamp or (utils.utcnow()-stamp).total_seconds()>spec.get("ttl_h",24)*3600 else "fresh"
        r["geo_uncertainty"]=r["geo_precision"]
    return rows


def poll_loop(stop):
    while not stop.wait(60):
        for spec in catalogue():
            interval=int(os.getenv("PRALAY_"+spec["name"].upper()+"_POLL_MIN","0") or 0)
            if not interval:continue
            h=db.one("SELECT attempted_at FROM acquisition_health WHERE provider=? AND dataset=?",(spec["name"],spec["dataset"]))
            if h and utils.parse_time(h["attempted_at"])>utils.utcnow()-timedelta(minutes=interval):continue
            try:run(spec["name"],spec["dataset"])
            except Exception:pass  # individual failure does not stop other providers


def _norm_district(name):
    return " ".join(str(name or "").upper().split())


def imd_warning_layer(hours=24):
    """IMD district nowcast RSS warnings (OFFICIAL_WARNING) joined to OFFICIAL IMD district polygons (IMD GeoServer
    district_warnings layer) by exact normalised district name. Unmatched districts are returned WITHOUT geometry:
    no invented points. A warning is never an incident."""
    since = utils.iso(utils.utcnow() - timedelta(hours=hours))
    polys = {}
    for r in db.query("SELECT district_name, title, geometry_json, retrieved_at FROM provider_records WHERE provider='imd_public' AND dataset='district_warning' "
                      "AND id IN (SELECT MAX(id) FROM provider_records WHERE provider='imd_public' AND dataset='district_warning' GROUP BY source_record_id)"):
        polys.setdefault(_norm_district(r["district_name"] or r["title"]), r)
    feats, unmapped, now = [], [], utils.utcnow()
    for r in db.query("SELECT * FROM provider_records WHERE provider='imd_public' AND dataset='rss' AND published_at>=? "
                      "AND id IN (SELECT MAX(id) FROM provider_records WHERE provider='imd_public' AND dataset='rss' GROUP BY source_record_id) "
                      "ORDER BY published_at DESC", (since,)):
        d = db.jload(r["data_json"], {})
        exp = utils.parse_time(r["valid_until"])
        props = {"district": r["title"], "guid": r["source_record_id"], "classification": "OFFICIAL_WARNING",
                 "description": (d.get("original_fields") or {}).get("description"), "issued_at": r["published_at"], "onset": d.get("onset"),
                 "expires": r["valid_until"], "active": bool(exp and exp > now), "retrieved_at": r["retrieved_at"],
                 "age_min": int((now - utils.parse_time(r["published_at"])).total_seconds() // 60) if r["published_at"] else None,
                 "source": "IMD district nowcast RSS", "source_url": r["source_url"],
                 "geometry_source": "IMD GeoServer district_warnings (official district polygon)",
                 "note": "Official warning, not confirmation that an event occurred."}
        poly = polys.get(_norm_district(r["title"]))
        if poly:
            feats.append({"type": "Feature", "geometry": db.jload(poly["geometry_json"]), "properties": props})
        else:
            unmapped.append({**props, "reason": "district name not found in official IMD district polygons; not placed on map"})
    return {"type": "FeatureCollection", "features": feats, "unmapped": unmapped, "window_hours": hours,
            "active": sum(f["properties"]["active"] for f in feats) + sum(u["active"] for u in unmapped)}
