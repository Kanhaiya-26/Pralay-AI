"""Source adapters for public observations, warnings, satellite imagery and context.

No adapter assigns incident confidence. Geographic precision is source precision.
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from PIL import Image
from . import db, utils
from .config import DATA_DIR

TOKEN_URL="https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_token={};_token_lock=threading.Lock()


def specs():
    return [
      {"name":"imd_public","dataset":layer,"label":"IMD public "+label,"endpoint":endpoint,"kind":kind,"implemented":True,"poll_min":30,"ttl_h":ttl}
      for layer,label,endpoint,kind,ttl in [
       ("aws","AWS station observations",wfs_url("aws_data_layer"),"OBSERVATION",6),
       ("synop","SYNOP observations",wfs_url("synop_data_layer"),"OBSERVATION",6),
       ("metar","METAR observations",wfs_url("metar_data_layer"),"OBSERVATION",3),
       ("station_nowcast","station nowcasts",wfs_url("NowcastWarningStation"),"WARNING",4),
       ("district_nowcast","district nowcasts",wfs_url("NowcastWarningDistrict",800),"WARNING",4),
       ("district_warning","district warnings",wfs_url("district_warnings_india",800,""),"FORECAST",120),
       ("rss","district nowcast RSS","https://mausam.imd.gov.in/imd_latest/contents/dist_nowcast_rss.php","WARNING",4)]
    ]+[
      {"name":"usgs","dataset":"earthquakes","label":"USGS earthquake observations","endpoint":"https://earthquake.usgs.gov/fdsnws/event/1/query","kind":"EARTHQUAKE_OBSERVATION","implemented":True,"poll_min":15,"ttl_h":168},
      {"name":"sachet","dataset":"alerts","label":"NDMA SACHET official alerts","endpoint":"https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml","kind":"WARNING","implemented":True,"poll_min":15,"ttl_h":24},
      {"name":"firms","dataset":"active_fire","label":"NASA FIRMS VIIRS / MODIS","endpoint":"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{BBOX}/{DAYS}","kind":"SATELLITE_FIRE_DETECTION","implemented":True,"credentials":["NASA_FIRMS_MAP_KEY"],"poll_min":60,"ttl_h":24},
      *[{"name":"copernicus","dataset":dataset,"label":"Sentinel Hub "+dataset,"endpoint":"https://sh.dataspace.copernicus.eu/catalog/v1/search","kind":"SATELLITE_IMAGERY","implemented":True,"credentials":["COPERNICUS_CLIENT_ID","COPERNICUS_CLIENT_SECRET"],"poll_min":180,"ttl_h":72} for dataset in ("sentinel-1-grd","sentinel-2-l2a")],
      {"name":"imerg","dataset":"public_rate_30min","label":"GPM IMERG Early precipitation-rate imagery","endpoint":"https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi","kind":"SATELLITE_RAINFALL_IMAGERY","implemented":True,"poll_min":60,"ttl_h":12},
      {"name":"imerg","dataset":"quantitative_subset","label":"IMERG Earthdata quantitative subset","endpoint":"https://gpm1.gesdisc.eosdis.nasa.gov/opendap/","kind":"SATELLITE_RAINFALL","implemented":True,"credentials":["NASA_EARTHDATA_USERNAME","NASA_EARTHDATA_PASSWORD","IMERG_SUBSET_URL"],"auth_required":True,"poll_min":60,"ttl_h":12},
      {"name":"reliefweb","dataset":"reports","label":"ReliefWeb humanitarian reports","endpoint":"https://api.reliefweb.int/v2/reports","kind":"HUMANITARIAN_REPORT","implemented":True,"credentials":["RELIEFWEB_APPNAME"],"auth_required":True,"poll_min":60,"ttl_h":168},
      {"name":"worldpop","dataset":"population","label":"WorldPop population exposure metadata","endpoint":"https://www.worldpop.org/rest/data/pop/wpgp?iso3=IND","kind":"POPULATION_CONTEXT","implemented":True,"poll_min":1440,"ttl_h":8760},
      {"name":"overpass","dataset":"context","label":"OpenStreetMap geographic context","endpoint":"https://overpass-api.de/api/interpreter","kind":"GEOGRAPHIC_CONTEXT","implemented":True,"poll_min":1440,"ttl_h":720},
      {"name":"ems","dataset":"activations","label":"Copernicus EMS public mapping","endpoint":"https://mapping.emergency.copernicus.eu/activations/","kind":"MAPPING_CONTEXT","implemented":True,"poll_min":180,"ttl_h":168},
      {"name":"bluesky_public","dataset":"posts","label":"Bluesky public post discovery","endpoint":"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts","kind":"SOCIAL_REPORT","implemented":True,"poll_min":60,"ttl_h":24},
      {"name":"google_news","dataset":"india_discovery","label":"Google News India discovery","endpoint":"https://news.google.com/rss/search","kind":"NEWS_REPORT","implemented":True,"poll_min":15,"ttl_h":48},
      {"name":"gdelt","dataset":"india_discovery","label":"GDELT India news discovery","endpoint":"https://api.gdeltproject.org/api/v2/doc/doc","kind":"NEWS_REPORT","implemented":True,"poll_min":30,"ttl_h":48},
    ]


def wfs_url(layer,count=2000,workspace="imd/"):
    # district_warnings_india is published on the root /geoserver/ows endpoint (official warning page), not /geoserver/imd/ows
    return "https://reactjs.imd.gov.in/geoserver/"+workspace+"ows?"+urllib.parse.urlencode({"service":"WFS","version":"1.0.0","request":"GetFeature","typeName":"imd:"+layer,"outputFormat":"application/json","maxFeatures":count})


def _get(url,payloads,**kwargs):
    from .acquisition import request,redact
    p=request(url,**kwargs);p["endpoint"]=redact(url);payloads.append(p)
    return p


def _json(p):
    if p.get("error") or not p.get("body"):return {}
    try:return json.loads(p["body"])
    except (ValueError,TypeError):p["error"]="Unexpected non-JSON response";return {}


def timestamp(value):
    if not value:return None
    text=re.sub(r"([+-])(\d):(\d\d)$",r"\g<1>0\2:\3",str(value))
    return utils.iso(utils.parse_time(text))


def _source_date(p,kind):
    day=str(p.get("dat") or p.get("Date") or p.get("Date of Observation") or p.get("date") or "")[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}$",day):return None
    if kind in ("station_nowcast","district_nowcast"):
        clock=str(p.get("toi") or "").zfill(4)
        return timestamp(day+"T"+clock[:2]+":"+clock[2:4]+":00+05:30") if clock.isdigit() else None
    if kind=="district_warning":return timestamp(day+"T"+str(p.get("UTC",0)).zfill(2)+":00:00Z")
    clock=p.get("time") or p.get("utc") or p.get("Time of Observation")
    if clock is None:return None
    clock=str(clock)
    if "T" in clock:clock=clock.split("T",1)[1].rstrip("Z")
    if clock.isdigit():clock=clock.zfill(2)+":00:00"
    if len(clock)==5:clock+=":00"
    return timestamp(day+"T"+clock+"Z")


_border_cache={}
def in_india(lon,lat):
    """Country polygon gate, not a bounding-box country guess. Missing boundary fails closed."""
    from .acquisition import point
    if not point(lon,lat):return False
    # Bundled national outline (DataMeet/ECI via geoBoundaries, CC BY 2.5 IN — see geo/ATTRIBUTION.md); data-dir copy may override.
    path=DATA_DIR/"india_boundary.geojson"
    if not path.exists():path=__import__("pathlib").Path(__file__).resolve().parent/"geo"/"india_boundary.geojson"
    if not path.exists():return False
    mtime=path.stat().st_mtime
    if _border_cache.get("mtime")!=mtime:
        fc=json.loads(path.read_text(encoding="utf-8"));polys=[]
        for f in fc.get("features",[]):
            g=f.get("geometry") or {};groups=[g.get("coordinates")] if g.get("type")=="Polygon" else g.get("coordinates",[])
            for rings in groups:
                if not rings:continue
                ring=rings[0];xs=[p[0] for p in ring];ys=[p[1] for p in ring]
                polys.append((min(xs),min(ys),max(xs),max(ys),rings))
        _border_cache.update(mtime=mtime,polys=polys)
    def inside(ring):
        found=False;j=len(ring)-1
        for i in range(len(ring)):
            x,y=ring[i][:2];xj,yj=ring[j][:2]
            if (y>lat)!=(yj>lat) and lon<(xj-x)*(lat-y)/((yj-y) or 1e-12)+x:found=not found
            j=i
        return found
    return any(a<=lon<=c and b<=lat<=d and inside(rings[0]) and not any(inside(hole) for hole in rings[1:]) for a,b,c,d,rings in _border_cache.get("polys",[]))


def imd_features(features,dataset,endpoint):
    from .acquisition import point
    records=[]
    for f in features:
        p=f.get("properties") or f;g=f.get("geometry")
        if not g:
            g=point(p.get("Longitude",p.get("lon")),p.get("Latitude",p.get("lat")))
        is_point=g and g.get("type")=="Point"
        country="in" if g and (not is_point or in_india(*g["coordinates"][:2])) else ("unresolved" if is_point else "in")
        sid=p.get("station_id") or p.get("Station_Code") or p.get("id") or p.get("ID") or p.get("Station") or f.get("id")
        stamp=_source_date(p,dataset)
        title=p.get("station") or p.get("station_name") or p.get("Station") or p.get("District") or p.get("State_District") or str(sid)
        warning=dataset in ("station_nowcast","district_nowcast","district_warning")
        active=any(str(p.get("cat"+str(i),0)) not in ("0","","None") for i in range(2,20)) if warning else False
        expires=None
        if p.get("vupto") and stamp:
            clock=str(p["vupto"]).zfill(4)
            if clock.isdigit():
                expires=timestamp(str(p.get("Date"))[:10]+"T"+clock[:2]+":"+clock[2:]+":00+05:30")
                if expires and expires<stamp:expires=utils.iso(utils.parse_time(expires)+timedelta(days=1))
        if dataset=="district_warning" and stamp:expires=utils.iso(utils.parse_time(stamp)+timedelta(days=5))
        measurements={k:v for k,v in p.items() if k in ("temp","dbtemp","Temperature","rh","Humidity","windspeed","windsp","winddir","rainfall","24hrlyrain","Last 24 hrs Rainfall","mslp","M.S.L.P")}
        records.append({"source_record_id":str(sid or f.get("id") or hashlib.sha256(db.jdump(p).encode()).hexdigest()),"title":str(title),"kind":"FORECAST" if dataset=="district_warning" else ("WARNING" if warning else "OBSERVATION"),
          "source_url":endpoint,"observed_at":None if warning else stamp,"published_at":stamp if warning else None,"valid_until":expires,
          "geometry":g,"geo_precision":"station_point" if is_point else ("district_polygon" if g else "district_unresolved"),"resolution_km":None,
          "country_code":country,"state_name":p.get("State") or p.get("state"),"district_name":p.get("District") or p.get("State_District"),"locality":str(title),
          "hazard":"weather","independence_key":"official:imd","data":{"station_id":sid,"source_feature_id":f.get("id"),"measurements":measurements,"original_properties":p,
             "units":{"temperature":"degrees Celsius","humidity":"percent","pressure":"hPa","rainfall":"mm","wind":"source units; verify product specification"},
             "active_warning":active,"timestamp_method":"source observation date plus UTC time; warning issue time IST","country_method":"country polygon" if is_point else "official IMD administrative warning geometry"}})
    return records


def rss_records(body,provider,endpoint):
    # Prefer 'sent' (carries the IMD offset, e.g. +5:30) over pubDate (no zone; parse_time then assumes IST, never UTC).
    # No item cap: the district feed routinely carries >100 districts. Activity is derived from Expires, not assumed.
    root=ET.fromstring(body);out=[];seen=set();now=datetime.now(timezone.utc)
    for item in root.iter("item"):
        fields={el.tag.rsplit("}",1)[-1]:utils.clean_html((el.text or "").strip()) for el in item};title=fields.get("title") or ""  # raw XML kept in acquisition_fetches
        rid=fields.get("guid") or fields.get("link") or title
        if not title or rid in seen:continue
        seen.add(rid)
        pub=fields.get("sent") or fields.get("Sent") or fields.get("pubDate") or fields.get("Onset")
        expires=timestamp(fields.get("Expires") or fields.get("expires"))
        out.append({"source_record_id":rid,"title":title,"source_url":fields.get("link") or endpoint,
                    "kind":"WARNING","published_at":timestamp(pub),"event_at":None,"valid_until":expires,
                    "country_code":"in","geometry":None,"geo_precision":"district_unresolved","locality":title,"hazard":"weather",
                    "independence_key":"official:"+provider,"data":{"original_fields":fields,"onset":timestamp(fields.get("Onset")),"classification":"OFFICIAL_WARNING",
                    "active_warning":bool(expires and utils.parse_time(expires)>now),"limitation":"Alert is not confirmation of occurrence; no source geometry supplied"}})
    return out


def _asset(data,content_type,bbox):
    from .acquisition import geometry_valid
    if not content_type.startswith("image/"):raise ValueError("Expected georeferenced image response")
    image=Image.open(io.BytesIO(data));image.verify()
    path=DATA_DIR/"provider_assets";path.mkdir(exist_ok=True)
    suffix=".png" if "png" in content_type else ".tif"
    name=hashlib.sha256(data).hexdigest()+suffix
    if not (path/name).exists():(path/name).write_bytes(data)
    return {"image_url":"/api/acquisition/assets/"+name,"bounds":[[bbox[1],bbox[0]],[bbox[3],bbox[2]]],"crs":"EPSG:4326","attribution":"NASA GPM / IMERG"}


def _copernicus_token(payloads):
    from .acquisition import request
    with _token_lock:
        if _token.get("expires",0)>time.monotonic()+30:return _token["value"]
        p=request(TOKEN_URL,method="POST",body=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":os.getenv("COPERNICUS_CLIENT_ID"),"client_secret":os.getenv("COPERNICUS_CLIENT_SECRET")}).encode(),headers={"Content-Type":"application/x-www-form-urlencoded"})
        data=_json(p)
        if not data.get("access_token"):
            payloads.append({**p,"body":b"", "endpoint":TOKEN_URL,"error":"OAuth authentication failed"});return None
        _token.update(value=data["access_token"],expires=time.monotonic()+max(0,int(data.get("expires_in",300))-30))
        return _token["value"]


def fetch(spec,opts):
    from .acquisition import point,INDIA_BBOX,request
    name,dataset=spec["name"],spec["dataset"];payloads=[];out=[]
    bbox=opts.get("bbox",INDIA_BBOX)
    if len(bbox)!=4 or not all(isinstance(x,(int,float)) for x in bbox) or not (68<=bbox[0]<bbox[2]<=98 and 6<=bbox[1]<bbox[3]<=38):raise ValueError("bbox must be within configured India extent, west,south,east,north")
    days=max(1,min(30,int(opts.get("days",7))));end=utils.utcnow();start=end-timedelta(days=days)
    if name in ("imd_public","imd"):
        headers={}
        if name=="imd" and os.getenv("IMD_API_TOKEN"):headers["Authorization"]="Bearer "+os.environ["IMD_API_TOKEN"]
        big=dataset in ("district_nowcast","district_warning")  # district MultiPolygons are ~35 KB each
        p=_get(spec["endpoint"],payloads,headers=headers,**({"max_bytes":60_000_000,"timeout":60} if big else {}))
        if dataset=="rss" and not p.get("error"):out=rss_records(p["body"],"imd",spec["endpoint"])
        else:
            data=_json(p);features=data.get("features",[]) if isinstance(data,dict) else data
            if isinstance(features,list):out=imd_features(features,dataset,spec["endpoint"])
    elif name=="usgs":
        url=spec["endpoint"]+"?"+urllib.parse.urlencode({"format":"geojson","starttime":utils.iso(start),"endtime":utils.iso(end),"minlatitude":bbox[1],"maxlatitude":bbox[3],"minlongitude":bbox[0],"maxlongitude":bbox[2],"limit":200,"orderby":"time"})
        data=_json(_get(url,payloads))
        for f in data.get("features",[]):
            p=f["properties"];coords=f.get("geometry",{}).get("coordinates",[])
            stamp=utils.iso(datetime.fromtimestamp(p["time"]/1000,timezone.utc))
            out.append({"source_record_id":f["id"],"title":p.get("title") or p.get("place"),"source_url":p.get("url"),"observed_at":stamp,"event_at":stamp,
               "geometry":point(*coords[:2]),"country_code":"in" if len(coords)>=2 and in_india(*coords[:2]) else "unresolved","geo_precision":"instrument_epicentre","hazard":"earthquake","independence_key":"usgs",
               "data":{"original_feature":f,"magnitude":p.get("mag"),"depth_km":coords[2] if len(coords)>2 else None,"location_uncertainty":"USGS epicentre; nearest-place name is not country proof"}})
    elif name=="sachet":
        p=_get(spec["endpoint"],payloads)
        if not p.get("error"):
            out=rss_records(p["body"],name,spec["endpoint"])
            # CAP links are explicitly published in the feed. Bound separate downloads.
            for r in out[:5]:
                url=r["source_url"]
                if urllib.parse.urlsplit(url).hostname!="sachet.ndma.gov.in":continue
                cap=_get(url,payloads)
                if cap.get("error"):continue
                try:
                    root=ET.fromstring(cap["body"]);elements={e.tag.rsplit("}",1)[-1]:e.text for e in root.iter()}
                    if elements.get("status") not in (None,"Actual"):r["country_code"]="unresolved";continue
                    r.update(published_at=timestamp(elements.get("sent")),valid_until=timestamp(elements.get("expires")),geo_precision="district_unresolved")
                    r["data"].update(cap=elements,active_warning=True)
                    polygon=elements.get("polygon")
                    if polygon:
                        ring=[[float(c.split(",")[1]),float(c.split(",")[0])] for c in polygon.split()]
                        if ring and ring[0]!=ring[-1]:ring.append(ring[0])
                        r.update(geometry={"type":"Polygon","coordinates":[ring]},geo_precision="official_alert_polygon")
                except (ET.ParseError,ValueError):continue
    elif name=="firms":
        source=opts.get("sensor","VIIRS_NOAA20_NRT")
        if source not in ("MODIS_NRT","MODIS_SP","VIIRS_SNPP_NRT","VIIRS_NOAA20_NRT","VIIRS_NOAA21_NRT","VIIRS_SNPP_SP","VIIRS_NOAA20_SP"):raise ValueError("Unsupported FIRMS sensor")
        url=f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{os.environ['NASA_FIRMS_MAP_KEY']}/{source}/{','.join(map(str,bbox))}/{min(days,5)}"
        if opts.get("date"):url+="/"+str(datetime.strptime(opts["date"],"%Y-%m-%d").date())
        p=_get(url,payloads)
        if not p.get("error"):
            for row in csv.DictReader(io.StringIO(p["body"].decode("utf-8-sig"))):
                g=point(row.get("longitude"),row.get("latitude"));clock=(row.get("acq_time") or "").zfill(4)
                stamp=timestamp(f"{row.get('acq_date')}T{clock[:2]}:{clock[2:]}:00Z")
                sid="|".join(row.get(k,"") for k in ("satellite","instrument","acq_date","acq_time","latitude","longitude"))
                out.append({"source_record_id":sid,"title":"Satellite thermal anomaly","source_url":"https://firms.modaps.eosdis.nasa.gov/","observed_at":stamp,"event_at":None,"geometry":g,"geo_precision":"satellite_pixel","resolution_km":1 if source.startswith("MODIS") else .375,
                    "country_code":"in" if g and in_india(*g["coordinates"]) else "unresolved","hazard":"fire","independence_key":"nasa:firms:"+source,"data":{**row,"sensor":source,"limitation":"Thermal anomaly is not a confirmed wildfire"}})
    elif name=="copernicus":
        token=_copernicus_token(payloads)
        if token:
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"}
            data=_json(_get(spec["endpoint"],payloads,method="POST",headers=headers,body=json.dumps({"bbox":bbox,"datetime":utils.iso(start)+"/"+utils.iso(end),"collections":[dataset],"limit":5}).encode()))
            if payloads[-1].get("status")==401:_token.clear()
            for f in data.get("features",[]):
                props=f.get("properties",{});out.append({"source_record_id":f["id"],"title":dataset+" scene","source_url":"https://browser.dataspace.copernicus.eu/","observed_at":props.get("datetime"),"geometry":f.get("geometry"),"geo_precision":"satellite_scene_footprint","country_code":"in","hazard":"context","data":{"original_feature":f,"limitation":"Scene footprint intersects India bbox; not a flood classification"}})
            if data.get("features") and opts.get("imagery",True):
                bands=["VV","VH","dataMask"] if dataset=="sentinel-1-grd" else ["B04","B03","B02","dataMask"]
                script='//VERSION=3\nfunction setup(){return {input:'+json.dumps(bands)+',output:{bands:4}};}function evaluatePixel(s){return '+('[Math.min(1,s.VV*4),Math.min(1,s.VH*4),0,s.dataMask]' if dataset=="sentinel-1-grd" else '[2.5*s.B04,2.5*s.B03,2.5*s.B02,s.dataMask]')+';}'
                body={"input":{"bounds":{"bbox":bbox,"properties":{"crs":"http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},"data":[{"type":dataset,"dataFilter":{"timeRange":{"from":utils.iso(start),"to":utils.iso(end)}}}]},"output":{"width":512,"height":512,"responses":[{"identifier":"default","format":{"type":"image/png"}}]},"evalscript":script}
                p=_get("https://sh.dataspace.copernicus.eu/process/v1",payloads,method="POST",headers=headers,body=json.dumps(body).encode());p["binary"]=True
                if not p.get("error") and p["content_type"].startswith("image/"):
                    overlay=_asset(p["body"],p["content_type"],bbox);overlay["attribution"]="Copernicus Sentinel / Sentinel Hub"
                    out[0]["data"].update(overlay=overlay,processing=body,limitation="Radar backscatter / optical composite; no flood extent has been inferred")
    elif name=="imerg":
        if dataset=="public_rate_30min":
            cap=_get(spec["endpoint"]+"?SERVICE=WMS&REQUEST=GetCapabilities",payloads)
            if not cap.get("error"):
                root=ET.fromstring(cap["body"]);stamp=None
                for layer in root.iter():
                    if layer.tag.rsplit("}",1)[-1]!="Layer":continue
                    children={e.tag.rsplit("}",1)[-1]:e for e in layer}
                    if children.get("Name") is not None and children["Name"].text=="IMERG_Precipitation_Rate_30min":
                        for d in layer:
                            if d.tag.rsplit("}",1)[-1] in ("Dimension","Extent") and d.get("name")=="time":stamp=d.get("default") or (d.text or "").split("/")[-2]
                        break
                if stamp:
                    q={"SERVICE":"WMS","REQUEST":"GetMap","VERSION":"1.1.1","LAYERS":"IMERG_Precipitation_Rate_30min","FORMAT":"image/png","TRANSPARENT":"true","SRS":"EPSG:4326","BBOX":",".join(map(str,bbox)),"WIDTH":480,"HEIGHT":512,"TIME":stamp}
                    p=_get(spec["endpoint"]+"?"+urllib.parse.urlencode(q),payloads);p["binary"]=True
                    if not p.get("error") and p["content_type"].startswith("image/"):
                        out=[{"source_record_id":"IMERG_Precipitation_Rate_30min@"+stamp,"title":"IMERG Early precipitation-rate imagery","source_url":"https://gpm.nasa.gov/data/imerg","observed_at":stamp,"country_code":"in","geometry":{"type":"Polygon","coordinates":[[[bbox[0],bbox[1]],[bbox[2],bbox[1]],[bbox[2],bbox[3]],[bbox[0],bbox[3]],[bbox[0],bbox[1]]]]},"geo_precision":"satellite_grid_0.1_degree","resolution_km":11.1,"hazard":"rainfall","data":{"overlay":_asset(p["body"],p["content_type"],bbox),"product":"GPM IMERG Early","run":"Early","temporal_resolution":"30 minutes","native_grid_degrees":.1,"units":"mm/hour (visualized rate)","representation":"rendered imagery, not numeric precipitation values","accumulated_rainfall_available":False,"limitation":"Rainfall is not proof of flooding; bbox imagery includes surrounding areas"}}]
        else:
            # Configured OPeNDAP ASCII geographic hyperslab; never download a global archive.
            url=os.environ["IMERG_SUBSET_URL"]
            u=urllib.parse.urlsplit(url)
            if u.scheme!="https" or u.hostname!="gpm1.gesdisc.eosdis.nasa.gov" or not u.path.endswith(".ascii") or not u.query:raise ValueError("Configure an official GES DISC .ascii geographic hyperslab, not a global file")
            auth=base64.b64encode((os.environ["NASA_EARTHDATA_USERNAME"]+":"+os.environ["NASA_EARTHDATA_PASSWORD"]).encode()).decode()
            p=_get(url,payloads,headers={"Authorization":"Basic "+auth},max_bytes=2_000_000)
            if p.get("status") in (301,302,303):p["status"]=401;p["error"]="Earthdata authorized session required; redirect not bypassed"
            if not p.get("error"):
                text=p["body"].decode();rows=re.findall(r"\[[0-9, ]+\],\s*([^\n]+)",text)
                values=[[float(v) for v in row.split(",")] for row in rows if row.strip()]
                if not values:raise ValueError("No numeric geographic precipitation subset returned")
                out=[{"source_record_id":hashlib.sha256(url.encode()).hexdigest(),"title":"IMERG configured numeric subset","source_url":"https://disc.gsfc.nasa.gov/","observed_at":opts.get("observed_at"),"country_code":"in","geo_precision":"satellite_grid_0.1_degree","resolution_km":11.1,"kind":"SATELLITE_RAINFALL","data":{"values":values,"bbox":bbox,"product":"IMERG","units":"mm/hour","temporal_resolution":"30 minutes","accumulation_method":None,"limitation":"Observation timestamp must be supplied from granule metadata; no inferred flood extent"}}]
    elif name=="reliefweb":
        params={"appname":os.environ["RELIEFWEB_APPNAME"],"limit":30,"profile":"full","filter[field]":"country.iso3","filter[value]":"IND","sort[]":"date.created:desc"}
        data=_json(_get(spec["endpoint"]+"?"+urllib.parse.urlencode(params),payloads))
        for item in data.get("data",[]):
            p=item.get("fields",{});countries=p.get("country",[])
            if not any(c.get("iso3")=="IND" and c.get("primary",True) for c in countries):continue
            published=p.get("date",{}).get("created")
            if not published or utils.parse_time(published)<start:continue
            if opts.get("hazard") and opts["hazard"].lower() not in db.jdump(p.get("disaster_type",[])).lower():continue
            if opts.get("location") and opts["location"].lower() not in db.jdump(p).lower():continue
            loc=p.get("primary_country",{}).get("location") or {}
            # Country centroid is intentionally not a report point.
            out.append({"source_record_id":str(item["id"]),"title":p.get("title"),"source_url":p.get("url_alias") or p.get("url"),"published_at":published,"event_at":None,"country_code":"in","geo_precision":"country_or_named_area_unresolved","hazard":opts.get("hazard","humanitarian"),"independence_key":"reliefweb:"+"|".join(str(x.get("id")) for x in p.get("source",[])),"data":p})
    elif name in ("google_news","gdelt","bluesky_public"):
        out=_news(spec,opts,payloads)
    elif name=="worldpop":
        data=_json(_get(spec["endpoint"],payloads))
        for row in data.get("data",[]) if isinstance(data,dict) else []:
            if row.get("iso3")!="IND":continue
            year=int(row.get("popyear") or 0)
            out.append({"source_record_id":str(row.get("id")),"title":row.get("title") or f"India population {year}","source_url":row.get("url_summary") or spec["endpoint"],"published_at":row.get("date") or f"{year}-01-01T00:00:00Z","country_code":"in","geo_precision":"population_raster_metadata","resolution_km":.1,"hazard":"exposure","data":{**row,"limitation":"Historical population exposure context, not current disaster observations; raster not downloaded"}})
    elif name=="overpass":
        # Context requires a specific small area, never an India-wide bulk download.
        if not opts.get("bbox") or bbox[2]-bbox[0]>.25 or bbox[3]-bbox[1]>.25:raise ValueError("Overpass context requires an explicit bbox <=0.25 degrees")
        q=f'[out:json][timeout:12];nwr["amenity"="hospital"]({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]});out center 50;'
        data=_json(_get(spec["endpoint"]+"?"+urllib.parse.urlencode({"data":q}),payloads))
        for e in data.get("elements",[]):
            loc=e.get("center") or e;g=point(loc.get("lon"),loc.get("lat"))
            out.append({"source_record_id":e["type"]+"/"+str(e["id"]),"title":e.get("tags",{}).get("name","OSM hospital"),"source_url":"https://www.openstreetmap.org/"+e["type"]+"/"+str(e["id"]),"published_at":data.get("osm3s",{}).get("timestamp_osm_base"),"geometry":g,"country_code":"in" if g and in_india(*g["coordinates"]) else "unresolved","geo_precision":"osm_feature_location","hazard":"context","data":e})
    elif name=="ems":
        p=_get(spec["endpoint"],payloads)
        # An HTML catalogue alone is not ingested as a validated mapping product.
        if not p.get("error"):p["error"]="Public catalogue fetched; no validated India geospatial product supplied"
    return out,payloads


def _news(spec,opts,payloads):
    from . import evidence_policy as policy
    out=[];name=spec["name"];q=opts.get("query") or "India (flood OR landslide OR cyclone OR earthquake OR wildfire)"
    if name=="google_news":
        params={"q":q+("" if opts.get("historical") else " when:7d"),"hl":"en-IN","gl":"IN","ceid":"IN:en"}
        p=_get(spec["endpoint"]+"?"+urllib.parse.urlencode(params),payloads)
        if p.get("error"):return []
        items=[]
        for it in ET.fromstring(p["body"]).iter("item"):
            source=it.find("source");items.append({"id":it.findtext("guid") or it.findtext("link"),"title":it.findtext("title"),"url":it.findtext("link"),"published":it.findtext("pubDate"),"publisher":source.text if source is not None else None})
    elif name=="gdelt":
        params={"query":q,"mode":"artlist","format":"json","maxrecords":50,"timespan":"7d","sort":"datedesc"}
        data=_json(_get(spec["endpoint"]+"?"+urllib.parse.urlencode(params),payloads));items=[]
        for it in data.get("articles",[]):
            t=it.get("seendate","");t=re.sub(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$",r"\1-\2-\3T\4:\5:\6Z",t)
            items.append({"id":it.get("url"),"title":it.get("title"),"url":it.get("url"),"published":t,"publisher":it.get("domain")})
    else:
        data=_json(_get(spec["endpoint"]+"?"+urllib.parse.urlencode({"q":q,"limit":25,"sort":"latest"}),payloads));items=[]
        for it in data.get("posts",[]):
            r=it.get("record",{});items.append({"id":it.get("uri"),"title":r.get("text"),"url":"https://bsky.app/profile/"+it.get("author",{}).get("handle","")+"/post/"+(it.get("uri") or "").split("/")[-1],"published":r.get("createdAt"),"publisher":it.get("author",{}).get("handle")})
    # Country/state name evidence, not publishers' country or a search query alone.
    states="Andhra Pradesh|Arunachal Pradesh|Assam|Bihar|Chhattisgarh|Goa|Gujarat|Haryana|Himachal Pradesh|Jharkhand|Karnataka|Kerala|Madhya Pradesh|Maharashtra|Manipur|Meghalaya|Mizoram|Nagaland|Odisha|Orissa|Punjab|Rajasthan|Sikkim|Tamil Nadu|Telangana|Tripura|Uttar Pradesh|Uttarakhand|West Bengal|Delhi|Jammu and Kashmir|Ladakh|Chandigarh|Puducherry|Andaman and Nicobar"
    foreign=re.compile(r"\b(Nepal|Bhutan|Bangladesh|Pakistan|China|Sri Lanka|Indonesia|Philippines|Vietnam|Japan|USA|United States|Australia)\b",re.I)
    seen=set()
    for it in items[:80]:
        title=it.get("title") or "";state=re.search(r"\b("+states+r")\b",title,re.I)
        country=bool(re.search(r"\bIndia\b",title,re.I) or state)
        if not country or foreign.search(title):continue  # mixed-country events stay unresolved
        pub=utils.parse_time(it.get("published"));norm=policy.norm_title(title)
        if not pub or norm in seen:continue
        seen.add(norm);classification=policy.classify(title,pub,utils.utcnow())
        if re.search(r"\b(scientists|research|study|anniversary|years ago|in 20[012]\d)\b",title,re.I):classification="historical"
        out.append({"source_record_id":str(it["id"]),"title":title,"source_url":it["url"],"published_at":utils.iso(pub),"event_at":None,"kind":"NEWS_REPORT" if name!="bluesky_public" else "SOCIAL_REPORT","country_code":"in","geo_precision":"state_reference_unresolved" if state else "country_reference_unresolved","state_name":state.group(1) if state else None,"hazard":"disaster","independence_key":"news:"+(policy.publisher_key(it.get("publisher")) or "unknown"),"data":{"original_item":it,"classification":classification,"limitation":"Headline reports unverified occurrence; publication time is not event time"}})
    return out
