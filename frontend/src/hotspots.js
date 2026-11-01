// Report concentration only. No severity inference, invented locations or duplicate votes.
export const validPoint = (lat, lon) => Number.isFinite(lat) && Number.isFinite(lon)
  && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && !(lat === 0 && lon === 0);

function distance(a, b) {
  const radians = Math.PI / 180;
  const dlat = (b.lat - a.lat) * radians, dlon = (b.lon - a.lon) * radians;
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(a.lat * radians) * Math.cos(b.lat * radians) * Math.sin(dlon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
}

export function buildHotspots(reports, incidents, radiusKm = 5) {
  const ids = new Set(incidents.map((i) => i.id));
  const unique = [...new Map(reports.map((r) => [r.id, r])).values()];
  const eligible = unique.filter((r) => ids.has(r.incident_id) && r.is_duplicate_of == null
    && r.location_confidence !== "contradicted" && r.location_confidence !== "unresolved"
    && validPoint(r.investigated_lat, r.investigated_lon))
    .map((r) => ({ ...r, lat: r.investigated_lat, lon: r.investigated_lon })).sort((a, b) => a.id - b.id);
  // Fixed-radius neighborhoods anchored to a real report; avoids long single-link chains.
  const remaining = new Set(eligible);
  const clusters = [];
  while (remaining.size) {
    const anchor = remaining.values().next().value;
    const members = [...remaining].filter((r) => distance(anchor, r) <= radiusKm);
    members.forEach((r) => remaining.delete(r));
    if (members.length < 2) continue;
    clusters.push({ id: members.map((r) => r.id).join("-"), lat: anchor.lat, lon: anchor.lon,
      reports: members, incidentIds: [...new Set(members.map((r) => r.incident_id))], radiusKm });
  }
  return { clusters, located: eligible.length,
    duplicates: unique.filter((r) => ids.has(r.incident_id) && r.is_duplicate_of != null).length,
    unlocated: unique.filter((r) => ids.has(r.incident_id) && r.is_duplicate_of == null && !eligible.some((e) => e.id === r.id)).length };
}
