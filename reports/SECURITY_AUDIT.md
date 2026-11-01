# PRALAY AI — Security & Privacy Audit

Date: 2026-10-31.

**Method:**
* A code review of the backend and frontend.
* A reproducible test suite, `backend/scripts/e2e_security.py` (run with `run_tests.py --suite security`). It runs against a production-like server: test hooks **off**, a 2 MB upload limit, and the vision endpoint deliberately set to a **public** IP.
* The suite includes a real-browser XSS check (Playwright + Microsoft Edge).

**Result: 24/24 PASS.** Earlier I described this suite as having 22 checks; it has 24.

## Issues found and fixed

| # | Issue | Severity | Root cause | Fix | Verified by |
|---|---|---|---|---|---|
| 1 | **Stored XSS** in map tooltips | High (local) | Leaflet tooltips are HTML strings. The incident title comes from the reporter's free-text *claimed location* and was interpolated unescaped. | `esc()` applied to every interpolated tooltip value (`MapView.jsx`) | Browser test: payload `<img src=x onerror=…>` renders as literal text in the opened tooltip; no execution anywhere |
| 2 | **SSRF** via the Mastodon connector | Medium | Third-party media URLs (and their redirects) and the user-supplied `instance` were fetched without host checks; could reach loopback, LAN or cloud-metadata addresses. | `utils.public_https_host` + `utils.safe_fetch`: https only; every DNS result must be public; redirects re-validated per hop; streamed size cap | Loopback and 169.254.169.254 instances refused; unit checks for 127.0.0.1, localhost (::1), 10.x, http:// |
| 3 | Incident markers covered by grid cells (found while testing #1) | Functional | Same canvas renderer; the cell layer is re-drawn on each refresh, so it ended up on top and swallowed hover and click | Separate Leaflet panes and canvas renderers (markers z = 450 > cells z = 410) | Tooltip opens on hover over a marker inside a cell |
| 4 | UTF-8 corruption in user-visible strings | Low | Earlier PowerShell `Get/Set-Content` edits | Repaired with Python; all modules re-parsed | Manual scan; no mojibake left |

## Checks (all PASS)

| Area | What was tested |
|---|---|
| Path traversal | 6 encoded and raw traversal paths against the SPA route, `/files/uploads` and `/assets`; no source, `.env.example` or database content served |
| Upload validation | Oversized file (limit 2 MB); PHP disguised as .jpg; SVG; HTML disguised as an image; decompression bomb (57 KB PNG, 484 MP); more than 5 files; **no orphan files** after rejections; `../../../evil.jpg` stored under a UUID name only |
| Unsafe input / URLs | `javascript:` source URL rejected; out-of-range coordinates rejected (and (0,0) — see extended suite) |
| SSRF | Connector refuses loopback and cloud-metadata instances |
| Test hooks | `/api/test/*` return 403 unless `PRALAY_TEST_MODE=1` |
| CORS | Foreign origin gets no `Access-Control-Allow-Origin` |
| Local-only AI | Public-IP Ollama endpoint → `BLOCKED_NON_LOCAL`; media not sent; pipeline completes; `paid=false` |
| Secrets / logs | No credential-like values in API responses; server logs contain no base64 image payloads or keys |
| Stored XSS | Real browser: tooltip opened, payload shown as text, never executed |

**Also verified elsewhere:**
* No paid or cloud AI provider can be configured: `VISION_PROVIDER=gemini` → `none` (`e2e_vision.py`).
* Repository scan: no secrets, `.env`, databases, uploads or model weights in the publishable file set.
* The GitHub Pages showcase makes no backend requests (`showcase_test.py`).

## Known residual risks (not fixed — design decisions or out of scope)
* **No authentication.** Every endpoint is open. Acceptable only because the server binds to `127.0.0.1`. A public deployment must add auth, roles, rate limits and quotas (see `docs/DEPLOYMENT.md`).
* The generic 500 handler returns the exception text. That's fine locally, but should be replaced with a generic message for public hosting.
* Uploaded media and thumbnails are kept indefinitely in `backend/data`. No retention or deletion policy exists yet.
* Video decoding uses OpenCV (FFmpeg) on untrusted files. Keep OpenCV updated.
* Each request can buffer up to 5 × `MAX_UPLOAD_MB` in memory. A reverse proxy should cap request size if exposed.
* Third-party evidence headlines and alert titles are stored as short excerpts with source links.
