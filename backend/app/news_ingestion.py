"""Per-report news corroboration (free Google News RSS). Fetch -> parse -> validate -> store -> score.
Validation = disaster type (word-boundary hazard terms) + location (place token in headline) + time (published within
24 h of the report) + actual occurrence (not a warning/forecast/metaphor, existing evidence_policy.classify).
Matched articles are stored as observations(kind='news'); fusion.assess_report only READS them (no network there)."""
import xml.etree.ElementTree as ET
from datetime import timedelta

from . import db, utils
from . import evidence_policy as policy
from .external import SEARCH_TERM, _rss_items

STOP = {"near", "the", "road", "city", "district", "area", "state", "india", "town", "village", "sector", "phase"}


def place_tokens(report):
    text = (report.get("claimed_location_text") or "").lower().replace(",", " ")
    return [t for t in text.split() if len(t) >= 4 and t not in STOP]


def fetch_articles(query: str, max_results: int = 10) -> list[dict]:
    res = utils.cached_get("https://news.google.com/rss/search", {"q": f"{query} when:24h", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                           ttl_min=15, source="google_news")
    if not res.ok:
        raise RuntimeError(f"Google News unavailable: {res.error}")
    try:
        items = list(_rss_items(res.body))
    except ET.ParseError as e:
        raise RuntimeError(f"Google News feed parse error: {e}")
    cutoff, out = utils.utcnow() - timedelta(hours=24), []
    for it in items:
        pub = utils.parse_time(it["pub"])
        if pub and pub >= cutoff:
            out.append({"title": utils.clean_html(it["title"]), "source": utils.clean_html(it["source"]) or "unknown publisher",
                        "published": utils.iso(pub), "link": it["link"]})
        if len(out) >= max_results:
            break
    return out


def check_evidence(article, report):
    title, pub = article["title"], utils.parse_time(article["published"])
    up = utils.parse_time(report.get("upload_time"))
    return {"disaster_type": policy.hazard_matches(title, report["category"]),
            # the MOST SPECIFIC place word must appear ("parliament" for "parliament delhi"): a same-city headline about a
            # different site (e.g. a Mangolpuri factory fire for a Parliament fire claim) must not match
            "location": bool(place_tokens(report)) and policy.mentions(title, place_tokens(report)[0]),
            "time": bool(pub and up and abs(pub - up) <= timedelta(hours=24)),
            "actual_occurrence": policy.classify(title, pub, utils.utcnow()) == "actual"}


def is_valid_evidence(article: dict, report) -> bool:
    return all(check_evidence(article, report).values())


def refresh(report_id):
    """Fetch, validate and store matching articles for one report. Returns (matched, evaluated)."""
    from .fusion import is_trusted
    r = db.one("SELECT * FROM reports WHERE id=?", (report_id,))
    if not r or not place_tokens(r) or not r.get("category"):
        return [], []
    query = f"{SEARCH_TERM.get(r['category'], r['category'])} {' '.join(place_tokens(r))}"
    evaluated = []
    for a in fetch_articles(query):
        c = check_evidence(a, r)
        evaluated.append({**a, "checks": c, "valid": all(c.values()), "trusted": is_trusted(a["source"])})
    matched = [a for a in evaluated if a["valid"]]
    with db.tx() as cx:  # replace this report's previous news snapshot
        cx.execute("DELETE FROM observations WHERE report_id=? AND kind='news'", (report_id,))
        for a in matched:
            cx.execute("INSERT INTO observations(report_id,kind,source,time_value,label,limitation,created_at) VALUES(?,?,?,?,?,?,?)",
                       (report_id, "news", "google_news", a["published"], f"{a['source']} | {a['title']}", a["link"], db.now_iso()))
    return matched, evaluated
