# Local Vision Model Benchmark — **PARTIAL**

Machine:
* Intel Core i5-1135G7 (4 cores / 8 threads, x64)
* 7.8 GB RAM, **1.1–1.6 GB free** during tests
* Intel Iris Xe integrated graphics: **Ollama ran 100% on CPU**
* Windows 11, Ollama 0.40.13

**Method** (`backend/scripts/vision_benchmark.py`):
* The application's exact prompt, JSON schema, image preprocessing (max edge 1024 px) and sanitizer.
* Models run strictly one at a time, unloaded between models.
* Synthetic images: 2 flood, heavy rain, waterlogging, unrelated still life, degraded/poor quality.
* Real photographs were **not** used. Wikimedia Commons refused automated download (HTTP 403, robot policy), and I did not work around it.

## Status
* **qwen2.5vl:3b: 5 of 12 planned runs completed** before the session holding the benchmark ended (it was not restarted, per the instruction not to delay deployment). The full JSON outputs of those runs were lost with the session; the per-run status and timing lines below were preserved from its log.
* **gemma3:4b: NOT BENCHMARKED.** It is downloaded and listed by Ollama, but no inference was run. **No claims are made about its speed or quality.**
* Load time, peak RAM and CPU utilisation were instrumented but **not captured**, because the run did not finish. Ollama reported qwen2.5vl:3b resident at **2.8 GB, 100% CPU** during the run.

## qwen2.5vl:3b — measured

| Run | Image (synthetic) | Status | Wall time | Rule-based relevance |
|---|---|---|---|---|
| 1 (first, includes model load) | flood_0 | valid JSON | 121.2 s | ✖ flood not recognised |
| 2 | flood_1 | valid JSON | 142.3 s | ✖ flood not recognised |
| 3 | heavyrain_2 | valid JSON | 139.8 s | ✔ |
| 4 | waterlogging_3 | valid JSON | 136.8 s | ✔ |
| 5 | unrelated_4 | valid JSON | 136.7 s | ✔ (no false disaster indicator) |
| (separate real-pipeline run) | seeded synthetic flood report | completed via deferred worker | 107.8 s | ✖ model saw "stylized urban scene"; emitted a self-contradictory `building_damage` indicator (now dropped by the sanitizer) |

**Summary for qwen2.5vl:3b (n = 6):**

| Metric | Result |
|---|---|
| Schema-valid structured output | 6/6 |
| Crashes / timeouts / out-of-memory errors | 0 |
| Latency | **108–142 s per image** (mean ≈131 s) |
| Relevance on the rule-based checks | 3/6 |
| Genuine/fake verdicts emitted | none observed |

* Its weakness was the cartoon-like synthetic flood scenes. These are not representative of real photos, so the relevance result is indicative only.

## Recommendation
* **Keep `qwen2.5vl:3b` as the default** for this laptop, as a *provisional* choice:
  * it is the smaller model (3.2 GB vs 3.4 GB)
  * it was reliable here: 6/6 valid outputs, no OOM, no timeouts
  * it is better suited to reading signboard text, which the location checks use
* **This is not a measured comparison**, because Gemma 3 4B was not run.
* At roughly 2 minutes per image on this CPU, visual AI is only viable as **deferred background enrichment** (`VISION_MODE=deferred`, the default). It must never sit in the live path.
* Treat its observations as weak evidence (as the scoring already does: ±10 points).

## To complete the benchmark
```powershell
cd backend
.venv\Scripts\python.exe scripts\vision_benchmark.py --models qwen2.5vl:3b gemma3:4b --runs 2 --timeout 900
# optional: add your own licensed, non-personal photos: --images <folder>  (file prefix = flood_, heavyrain_, waterlogging_, unrelated_, poor_)
```
Expect roughly 50–60 minutes on this laptop. Close other applications first, because free RAM was only ~1.5 GB. Results go to `reports/model_benchmark_raw.json`.
