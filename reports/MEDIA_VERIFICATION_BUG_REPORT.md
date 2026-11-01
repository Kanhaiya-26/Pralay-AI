# Media verification bug: helmet photo submitted as FLOOD (report #10)

## Symptom
Report #10 contained a helmet photo with the category set to **flood**. It finished PROCESSED, UNVERIFIED, with "confidence 50" and location LOW. Nothing said the image had nothing to do with a flood.

## Root cause (traced on the live report)
1. **The 50 was not a model output.** The report score is a heuristic that starts at 50 and adds weighted factors. At assessment time the only factor was `loc:low` (weight 0). The visual analysis was still queued (`VISION_MODE=deferred`), so the score meant "no evidence either way". It was shown as if it were a confidence.
2. **The claimed category was not treated as ground truth, but a mismatch had almost no effect.** A `no_match` from the vision model only subtracted 10 points. It produced no mismatch finding, no needs-review flag, and nothing stopped the report counting as an independent report toward incident corroboration.
3. **Over-broad "related category" matching.** `category_match` accepted `cyclone_storm` as a match for a flood claim. When the real `qwen2.5vl:3b` model analysed the helmet photo, it described it correctly ("A close-up view of a pilot's helmet and cockpit, with the sky and clouds reflected in the helmet's visor"). But it also produced a **strong `cyclone_storm` indicator** from the reflected clouds. Under the old rule that would have counted as visual **support** for the flood claim: a real false positive.
4. **The image is sent to the model.** `vision.py` sends the stored file as base64 JPEG (≤1024 px) in `messages[0].images`. The protocol stub records `had_image`, `image_bytes` and `image_size` for every request, and `e2e_vision.py` asserts them. The real model's description of #10 matches the uploaded image.

## Fix
| File | Change |
|---|---|
| `backend/app/fusion.py` | New `visual_state()`. Every assessment now carries `fields`: `user_claimed_category`, `ai_detected_category`, `visual_relevance` (MATCH / RELATED_ONLY / MISMATCH / NOT_ANALYSED), `visual_confidence`, `media_authenticity_status` (forensics, kept separate from relevance), `report_verification_status`, `needs_review` and `score_basis` (explains that 50 = no evidence, not 50%). A MISMATCH or RELATED_ONLY result adds an explicit "Category mismatch" concern, keeps the report **UNVERIFIED with a needs-review reason**, and **excludes it from `independent_reports`**, so it can never corroborate an incident. NOT_ANALYSED is listed under missing evidence. New weight `vision:related = -10`. |
| `backend/app/vision.py` | `category_match` returns `match` only for the claimed category itself (fire/smoke are treated as synonyms). An associated hazard (for example cyclone for a flood claim) is `related`, which is **not** support. |
| `backend/scripts/fake_ollama.py` | Stub modes `helmet` (no indicators), `helmet_cyclone` (the shape of the real model's #10 output) and `fire`. |
| `backend/scripts/e2e_media_mismatch.py` (new), `run_tests.py` | Isolated regression suite: `run_tests.py --suite media-mismatch`. |

Fail-safe behaviour (already present, now asserted):
- Ollama unavailable, timeout, malformed or non-JSON output, HTTP 500 and a missing image all leave the analysis `failed`. The relevance is then NOT_ANALYSED and is never counted as support, and the report stays UNVERIFIED.

## Before / after (report #10, live database, real qwen2.5vl:3b output)
| | Before | After |
|---|---|---|
| Status | UNVERIFIED, reason "Insufficient evidence…" | UNVERIFIED, reason "Needs review: image does not show the claimed 'flood' hazard (visual relevance RELATED_ONLY); not counted as evidence for the incident." |
| Visual fields | none | claimed `flood`, AI detected `cyclone_storm` (strong), relevance `RELATED_ONLY`, `needs_review: true` |
| Counts toward incident corroboration | yes (if other criteria were met) | **no** |

The report was re-assessed in place. No report, media, evidence or incident was deleted.

## Test results (actual runs, 2026-10-31)
| Suite | Result |
|---|---|
| `media-mismatch` (new; isolated server, protocol **stub**, synthetic images) | **11/11 PASS** |
| `all` (e2e 39, extended 53, restart 11, worker 20, vision 31) | **154/154 PASS** |

The media-mismatch cases:
- helmet as flood → MISMATCH, UNVERIFIED, needs_review
- 3 GPS-consistent helmet "flood" reports → incident **not** corroborated, 0 independent reports
- helmet with a hallucinated strong cyclone indicator → RELATED_ONLY, needs_review, no score boost
- fire image as flood → MISMATCH
- flood image as flood → MATCH
- Ollama unavailable → NOT_ANALYSED, listed as missing
- "50 = no evidence" explanation present
- malformed model output → NOT_ANALYSED
- duplicate still detected
- old image with a current claim → CONTRADICTED (OLD_MEDIA)

A first `all` run failed (e2e_test `KeyError 'category'` in `independent_reports`, introduced by this fix). It was fixed and re-run green.

## Limitations
- The stub tests prove the **decision logic**, not model accuracy. The real model was observed on **one** real image (#10), and it produced a hallucinated indicator. Visual AI remains advisory, and it is never a verification.
- The "road image submitted as flood" case is covered by the no-indicator path (the same code as the helmet case). It was not run with a real road photo. Corrupt or unsupported images are rejected at upload by the existing validation tests (`e2e_test`: non-image disguised as JPG).
- The score is still an uncalibrated heuristic (base 50). It is now labelled as such in `score_basis`.
- In deferred mode the first assessment happens before the vision model finishes (about 2 min on this CPU). Until then the relevance shows NOT_ANALYSED.
- The UI does not yet render the new `fields` block specifically. It shows the concern and reason text that already exist.
