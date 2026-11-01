"""Conservative occurrence eligibility, shared by connectors, chat and fusion.

These are explainable text rules, not model confidence or proof of occurrence.
Unknown, missing-date and future/alert language stays context only.
"""
import re
from datetime import timedelta
from urllib.parse import urlsplit

VERSION = "occurrence-v2"
WARN_RX = re.compile(r"\b(warnings?|alerts?|advisory|watch|brac\w*|risk|threat|fears?|preparedness)\b", re.I)
FORECAST_RX = re.compile(r"\b(forecasts?|likely|expected|predict\w*|may|might|could|will|to hit|to make|approaching|set to|probability|potential)\b", re.I)
OCCURRENCE_RX = re.compile(r"\b(hit|hits|struck|strikes|landfall|landed|occurred|confirmed|reported|flooded|inundated|waterlogged|"
                           r"damaged|damages|destroyed|killed|dead|deaths|erupted|burning|broke out|breaks out|collapsed|"
                           r"blocked|washed away|aftermath|continues to flow|engulfed|submerged|displaced)\b", re.I)
HAZARDS = {
    "flood": r"\b(flood(?:s|ing|ed)?|waterlog(?:ged|ging)|inundat\w*|deluge)\b",
    "cyclone_storm": r"\b(cyclones?|storms?|hurricanes?|typhoons?)\b",
    "fire": r"\b(fires?|blaze|wildfires?)\b", "smoke": r"\bsmoke\b",
    "landslide": r"\b(landslides?|landslips?|mudslides?)\b",
    "building_damage": r"\b(building|house|roof|hotel|factory)\b.*\b(collaps\w*|damag\w*|destroy\w*)\b",
    "road_blockage": r"\b(road|highway|street)\b.*\b(block\w*|clos\w*|cut off)\b",
    "infrastructure_damage": r"\b(bridge|power line|infrastructure)\b.*\b(collaps\w*|damag\w*|destroy\w*|washed away)\b",
}
STORM_RX = re.compile(r"\b(?:cyclone|storm|hurricane|typhoon)\s+[‘'\"]?([a-z]{3,})\b", re.I)
NOT_NAMES = set("in near at over warning warnings alert alerts advisory watch incidents effects hits hit makes made has had is was will expected likely approaching aftermath damage causes brings kills kills the a an tropical severe named called query".split())


def storm_name(text):
    m = STORM_RX.search(text or "")
    return m.group(1).lower() if m and m.group(1).lower() not in NOT_NAMES else None


def mentions(text, phrase):
    return bool(phrase and re.search(r"(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)", text or "", re.I))


def hazard_matches(text, category, hazard=None):
    if category == "other":
        return bool(hazard and mentions(text, hazard))
    return bool(category in HAZARDS and re.search(HAZARDS[category], text or "", re.I))


def classify(text, published, now):
    if WARN_RX.search(text or ""):
        return "warning"
    if FORECAST_RX.search(text or ""):
        return "forecast"
    if published and published < now - timedelta(days=30):
        return "historical"
    if not published or published > now + timedelta(minutes=5):
        return "unknown"
    if re.search(r"\b(no|not|never|denies|denied|false|rumou?r|drill|exercise|anniversary)\b", text or "", re.I):
        return "unknown"
    return "actual" if OCCURRENCE_RX.search(text or "") else "unknown"


def norm_title(text):
    text = re.sub(r"\s+-\s+[^-]+$", "", text or "")
    return " ".join(re.findall(r"\w+", text.lower()))


def publisher_key(publisher):
    p = (publisher or "").strip().lower()
    if p in ("", "unknown", "unknown publisher"):
        return None
    p = p.removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
    p = re.sub(r"\.(co\.in|com|org|net|in)$", "", p)
    return re.sub(r"\W+", "", p)


def signature(incident):
    return {k: incident.get(k) for k in ("category", "place_name", "state_name", "country_code", "event_name", "hazard",
                                         "center_lat", "center_lon", "event_time_start", "event_time_end")}


def evaluate(incident, title, published, now, geo_ok, in_window):
    kind = classify(title, published, now)
    name = incident.get("event_name") or storm_name(incident.get("title"))
    checks = {"actual_occurrence": kind == "actual", "hazard_match": hazard_matches(title, incident.get("category"), incident.get("hazard")),
              "geographic_match": bool(geo_ok), "named_event_match": not name or storm_name(title) == name.lower(),
              "time_match": bool(in_window)}
    return {"policy": VERSION, "classification": kind, "title": title, "incident": signature(incident),
            "checks": checks, "eligible": all(checks.values()),
            "excluded_reasons": [k for k, ok in checks.items() if not ok]}


def supporting_items(incident, evidence):
    """Legacy/unvalidated rows remain visible but cannot enter new occurrence rules.

    Copies share a title OR canonical URL; a publisher can contribute only one group.
    Weather is context, never occurrence confirmation.
    """
    import json
    out, titles, urls = [], set(), set()
    # Retain old snapshots for audit, but a later correction/context result for
    # the same source item supersedes its earlier supporting snapshot.
    latest = {}
    for index, e in enumerate(evidence):
        item = e.get("source_url") or e.get("summary") or ""
        key = (e.get("source_name"), publisher_key(e.get("publisher")), item)
        order = (e.get("retrieval_time") or "", e.get("created_at") or "", e.get("id") or index)
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, e)
    for _, e in latest.values():
        if e.get("stance") != "supports" or e.get("result_type") != "match":
            continue
        try:
            v = json.loads(e.get("validation_json") or "{}")
        except (ValueError, TypeError):
            continue
        if (v.get("policy") != VERSION or not v.get("eligible") or v.get("incident") != signature(incident)
                or not v.get("checks") or not all(v["checks"].get(k) is True for k in
                    ("actual_occurrence", "hazard_match", "geographic_match", "named_event_match", "time_match"))):
            continue
        title = norm_title(v.get("title"))
        try:
            u = urlsplit(e.get("source_url") or "")
        except ValueError:
            continue
        url = (u.netloc.lower().removeprefix("www."), u.path.rstrip("/")) if u.netloc else None
        publisher = publisher_key(e.get("publisher"))
        if not publisher or not title or title in titles or (url and url in urls):
            continue
        titles.add(title)
        if url:
            urls.add(url)
        out.append({**e, "independence_group": "official:ndma-sachet" if e.get("source_origin") == "official" else f"news:{publisher}"})
    return out
