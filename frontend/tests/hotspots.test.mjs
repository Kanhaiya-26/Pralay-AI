import assert from "node:assert/strict";
import { buildHotspots, validPoint } from "../src/hotspots.js";

const incidents = [{ id: 1 }, { id: 2 }];
const r = (id, lat = 30.7, lon = 76.7, extra = {}) => ({ id, incident_id: 1,
  investigated_lat: lat, investigated_lon: lon, location_confidence: "high", is_duplicate_of: null, ...extra });
let count = 0;
function check(name, fn) { fn(); count++; console.log("PASS", name); }
check("nearby reports share one hotspot but retain unique incident identities", () => {
  const h = buildHotspots([r(1), r(2, 30.701), r(3, 30.702, 76.7, { incident_id: 2 })], incidents);
  assert.equal(h.clusters.length, 1); assert.equal(h.clusters[0].reports.length, 3);
  assert.deepEqual(h.clusters[0].incidentIds, [1, 2]);
  assert.equal(h.clusters[0].lat, 30.7); // anchor is an actual stored location
});
check("duplicate copies never add density or extra incidents", () => {
  const h = buildHotspots([r(1), r(2, 30.7, 76.7, { is_duplicate_of: 1 })], incidents);
  assert.equal(h.clusters.length, 0); assert.equal(h.located, 1); assert.equal(h.duplicates, 1);
});
check("repeated API rows count once", () => assert.equal(buildHotspots([r(1), r(1)], incidents).located, 1));
check("missing, invalid, placeholder and uncertain coordinates are excluded", () => {
  for (const [lat, lon] of [[null, 76], [30, null], [NaN, 76], [30, Infinity], [91, 76], [30, 181], [0, 0], ["30", 76]]) {
    assert.equal(validPoint(lat, lon), false);
    assert.equal(buildHotspots([r(1, lat, lon)], incidents).located, 0);
  }
  for (const confidence of ["contradicted", "unresolved"]) assert.equal(buildHotspots([r(1, 30, 76, { location_confidence: confidence })], incidents).located, 0);
});
check("far-apart points and single reports create no hotspots", () => assert.equal(buildHotspots([r(1), r(2, 32, 78)], incidents).clusters.length, 0));
check("filtered-out incidents cannot contribute reports", () => assert.equal(buildHotspots([r(1), r(2, 30.7, 76.7, { incident_id: 2 })], [{ id: 1 }]).clusters.length, 0));
check("report status never becomes an inferred severity score", () => {
  const h = buildHotspots([r(1, 30.7, 76.7, { effective_status: "UNVERIFIED" }), r(2, 30.7, 76.7, { effective_status: "CONTRADICTED" })], incidents);
  assert.equal(h.clusters.length, 1); assert.equal("severity" in h.clusters[0], false);
});
check("empty data is safe", () => assert.equal(buildHotspots([], []).clusters.length, 0));
console.log(`\n${count}/${count} hotspot checks passed`);
