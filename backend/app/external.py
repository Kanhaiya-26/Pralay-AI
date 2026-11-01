"""External Evidence Verification Engine (Module 7) + incident-level evidence cache (Module 10).

Real integrations (no fabricated sources):
  * NDMA SACHET CAP public alert RSS (official alerts)        — sachet.ndma.gov.in
  * Google News RSS search (news, publisher-level independence)
  * GDELT DOC 2.0 API (news, rate-limited to 1 req / 5 s)
  * Open-Meteo (modelled rainfall/weather context)
Each record stores source, URL, origin, publication/event/retrieval time, summary, stance and an
independence group. Syndicated copies (same headline) collapse into one independent item.
Unreachable sources are recorded with stance 'unavailable' rather than silently ignored.
"""
import hashlib
import json
import re
import threading
import xml.etree.ElementTree as ET
from datetime import timedelta

from . import db, utils, evidence_policy as policy
from .config import ACTIVE_INCIDENT_WINDOW_H, EVIDENCE_TTL_ACTIVE_MIN, EVIDENCE_TTL_OLD_MIN

KEYWORDS = {"flood": ["flood", "flooding", "waterlogging", "inundat", "deluge", "heavy rain"],
            "fire": ["fire", "blaze", "burn"], "smoke": ["smoke", "fire"],
            "landslide": ["landslide", "landslip", "mudslide"],
            "building_damage": ["collapse", "building", "damage"], "road_blockage": ["road", "block", "landslide"],
            "infrastructure_damage": ["bridge", "damage", "collapse", "power"], "cyclone_storm": ["cyclone", "storm", "gale"],
            "other": ["disaster"]}
SEARCH_TERM = {"flood": "flood", "fire": "fire", "smoke": "fire", "landslide": "landslide", "building_damage": "building collapse",
               "road_blockage": "road blocked", "infrastructure_damage": "damage", "cyclone_storm": "cyclone", "other": "disaster"}
SACHET_CATS = {"flood": {"met", "hydro"}, "cyclone_storm": {"met"}, "landslide": {"geo", "met"}, "fire": {"fire"}, "smoke": {"fire", "env"}}
HEAVY_RAIN_MM = 64.5  # IMD "heavy rainfall" lower bound (24 h)


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _norm_title(t):
    return policy.norm_title(t)


def geographic_match(inc, title):
    """Validate country and incident coordinates, then the item's locality.

    A state-wide alert is not local confirmation for a city. Unknown or foreign
    place resolution is context only; the old India-restricted cache is not used.
    """
    place = (inc.get("place_name") or "").split(",")[0].strip()
    if inc.get("country_code") not in (None, "in"):
        return False
    if not policy.mentions(title, place):
        return False
    resolved = utils.resolve_india(inc.get("place_name"))
    geo = resolved.get("geo")
    if not geo or inc.get("center_lat") is None or inc.get("center_lon") is None:
        return False
    distance = utils.haversine_km(geo["lat"], geo["lon"], inc["center_lat"], inc["center_lon"])
    if distance > 25:
        return False
    # An explicit qualifier after a same-named place must resolve to India too.
    m = re.search(re.escape(place) + r"(?:,\s*|\s+in\s+)([A-Za-z][A-Za-z ]{1,45}?)(?=[,.—:;]|\s+-\s+|\s+(?:as|after|during|has|was|is|hit|flood|cyclone)\b|$)", title, re.I)
    if m:
        qualified = utils.resolve_india(f"{place}, {m.group(1).strip()}")
        if not qualified.get("geo"):
            return False
        qgeo = qualified["geo"]
        if utils.haversine_km(qgeo["lat"], qgeo["lon"], geo["lat"], geo["lon"]) > 25:
            return False
    return True


def _validate(inc, title, pub, lo, hi):
    return policy.evaluate(inc, title, pub, utils.utcnow(), geographic_match(inc, title),
                           pub is not None and lo <= pub <= hi)


def _validated_record(inc, title, published, lo, hi, **kwargs):
    validation = _validate(inc, title, published, lo, hi)
    rec = _rec(stance="supports" if validation["eligible"] else "neutral", **kwargs)
    rec["validation"] = validation
    return rec


def _window(inc):
    start = utils.parse_time(inc["event_time_start"]) or utils.parse_time(inc["created_at"])
    end = utils.parse_time(inc["event_time_end"]) or start
    return start - timedelta(hours=24), end + timedelta(hours=96)


def _rec(source_name, origin, stance, summary, url=None, publisher=None, pub=None, event=None, group=None, dedupe=None,
         result_type=None, retrieved=None):
    """result_type distinguishes: match (relevant item found) | no_match (source answered, nothing relevant) |
    unavailable (source failed / skipped). retrieved = when the data was actually fetched (cache-aware)."""
    if result_type is None:
        result_type = "unavailable" if stance == "unavailable" else "match"
    return dict(source_name=source_name, source_origin=origin, stance=stance, summary=summary, source_url=url,
                publisher=publisher, publication_time=pub, event_time=event, independence_group=group,
                dedupe_key=dedupe or _key(source_name, url, summary), result_type=result_type, retrieved=retrieved)


def _unavail(name, origin, res, key):
    return _rec(name, origin, "unavailable", f"Source could not be accessed: {res.error}", dedupe=key, result_type="unavailable")


def _stamp(records, res):
    """Attach the real fetch time and flag stale cached copies."""
    for r in records:
        r["retrieved"] = res.fetched_at
        if res.stale and r["result_type"] != "unavailable":
            r["summary"] += f" [STALE: served from cache fetched {res.fetched_at} because the live request failed or the source is cooling down]"
            if r.get("validation"):
                r["validation"]["eligible"] = False
                r["validation"]["excluded_reasons"].append("stale_retrieval")
                r["stance"] = "neutral"
    return records


def _rss_items(xml_text):
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        src = it.find("source")
        yield {"title": utils.clean_html((it.findtext("title") or "").strip()), "link": (it.findtext("link") or "").strip(),
               "pub": it.findtext("pubDate"), "category": (it.findtext("category") or "").strip(),
               "author": (it.findtext("author") or "").strip(), "source": src.text if src is not None else None,
               "source_url": src.get("url") if src is not None else None}


def google_news(inc, ttl):
    place = inc["place_name"] or ""
    q = f'{SEARCH_TERM.get(inc["category"], "disaster")} "{place.split(",")[0]}" when:14d'
    res = utils.cached_get("https://news.google.com/rss/search", {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}, ttl_min=ttl, source="google_news")
    if not res.ok:
        return [_rec("Google News RSS", "news", "unavailable", f"Source could not be accessed: {res.error}", dedupe="unavailable:gnews")]
    lo, hi = _window(inc)
    ptok = place.split(",")[0].strip().lower()
    out = []
    try:
        items = list(_rss_items(res.body))[:25]
    except ET.ParseError as e:
        return [_rec("Google News RSS", "news", "unavailable", f"Feed parse error: {e}", dedupe="unavailable:gnews")]
    for it in items:
        nt = _norm_title(it["title"])
        # Preserve each publisher's provenance; fusion collapses syndicated copies.
        pub = utils.parse_time(it["pub"])
        tl = it["title"].lower()
        relevant = policy.mentions(tl, ptok)
        if not relevant:
            continue
        publisher = it["source"] or "unknown publisher"
        out.append(_validated_record(inc, it["title"], pub, lo, hi, source_name="Google News RSS", origin="news", summary=f"Headline: {it['title']}", url=it["link"],
                        publisher=publisher, pub=utils.iso(pub), group=f"news:{publisher.lower()}", dedupe=_key("gn", nt)))
        if len(out) >= 8:
            break
    if not out:
        out.append(_rec("Google News RSS", "news", "neutral",
                        f"No matching headlines for query [{q}] among {len(items)} results. Absence of coverage does not disprove the report.",
                        dedupe="gnews:none", result_type="no_match"))
    return _stamp(out, res)


def gdelt(inc, ttl):
    place = (inc["place_name"] or "").split(",")[0]
    q = f'"{place}" {SEARCH_TERM.get(inc["category"], "disaster").split()[0]}'
    res = utils.cached_get("https://api.gdeltproject.org/api/v2/doc/doc",
                           {"query": q, "mode": "artlist", "format": "json", "maxrecords": 15, "timespan": "14d", "sort": "datedesc"}, ttl_min=ttl, source="gdelt")
    if not res.ok:
        return [_rec("GDELT DOC 2.0", "news", "unavailable", f"Source could not be accessed: {res.error}", dedupe="unavailable:gdelt")]
    try:
        arts = json.loads(res.body).get("articles", [])
    except ValueError:
        return [_rec("GDELT DOC 2.0", "news", "unavailable", "GDELT returned a non-JSON response (likely rate-limited).", dedupe="unavailable:gdelt")]
    lo, hi = _window(inc)
    out = []
    for a in arts:
        nt = _norm_title(a.get("title"))
        pub = utils.parse_time(re.sub(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$", r"\1-\2-\3T\4:\5:\6+00:00", a.get("seendate", "")))
        dom = a.get("domain") or "unknown"
        out.append(_validated_record(inc, a.get("title") or "", pub, lo, hi, source_name="GDELT DOC 2.0", origin="news", summary=f"Article: {a.get('title')}", url=a.get("url"), publisher=dom,
                        pub=utils.iso(pub), group=f"news:{dom}", dedupe=_key("gd", nt)))
        if len(out) >= 6:
            break
    if not out:
        out.append(_rec("GDELT DOC 2.0", "news", "neutral", f"No GDELT articles for [{q}] in last 14 days.", dedupe="gdelt:none", result_type="no_match"))
    return _stamp(out, res)


def sachet(inc, ttl):
    res = utils.cached_get("https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml", ttl_min=ttl, source="ndma_sachet")
    if not res.ok:
        return [_rec("NDMA SACHET (CAP alerts)", "official", "unavailable", f"Source could not be accessed: {res.error}",
                     dedupe="unavailable:sachet")]
    try:
        items = list(_rss_items(res.body))
    except ET.ParseError as e:
        return [_rec("NDMA SACHET (CAP alerts)", "official", "unavailable", f"Feed parse error: {e}", dedupe="unavailable:sachet")]
    state = (inc["state_name"] or "").lower()
    place = (inc["place_name"] or "").split(",")[0].lower()
    lo, hi = _window(inc)
    out = []
    for it in items:
        blob = f"{it['title']} {it['author']}".lower()
        loc_match = (state and state in blob) or (place and place in blob)
        if not loc_match:
            continue
        pub = utils.parse_time(it["pub"])
        issuer = re.sub(r"^.*\((.*)\)$", r"\1", it["author"]) or "NDMA"
        out.append(_validated_record(inc, it["title"], pub, lo, hi, source_name="NDMA SACHET (CAP alerts)", origin="official",
                        summary=f"Official alert [{it['category']}] by {issuer}: {it['title']}",
                        url=it["link"], publisher=issuer, pub=utils.iso(pub), group="official:ndma-sachet", dedupe=_key("sachet", it["link"])))
        if len(out) >= 5:
            break
    if not out:
        out.append(_rec("NDMA SACHET (CAP alerts)", "official", "neutral",
                        f"{len(items)} current national CAP alerts checked; none mention '{inc['state_name'] or inc['place_name']}'. "
                        "Lack of an official alert does not prove the event did not occur. (Alert titles in regional languages may not match.)",
                        url="https://sachet.ndma.gov.in/", group="official:ndma-sachet", dedupe="sachet:none", result_type="no_match"))
    return _stamp(out, res)


def open_meteo(inc, ttl):
    if inc["center_lat"] is None:
        return []
    start = utils.parse_time(inc["event_time_start"]) or utils.utcnow()
    end = utils.parse_time(inc["event_time_end"]) or start
    now = utils.utcnow()
    d0, d1 = (start - timedelta(days=2)).date(), min(end + timedelta(days=1), now).date()
    lat, lon = round(inc["center_lat"], 3), round(inc["center_lon"], 3)
    params = {"latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata",
              "daily": "precipitation_sum,temperature_2m_max,relative_humidity_2m_min,wind_speed_10m_max",
              "start_date": d0.isoformat(), "end_date": d1.isoformat()}
    url = "https://api.open-meteo.com/v1/forecast" if (now.date() - d0).days <= 90 else "https://archive-api.open-meteo.com/v1/archive"
    res = utils.cached_get(url, params, ttl_min=ttl, source="open_meteo")
    if not res.ok:
        return [_rec("Open-Meteo", "weather", "unavailable", f"Source could not be accessed: {res.error}", dedupe="unavailable:openmeteo")]
    try:
        d = json.loads(res.body)["daily"]
    except (ValueError, KeyError):
        return [_rec("Open-Meteo", "weather", "unavailable", "Unexpected response format.", dedupe="unavailable:openmeteo")]
    rain = [x for x in d.get("precipitation_sum", []) if x is not None]
    mx = max(rain) if rain else None
    days = ", ".join(f"{t}: {p} mm" for t, p in zip(d.get("time", []), d.get("precipitation_sum", [])))
    cat = inc["category"]
    if cat in ("flood", "landslide", "cyclone_storm"):
        if mx is None:
            stance, s = "unavailable", "No precipitation values returned."
        elif mx >= HEAVY_RAIN_MM:
            stance, s = "neutral", f"Weather context only: modelled daily rainfall reached {mx} mm (≥ {HEAVY_RAIN_MM} mm). This does not confirm a disaster occurrence. {days}"
        else:
            stance, s = "neutral", (f"Modelled daily rainfall max {mx} mm. {days}. Low modelled rain does NOT disprove a flood "
                                    "(drainage failure, upstream release, local cloudburst, model grid ~11 km).")
    else:
        stance = "neutral"
        s = (f"Weather context: max temp {max(d.get('temperature_2m_max') or [0])} °C, min RH "
             f"{min(d.get('relative_humidity_2m_min') or [0])} %, max wind {max(d.get('wind_speed_10m_max') or [0])} km/h.")
    return _stamp([_rec("Open-Meteo", "weather", stance, s, url=f"{url}?latitude={lat}&longitude={lon}&daily=precipitation_sum&start_date={d0}&end_date={d1}", publisher="Open-Meteo (model data)",
                 event=f"{d0}..{d1}", group="weather:open-meteo", dedupe=_key("om", lat, lon, d0, d1),
                 result_type="match" if stance == "supports" else "no_match")], res)


SOURCES = [("sachet", sachet), ("google_news", google_news), ("open_meteo", open_meteo), ("gdelt", gdelt)]

_inflight = {}
_inflight_lock = threading.Lock()


def is_active(inc):
    la = utils.parse_time(inc["last_activity_at"]) or utils.parse_time(inc["created_at"])
    return la and (utils.utcnow() - la).total_seconds() / 3600 <= ACTIVE_INCIDENT_WINDOW_H


def freshness(inc):
    ttl = EVIDENCE_TTL_ACTIVE_MIN if is_active(inc) else EVIDENCE_TTL_OLD_MIN
    last = utils.parse_time(inc["last_evidence_check"])
    if not last:
        return {"state": "never_checked", "ttl_min": ttl, "last_checked": None}
    age = (utils.utcnow() - last).total_seconds() / 60
    return {"state": "fresh" if age <= ttl else "stale", "ttl_min": ttl, "last_checked": inc["last_evidence_check"],
            "age_min": round(age, 1)}


def gather(incident_id, force=False, include_gdelt=True):
    """Refresh external evidence for an incident unless fresh evidence exists (reuse).
    Simultaneous calls for the same incident are coalesced onto one investigation."""
    with _inflight_lock:
        ev = _inflight.get(incident_id)
        leader = ev is None
        if leader:
            ev = _inflight[incident_id] = threading.Event()
    if not leader:
        ev.wait(timeout=90)
        return {"action": "coalesced", "detail": "Joined an investigation already in progress for this incident."}
    try:
        inc = db.one("SELECT * FROM incidents WHERE id=?", (incident_id,))
        fr = freshness(inc)
        if fr["state"] == "fresh" and not force:
            return {"action": "reused", "freshness": fr}
        ttl = fr["ttl_min"]
        before = db.one("SELECT COUNT(*) c FROM evidence WHERE incident_id=?", (incident_id,))["c"]
        records = []
        for name, fn in SOURCES:
            if name == "gdelt" and not include_gdelt:
                continue
            try:
                records += fn(inc, ttl)
            except Exception as e:  # one failing source must not stop the others
                records.append(_rec(name, "news", "unavailable", f"Integration error: {type(e).__name__}: {e}", dedupe=f"unavailable:{name}"))
        now = db.now_iso()
        with db.tx() as c:
            # Append-only retrieval snapshots. Never overwrite the evidence on which
            # an earlier assessment was based, including unavailable/no-match rows.
            for r in records:
                retrieved = r.get("retrieved") or now
                validation = db.jdump(r.get("validation"))
                snapshot_key = _key(policy.VERSION, r["dedupe_key"], retrieved, r["summary"], r["stance"], validation,
                                    r["source_name"], r["source_url"], r["publisher"], r["publication_time"], r["event_time"])
                c.execute("INSERT INTO evidence(incident_id,source_name,source_url,source_origin,publisher,publication_time,event_time,"
                          "retrieval_time,summary,stance,independence_group,dedupe_key,result_type,created_at,validation_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT(incident_id,dedupe_key) DO NOTHING",
                          (incident_id, r["source_name"], r["source_url"], r["source_origin"], r["publisher"], r["publication_time"],
                           r["event_time"], retrieved, r["summary"], r["stance"], r["independence_group"], snapshot_key,
                           r.get("result_type"), now, validation))
            c.execute("UPDATE incidents SET last_evidence_check=? WHERE id=?", (now, incident_id))
        after = db.one("SELECT COUNT(*) c FROM evidence WHERE incident_id=?", (incident_id,))["c"]
        return {"action": "refreshed" if before else "investigated", "new_records": after - before,
                "sources": [n for n, _ in SOURCES if n != "gdelt" or include_gdelt]}
    finally:
        with _inflight_lock:
            _inflight.pop(incident_id, None)
        ev.set()
