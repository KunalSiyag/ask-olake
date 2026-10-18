"""Ask OLake API.

POST /ask         {"question": "..."} -> {"answer": "...", "sources": ["<url>", ...]}
POST /ask/stream  same input, Server-Sent Events for the chat widget
POST /feedback    thumbs up/down from the widget
GET  /healthz, /version
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import answer, config, llm
from app.search import SearchIndex

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
log = logging.getLogger("ask-olake")


def log_event(event: str, **fields) -> None:
    log.info(json.dumps({"event": event, "ts": round(time.time(), 3), **fields}, ensure_ascii=False))


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
META = json.loads((Path(os.getenv("DATA_DIR", "data")) / "meta.json").read_text())
index = SearchIndex()

app = FastAPI(title="Ask OLake", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])


# ---------- guards: rate limit, daily budget cap, answer cache ----------

_hits_per_ip: dict[str, deque] = defaultdict(deque)
_daily = {"day": date.today(), "llm_calls": 0}
_cache: OrderedDict[str, dict] = OrderedDict()
CACHE_SIZE = 1000


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def check_rate_limit(ip: str) -> None:
    now, window = time.monotonic(), _hits_per_ip[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= config.RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "Too many questions, please wait a minute.")
    window.append(now)


def take_llm_budget() -> bool:
    if _daily["day"] != date.today():
        _daily.update(day=date.today(), llm_calls=0)
    if _daily["llm_calls"] >= config.DAILY_LLM_CALL_CAP:
        return False
    _daily["llm_calls"] += 1
    return True


def cache_key(question: str) -> str:
    return re.sub(r"[\s?!.]+", " ", question.lower()).strip()


def cache_get(key: str) -> dict | None:
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


def cache_put(key: str, value: dict) -> None:
    _cache[key] = value
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


# ---------- core ----------

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)


def clean_question(q: str) -> str:
    q = q.strip()
    if not q:
        raise HTTPException(422, "Question is empty.")
    if len(q) > config.MAX_QUESTION_CHARS:
        raise HTTPException(413, f"Question is too long (max {config.MAX_QUESTION_CHARS} characters).")
    return q


def no_llm_result(text: str, hits) -> dict:
    return {"answer": text, "sources": [], "citations": [], "model": None,
            "candidates": [h.chunk["url"] for h in hits[:3]]}


async def answer_question(question: str) -> dict:
    hits = index.search(question, k=config.TOP_K)
    if answer.is_weak(hits):
        return no_llm_result(answer.IDK_ANSWER, hits)
    if not take_llm_budget():
        return no_llm_result("Ask OLake has reached its daily usage limit. Please try again tomorrow, "
                             "or search the docs at https://olake.io/docs/.", hits)
    raw, model = await llm.complete(answer.build_messages(question, hits))
    text, sources, citations = answer.finalize(raw, hits)
    return {"answer": text, "sources": sources, "citations": citations, "model": model,
            "candidates": [h.chunk["url"] for h in hits]}


@app.post("/ask")
async def ask(body: AskRequest, request: Request):
    question = clean_question(body.question)
    ip, started, key = client_ip(request), time.perf_counter(), cache_key(question)
    result, cached = cache_get(key), True
    if result is None:
        cached = False
        check_rate_limit(ip)
        try:
            result = await answer_question(question)
        except Exception as e:  # never leak stack traces; still return the contract shape
            log_event("error", question=question, error=f"{type(e).__name__}: {e}")
            return JSONResponse(status_code=503, content={
                "answer": "Sorry, the answering service is temporarily unavailable. Please try again shortly.",
                "sources": []})
        if result["model"]:
            cache_put(key, result)
    log_event("ask", question=question, ip=ip, cached=cached, model=result["model"],
              sources=result["sources"], ms=round(1000 * (time.perf_counter() - started)))
    return {"answer": result["answer"], "sources": result["sources"]}


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/ask/stream")
async def ask_stream(body: AskRequest, request: Request):
    question = clean_question(body.question)
    ip, key, answer_id = client_ip(request), cache_key(question), uuid.uuid4().hex[:12]
    cached = cache_get(key)
    if cached is None:
        check_rate_limit(ip)

    async def events():
        started = time.perf_counter()
        result = cached
        if result is None:
            hits = index.search(question, k=config.TOP_K)
            if answer.is_weak(hits):
                result = no_llm_result(answer.IDK_ANSWER, hits)
            elif not take_llm_budget():
                result = no_llm_result("Ask OLake has reached its daily usage limit. Please try again tomorrow.", hits)
            else:
                raw, model = [], None
                try:
                    async for delta, model in llm.stream(answer.build_messages(question, hits)):
                        raw.append(delta)
                        yield sse("delta", delta)
                except Exception as e:
                    log_event("error", question=question, error=f"{type(e).__name__}: {e}")
                    yield sse("error", "The answering service is temporarily unavailable. Please try again.")
                    return
                text, sources, citations = answer.finalize("".join(raw), hits)
                result = {"answer": text, "sources": sources, "citations": citations, "model": model}
                cache_put(key, result)
        yield sse("final", {"id": answer_id, "answer": result["answer"], "sources": result["sources"],
                            "citations": result.get("citations", [])})
        log_event("ask", question=question, ip=ip, id=answer_id, cached=cached is not None, stream=True,
                  model=result["model"], sources=result["sources"], ms=round(1000 * (time.perf_counter() - started)))

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Feedback(BaseModel):
    id: str = Field(..., max_length=64)
    vote: int = Field(..., ge=-1, le=1)
    question: str = Field("", max_length=1000)
    comment: str = Field("", max_length=1000)


@app.post("/feedback")
async def feedback(body: Feedback, request: Request):
    check_rate_limit(client_ip(request))
    log_event("feedback", **body.model_dump())
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "chunks": len(index.chunks)}


@app.get("/version")
async def version():
    return {**META, "model": config.LLM_MODEL, "fallback_models": config.LLM_FALLBACK_MODELS,
            "embeddings": index.model is not None}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/widget.js")
    async def widget_js():
        return FileResponse(WEB_DIR / "widget.js", media_type="application/javascript",
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/")
    async def demo_page():
        return FileResponse(WEB_DIR / "index.html")
