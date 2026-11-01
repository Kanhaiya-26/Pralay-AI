"""Disaster Control Grid Mapping Engine (Module 11).

CUSTOM / DEMO GRID — NOT AN OFFICIAL IMD/NDMA GRID.
No official IMD/NDMA grid-cell specification or API was available, so this engine implements a
configurable regular lat/lon grid over India. The `GridProvider` interface isolates the cell
lookup so an official grid definition (e.g. a published shapefile) can replace it later.

Eligibility criteria for mapping a report as VERIFIED grid intelligence (documented in README):
  1. Report verification status is CORROBORATED (by fusion or human review).
  2. Report has an investigated location with confidence medium/high (not unresolved/contradicted).
  3. Report is not a duplicate of an already-mapped report.
  4. Report processing has completed.
Every located report also gets a cell id for *display* (unverified/contradicted counts), but only
eligible reports are flagged grid_mapped=1.
"""
import json
import math
import os
from pathlib import Path

from . import db
from .config import GRID_BBOX, GRID_RESOLUTION_DEG

DEMO_LABEL = "CUSTOM / DEMO GRID — NOT AN OFFICIAL IMD/NDMA GRID"
LABEL = DEMO_LABEL


class RegularGridProvider:
    """Default: configurable regular lat/lon lattice. CUSTOM / DEMO — not official."""
    kind = "regular_latlon"

    def __init__(self, resolution, bbox, version):
        self.res, self.bbox, self.version = resolution, bbox, version
        self.prefix = f"DEMO{version}-"
        self.label = DEMO_LABEL

    def owns(self, cell_id):
        return bool(cell_id) and cell_id.startswith(self.prefix)

    def polygon(self, cell_id):
        la0, lo0, la1, lo1 = self.bounds(cell_id)
        return [[la0, lo0], [la0, lo1], [la1, lo1], [la1, lo0]]

    def cell_for(self, lat, lon):
        la0, lo0, la1, lo1 = self.bbox
        if lat is None or lon is None or not (la0 <= lat < la1 and lo0 <= lon < lo1):
            return None
        r, c = int((lat - la0) // self.res), int((lon - lo0) // self.res)
        return f"DEMO{self.version}-R{r:03d}C{c:03d}"

    def bounds(self, cell_id):
        r, c = int(cell_id.split("-R")[1][:3]), int(cell_id.split("C")[-1])
        la0, lo0 = self.bbox[0], self.bbox[1]
        return [la0 + r * self.res, lo0 + c * self.res, la0 + (r + 1) * self.res, lo0 + (c + 1) * self.res]


class GeoJSONGridProvider:
    """Cells loaded from an operator-supplied GeoJSON FeatureCollection of Polygons (property `cell_id`).
    This is the integration point for an official grid definition when one becomes available. PRALAY cannot
    verify a file's provenance, so the label states the file name and that officialness is NOT verified."""
    kind = "geojson_file"

    def __init__(self, path, version):
        self.path, self.version = Path(path), version
        fc = json.loads(self.path.read_text(encoding="utf-8"))
        self.cells = {}
        for f in fc.get("features", []):
            geom = f.get("geometry") or {}
            cid = str((f.get("properties") or {}).get("cell_id") or "").strip()
            if geom.get("type") != "Polygon" or not cid:
                continue
            ring = [(pt[1], pt[0]) for pt in geom["coordinates"][0]]  # GeoJSON is lon,lat
            lats, lons = [p[0] for p in ring], [p[1] for p in ring]
            self.cells[cid] = {"ring": ring, "bbox": [min(lats), min(lons), max(lats), max(lons)]}
        if not self.cells:
            raise ValueError("GeoJSON grid has no Polygon features with a cell_id property")
        lats = [c["bbox"][0] for c in self.cells.values()] + [c["bbox"][2] for c in self.cells.values()]
        lons = [c["bbox"][1] for c in self.cells.values()] + [c["bbox"][3] for c in self.cells.values()]
        self.bbox = (min(lats), min(lons), max(lats), max(lons))
        self.res = None
        self.prefix = f"FILE{version}-"
        self.label = (f"GRID FROM FILE '{self.path.name}' ({len(self.cells)} cells) — provenance not verified by PRALAY; "
                      "NOT confirmed as an official IMD/NDMA grid")

    @staticmethod
    def _inside(lat, lon, ring):
        inside = False
        j = len(ring) - 1
        for i in range(len(ring)):
            yi, xi = ring[i]
            yj, xj = ring[j]
            if (xi > lon) != (xj > lon) and lat < (yj - yi) * (lon - xi) / ((xj - xi) or 1e-12) + yi:
                inside = not inside
            j = i
        return inside

    def cell_for(self, lat, lon):
        if lat is None or lon is None:
            return None
        for cid, c in self.cells.items():
            b = c["bbox"]
            if b[0] <= lat <= b[2] and b[1] <= lon <= b[3] and self._inside(lat, lon, c["ring"]):
                return self.prefix + cid
        return None

    def owns(self, cell_id):
        return bool(cell_id) and cell_id.startswith(self.prefix) and cell_id[len(self.prefix):] in self.cells

    def bounds(self, cell_id):
        return self.cells[cell_id[len(self.prefix):]]["bbox"]

    def polygon(self, cell_id):
        return [list(p) for p in self.cells[cell_id[len(self.prefix):]]["ring"]]


def config():
    row = db.one("SELECT * FROM grid_config WHERE id=1")
    if not row:
        db.execute("INSERT INTO grid_config(id,version,resolution_deg,lat_min,lon_min,lat_max,lon_max,label,is_official,updated_at) "
                   "VALUES(1,1,?,?,?,?,?,?,0,?)", (GRID_RESOLUTION_DEG, *GRID_BBOX, LABEL, db.now_iso()))
        row = db.one("SELECT * FROM grid_config WHERE id=1")
    return row


_prov_cache = {}


def provider():
    """Grid provider selected by env GRID_PROVIDER (regular | geojson) + GRID_GEOJSON_PATH."""
    c = config()
    kind = os.getenv("GRID_PROVIDER", "regular").strip().lower()
    if kind == "geojson" and os.getenv("GRID_GEOJSON_PATH"):
        key = (os.getenv("GRID_GEOJSON_PATH"), c["version"])
        if key not in _prov_cache:
            _prov_cache.clear()
            _prov_cache[key] = GeoJSONGridProvider(key[0], c["version"])
        return _prov_cache[key]
    return RegularGridProvider(c["resolution_deg"], (c["lat_min"], c["lon_min"], c["lat_max"], c["lon_max"]), c["version"])


def describe():
    p = provider()
    return {**config(), "provider": p.kind, "label": p.label, "is_official": False, "cell_prefix": p.prefix,
            "extent": list(p.bbox), "resolution_deg": p.res if p.res else None}


def eligibility(report):
    crit = {
        "status_corroborated": (report.get("override_status") or report["verification_status"]) == "CORROBORATED",
        "location_confidence_ok": report.get("location_confidence") in ("medium", "high"),
        "not_duplicate": not report.get("is_duplicate_of"),
        "has_coordinates": report.get("investigated_lat") is not None,
    }
    return all(crit.values()), crit


def map_report(report_id):
    """Assign cell (display) and, if eligible, mark as verified grid intelligence. Returns state."""
    r = db.one("SELECT * FROM reports WHERE id=?", (report_id,))
    p = provider()
    # Display cell: where the report is investigated to be; a report whose location is CONTRADICTED is
    # shown (as contradicted) in the cell it *claims*, since that is where it would mislead responders.
    use_claim = r["investigated_lat"] is None or r["location_confidence"] == "contradicted"
    lat = r["claimed_lat"] if use_claim else r["investigated_lat"]
    lon = r["claimed_lon"] if use_claim else r["investigated_lon"]
    cell = p.cell_for(lat, lon)
    ok, crit = eligibility(r)
    ok = ok and cell is not None
    crit["inside_grid_extent"] = cell is not None
    was = bool(r["grid_mapped"])
    now = db.now_iso()
    db.execute("UPDATE reports SET grid_cell_id=?, grid_mapped=?, grid_mapped_at=?, grid_eligibility_json=?, updated_at=? WHERE id=?",
               (cell, 1 if ok else 0, now if ok and not was else (r["grid_mapped_at"] if ok else None), db.jdump(crit), now, report_id))
    if ok != was and cell:
        db.execute("INSERT INTO grid_mappings(report_id,incident_id,cell_id,grid_version,action,criteria_json,created_at) VALUES(?,?,?,?,?,?,?)",
                   (report_id, r["incident_id"], cell, p.version, "mapped" if ok else "unmapped", db.jdump(crit), now))
    if r["incident_id"]:
        inc = db.one("SELECT center_lat, center_lon FROM incidents WHERE id=?", (r["incident_id"],))
        db.execute("UPDATE incidents SET grid_cell_id=? WHERE id=?", (p.cell_for(inc["center_lat"], inc["center_lon"]), r["incident_id"]))
    return {"cell_id": cell, "mapped": ok, "criteria": crit}


def reconfigure(resolution):
    if provider().kind != "regular_latlon":
        raise ValueError("Resolution applies only to the regular demo grid (a GeoJSON grid is configured)")
    if not (0.05 <= resolution <= 2.0):
        raise ValueError("resolution must be between 0.05 and 2.0 degrees")
    c = config()
    db.execute("UPDATE grid_config SET version=?, resolution_deg=?, updated_at=? WHERE id=1", (c["version"] + 1, resolution, db.now_iso()))
    for r in db.query("SELECT id FROM reports WHERE processing_state IN ('PROCESSED','MAPPED')"):
        map_report(r["id"])
    for r in db.query("SELECT id FROM reports WHERE processing_state IN ('PROCESSED','MAPPED')"):
        rr = db.one("SELECT grid_mapped FROM reports WHERE id=?", (r["id"],))
        db.execute("UPDATE reports SET processing_state=? WHERE id=?", ("MAPPED" if rr["grid_mapped"] else "PROCESSED", r["id"]))
    return config()


def cells(filters):
    """Aggregate per-cell statistics with filters: category, status, since/until, bbox, mapped_only."""
    where, params = ["r.grid_cell_id IS NOT NULL"], []
    if filters.get("category"):
        where.append("r.category=?"); params.append(filters["category"])
    if filters.get("status"):
        where.append("COALESCE(r.override_status, r.verification_status)=?"); params.append(filters["status"])
    if filters.get("since"):
        where.append("r.upload_time>=?"); params.append(filters["since"])
    if filters.get("until"):
        where.append("r.upload_time<=?"); params.append(filters["until"])
    if filters.get("mapped_only"):
        where.append("r.grid_mapped=1")
    if filters.get("bbox"):
        la0, lo0, la1, lo1 = filters["bbox"]
        where.append("COALESCE(r.investigated_lat, r.claimed_lat) BETWEEN ? AND ? AND COALESCE(r.investigated_lon, r.claimed_lon) BETWEEN ? AND ?")
        params += [la0, la1, lo0, lo1]
    rows = db.query(f"SELECT r.id, r.grid_cell_id, r.category, r.incident_id, r.grid_mapped, r.upload_time, "
                    f"COALESCE(r.override_status, r.verification_status) vs FROM reports r WHERE {' AND '.join(where)}", params)
    p = provider()
    out = {}
    for r in rows:
        if not p.owns(r["grid_cell_id"]):
            continue
        c = out.setdefault(r["grid_cell_id"], {"cell_id": r["grid_cell_id"], "bounds": p.bounds(r["grid_cell_id"]),
                                              "reports": 0, "mapped_verified": 0, "CORROBORATED": 0, "UNVERIFIED": 0,
                                              "CONTRADICTED": 0, "categories": {}, "incident_ids": set(), "last_update": None})
        c["reports"] += 1
        c[r["vs"]] = c.get(r["vs"], 0) + 1
        c["mapped_verified"] += r["grid_mapped"]
        c["categories"][r["category"]] = c["categories"].get(r["category"], 0) + 1
        if r["incident_id"]:
            c["incident_ids"].add(r["incident_id"])
        c["last_update"] = max(c["last_update"] or "", r["upload_time"])
    res = []
    all_iids = sorted({i for c in out.values() for i in c["incident_ids"]})
    inc_status = {i["id"]: i["s"] for i in db.query(
        f"SELECT id, COALESCE(override_status,status) s FROM incidents WHERE id IN ({','.join('?' * len(all_iids))})", all_iids)} if all_iids else {}
    for c in out.values():
        c["incident_ids"] = sorted(c["incident_ids"])
        c["incidents"] = len(c["incident_ids"])
        c["incident_status"] = {}
        for i in c["incident_ids"]:
            st = inc_status.get(i, "UNKNOWN")
            c["incident_status"][st] = c["incident_status"].get(st, 0) + 1
        c["top_category"] = max(c["categories"], key=c["categories"].get) if c["categories"] else None
        c["polygon"] = p.polygon(c["cell_id"])
        c["grid_label"] = p.label
        c["cell_status"] = "VERIFIED" if c["mapped_verified"] else ("CONTRADICTED_ONLY" if c["CONTRADICTED"] == c["reports"] else "UNVERIFIED")
        res.append(c)
    res.sort(key=lambda c: (-c["mapped_verified"], -c["reports"]))
    return res


def cell_detail(cell_id):
    p = provider()
    reports = db.query("SELECT id, category, description, claimed_location_text, incident_id, grid_mapped, location_confidence, "
                       "COALESCE(override_status, verification_status) verification_status, processing_state, upload_time, report_confidence "
                       "FROM reports WHERE grid_cell_id=? ORDER BY upload_time DESC", (cell_id,))
    for r in reports:  # never a made-up score: say why it is missing
        r["confidence_unavailable_reason"] = None if r["report_confidence"] is not None else (
            f"not yet assessed (processing state {r['processing_state']})" if r["processing_state"] not in ("PROCESSED", "MAPPED")
            else "no assessment recorded for this report")
    iids = sorted({r["incident_id"] for r in reports if r["incident_id"]})
    incidents = db.query(f"SELECT id, title, category, COALESCE(override_status,status) status, confidence_score, last_activity_at "
                         f"FROM incidents WHERE id IN ({','.join('?' * len(iids))})", iids) if iids else []
    history = db.query("SELECT * FROM grid_mappings WHERE cell_id=? ORDER BY id DESC LIMIT 50", (cell_id,))
    return {"cell_id": cell_id, "bounds": p.bounds(cell_id) if p.owns(cell_id) else None,
            "polygon": p.polygon(cell_id) if p.owns(cell_id) else None,
            "current_grid_version": p.owns(cell_id), "grid_label": p.label, "reports": reports, "incidents": incidents,
            "mapping_history": history}


def lattice(bbox):
    """Cell boundaries within a viewport (for overlay drawing), capped for performance."""
    p = provider()
    if p.res is None:
        return {"lat_lines": [], "lon_lines": [], "note": "Polygon grid: cells drawn individually"}
    la0, lo0, la1, lo1 = bbox
    res = p.res
    g = p.bbox
    la0, lo0 = max(la0, g[0]), max(lo0, g[1])
    la1, lo1 = min(la1, g[2]), min(lo1, g[3])
    if la1 <= la0 or lo1 <= lo0:
        return {"lat_lines": [], "lon_lines": []}
    if (la1 - la0) / res * (lo1 - lo0) / res > 6000:
        return {"lat_lines": [], "lon_lines": [], "note": "Zoom in to see grid lines"}
    lat_lines = [g[0] + i * res for i in range(math.floor((la0 - g[0]) / res), math.ceil((la1 - g[0]) / res) + 1)]
    lon_lines = [g[1] + i * res for i in range(math.floor((lo0 - g[1]) / res), math.ceil((lo1 - g[1]) / res) + 1)]
    return {"lat_lines": lat_lines, "lon_lines": lon_lines, "resolution": res}
