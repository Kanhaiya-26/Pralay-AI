"""Isolated test runner: starts a throwaway PRALAY server (temp data dir, port 8001), seeds the synthetic
scenario, runs the end-to-end suite, and shuts down. The demo database in backend/data is never touched.

    .venv\\Scripts\\python.exe scripts\\run_tests.py [--suite e2e_test.py]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
PY = sys.executable


def start(data_dir, port, extra_env=None):
    env = {**os.environ, "PRALAY_DATA_DIR": str(data_dir), "PRALAY_TEST_MODE": "1", "MONITOR_INTERVAL_SEC": "600",
           # IP_BAN_STRIKES=0: suites submit many mismatches from 127.0.0.1; the ip-ban suite re-enables it
           "PYTHONIOENCODING": "utf-8", "IP_BAN_STRIKES": "0", **(extra_env or {})}
    log = open(Path(data_dir) / f"server_{int(time.time())}.log", "w", encoding="utf-8")
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                         cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                return p
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    p.kill()
    raise RuntimeError("test server did not start; see log in " + str(data_dir))


def stop(p):
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="e2e_test.py")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--keep", action="store_true", help="keep temp data dir for inspection")
    a = ap.parse_args()
    data = Path(tempfile.mkdtemp(prefix="pralay_test_"))
    api = f"http://127.0.0.1:{a.port}"
    print(f"[runner] isolated data dir: {data}")
    fake_key = "pralay-test-invalid-key-" + str(int(time.time()))
    env = {**os.environ, "PRALAY_TEST_DATA_DIR": str(data), "PRALAY_TEST_PORT": str(a.port), "PYTHONIOENCODING": "utf-8",
           "PRALAY_TEST_FAKE_GEMINI_KEY": fake_key}
    if a.suite == "media-mismatch":
        p = start(data, a.port, {"VISION_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:11555", "OLLAMA_TIMEOUT_SEC": "4", "VISION_MODE": "inline"})
        try:
            rc = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_media_mismatch.py"), api], cwd=BACKEND, env=env).returncode
        finally:
            stop(p); shutil.rmtree(data, ignore_errors=True)
        sys.exit(rc)
    if a.suite == "ip-ban":
        p = start(data, a.port, {"VISION_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:11555", "OLLAMA_TIMEOUT_SEC": "4", "VISION_MODE": "inline",
                                 "IP_BAN_STRIKES": "3"})
        try:
            rc = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_ip_ban.py"), api], cwd=BACKEND, env=env).returncode
        finally:
            stop(p); shutil.rmtree(data, ignore_errors=True)
        sys.exit(rc)
    if a.suite == "confidence-verified":
        p = start(data, a.port, {"VISION_PROVIDER": "none"})
        try:
            subprocess.run([PY, str(BACKEND / "scripts" / "seed_demo.py"), api], cwd=BACKEND, check=True)
            rc = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_confidence_verified.py"), api], cwd=BACKEND, env=env).returncode
        finally:
            stop(p); shutil.rmtree(data, ignore_errors=True)
        sys.exit(rc)
    if a.suite == "security":
        # Production-like server: test hooks OFF, 2 MB upload limit, vision endpoint on a PUBLIC address (must be refused).
        p = start(data, a.port, {"PRALAY_TEST_MODE": "0", "MAX_UPLOAD_MB": "2", "VISION_PROVIDER": "ollama",
                                 "OLLAMA_URL": "http://93.184.216.34:11434", "VISION_MODE": "inline"})
        try:
            rc = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_security.py"), api], cwd=BACKEND, env=env).returncode
        finally:
            stop(p)
            if not a.keep:
                shutil.rmtree(data, ignore_errors=True)
        print("\n[runner] security exit code:", rc)
        sys.exit(rc)
    if a.suite == "vision-real":
        # Real local model via the machine's own Ollama. Exit 2 = BLOCKED (Ollama not running / model not installed).
        p = start(data, a.port, {"VISION_PROVIDER": "ollama", "VISION_MODE": "inline", "OLLAMA_TIMEOUT_SEC": "900"})
        try:
            rc = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_vision_real.py"), api], cwd=BACKEND, env=env).returncode
        finally:
            stop(p)
            shutil.rmtree(data, ignore_errors=True)
        print("\n[runner] vision-real exit code:", rc, "(2 = BLOCKED)")
        sys.exit(rc)
    if a.suite == "mastodon-real":
        # Live, network-dependent; fresh server, no synthetic seed. Exit 2 = BLOCKED (no qualifying public post).
        p = start(data, a.port, {"VISION_PROVIDER": "none"})
        try:
            rc = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_mastodon_real.py"), api], cwd=BACKEND, env=env).returncode
        finally:
            stop(p)
            if a.keep:
                print(f"[runner] kept {data}")
            else:
                shutil.rmtree(data, ignore_errors=True)
        print("\n[runner] mastodon-real exit code:", rc, "(2 = BLOCKED)")
        sys.exit(rc)
    suites = [a.suite] if a.suite != "all" else ["e2e_test.py", "e2e_extended.py"]
    p = start(data, a.port, {"VISION_PROVIDER": "none"})
    codes = {}
    try:
        subprocess.run([PY, str(BACKEND / "scripts" / "seed_demo.py"), api], cwd=BACKEND, check=True)
        for s in suites:
            print(f"\n[runner] ===== {s} =====")
            codes[s] = subprocess.run([PY, str(BACKEND / "scripts" / s), api], cwd=BACKEND, env=env).returncode
        if a.suite == "all":
            print("\n[runner] ===== restarting server on the same data dir with visual AI pointed at a local port where nothing listens =====")
            stop(p)
            p = start(data, a.port, {"VISION_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:9", "VISION_MODE": "inline"})
            codes["e2e_restart.py"] = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_restart.py"), api], cwd=BACKEND, env=env).returncode
            print("\n[runner] ===== fresh server for worker-reliability tests (lease 8 s, recovery check 2 s, polling 1 min) =====")
            stop(p)
            wdata = Path(tempfile.mkdtemp(prefix="pralay_test_worker_"))
            p = start(wdata, a.port, {"VISION_PROVIDER": "none", "PRALAY_LEASE_SEC": "8", "PRALAY_LEASE_CHECK_SEC": "2",
                                      "PRALAY_STAGE_MAX_SEC": "15", "MASTODON_POLL_MIN": "1", "MASTODON_TAGS": "flood"})
            codes["e2e_worker.py"] = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_worker.py"), api], cwd=BACKEND,
                                                    env={**env, "PRALAY_TEST_DATA_DIR": str(wdata)}).returncode
            if not a.keep:
                shutil.rmtree(wdata, ignore_errors=True)
            print("\n[runner] ===== fresh server for local visual-AI adapter tests (Ollama protocol stub on :11555) =====")
            stop(p)
            vdata = Path(tempfile.mkdtemp(prefix="pralay_test_vision_"))
            p = start(vdata, a.port, {"VISION_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:11555", "OLLAMA_TIMEOUT_SEC": "4",
                                      "VISION_MODE": "inline", "OLLAMA_MODEL": "qwen2.5vl:3b"})
            codes["e2e_vision.py"] = subprocess.run([PY, str(BACKEND / "scripts" / "e2e_vision.py"), api], cwd=BACKEND,
                                                    env={**env, "PRALAY_TEST_DATA_DIR": str(vdata)}).returncode
            if not a.keep:
                stop(p)
                shutil.rmtree(vdata, ignore_errors=True)
    finally:
        stop(p)
        if not a.keep:
            shutil.rmtree(data, ignore_errors=True)
        else:
            print(f"[runner] kept {data}")
    print("\n[runner] suite exit codes:", codes)
    sys.exit(0 if codes and all(v == 0 for v in codes.values()) else 1)


if __name__ == "__main__":
    main()
