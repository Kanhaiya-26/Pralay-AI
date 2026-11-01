"""Build the read-only snapshot used by the GitHub Pages STATIC SHOWCASE.

Starts its OWN isolated server (temp data dir, port 8002), seeds ONLY the 8 SYNTHETIC DEMO reports, waits for
processing, then exports the GET API responses + synthetic media to frontend/showcase-public/snapshot/.
Safety: aborts if any non-demo report exists, if any response contains a local filesystem path, or if any media file
does not belong to a demo report. The local demo database (backend/data) is never read.

    .venv\\Scripts\\python.exe scripts\\export_showcase.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_tests as rt  # noqa: E402

PORT = 8002
API = f"http://127.0.0.1:{PORT}"
OUT = HERE.parent.parent / "frontend" / "showcase-public" / "snapshot"
PATH_LEAK = re.compile(r"[A-Za-z]:\\\\|\\\\Users\\\\|/home/|/Users/|AppData")


def main():
    data = Path(tempfile.mkdtemp(prefix="pralay_showcase_"))
    p = rt.start(data, PORT, {"VISION_PROVIDER": "none", "MASTODON_POLL_MIN": "0"})
    try:
        subprocess.run([sys.executable, str(HERE / "seed_demo.py"), API], cwd=HERE.parent, check=True)
        c = httpx.Client(base_url=API, timeout=120)
        t0 = time.time()
        while time.time() - t0 < 300:
            s = c.get("/api/summary").json()["reports"]
            if s["awaiting"] == 0 and s["processing"] == 0 and s["processed"] >= 8:
                break
            time.sleep(3)
        reports = c.get("/api/reports").json()
        if any(not r["is_demo"] for r in reports):
            raise SystemExit("ABORT: non-demo report present — refusing to publish")
        if OUT.exists():
            shutil.rmtree(OUT)
        (OUT / "files").mkdir(parents=True)
        out = {}

        def grab(path, key):
            out[key] = c.get(path).json()

        for path, key in [("/api/meta", "meta"), ("/api/summary", "summary"), ("/api/incidents", "incidents"), ("/api/reports", "reports"),
                          ("/api/grid/config", "grid__config"), ("/api/sources", "sources"), ("/api/vision/status", "vision__status"),
                          ("/api/connectors", "connectors"), ("/api/connectors/runs", "connectors__runs"), ("/api/archive", "archive"),
                          ("/api/pipeline", "pipeline"), ("/api/cache/stats", "cache__stats"), ("/api/processing/stats", "processing__stats")]:
            grab(path, key)
        for i in out["incidents"]:
            grab(f"/api/incidents/{i['id']}", f"incidents__{i['id']}")
        for r in reports:
            grab(f"/api/reports/{r['id']}", f"reports__{r['id']}")
        cats = [""] + out["meta"]["categories"]
        for cat in cats:
            for st in ["", "UNVERIFIED", "CORROBORATED", "CONTRADICTED"]:
                for mo in ["", "true"]:
                    q = {k: v for k, v in (("category", cat), ("status", st), ("mapped_only", mo)) if v}
                    out[f"grid__cells@category={cat}&status={st}&mapped_only={mo}"] = c.get("/api/grid/cells", params=q).json()
        for cell in out["grid__cells@category=&status=&mapped_only="]["cells"]:
            grab(f"/api/grid/cells/{cell['cell_id']}", f"grid__cells__{cell['cell_id']}")

        # Showcase metadata shown in the banner / Settings
        out["meta"]["showcase"] = {"exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "note": "Static read-only snapshot of SYNTHETIC DEMO data. External evidence was retrieved live at export time."}
        demo_ids = {r["id"] for r in reports}
        media_urls = set()
        for key, obj in out.items():
            txt = json.dumps(obj, ensure_ascii=False)
            if PATH_LEAK.search(txt):
                raise SystemExit(f"ABORT: local filesystem path found in {key}")
            media_urls |= set(re.findall(r'"(/files/(?:uploads|thumbs)/[A-Za-z0-9_.\-]+)"', txt))
            txt = txt.replace('"/files/', '"./snapshot/files/')
            (OUT / f"{quote(key, safe='')}.json").write_text(txt, encoding="utf-8")
        allowed = set()
        for r in reports:
            for m in c.get(f"/api/reports/{r['id']}").json()["media"]:
                allowed |= {m["url"], m["thumb_url"], *m["frames"]}
        for u in sorted(media_urls):
            if u not in allowed:
                raise SystemExit(f"ABORT: media {u} not owned by a demo report")
            dst = OUT / "files" / u.split("/files/")[1]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(c.get(u).content)
        size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
        print(f"exported {len(out)} JSON responses + {len(media_urls)} synthetic media files ({size / 1e6:.2f} MB) to {OUT}")
        print(f"demo report ids: {sorted(demo_ids)}")
    finally:
        rt.stop(p)
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()
