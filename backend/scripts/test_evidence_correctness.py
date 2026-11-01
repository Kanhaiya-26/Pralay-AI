"""Offline, isolated regression tests. No server, model or live data is used.

Run: backend/.venv/Scripts/python.exe -B backend/scripts/test_evidence_correctness.py
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta

TEMP = tempfile.TemporaryDirectory(prefix="pralay_evidence_tests_")
os.environ["PRALAY_DATA_DIR"] = TEMP.name
os.environ["PRALAY_DB_PATH"] = str(Path(TEMP.name) / "initial.db")
os.environ["EXTERNAL_LOOKUPS"] = "false"
os.environ["VISION_PROVIDER"] = "none"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, utils, external, fusion, chatbot, pipeline, clustering, evidence_policy as policy


class EvidenceCorrectness(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = Path(TEMP.name) / f"{self._testMethodName}.db"
        db.init_db()
        self.now = utils.utcnow()
        self.inc = {"id": 1, "category": "cyclone_storm", "place_name": "Odisha", "state_name": "Odisha",
                    "center_lat": 20.5, "center_lon": 84.6, "event_name": "arnab", "hazard": "cyclone",
                    "event_time_start": utils.iso(self.now - timedelta(hours=24)), "event_time_end": utils.iso(self.now),
                    "created_at": utils.iso(self.now), "last_activity_at": utils.iso(self.now), "title": "Cyclone Arnab",
                    "origin": "investigation", "status": "UNVERIFIED", "assessment_json": None, "confidence_score": None}

    def validation(self, title, inc=None, **kw):
        return policy.evaluate(inc or self.inc, title, kw.get("pub", self.now), self.now,
                               kw.get("geo", True), kw.get("window", True))

    def evidence(self, title, publisher="Publisher A", inc=None, **kw):
        return {"source_name": "Google News RSS", "source_origin": "news", "publisher": publisher,
                "stance": "supports", "result_type": "match", "summary": title,
                "source_url": kw.pop("url", f"https://example.org/{publisher}/{title}"),
                "validation_json": json.dumps(self.validation(title, inc=inc, **kw))}

    def insert_incident(self, **changes):
        inc = {**self.inc, **changes}
        keys = list(inc)
        db.execute(f"INSERT INTO incidents({','.join(keys)},updated_at) VALUES({','.join('?' for _ in keys)},?)",
                   [inc[k] for k in keys] + [utils.iso(self.now)])
        return db.one("SELECT * FROM incidents WHERE id=1")

    def insert_evidence(self, e):
        e = {**e, "incident_id": 1, "retrieval_time": utils.iso(self.now), "created_at": utils.iso(self.now)}
        keys = list(e)
        db.execute(f"INSERT INTO evidence({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", list(e.values()))

    def fetch_result(self, body, stale=False):
        return utils.FetchResult(True, body, status=200, fetched_at=utils.iso(self.now), stale=stale)

    def rss(self, title, publisher="Publisher A", category="Met"):
        from xml.sax.saxutils import escape
        return (f"<rss><channel><item><title>{escape(title)}</title><link>https://news.example/item</link>"
                f"<pubDate>{utils.iso(self.now)}</pubDate><source>{publisher}</source><category>{category}</category>"
                "<author>CWC</author></item></channel></rss>")

    def geo(self, place):
        if "bhutan" in place.lower() or "canada" in place.lower():
            return {"state": "outside_india", "geo": None, "country_code": "bt"}
        return {"state": "resolved", "geo": {"lat": 20.5, "lon": 84.6, "label": "Odisha, India", "state": "Odisha", "country_code": "in"}}

    def test_cyclone_warning_not_occurrence(self):
        for title in ["Cyclone Arnab warning in Odisha", "Odisha braces for Cyclone Arnab", "Cyclone Arnab may hit Odisha",
                      "Cyclone Arnab expected to make landfall in Odisha", "Cyclone Arnab to hit Odisha"]:
            with self.subTest(title=title):
                self.assertFalse(self.validation(title)["eligible"])

    def test_actual_cyclone_occurrence(self):
        self.assertTrue(self.validation("Cyclone Arnab struck Odisha and damaged homes")["eligible"])

    def test_flood_warning_vs_occurrence(self):
        inc = {**self.inc, "category": "flood", "event_name": None, "title": "Flood Odisha"}
        self.assertFalse(self.validation("Flood warning issued for Odisha", inc)["eligible"])
        self.assertFalse(self.validation("Floods likely in Odisha", inc)["eligible"])
        self.assertTrue(self.validation("Floods hit Odisha; homes submerged", inc)["eligible"])

    def test_unrelated_hazard(self):
        self.assertFalse(self.validation("Floods hit Odisha and damaged homes")["eligible"])
        self.assertFalse(policy.hazard_matches("Heavy rain hits Odisha", "flood"))
        self.assertFalse(policy.hazard_matches("Bridge inaugurated in Odisha", "infrastructure_damage"))

    def test_unknown_missing_and_future_dates(self):
        for title, pub in [("Cyclone Arnab in Odisha", self.now), ("Cyclone Arnab struck Odisha", None),
                           ("Cyclone Arnab struck Odisha", self.now + timedelta(days=1))]:
            self.assertFalse(self.validation(title, pub=pub)["eligible"])

    def test_historical_out_of_window_negation(self):
        self.assertFalse(self.validation("Cyclone Arnab struck Odisha", pub=self.now-timedelta(days=40))["eligible"])
        self.assertFalse(self.validation("Cyclone Arnab struck Odisha", window=False)["eligible"])
        self.assertFalse(self.validation("No cyclone Arnab landfall in Odisha")["eligible"])

    def test_named_storm_mismatch_and_unknown_name(self):
        for title in ["Cyclone Dana struck Odisha", "Cyclone struck Odisha"]:
            self.assertFalse(self.validation(title)["eligible"])
        self.assertEqual(policy.storm_name("Cyclone ‘Arnab’ struck Odisha"), "arnab")
        self.assertIsNone(policy.storm_name("Cyclone warnings in Odisha"))

    def test_google_news_connector_classifies_occurrence(self):
        for title, expected in [("Cyclone Arnab warning in Odisha", "neutral"),
                                ("Cyclone Arnab struck Odisha", "supports"), ("Floods hit Odisha", "neutral")]:
            with self.subTest(title=title), patch.object(utils, "cached_get", return_value=self.fetch_result(self.rss(title))), patch.object(utils, "resolve_india", side_effect=self.geo):
                e = external.google_news(self.inc, 20)[0]
                self.assertEqual(e["stance"], expected)
                self.assertEqual(e["retrieved"], utils.iso(self.now))
                self.assertEqual(e["publication_time"], utils.iso(self.now))
                self.assertEqual(e["source_url"], "https://news.example/item")

    def test_sachet_broad_met_category_cannot_confirm_cyclone(self):
        with patch.object(utils, "cached_get", return_value=self.fetch_result(self.rss("River flood continues to flow in Odisha"))), patch.object(utils, "resolve_india", side_effect=self.geo):
            self.assertEqual(external.sachet(self.inc, 20)[0]["stance"], "neutral")

    def test_sachet_actual_flood_and_flood_alert(self):
        inc = {**self.inc, "category": "flood", "event_name": None, "title": "Flood Odisha"}
        for title, stance in [("River flood continues to flow in Odisha", "supports"), ("Flood warning for Odisha", "neutral")]:
            with patch.object(utils, "cached_get", return_value=self.fetch_result(self.rss(title))), patch.object(utils, "resolve_india", side_effect=self.geo):
                self.assertEqual(external.sachet(inc, 20)[0]["stance"], stance)

    def test_gdelt_validates_hazard_name_and_place(self):
        for title in ["Cyclone Dana struck Odisha", "Floods hit Odisha", "Cyclone Arnab struck Gujarat", "Cyclone Arnab expected in Odisha"]:
            body = json.dumps({"articles": [{"title": title, "domain": "news.example", "seendate": utils.iso(self.now)}]})
            with patch.object(utils, "cached_get", return_value=self.fetch_result(body)), patch.object(utils, "resolve_india", side_effect=self.geo):
                self.assertEqual(external.gdelt(self.inc, 20)[0]["stance"], "neutral")

    def test_stale_news_never_supports(self):
        with patch.object(utils, "cached_get", return_value=self.fetch_result(self.rss("Cyclone Arnab struck Odisha"), stale=True)), patch.object(utils, "resolve_india", side_effect=self.geo):
            e = external.google_news(self.inc, 20)[0]
            self.assertEqual(e["stance"], "neutral")
            self.assertIn("stale_retrieval", e["validation"]["excluded_reasons"])

    def test_weather_context_never_confirms_occurrence(self):
        result = self.fetch_result(json.dumps({"daily": {"time": ["2026-10-31"], "precipitation_sum": [150]}}))
        with patch.object(utils, "cached_get", return_value=result):
            self.assertEqual(external.open_meteo(self.inc, 20)[0]["stance"], "neutral")

    def test_bhutan_country_cannot_be_indian_namesake(self):
        body = json.dumps([{"lat": "27.5", "lon": "90.5", "address": {"country_code": "bt"}, "type": "country"}])
        with patch.object(utils, "cached_get", return_value=self.fetch_result(body)), patch.object(utils, "geocode", side_effect=AssertionError("India fallback forbidden")):
            self.assertEqual(utils.resolve_india("Bhutan")["state"], "outside_india")
            ids, provenance = chatbot.investigate(chatbot.parse("Flooding in Bhutan"))
            self.assertEqual(ids, [])
            self.assertIn("out_of_scope", provenance)
            self.assertEqual(db.query("SELECT * FROM incidents"), [])

    def test_country_validation_fails_closed(self):
        for res in [utils.FetchResult(False, error="offline"), self.fetch_result("[]"), self.fetch_result('[{"address": {}}]'),
                    self.fetch_result("not json"), self.fetch_result('[{"address":{"country_code":"in"}}]', stale=True)]:
            with patch.object(utils, "cached_get", return_value=res):
                self.assertIsNone(utils.resolve_india("Bhutan")["geo"])

    def test_valid_indian_geography_does_not_use_restricted_cache(self):
        body = json.dumps([{"lat": "20.5", "lon": "84.6", "display_name": "Odisha, India", "address": {"country_code": "in", "state": "Odisha"}}])
        with patch.object(utils, "cached_get", return_value=self.fetch_result(body)), patch.object(utils, "geocode", side_effect=AssertionError("legacy cache forbidden")):
            self.assertEqual(utils.resolve_india("Odisha")["geo"]["country_code"], "in")

    def test_geographic_namesakes_and_wrong_coordinates(self):
        with patch.object(utils, "resolve_india", side_effect=self.geo):
            self.assertFalse(external.geographic_match({**self.inc, "place_name": "Bhutan"}, "Floods hit Bhutan"))
            self.assertFalse(external.geographic_match(self.inc, "Cyclone Arnab struck Odisha, Canada"))
            self.assertFalse(external.geographic_match(self.inc, "Cyclone Arnab struck Odisha, Canada - Publisher"))
            self.assertFalse(external.geographic_match(self.inc, "Cyclone Arnab struck Odisha in Canada - Publisher"))
            self.assertFalse(external.geographic_match({**self.inc, "center_lat": 33}, "Cyclone Arnab struck Odisha"))
            self.assertFalse(external.geographic_match(self.inc, "Cyclone Arnab struck Gujarat"))
            self.assertTrue(external.geographic_match(self.inc, "Cyclone Arnab struck Odisha"))

    def test_named_candidate_requires_positive_identity(self):
        self.insert_incident(event_name="dana", title="Cyclone Dana")
        p = chatbot.parse("Cyclone Arnab in Odisha")
        self.assertEqual(chatbot.candidates(p, self.geo("Odisha")["geo"]), [])
        db.execute("UPDATE incidents SET event_name='arnab',title='Cyclone Arnab' WHERE id=1")
        self.assertEqual([i["id"] for i in chatbot.candidates(p, self.geo("Odisha")["geo"])], [1])

    def test_typhoon_has_category_and_missing_category_is_safe(self):
        self.assertEqual(chatbot.parse("Typhoon Haiyan in India")["category"], "cyclone_storm")
        p = {**chatbot.parse("Cyclone Arnab in Odisha"), "category": None}
        self.assertIn("category_required", chatbot._answer(p)["provenance"])
        self.assertEqual(chatbot.investigate(p)[0], [])

    def test_syndicated_titles_across_connectors_count_once(self):
        a = self.evidence("Cyclone Arnab struck Odisha - Publisher A")
        b = self.evidence("Cyclone Arnab struck Odisha - Publisher B", "Publisher B")
        b["source_name"] = "GDELT DOC 2.0"
        self.assertEqual(len(policy.supporting_items(self.inc, [a, b])), 1)

    def test_duplicate_urls_and_normalized_publishers(self):
        a = self.evidence("Cyclone Arnab struck Odisha", "The Hindu", url="https://example.org/a?utm_source=one")
        b = self.evidence("Cyclone Arnab damaged homes in Odisha", "thehindu.com", url="https://www.example.org/a?utm_source=two")
        self.assertEqual(len(policy.supporting_items(self.inc, [a, b])), 1)
        self.assertEqual(policy.publisher_key("The Hindu"), policy.publisher_key("thehindu.com"))

    def test_unknown_publisher_and_legacy_support_excluded(self):
        a = self.evidence("Cyclone Arnab struck Odisha", "unknown publisher")
        b = self.evidence("Cyclone Arnab struck Odisha"); b.pop("validation_json")
        self.assertEqual(policy.supporting_items(self.inc, [a, b]), [])

    def test_fusion_warning_and_wrong_hazard_cannot_corroborate(self):
        self.insert_incident()
        for title, pub in [("Cyclone Arnab warning in Odisha", "A"), ("Floods hit Odisha", "B")]:
            self.insert_evidence(self.evidence(title, pub))
        assessment, _ = fusion.assess_incident(1)
        self.assertEqual(assessment["status"], "UNVERIFIED")
        self.assertEqual(assessment["independent_groups"], [])

    def test_fusion_insufficient_syndicated_evidence(self):
        self.insert_incident()
        for pub in ["Publisher A", "Publisher B"]:
            self.insert_evidence(self.evidence(f"Cyclone Arnab struck Odisha - {pub}", pub))
        assessment, _ = fusion.assess_incident(1)
        self.assertEqual(assessment["status"], "UNVERIFIED")
        self.assertEqual(len(assessment["independent_groups"]), 1)

    def test_two_independent_actual_sources_preserve_working_rule(self):
        self.insert_incident()
        self.insert_evidence(self.evidence("Cyclone Arnab struck Odisha", "Publisher A"))
        self.insert_evidence(self.evidence("Cyclone Arnab damaged homes in Odisha", "Publisher B"))
        assessment, _ = fusion.assess_incident(1)
        self.assertEqual(assessment["status"], "CORROBORATED")
        self.assertTrue(assessment["rules"]["R4_two_independent_external"])
        self.assertEqual(assessment["evidence_policy"], policy.VERSION)

    def test_changed_incident_identity_invalidates_support(self):
        e = self.evidence("Cyclone Arnab struck Odisha")
        self.assertEqual(policy.supporting_items({**self.inc, "event_name": "dana"}, [e]), [])

    def test_later_neutral_snapshot_supersedes_support_without_deletion(self):
        first = {**self.evidence("Cyclone Arnab struck Odisha"), "retrieval_time": utils.iso(self.now)}
        later = {**first, "stance": "neutral", "retrieval_time": utils.iso(self.now+timedelta(minutes=1))}
        self.assertEqual(policy.supporting_items(self.inc, [later, first]), [])

    def test_full_named_query_creates_separate_event_and_caches_safely(self):
        self.insert_incident(event_name="dana", title="Cyclone Dana")
        with patch.object(utils, "resolve_india", side_effect=self.geo), patch.object(external, "SOURCES", []), patch.object(chatbot, "live_search", return_value={"items": [], "ok": True, "error": None}):
            result = chatbot.answer("Cyclone Arnab in Odisha")
            named = db.one("SELECT * FROM incidents WHERE event_name='arnab'")
            self.assertIsNotNone(named)
            self.assertEqual(named["country_code"], "in")
            self.assertEqual(named["status"], "UNVERIFIED")
            self.assertNotEqual(named["id"], 1)
            again = chatbot.answer("Cyclone Arnab in Odisha")
            self.assertEqual(again["provenance"]["query_cache"], "hit")
            self.assertEqual([i["id"] for i in again["incidents"]], [named["id"]])

    def test_full_missing_category_request_has_no_live_or_database_write(self):
        p = {**chatbot.parse("Cyclone Arnab in Odisha"), "category": None}
        with patch.object(chatbot, "parse", return_value=p), patch.object(chatbot, "live_search", side_effect=AssertionError("clarify first")):
            result = chatbot.answer("unknown category")
            self.assertIn("specify", result["summary"]["headline"])
            self.assertEqual(db.query("SELECT * FROM incidents"), [])

    def test_cached_incident_ids_are_revalidated_for_storm_identity(self):
        inc = self.insert_incident(event_name="dana", title="Cyclone Dana")
        p = chatbot.parse("Cyclone Arnab in Odisha")
        now = utils.iso(self.now)
        db.execute("INSERT INTO query_cache(norm_key,incident_ids,created_at,refreshed_at,expires_at) VALUES(?,?,?,?,?)",
                   (p["norm_key"], "[1]", now, now, utils.iso(self.now+timedelta(minutes=15))))
        with patch.object(utils, "resolve_india", side_effect=self.geo):
            self.assertEqual(chatbot._answer(p)["incidents"], [])

    def test_legacy_corroboration_cannot_promote_new_report(self):
        self.insert_incident(status="CORROBORATED", assessment_json=json.dumps({"status": "CORROBORATED"}))
        now = utils.iso(self.now)
        db.execute("INSERT INTO reports(category,incident_id,location_confidence,upload_time,updated_at) VALUES('cyclone_storm',1,'high',?,?)", (now, now))
        self.assertEqual(fusion.assess_report(1)["status"], "UNVERIFIED")

    def test_connector_keeps_syndicated_publisher_provenance(self):
        first = self.rss("Cyclone Arnab struck Odisha", "Publisher A")
        second = self.rss("Cyclone Arnab struck Odisha", "Publisher B")
        body = first.replace("</channel>", second.split("<channel>")[1].split("</channel>")[0] + "</channel>")
        with patch.object(utils, "cached_get", return_value=self.fetch_result(body)), patch.object(utils, "resolve_india", side_effect=self.geo):
            records = external.google_news(self.inc, 20)
            self.assertEqual({r["publisher"] for r in records}, {"Publisher A", "Publisher B"})

    def test_crowd_consensus_excludes_warning_forecast_and_foreign_place(self):
        self.insert_incident()
        now = utils.iso(self.now)
        for description in ["Cyclone Arnab warning for Odisha", "Cyclone Arnab expected to hit Odisha", "Odisha braces as Cyclone Arnab hits"]:
            db.execute("INSERT INTO reports(category,description,incident_id,location_confidence,processing_state,upload_time,updated_at) "
                       "VALUES('cyclone_storm',?,1,'high','PROCESSED',?,?)", (description, now, now))
        with patch.object(utils, "resolve_india", side_effect=self.geo):
            self.assertEqual(fusion.assess_incident(1)[0]["status"], "UNVERIFIED")
            db.execute("UPDATE reports SET description='Cyclone Arnab struck Odisha'")
            self.assertEqual(fusion.assess_incident(1)[0]["status"], "CORROBORATED")
        with patch.object(utils, "resolve_india", return_value={"geo": None}):
            self.assertEqual(fusion.assess_incident(1)[0]["status"], "UNVERIFIED")

    def test_report_clustering_requires_storm_identity(self):
        self.insert_incident(event_name="dana", title="Cyclone Dana")
        report = {"category": "cyclone_storm", "description": "Cyclone Arnab struck Odisha", "claimed_location_text": "Odisha",
                  "claimed_lat": 20.5, "claimed_lon": 84.6, "upload_time": utils.iso(self.now)}
        iid, _ = clustering.find_or_create_incident(report, [])
        self.assertNotEqual(iid, 1)
        self.assertEqual(db.one("SELECT event_name FROM incidents WHERE id=?", (iid,))["event_name"], "arnab")

    def test_report_clustering_does_not_mutate_legacy_incident(self):
        self.insert_incident(assessment_json=json.dumps({"status": "CORROBORATED"}))
        before = db.one("SELECT * FROM incidents WHERE id=1")
        report = {"category": "cyclone_storm", "description": "Cyclone Arnab struck Odisha", "claimed_location_text": "Odisha",
                  "claimed_lat": 20.5, "claimed_lon": 84.6, "upload_time": utils.iso(self.now)}
        self.assertNotEqual(clustering.find_or_create_incident(report, [])[0], 1)
        self.assertEqual(db.one("SELECT * FROM incidents WHERE id=1"), before)

    def test_legacy_assessment_and_history_remain_unchanged(self):
        self.insert_incident(status="CORROBORATED", assessment_json=json.dumps({"status": "CORROBORATED", "score": 55}), confidence_score=55)
        before = db.query("SELECT * FROM incidents")
        assessment, changed = fusion.assess_incident(1)
        pipeline.cascade_incident(1, "background refresh")
        self.assertTrue(assessment["review_required"])
        self.assertFalse(changed)
        self.assertEqual(db.query("SELECT * FROM incidents"), before)
        self.assertEqual(db.query("SELECT * FROM assessments"), [])

    def test_historical_assessment_held_even_under_current_policy(self):
        self.insert_incident(event_time_end=utils.iso(self.now-timedelta(days=60)), status="UNVERIFIED",
                             assessment_json=json.dumps({"status": "UNVERIFIED", "evidence_policy": policy.VERSION}))
        self.assertTrue(fusion.assess_incident(1)[0]["review_required"])

    def test_historical_incident_without_assessment_is_not_automatically_scored(self):
        self.insert_incident(event_time_end=utils.iso(self.now-timedelta(days=60)))
        before = db.one("SELECT * FROM incidents WHERE id=1")
        self.assertTrue(fusion.assess_incident(1)[0]["review_required"])
        self.assertEqual(db.one("SELECT * FROM incidents WHERE id=1"), before)

    def test_append_only_evidence_and_original_timestamps(self):
        self.insert_incident()
        title = "Cyclone Arnab struck Odisha"
        r = external._rec("Google News RSS", "news", "supports", title, pub=utils.iso(self.now),
                          retrieved=utils.iso(self.now), publisher="Publisher A", dedupe="old-key")
        r["validation"] = self.validation(title)
        with patch.object(external, "SOURCES", [("test", lambda inc, ttl: [r])]):
            external.gather(1, force=True)
            before = db.query("SELECT * FROM evidence")
            r["retrieved"] = utils.iso(self.now+timedelta(minutes=1))
            r["stance"] = "neutral"
            external.gather(1, force=True)
            self.assertEqual(db.query("SELECT * FROM evidence WHERE id=?", (before[0]["id"],)), before)
            self.assertEqual(len(db.query("SELECT * FROM evidence")), 2)
            external.gather(1, force=True)
            self.assertEqual(len(db.query("SELECT * FROM evidence")), 2)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        TEMP.cleanup()
