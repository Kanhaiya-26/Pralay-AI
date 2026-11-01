"""Central configuration. All secrets come from environment variables / backend/.env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DATA_DIR = Path(os.getenv("PRALAY_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbs"
DB_PATH = Path(os.getenv("PRALAY_DB_PATH", DATA_DIR / "pralay.db"))
for _d in (DATA_DIR, UPLOAD_DIR, THUMB_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Visual AI — local and free only. No cloud provider exists in the code, so there is no paid fallback.
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "ollama").strip().lower()        # ollama | none
if VISION_PROVIDER not in ("ollama", "none"):
    VISION_PROVIDER = "none"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b").strip()
OLLAMA_TIMEOUT_SEC = _float("OLLAMA_TIMEOUT_SEC", 240)                           # CPU-only inference is slow
VISION_MODE = os.getenv("VISION_MODE", "deferred").strip().lower()               # deferred | inline
if VISION_MODE not in ("deferred", "inline"):
    VISION_MODE = "deferred"
VISION_ALLOW_LAN = _bool("VISION_ALLOW_LAN", False)
VISION_MAX_EDGE = _int("VISION_MAX_EDGE", 1024)
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "60s").strip()               # unload the model soon after the queue empties
VISION_MIN_FREE_MB = _int("VISION_MIN_FREE_MB", 600)                            # deferred analysis waits while RAM is lower

MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 25)
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}

# Network integrations can be disabled for offline demos.
EXTERNAL_LOOKUPS = _bool("EXTERNAL_LOOKUPS", True)
HTTP_TIMEOUT = _float("HTTP_TIMEOUT", 12)
USER_AGENT = os.getenv("PRALAY_USER_AGENT", "PRALAY-AI/0.1 (GRAVITON 2026 hackathon prototype)")

# Grid — CUSTOM / DEMO GRID, NOT AN OFFICIAL IMD/NDMA GRID.
GRID_RESOLUTION_DEG = _float("GRID_RESOLUTION_DEG", 0.25)
GRID_BBOX = (6.0, 68.0, 38.0, 98.0)  # lat_min, lon_min, lat_max, lon_max (India extent)

# Incident clustering thresholds.
CLUSTER_RADIUS_KM = _float("CLUSTER_RADIUS_KM", 12)
CLUSTER_TIME_WINDOW_H = _float("CLUSTER_TIME_WINDOW_H", 72)
ACTIVE_INCIDENT_WINDOW_H = _float("ACTIVE_INCIDENT_WINDOW_H", 48)

# Cache / monitoring freshness.
EVIDENCE_TTL_ACTIVE_MIN = _int("EVIDENCE_TTL_ACTIVE_MIN", 20)
EVIDENCE_TTL_OLD_MIN = _int("EVIDENCE_TTL_OLD_MIN", 360)
QUERY_CACHE_TTL_MIN = _int("QUERY_CACHE_TTL_MIN", 15)
MONITOR_INTERVAL_SEC = _int("MONITOR_INTERVAL_SEC", 120)
WORKER_POLL_SEC = _float("WORKER_POLL_SEC", 1.5)
