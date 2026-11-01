"""Optional OpenRouter phrasing layer for the Investigate chat. FREE models only (":free" suffix; no paid fallback).
The LLM only rewords PRALAY's own evidence; it is never an evidence source. Any failure -> caller keeps the template answer."""
import json
import logging
import os
import re

import httpx

log = logging.getLogger("pralay.openrouter")
FREE_MODELS = [m for m in os.getenv("OPENROUTER_MODELS", "google/gemma-2-9b-it:free,meta-llama/llama-3.2-3b-instruct:free,"
                                    "mistralai/mistral-7b-instruct:free").split(",") if m.strip().endswith(":free")]
SYSTEM = ("You are an expert disaster intelligence assistant. You have TWO sources of information:\n"
          "1. PRALAY Database: Verified local citizen reports and incidents.\n"
          "2. External News: Live articles fetched from the internet.\n"
          "Answer the user's question by combining both. First, state what the local database knows (if anything). "
          "Then, provide external context from the news articles. ALWAYS cite external news using markdown links like [Source](URL). "
          "Never invent data.")


def context_of(resp):
    """Compact, non-personal context: PRALAY's assessed answer, incidents and evidence summaries (no reporter details)."""
    s = resp.get("summary") or {}
    return {"pralay_assessment": {k: s.get(k) for k in ("headline", "status", "confidence", "reports", "incidents") if k in s},
            "incidents": [{k: i.get(k) for k in ("id", "title", "category", "place_name", "effective_status", "report_count", "last_activity_at")}
                          for i in (resp.get("incidents") or [])[:8]],
            "evidence": [{k: e.get(k) for k in ("source_name", "publisher", "stance", "summary", "publication_time")}
                         for e in (resp.get("sources") or [])[:10]],
            "template_answer": (resp.get("answer") or "")[:1500]}


def phrase(question, resp, news=None, timeout=25):
    key = os.getenv("OPENROUTER_API_KEY")
    if not key or not FREE_MODELS:
        return None
    ext = [{k: a.get(k) for k in ("title", "source", "published", "link")} for a in (news or [])[:3]]
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"PRALAY Database context (JSON):\n{json.dumps(context_of(resp), default=str)[:6000]}\n\n"
                                        f"External News (JSON, fetched live; unverified by PRALAY):\n{json.dumps(ext, default=str)}\n\nQuestion: {question}"}]
    for model in FREE_MODELS:
        try:
            r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=timeout,
                           headers={"Authorization": f"Bearer {key}", "X-Title": "PRALAY AI"},
                           json={"model": model, "messages": msgs, "temperature": 0.1, "max_tokens": 1500,
                                 "reasoning": {"effort": "low", "exclude": True}})  # reasoning models: think briefly, return the answer
            if r.status_code == 200:
                # reasoning models may return content=None (all tokens spent thinking): treat as no answer
                text = (((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                if text:
                    # keep only links we actually supplied; an invented URL becomes plain text
                    allowed = {a["link"] for a in ext if a.get("link")}
                    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", lambda m: m.group(0) if m.group(2) in allowed else m.group(1), text)
                    return {"text": text, "model": model}
                log.warning("OpenRouter %s -> HTTP 200 but empty answer", model)
                continue
            log.warning("OpenRouter %s -> HTTP %s", model, r.status_code)
        except Exception as e:
            log.warning("OpenRouter %s failed: %s", model, type(e).__name__)
    return None
