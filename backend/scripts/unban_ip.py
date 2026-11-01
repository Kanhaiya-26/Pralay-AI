"""Lift an anti-spam IP ban (or list bans).
    .venv\\Scripts\\python.exe scripts\\unban_ip.py            # list bans
    .venv\\Scripts\\python.exe scripts\\unban_ip.py 127.0.0.1  # unban one IP
Reports and their history are not touched; only the banned_ips row is removed."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DB_PATH  # noqa: E402

c = sqlite3.connect(DB_PATH)
if len(sys.argv) > 1:
    n = c.execute("DELETE FROM banned_ips WHERE ip_address=?", (sys.argv[1],)).rowcount
    c.commit()
    print(f"unbanned {sys.argv[1]}" if n else f"{sys.argv[1]} was not banned")
else:
    rows = c.execute("SELECT ip_address, reason, banned_at FROM banned_ips").fetchall()
    print("\n".join(f"{ip}  {reason}  (since {at})" for ip, reason, at in rows) or "no banned IPs")
