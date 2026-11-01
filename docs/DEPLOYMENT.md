# PRALAY AI — Deployment Guide

| Target | Status |
|---|---|
| **A. Local Windows** | **Working and verified.** Clean-clone first-run setup tested; start, duplicate guard, stop and port-conflict handling tested. |
| **B. GitHub repository** | **Prepared, not pushed.** `.gitignore` and secret/large-file scan done. Pushing needs the owner's approval and git. |
| **C. GitHub Pages** | **Static showcase built and browser-tested locally** under a `/PRALAY-AI/` subpath. The workflow has not been executed on GitHub yet. |
| **D. Public cloud** | **Documented only.** Nothing deployed and no paid infrastructure. |
| **E. Hybrid** | **Design only.** The secure relay described below is *not implemented*. |

---

## A. Local Windows deployment (the real application)

**Prerequisites:**
* Windows 10/11
* Python 3.11+ (tick "Add to PATH")
* Node.js 18+ (only needed to build the UI)
* Internet on first run (packages) and for live evidence sources
* Optional: [Ollama](https://ollama.com) for free, local visual AI

```powershell
# from the project folder
.\start.ps1                 # first run: creates backend\.venv, installs packages, builds the UI, creates backend\.env
.\start.ps1 -NoBrowser      # don't open the browser
.\start.ps1 -Port 8010      # other port
.\start.ps1 -Foreground     # attached to this window (Ctrl+C stops it) - useful for debugging
.\start.ps1 -Rebuild        # force a UI rebuild
.\stop.ps1                  # stop the server (data in backend\data is kept)
```

If scripts are blocked: `powershell -ExecutionPolicy Bypass -File .\start.ps1`.

**What `start.ps1` does:**
1. Refuses to start a duplicate. If PRALAY is already on the port, it just opens it; if another program holds the port, it explains.
2. Checks Python and the venv.
3. Installs missing backend packages.
4. Creates `backend\.env` from `.env.example` if absent (never overwrites it).
5. Rebuilds the UI if it is missing or stale.
6. Starts the server **bound to 127.0.0.1 only**, in the background, with logs in `logs\`.
7. Waits for `/api/health`, then verifies the UI.
8. Reports the visual-AI state and database contents.

**Visual AI (optional, free, local):** install Ollama, run `ollama pull qwen2.5vl:3b`, and keep `VISION_PROVIDER=ollama` and `VISION_MODE=deferred` (defaults).
* Reports never wait for the model. Analysis runs one image at a time in the background.
* It is postponed while free RAM is below `VISION_MIN_FREE_MB` (600 MB), and the model is unloaded after `OLLAMA_KEEP_ALIVE` (60 s).
* If Ollama is stopped, everything else works and the AI shows "unavailable".

**Demo data:**
```powershell
.\stop.ps1; Remove-Item -Recurse backend\data; .\start.ps1 -NoBrowser
backend\.venv\Scripts\python.exe backend\scripts\seed_demo.py        # 8 SYNTHETIC DEMO reports
```

## B. Publishing to GitHub (owner action required)

Git is not installed on the development machine, and no remote was configured, so nothing has been pushed.

1. Install git (`winget install Git.Git`) and sign in to GitHub. GitHub CLI is optional: `winget install GitHub.cli`, then `gh auth login`.
2. Choose a license. **None is included until the owner decides.** For example, MIT allows broad reuse; GPL-3.0 requires derivatives to stay open.
3. Create the repository on github.com (choose **private** or **public**; visibility is the owner's decision), then:

```powershell
cd <path-to>\PRALAY-AI
git init -b main
git add .
git status                      # check: no backend/.env, backend/data, .venv, node_modules, dist, logs, run, *.db, model weights
git commit -m "PRALAY AI - GRAVITON 2026 PS#06"
git remote add origin https://github.com/<OWNER>/<REPO>.git
git push -u origin main
```

**What is excluded (`.gitignore`):**
* `.env` files
* `backend/data/` (database, uploads, thumbnails)
* `*.db` / `*.sqlite`
* logs and `run/`
* virtual envs and `__pycache__`
* `node_modules`, `dist`, `dist-showcase`
* model weights (`*.gguf`, `*.safetensors`, `*.bin`, `models/`, `.ollama/`)
* test artefacts

**Scan result:** 66 source files (≈1 MB) plus the synthetic showcase snapshot; no file over 1 MB; no secrets or credentials found.

**CI:** `.github/workflows/ci.yml` installs, builds both UIs, starts the backend **offline** (no external lookups, AI disabled) and runs `backend/scripts/smoke_test.py`. The smoke test passes locally (8/8); the workflow itself only runs once the code is on GitHub.

## C. GitHub Pages — static showcase only

GitHub Pages serves static files. It **cannot** run the FastAPI backend, background workers, SQLite or Ollama. Publishing the frontend therefore does **not** deploy PRALAY AI.

**What gets published:**
* A read-only **STATIC SHOWCASE**: the React UI plus a snapshot of the **8 SYNTHETIC DEMO reports** (exported from an isolated, freshly seeded server; external evidence as retrieved at export time).
* A permanent banner says so.
* Uploads, chat, reviews and refreshes are refused with an explanation.
* **No backend URL is embedded.** It is browser-tested to make no `/api` or `localhost` requests; only OpenStreetMap tiles are fetched.

**Steps:**
```powershell
cd backend
.venv\Scripts\python.exe scripts\export_showcase.py   # refresh frontend/showcase-public/snapshot (aborts on any non-demo data or local path)
cd ..\frontend; npm run build:showcase                # -> frontend/dist-showcase (relative base, works under /<repo>/)
cd ..\backend; .venv\Scripts\python.exe scripts\showcase_test.py   # browser test from a /PRALAY-AI/ subpath
```

Then commit `frontend/showcase-public/` and push. In the repo, go to **Settings → Pages → Source: GitHub Actions**. `.github/workflows/pages.yml` builds and deploys on pushes to `frontend/**`.

## D. Future public cloud deployment (documentation only — nothing deployed)

| Concern | Requirement |
|---|---|
| Backend | Python 3.12 container or VM running `uvicorn app.main:app` behind a TLS reverse proxy (Caddy or nginx). It **must not** be exposed without authentication (see below). Low cost or free tiers are possible, but nothing paid is configured here. |
| Frontend | Build `npm run build` and serve it from the backend (as locally) or from any static host. If hosted separately, add its origin to CORS (`app/main.py` currently allows only `localhost:5173`). |
| Database | SQLite is fine for a single instance with a persistent disk. Several instances would need **PostgreSQL (+ optional PostGIS)**, an *optional future migration* that is **not implemented**. All SQL goes through `app/db.py`, which keeps that change contained. |
| Files | `backend/data/uploads` and `thumbs` need persistent storage (a volume or an object store). Uploads contain citizen media, so define retention and deletion policies. |
| Workers | Workers run as threads inside the API process (DB-backed queue, leases, retries). Scaling out would need a single worker process or a real queue (e.g. a Redis/Celery-style design, **not implemented**). |
| Visual AI | Ollama on the same host or private network (`VISION_ALLOW_LAN=true` for a private IP). CPU inference needs roughly 3–4 GB RAM per model and minutes per image on small VMs. **No cloud AI provider exists in the code.** |
| Environment | Everything in `backend/.env.example`. Set `PRALAY_DATA_DIR` to the persistent volume. Never set `PRALAY_TEST_MODE`. |
| Auth / access | **Not implemented.** There is no login and every API endpoint is open, which is acceptable only on 127.0.0.1. A public deployment needs authentication (e.g. OIDC via the reverse proxy), role separation (citizen submit vs analyst review/override), rate limiting and upload quotas. |
| API security | Already in place: magic-byte validation, size and pixel limits, UUID storage names, SSRF guard on third-party fetches, escaped map tooltips, loopback/LAN-only AI endpoint, test hooks disabled unless `PRALAY_TEST_MODE=1`. To add: auth, rate limits, request-size limits at the proxy, and generic 500 messages (the current handler returns the exception text). |
| Logging / monitoring | Uvicorn logs go to stdout. Add structured logs, health checks (`/api/health`) and alerts on `source_status` failures and `FAILED_NEEDS_REVIEW` counts. |
| Resources | Image processing is light (Pillow/NumPy; OpenCV for video): 1 vCPU / 1–2 GB RAM is enough without AI. Add 4+ GB RAM for local AI. |

## E. Future hybrid deployment (design only — not implemented)

Goal: a public frontend, a hosted backend, and **visual AI staying on the operator's own machine**.

```
Browser ──HTTPS──► public frontend (static) ──HTTPS + auth──► hosted backend (API, DB, workers)
                                                                   ▲
            operator's machine: "vision agent" ── outbound HTTPS ──┘   (polls for pending images, posts observations)
                    └── local Ollama on 127.0.0.1 (never exposed)
```

* **Ollama is never exposed to a network.** A small *vision agent* on the operator's machine would make **outbound** authenticated requests to the backend: fetch pending images, analyse them locally, return sanitized observations.
* This keeps the existing rule that media is only sent to a loopback model.
* It needs:
  * an agent token and an authenticated endpoint pair (`GET /api/vision/jobs`, `POST /api/vision/jobs/{id}`)
  * the agent itself
  * TLS end to end
  * job leases (the same pattern as the report queue)
* **None of this exists yet.** Today, visual AI only works when the backend and Ollama run on the same machine.
* **Do not** tunnel or port-forward `127.0.0.1:11434`. That would expose an unauthenticated model server and bypass PRALAY's local-only design.
