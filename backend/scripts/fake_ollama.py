"""TEST STUB — NOT A MODEL. A minimal local HTTP server that speaks the subset of the Ollama API PRALAY uses
(/api/tags, /api/chat), so the adapter's behaviour (success, timeout, malformed output, missing model, verdict
stripping) can be tested without installing Ollama. Its "observations" are canned and say nothing about real images.

    python fake_ollama.py <port> [model]
Control: POST /_mode {"mode": "ok"|"slow"|"malformed"|"verdict"|"no_model"|"error500"}; GET /_last -> last request info.
"""
import base64
import io
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11555
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen2.5vl:3b"
STATE = {"mode": "ok", "last": None, "requests": 0}

CANNED = {
    "description": "Street with brown floodwater covering the road up to car wheel height; buildings on both sides.",
    "disaster_indicators": [{"category": "flood", "evidence": "standing water across the road", "strength": "strong"}],
    "objects_and_conditions": ["standing water", "buildings", "overcast sky"],
    "visible_text": ["SYNTHETIC DEMO IMAGE - PRALAY AI"],
    "landmarks": [],
    "context_inconsistencies": ["text overlay in the top-left corner"],
    "limitations": ["water depth cannot be measured", "location cannot be determined from the image alone"],
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tags":
            name = "llava:7b" if STATE["mode"] == "no_model" else MODEL
            return self._send(200, {"models": [{"name": name}]})
        if self.path == "/_last":
            return self._send(200, {**(STATE["last"] or {}), "requests": STATE["requests"]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/_mode":
            STATE["mode"] = body.get("mode", "ok")
            return self._send(200, {"mode": STATE["mode"]})
        if self.path != "/api/chat":
            return self._send(404, {"error": "not found"})
        STATE["requests"] += 1
        msg = (body.get("messages") or [{}])[0]
        imgs = msg.get("images") or []
        info = {"model": body.get("model"), "had_image": bool(imgs), "has_schema": isinstance(body.get("format"), dict), "stream": body.get("stream")}
        if imgs:
            raw = base64.b64decode(imgs[0])
            info["image_bytes"] = len(raw)
            try:
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as im:
                    info["image_size"] = list(im.size)
            except Exception:
                info["image_size"] = None
        STATE["last"] = info
        mode = STATE["mode"]
        if mode == "slow":
            time.sleep(8)
        if mode == "error500":
            return self._send(500, {"error": "model crashed (stub)"})
        if mode == "malformed":
            content = "Sure! Here is my analysis: there is a lot of water everywhere, very scary."
        elif mode == "helmet":  # unrelated object: model sees no disaster
            content = json.dumps({**CANNED, "description": "A black motorcycle helmet on a plain table.", "disaster_indicators": [],
                                  "objects_and_conditions": ["helmet", "table"], "visible_text": [], "context_inconsistencies": []})
        elif mode == "helmet_cyclone":  # shape of the REAL qwen2.5vl:3b output for report #10 (helmet photo)
            content = json.dumps({**CANNED, "description": "A close-up view of a pilot's helmet and cockpit, with clouds reflected in the visor.",
                                  "disaster_indicators": [{"category": "cyclone_storm", "evidence": "clouds reflected in the visor", "strength": "strong"}]})
        elif mode == "fire":
            content = json.dumps({**CANNED, "description": "A building on fire with thick smoke.",
                                  "disaster_indicators": [{"category": "fire", "evidence": "open flames", "strength": "strong"}]})
        elif mode == "verdict":
            content = json.dumps({**CANNED, "description": "This image is fake. Water covers the street.", "is_fake": True,
                                  "verdict": "AI-generated", "authenticity_score": 0.1})
        else:
            content = json.dumps(CANNED)
        self._send(200, {"model": body.get("model"), "message": {"role": "assistant", "content": content}, "done": True})


if __name__ == "__main__":
    print(f"fake_ollama (TEST STUB) on 127.0.0.1:{PORT}, model {MODEL}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
