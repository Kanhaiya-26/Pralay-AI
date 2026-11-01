"""Run every suite sequentially (each on its own isolated server; never the demo DB) and print one summary table.

    .venv\\Scripts\\python.exe scripts\\final_regression.py [--with-real]   (--with-real adds the live Mastodon + real-model suites)
"""
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PY = sys.executable
runs = [("full automated (39+53+11+20+vision)", ["scripts/run_tests.py", "--suite", "all"]),
        ("security", ["scripts/run_tests.py", "--suite", "security"]),
        ("browser UI", ["scripts/run_tests.py", "--suite", "ui_test.py"]),
        ("GitHub Pages showcase (browser)", ["scripts/showcase_test.py"])]
if "--with-real" in sys.argv:
    runs += [("real Mastodon end-to-end (live)", ["scripts/run_tests.py", "--suite", "mastodon-real"]),
             ("real local model (Ollama)", ["scripts/run_tests.py", "--suite", "vision-real"])]

summary = []
for name, cmd in runs:
    t0 = time.time()
    p = subprocess.run([PY, *cmd], cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    counts = re.findall(r"(\d+)/(\d+) [a-zA-Z\- ]*checks passed", out)
    fails = [l.strip() for l in out.splitlines() if l.startswith("FAIL")]
    log = Path(tempfile.gettempdir()) / f"pralay_regression_{re.sub(r'[^a-z]+', '_', name.lower())}.log"
    log.write_text(out, encoding="utf-8")
    summary.append((name, p.returncode, counts, fails, round(time.time() - t0), log))
    print(f"[{name}] exit={p.returncode} counts={counts} {round(time.time() - t0)}s", flush=True)

print("\n==== FINAL REGRESSION SUMMARY ====")
tp = tt = 0
for name, rc, counts, fails, secs, log in summary:
    pas = sum(int(a) for a, _ in counts)
    tot = sum(int(b) for _, b in counts)
    tp, tt = tp + pas, tt + tot
    status = "BLOCKED" if rc == 2 else ("PASS" if rc == 0 else "FAIL")
    print(f"{status:8} {name:42} {pas}/{tot}  ({secs}s)  log: {log}")
    for f in fails[:10]:
        print("           ", f)
print(f"TOTAL executed checks passed: {tp}/{tt}")
