"""Authorised social-media ingestion connectors (extensible registry). No scraping.

* mastodon_public_tags — REAL: Mastodon's documented public endpoint GET /api/v1/timelines/tag/:hashtag
  (no authentication needed on instances that allow public timelines). Disabled by default; run on demand
  or enable periodic polling via env. Only posts with image/video attachments are considered, and (by
  default) only posts whose text names a place that geocodes inside India are ingested.
* x_api, youtube_data_api, bluesky — registered for extensibility; report NOT_CONFIGURED / UNAVAILABLE
  honestly. They never fetch anything without credentials.
"""
import json
import os
import re
import threading
import time
from html import unescape

import httpx

from . import db, ingest, utils
from .config import HTTP_TIMEOUT, MAX_UPLOAD_MB, USER_AGENT

TAG_CATEGORY = {"flood": "flood", "floods": "flood", "flooding": "flood", "waterlogging": "flood", "indiaflood": "flood",
                "landslide": "landslide", "landslides": "landslide", "cyclone": "cyclone_storm", "storm": "cyclone_storm",
                "wildfire": "fire", "fire": "fire", "forestfire": "fire"}
_run_lock = threading.Lock()


def _env(name, default=""):
    return os.getenv(name, default).strip()


def registry():
    inst = _env("MASTODON_INSTANCE", "https://mastodon.social")
    tags = [t.strip().lstrip("#").lower() for t in _env("MASTODON_TAGS", "flood,floods,landslide,cyclone,waterlogging").split(",") if t.strip()]
    poll = int(_env("MASTODON_POLL_MIN", "0") or 0)
    last = db.one("SELECT * FROM connector_runs WHERE connector='mastodon_public_tags' ORDER BY id DESC LIMIT 1")
    return [
        {"name": "mastodon_public_tags", "platform": "Mastodon", "kind": "public API (no credentials required)",
         "state": "ENABLED_POLLING" if poll > 0 else "AVAILABLE_MANUAL",
         "detail": f"{inst} · tags {', '.join('#' + t for t in tags)} · "
                   + (f"polls every {poll} min" if poll > 0 else "runs on demand (set MASTODON_POLL_MIN to poll)"),
         "config": {"instance": inst, "tags": tags, "poll_min": poll,
                    "require_india_location": _env("MASTODON_REQUIRE_INDIA", "true").lower() != "false",
                    "max_age_days": float(_env("MASTODON_MAX_AGE_DAYS", "7") or 7)},
         "last_run": last, "can_run": True},
        {"name": "x_api", "platform": "X (Twitter)", "kind": "official API v2 (bearer token, paid tier)",
         "state": "NOT_CONFIGURED" if not _env("X_BEARER_TOKEN") else "CONFIGURED_NOT_IMPLEMENTED",
         "detail": "X API read access requires a paid tier — excluded by PRALAY's free-only policy. Not implemented; nothing is fetched.",
         "can_run": False},
        {"name": "youtube_data_api", "platform": "YouTube", "kind": "YouTube Data API v3 (API key)",
         "state": "NOT_CONFIGURED" if not _env("YOUTUBE_API_KEY") else "CONFIGURED_NOT_IMPLEMENTED",
         "detail": "Set YOUTUBE_API_KEY to enable. Adapter planned.", "can_run": False},
        {"name": "bluesky", "platform": "Bluesky", "kind": "AT Protocol (app password)",
         "state": "NOT_CONFIGURED" if not _env("BLUESKY_APP_PASSWORD") else "CONFIGURED_NOT_IMPLEMENTED",
         "detail": "Public unauthenticated search returned HTTP 403 when tested (2026-10-31); requires BLUESKY_HANDLE + "
                   "BLUESKY_APP_PASSWORD. Adapter planned.", "can_run": False},
    ]


DISASTER_TERMS = [  # (category, regex) — first match wins
    ("flood", r"\bflood(s|ed|ing)?\b|waterlog|inundat|deluge|cloudburst|flash flood|overflow(ing)? (river|dam)"),
    ("landslide", r"\blandslides?\b|\blandslip|mudslide|debris flow"),
    ("cyclone_storm", r"\bcyclone|\bstorm surge|\bhurricane|\btyphoon"),
    ("fire", r"\bwildfire|\bforest fire|\bblaze\b|\bon fire\b|\bfire (broke|at|in)\b"),
    ("building_damage", r"\bbuilding collapse|\bcollapsed (building|house|wall)"),
    ("infrastructure_damage", r"\bbridge (collapse|washed)|\bdam (breach|burst)"),
]


FIGURATIVE = re.compile(r"\bflood(ed|ing)? (of|with) (emails?|messages?|memes?|calls?|requests?|comments?|tweets?|posts?|questions?|orders?|"
                        r"love|support|tears|memories|news|content|spam|bots?|ads?)\b|flood(ing)? (my|your|the) (timeline|feed|inbox|zone)|"
                        r"\bfloodgates? (open|opened)\b", re.I)


def classify_disaster(text, tag_names):
    """Category for a post that itself describes a physical disaster, else None. Figurative uses
    ('a flood of emails') are removed before matching, so a keyword alone is not enough."""
    clean = FIGURATIVE.sub(" ", text or "")
    blob = clean.lower() + " " + " ".join("#" + t.lower() for t in tag_names)
    for t in tag_names:
        if TAG_CATEGORY.get(t.lower()):
            return TAG_CATEGORY[t.lower()]
    for cat, rx in DISASTER_TERMS:
        if re.search(rx, blob):
            return cat
    return None


def _text(html):
    t = re.sub(r"<br\s*/?>|</p>", "\n", html or "")
    return unescape(re.sub(r"<[^>]+>", "", t)).strip()


_PLACE = re.compile(r"\b(?:in|at|near|across|around)\s+([A-Z][\w'.-]+(?:[ ,]+[A-Z][\w'.-]+){0,2})")


def extract_place(text):
    """Find a place phrase like 'in Guwahati, Assam' and geocode it (India only). Returns (phrase, geocode) or (None, None)."""
    cands = [m.group(1).strip(" ,.") for m in _PLACE.finditer(text or "")]
    cands += [h for h in re.findall(r"#([A-Z][a-zA-Z]{3,})", text or "")]
    seen = set()
    for c in cands:
        if c.lower() in seen:
            continue
        seen.add(c.lower())
        if len(seen) > 4:
            break
        g = utils.geocode_india_strict(c)
        if g:
            return c, g
    return None, None


def run_mastodon(tags=None, limit=20, dry_run=False, instance=None, trigger="manual"):
    """Fetch public tagged posts with media and ingest qualifying ones as social_post reports.
    dry_run=True applies every gate but downloads nothing and creates nothing: returns candidate metadata only."""
    cfg = dict(next(c for c in registry() if c["name"] == "mastodon_public_tags")["config"])
    if instance:
        ok, why = utils.public_https_host(instance)
        if not ok:
            return {"status": "failed", "errors": [f"instance refused: {why}"], "fetched": 0, "ingested": 0, "skipped": {}, "report_ids": []}
        cfg["instance"] = instance
    tags = tags or cfg["tags"]
    candidates = []
    if not _run_lock.acquire(blocking=False):
        return {"status": "already_running"}
    run_id = db.execute("INSERT INTO connector_runs(connector,started_at,status,params_json) VALUES(?,?,?,?)",
                        ("mastodon_public_tags", db.now_iso(), "running", db.jdump({"tags": tags, "limit": limit, "dry_run": dry_run, "trigger": trigger, **cfg})))
    fetched = ingested = 0
    skipped, errors, created = {}, [], []

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    try:
        for tag in tags:
            host = cfg["instance"].split("//")[-1].strip("/")
            # Health/cooldown tracked per instance so one failing server does not block others.
            src_key = "mastodon" if host == "mastodon.social" else f"mastodon:{host}"
            res = utils.cached_get(f"{cfg['instance'].rstrip('/')}/api/v1/timelines/tag/{tag}",
                                   {"limit": min(limit, 40), "only_media": "true"}, ttl_min=5, source=src_key)
            if not res.ok:
                errors.append(f"#{tag}: {res.error}")
                continue
            try:
                posts = json.loads(res.body)
            except ValueError:
                errors.append(f"#{tag}: non-JSON response")
                continue
            for p in posts:
                fetched += 1
                if fetched % 10 == 0:  # progress for the UI while the run is in flight
                    db.execute("UPDATE connector_runs SET fetched=?, ingested=?, skipped=? WHERE id=?",
                               (fetched, ingested, sum(skipped.values()), run_id))
                if p.get("visibility") != "public" or p.get("reblog"):
                    skip("not an original public post"); continue
                if p.get("sensitive"):
                    skip("marked sensitive"); continue
                post_time = utils.parse_time(p.get("created_at"))
                if not post_time or (utils.utcnow() - post_time).total_seconds() > cfg["max_age_days"] * 86400:
                    skip(f"older than {cfg['max_age_days']:g} days (not an active-disaster report)"); continue
                media = [m for m in p.get("media_attachments", []) if m.get("type") in ("image", "video", "gifv") and str(m.get("url", "")).startswith("https://")][:3]
                if not media:
                    skip("no image/video attachment"); continue
                ext_id = p.get("uri") or p.get("url") or p["id"]
                if db.one("SELECT id FROM reports WHERE ingest_connector='mastodon_public_tags' AND external_id=?", (ext_id,)):
                    skip("already ingested"); continue
                text = _text(p.get("content"))
                # Relevance gate BEFORE anything is downloaded: the post itself (text or its own hashtags) must describe
                # a disaster. A post that merely names a place (e.g. #kerala) is never collected.
                cat = classify_disaster(text, [t.get("name", "") for t in p.get("tags", [])])
                if not cat:
                    skip("not disaster-related (no disaster term in text or hashtags)"); continue
                phrase, g = extract_place(text)
                if cfg["require_india_location"] and not g:
                    skip("no place in India found in post text"); continue
                if dry_run:
                    candidates.append({"url": p.get("url"), "author": "@" + p.get("account", {}).get("acct", "?"), "created_at": p.get("created_at"),
                                       "category": cat, "place_phrase": phrase, "geocoded_as": g and g.get("label"),
                                       "media": len(media), "text_excerpt": text[:200]})
                    skip("dry run: qualifying candidate (not downloaded)"); continue
                files = []
                for m in media:
                    # SSRF guard: third-party media URLs (and their redirects) must be public https hosts.
                    data, err = utils.safe_fetch(m["url"], MAX_UPLOAD_MB * 1024 * 1024)
                    if data:
                        files.append((data, m["url"].rsplit("/", 1)[-1].split("?")[0][:80], None))
                    else:
                        errors.append(f"media download refused/failed: {err}")
                if not files:
                    skip("media download failed"); continue
                try:
                    rid, _, _ = ingest.create_report(
                        category=cat, description=text[:2000], claimed_location=phrase or "", source_type="social_post",
                        source_platform=f"Mastodon ({cfg['instance'].split('//')[-1]})", source_url=p.get("url") or "",
                        original_publication_time=p.get("created_at") or "", reporter_ref="@" + p.get("account", {}).get("acct", "unknown"),
                        files=files, ingest_connector="mastodon_public_tags", external_id=ext_id, require_location=False)
                    ingested += 1
                    created.append(rid)
                except ingest.IngestError as e:
                    skip(f"rejected: {str(e)[:60]}")
        status = "ok" if not errors else ("partial" if fetched else "failed")
        return {"status": status, "fetched": fetched, "ingested": ingested, "skipped": skipped, "errors": errors, "report_ids": created,
                "dry_run": dry_run, "candidates": candidates, "instance": cfg["instance"], "tags": tags}
    finally:
        db.execute("UPDATE connector_runs SET finished_at=?, status=?, fetched=?, ingested=?, skipped=?, error=? WHERE id=?",
                   (db.now_iso(), "ok" if not errors else ("partial" if fetched else "failed"), fetched, ingested,
                    sum(skipped.values()), "; ".join(errors)[:1000] or None, run_id))
        _run_lock.release()


def run(name, **kw):
    if name == "mastodon_public_tags":
        return run_mastodon(**kw)
    c = next((c for c in registry() if c["name"] == name), None)
    if not c:
        raise KeyError(name)
    return {"status": c["state"], "detail": c["detail"]}


def poll_loop(stop_event):
    while True:
        poll = int(_env("MASTODON_POLL_MIN", "0") or 0)
        if stop_event.wait(max(poll, 1) * 60):
            return
        if poll > 0:
            try:
                run_mastodon(trigger="schedule")
            except Exception:
                __import__('logging').getLogger('pralay.connectors').exception('mastodon poll failed')
