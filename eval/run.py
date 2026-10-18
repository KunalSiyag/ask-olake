"""Evaluate Ask OLake.

  uv run python -m eval.run retrieval            # free: is the expected page among the search hits?
  uv run python -m eval.run answer [BASE_URL]    # calls POST /ask (default http://localhost:8000); costs credit

Answerable questions list acceptable page URLs; unanswerable ones have expected=null
and should get an "I don't know" or a polite decline.
"""

import json
import statistics
import sys
import time

import httpx

QUESTIONS = [json.loads(line) for line in open("eval/questions.jsonl")]
IDK_MARKERS = ("couldn't find", "could not find", "can't help", "cannot help", "not covered", "doesn't mention",
               "does not mention", "no information", "only answer", "only help", "not able to", "don't have")


def says_idk(answer: str) -> bool:
    a = answer.lower().replace("’", "'")
    return any(m in a for m in IDK_MARKERS)


def retrieval(k: int = 8) -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from app import answer as ans
    from app.search import SearchIndex

    index = SearchIndex()
    hit, total, gated_ok, gated_total = 0, 0, 0, 0
    for q in QUESTIONS:
        hits = index.search(q["question"], k=k)
        pages = [h.chunk["page_url"] for h in hits]
        weak = ans.is_weak(hits)
        top = f"bm25={hits[0].bm25:.1f} cos={hits[0].cosine:.2f}" if hits else "-"
        if q["expected"]:
            total += 1
            rank = next((i + 1 for i, p in enumerate(pages) if p in q["expected"]), None)
            hit += rank is not None
            flag = f"rank {rank}" if rank else "MISS"
            print(f"{flag:7} {'GATED!' if weak else '':6} {top}  {q['question']}")
            if not rank:
                print("          got:", ", ".join(p.replace("https://olake.io/docs", "") for p in pages[:4]))
        else:
            gated_total += 1
            gated_ok += weak
            print(f"{'gated' if weak else 'to-LLM':7} {'':6} {top}  {q['question']}")
    print(f"\nretrieval hit@{k}: {hit}/{total} = {hit / total:.0%}   unanswerable gated before LLM: {gated_ok}/{gated_total}")


def answer(base: str) -> None:
    lat, cite_hit, total, idk_ok, idk_total, wrong_idk = [], 0, 0, 0, 0, 0
    with httpx.Client(timeout=60) as client:
        for q in QUESTIONS:
            t = time.perf_counter()
            r = client.post(f"{base}/ask", json={"question": q["question"]})
            lat.append(time.perf_counter() - t)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if "answer" not in body:
                print(f"HTTP {r.status_code} for {q['question']!r}: {str(body)[:120]}")
                body = {"answer": "", "sources": []}
            idk = says_idk(body["answer"])
            if q["expected"]:
                total += 1
                ok = any(s in q["expected"] for s in body["sources"])
                cite_hit += ok
                wrong_idk += idk
                print(f"{'OK  ' if ok else 'MISS'} {'IDK!' if idk else '    '} {lat[-1]:5.1f}s  {q['question']}")
                if not ok:
                    print("        sources:", body["sources"][:3])
            else:
                idk_total += 1
                idk_ok += idk
                print(f"{'IDK ' if idk else 'ANSW'}      {lat[-1]:5.1f}s  {q['question']}\n        -> {body['answer'][:160]!r}")
    lat.sort()
    print(f"\ncitation hit: {cite_hit}/{total} = {cite_hit / total:.0%}   false 'I don't know': {wrong_idk}")
    print(f"unanswerable handled honestly: {idk_ok}/{idk_total}")
    print(f"latency p50={statistics.median(lat):.1f}s p95={lat[int(0.95 * (len(lat) - 1))]:.1f}s")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "retrieval"
    if mode == "retrieval":
        retrieval()
    else:
        answer(sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000")
