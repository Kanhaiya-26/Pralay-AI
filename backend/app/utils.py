"""Shared helpers: time normalisation, geodesy, cached + throttled HTTP (Evidence Cache, Module 10)."""
import hashlib
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import db
from .config import EXTERNAL_LOOKUPS, HTTP_TIMEOUT, USER_AGENT

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------- time
def parse_time(value, assume_tz=IST):
    """Parse many timestamp formats into an aware UTC datetime. Returns None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        dt = None
        # EXIF style "2024:07:10 14:22:05"
        m = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
        if m:
            dt = datetime(*map(int, m.groups()))
        if dt is None:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                dt = None
        if dt is None:
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(timezone.utc)


_TAG_RX = re.compile(r"</?[a-zA-Z!][^<>]{0,500}>")  # real tags only: "5 < 6 and 7 > 3" stays intact


def clean_html(text):
    """Feed text -> plain text: drop tags, decode entities (twice, for double-encoded feeds), collapse whitespace.
    Used on DISPLAY fields only; raw payloads are stored unmodified for provenance."""
    import html
    if not text:
        return text
    s = html.unescape(html.unescape(_TAG_RX.sub(" ", str(text))))
    return re.sub(r"[ \t\r\f\v]+", " ", _TAG_RX.sub(" ", s)).strip()


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat() if dt else None


def utcnow():
    return datetime.now(timezone.utc)


def hours_between(a, b):
    return abs((a - b).total_seconds()) / 3600.0


# ---------------------------------------------------------------- geo
def haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- HTTP with persistent cache
_host_locks = {}
_host_last = {}
_host_guard = threading.Lock()
_MIN_INTERVAL = {"nominatim.openstreetmap.org": 1.1, "api.gdeltproject.org": 5.5}


def _throttle(host):
    with _host_guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    lock.acquire()
    try:
        wait = _MIN_INTERVAL.get(host, 0) - (time.time() - _host_last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()
    finally:
        lock.release()


class FetchResult:
    def __init__(self, ok, body=None, status=None, error=None, from_cache=False, fetched_at=None, stale=False, skipped=False):
        self.ok, self.body, self.status, self.error = ok, body, status, error
        self.from_cache, self.fetched_at, self.stale, self.skipped = from_cache, fetched_at, stale, skipped


# Test-mode only: sources forced to fail so failure handling can be exercised (never active in normal runs).
SIMULATED_FAILURES = set()


def source_record(source, ok, result=None, error=None, status=None, skipped=False):
    """Update per-source health. 429 / repeated failures open a cooldown (circuit breaker)."""
    if not source:
        return
    now = utcnow()
    row = db.one("SELECT * FROM source_status WHERE source=?", (source,)) or {}
    fails = 0 if ok else (row.get("consecutive_failures") or 0) + (0 if skipped else 1)
    cooldown = row.get("cooldown_until")
    if not ok and not skipped:
        if status == 429:
            cooldown = iso(now + timedelta(seconds=min(900, 60 * 2 ** min(fails - 1, 4))))
        elif fails >= 3:
            cooldown = iso(now + timedelta(seconds=min(900, 30 * 2 ** min(fails - 3, 5))))
    if ok:
        cooldown = None
    db.execute(
        "INSERT INTO source_status(source,attempts,successes,consecutive_failures,last_attempt_at,last_success_at,last_result,last_error,cooldown_until) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET attempts=attempts+excluded.attempts, successes=successes+excluded.successes, "
        "consecutive_failures=excluded.consecutive_failures, last_attempt_at=excluded.last_attempt_at, "
        "last_success_at=COALESCE(excluded.last_success_at, last_success_at), last_result=excluded.last_result, "
        "last_error=CASE WHEN excluded.last_error IS NULL THEN last_error ELSE excluded.last_error END, cooldown_until=excluded.cooldown_until",
        (source, 0 if skipped else 1, 1 if ok else 0, fails, iso(now), iso(now) if ok else None, result or ("ok" if ok else "error"),
         None if ok else error, cooldown))


def cached_get(url, params=None, ttl_min=30, headers=None, source=None, force=False):
    """GET with a persistent DB-backed cache, per-source health tracking and cooldown. Never raises."""
    full = str(httpx.URL(url, params=params or {}))
    key = hashlib.sha256(full.encode()).hexdigest()
    row = db.one("SELECT * FROM http_cache WHERE cache_key=?", (key,))
    now = utcnow()
    if not force and row and parse_time(row["expires_at"]) > now and row["error"] is None:  # force: fresh fetch (follow-ups)
        db.execute("UPDATE http_cache SET hit_count=hit_count+1 WHERE cache_key=?", (key,))
        return FetchResult(True, row["body"], row["status_code"], None, True, row["fetched_at"])
    if not EXTERNAL_LOOKUPS:
        return FetchResult(False, error="External lookups disabled (EXTERNAL_LOOKUPS=false)", skipped=True)
    st = db.one("SELECT cooldown_until FROM source_status WHERE source=?", (source,)) if source else None
    cd = parse_time(st["cooldown_until"]) if st and st["cooldown_until"] else None
    if cd and cd > now:
        msg = f"skipped: source in cooldown until {iso(cd)} after rate limiting/repeated failures"
        source_record(source, False, "skipped_cooldown", msg, skipped=True)
        if row and row["body"]:
            return FetchResult(True, row["body"], row["status_code"], msg + " — serving stale cached copy", True, row["fetched_at"], stale=True)
        return FetchResult(False, error=msg, skipped=True)
    if source in SIMULATED_FAILURES or f"{source}:429" in SIMULATED_FAILURES:
        rl = f"{source}:429" in SIMULATED_FAILURES
        err = "HTTP 429 (rate limited) — simulated (test mode)" if rl else "simulated failure (test mode)"
        source_record(source, False, "rate_limited" if rl else "error", err, 429 if rl else None)
        return FetchResult(False, error=err, status=429 if rl else None)
    host = httpx.URL(full).host
    _throttle(host)
    try:
        h = {"User-Agent": USER_AGENT}
        h.update(headers or {})
        r = httpx.get(full, headers=h, timeout=HTTP_TIMEOUT, follow_redirects=True)
        ok = 200 <= r.status_code < 300
        body, err = r.text, None if ok else f"HTTP {r.status_code}" + (" (rate limited)" if r.status_code == 429 else "")
        status = r.status_code
    except httpx.HTTPError as e:
        ok, body, err, status = False, None, f"{type(e).__name__}: {e}", None
    source_record(source, ok, "ok" if ok else ("rate_limited" if status == 429 else "error"), err, status)
    fetched = db.now_iso()
    # Failures are cached briefly (2 min) to avoid hammering a failing source.
    exp = now + (timedelta(minutes=ttl_min) if ok else timedelta(minutes=2))
    db.execute(
        "INSERT INTO http_cache(cache_key,url,fetched_at,expires_at,status_code,body,error) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET fetched_at=excluded.fetched_at, expires_at=excluded.expires_at, "
        "status_code=excluded.status_code, body=excluded.body, error=excluded.error",
        (key, full, fetched, iso(exp), status, body if ok else None, err),
    )
    if ok:
        return FetchResult(True, body, status, None, False, fetched)
    # Serve stale body if available, flagged as stale.
    if row and row["body"]:
        return FetchResult(True, row["body"], row["status_code"], f"stale cache used after error: {err}", True, row["fetched_at"], stale=True)
    return FetchResult(False, None, status, err, False, fetched)


# ---------------------------------------------------------------- geocoding (OSM Nominatim)
def geocode(text):
    """Return dict(lat, lon, label, state, source) or None. Cached permanently (failures for 1 day)."""
    if not text or not text.strip():
        return None
    q = re.sub(r"\s+", " ", text.strip().lower())
    row = db.one("SELECT * FROM geocode_cache WHERE query=?", (q,))
    if row:
        if row["ok"] and row.get("radius_km") is not None:
            return {"lat": row["lat"], "lon": row["lon"], "label": row["label"], "state": row["state"], "source": "geocode_cache",
                    "radius_km": row["radius_km"], "place_type": row.get("place_type")}
        if not row["ok"] and parse_time(row["fetched_at"]) > utcnow() - timedelta(days=1):
            return None
        # ok rows cached before radius support fall through and are refreshed once
    import json
    res = cached_get(
        "https://nominatim.openstreetmap.org/search",
        {"q": text, "format": "json", "limit": 1, "countrycodes": "in", "addressdetails": 1},
        ttl_min=60 * 24 * 30, source="nominatim",
    )
    hit = None
    if res.ok:
        try:
            arr = json.loads(res.body)
            if arr:
                a = arr[0]
                addr = a.get("address", {})
                hit = {"lat": float(a["lat"]), "lon": float(a["lon"]), "label": a.get("display_name"),
                       "state": addr.get("state"), "source": "nominatim", "place_type": a.get("addresstype") or a.get("type")}
                # Approximate extent of the named place: half the bounding-box diagonal, clamped to 0.5–60 km.
                try:
                    s, n, w, e = (float(x) for x in a["boundingbox"])
                    hit["radius_km"] = round(min(60.0, max(0.5, haversine_km(s, w, n, e) / 2)), 2)
                except (KeyError, ValueError, TypeError):
                    hit["radius_km"] = 2.0
        except (ValueError, KeyError):
            hit = None
    if not res.ok:
        if row and row["ok"]:  # network failure: keep using the old cached point
            return {"lat": row["lat"], "lon": row["lon"], "label": row["label"], "state": row["state"], "source": "geocode_cache",
                    "radius_km": None, "place_type": None}
        if not row:
            return None  # don't poison the cache on network failure
    db.execute(
        "INSERT INTO geocode_cache(query,ok,lat,lon,label,state,fetched_at,radius_km,place_type) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(query) DO UPDATE SET ok=excluded.ok, lat=excluded.lat, lon=excluded.lon, label=excluded.label, "
        "state=excluded.state, fetched_at=excluded.fetched_at, radius_km=excluded.radius_km, place_type=excluded.place_type",
        (q, 1 if hit else 0, hit and hit["lat"], hit and hit["lon"], hit and hit["label"], hit and hit["state"], db.now_iso(),
         hit and hit.get("radius_km"), hit and hit.get("place_type")),
    )
    return hit


def resolve_india(text):
    """Global country validation BEFORE India-restricted lookup or cache reuse.

    Fail closed on unknown country/network failure. In particular, a country name
    must never fall back to a same-named Indian hamlet. No legacy cache is edited.
    """
    import json
    if not text:
        return {"state": "unresolved", "geo": None}
    res = cached_get("https://nominatim.openstreetmap.org/search",
                     {"q": text, "format": "json", "limit": 1, "addressdetails": 1},
                     ttl_min=60 * 24 * 30, source="nominatim")
    if not res.ok or res.stale:
        return {"state": "unavailable", "geo": None}
    try:
        a = json.loads(res.body)[0]
        country = a.get("address", {}).get("country_code")
        if not isinstance(country, str) or not country:
            return {"state": "unresolved", "geo": None}
        if country.lower() != "in":
            return {"state": "outside_india", "country_code": country, "geo": None}
        lat, lon = float(a["lat"]), float(a["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return {"state": "unresolved", "geo": None}
        return {"state": "resolved", "geo": {"lat": lat, "lon": lon, "country_code": "in",
                "label": a.get("display_name"), "state": a.get("address", {}).get("state"),
                "source": "nominatim_global", "place_type": a.get("addresstype") or a.get("type")}}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return {"state": "unresolved", "geo": None}


def public_https_host(url):
    """SSRF guard for URLs that come from third parties (connector media, user-supplied instances).
    Allows only https:// URLs whose host resolves exclusively to public addresses. Returns (ok, reason)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except ValueError:
        return False, "invalid URL"
    if u.scheme != "https" or not u.hostname:
        return False, "only https:// URLs are fetched"
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return False, f"DNS resolution failed ({e.__class__.__name__})"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, f"{u.hostname} resolves to a non-public address ({ip}) — refused"
    return True, "public"


def safe_fetch(url, max_bytes, timeout=None, max_redirects=3):
    """GET a third-party URL with the SSRF guard applied to the URL and to every redirect hop.
    Returns (bytes or None, error or None). Never raises."""
    from urllib.parse import urljoin
    for _ in range(max_redirects + 1):
        ok, why = public_https_host(url)
        if not ok:
            return None, why
        try:
            with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=timeout or HTTP_TIMEOUT, follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    return None, f"HTTP {r.status_code}"
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        return None, f"exceeds {max_bytes // (1024 * 1024)} MB limit"
                return bytes(buf), None
        except httpx.HTTPError as e:
            return None, f"{type(e).__name__}"
    return None, "too many redirects"


INDIA_PLACE_TYPES = {"city", "town", "village", "suburb", "municipality", "state", "state_district", "district", "county",
                     "city_district", "borough", "neighbourhood", "quarter", "hamlet", "region", "province"}
NOT_PLACES = {"flood", "floods", "flooding", "flash", "warning", "watch", "advisory", "update", "alert", "storm", "rain", "cyclone",
              "landslide", "fire", "news", "breaking", "today", "the", "this", "emergency", "weather", "climate", "river", "water"}


def geocode_india_strict(text):
    """For untrusted free text (social posts): geocode WITHOUT a country restriction and accept only results that
    are settlements/administrative areas inside India. Prevents words like 'Flood' matching some Indian canal."""
    import json
    t = (text or "").strip(" ,.#")
    if len(t) < 3 or t.lower() in NOT_PLACES or all(w.lower() in NOT_PLACES for w in re.findall(r"\w+", t)):
        return None
    res = cached_get("https://nominatim.openstreetmap.org/search", {"q": t, "format": "json", "limit": 1, "addressdetails": 1},
                     ttl_min=60 * 24 * 30, source="nominatim")
    if not res.ok:
        return None
    try:
        arr = json.loads(res.body)
    except ValueError:
        return None
    if not arr:
        return None
    a = arr[0]
    if a.get("address", {}).get("country_code") != "in" or a.get("class") not in ("place", "boundary"):
        return None
    if (a.get("addresstype") or a.get("type")) not in INDIA_PLACE_TYPES:
        return None
    return geocode(t)  # normal path (India-restricted) for consistent radius/label caching


def reverse_state(lat, lon):
    import json
    res = cached_get("https://nominatim.openstreetmap.org/reverse",
                     {"lat": round(lat, 3), "lon": round(lon, 3), "format": "json", "zoom": 10}, ttl_min=60 * 24 * 30, source="nominatim")
    if not res.ok:
        return None
    try:
        a = json.loads(res.body)
        addr = a.get("address", {})
        return {"state": addr.get("state"), "label": a.get("display_name"),
                "place": addr.get("city") or addr.get("town") or addr.get("county") or addr.get("state_district")}
    except ValueError:
        return None


# ---------------------------------------------------------------- text
_STOP = set("the a an of in at on near is are was were there any and or to for with from by this that it be".split())


def tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 2}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
