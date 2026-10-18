"""Evaluate Ask OLake.

  uv run python -m eval.run retrieval            # free: is the expected page among the search hits?
  uv run python -m eval.run answer [BASE_URL]    # calls POST /ask (default http://localhost:8000); costs credit

Answerable questions list acceptable page URLs; unanswerable ones have expected=null
and should get an "I don't know" or a polite decline.
"""

import json
import os
import statistics
import sys
import time

import httpx

EVAL_FILE = os.getenv("EVAL_FILE", "eval/questions.jsonl")  # e.g. eval/hard_questions.jsonl
QUESTIONS = [json.loads(line) for line in open(EVAL_FILE)]
IDK_MARKERS = ("couldn't find", "could not find", "can't help", "cannot help", "not covered", "doesn't mention",
               "does not mention", "no information", "only answer", "only help", "not able to", "don't have")


def says_idk(answer: str) -> bool:
    """Honest non-answer: "I couldn't find…", a decline, or a direct "No." / "docs don't list X"."""
    a = answer.lower().replace("’", "'")
    return a.startswith(("no.", "no,", "no —", "no -")) or "don't list" in a or any(m in a for m in IDK_MARKERS)


def retrieval(k: int = 10) -> None:  # matches config.TOP_K
    from dotenv import load_dotenv

    load_dotenv()
    from app import answer as ans
    from app.search import SearchIndex

    index = SearchIndex()
    hit, total, gated_ok, gated_total, rr = 0, 0, 0, 0, 0.0
    rewrite = None
    if os.getenv("EVAL_REWRITE") == "1":  # include the LLM query-rewrite step (tiny cost)
        import asyncio

        from app import llm
        rewrite = lambda text: asyncio.run(llm.rewrite_query(text))  # noqa: E731
    for q in QUESTIONS:
        hits = index.search(q["question"], k=k, alt_query=rewrite(q["question"]) if rewrite else None)
        pages = [h.chunk["page_url"] for h in hits]
        weak = ans.is_weak(hits)
        top = f"bm25={hits[0].bm25:.1f} cos={hits[0].cosine:.2f}" if hits else "-"
        if q["expected"]:
            total += 1
            rank = next((i + 1 for i, p in enumerate(pages) if p in q["expected"]), None)
            hit += rank is not None
            rr += 1 / rank if rank else 0
            flag = f"rank {rank}" if rank else "MISS"
            print(f"{flag:7} {'GATED!' if weak else '':6} {top}  {q['question']}")
            if not rank:
                print("          got:", ", ".join(p.replace("https://olake.io/docs", "") for p in pages[:4]))
        else:
            gated_total += 1
            gated_ok += weak
            print(f"{'gated' if weak else 'to-LLM':7} {'':6} {top}  {q['question']}")
    print(f"\nretrieval hit@{k}: {hit}/{total} = {hit / total:.0%}   MRR={rr / total:.2f}   unanswerable gated before LLM: {gated_ok}/{gated_total}")


def answer(base: str) -> None:
    lat, cite_hit, total, idk_ok, idk_total, wrong_idk = [], 0, 0, 0, 0, 0
    facts_hit, facts_total, transcript = 0, 0, []
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
            transcript.append({**q, "answer": body["answer"], "sources": body["sources"]})
            facts = q.get("must_mention") or []
            found = [f for f in facts if f.lower() in body["answer"].lower()]
            if q["expected"] and facts:
                facts_hit += len(found)
                facts_total += len(facts)
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
    out = EVAL_FILE.replace(".jsonl", ".answers.jsonl")
    with open(out, "w") as f:
        for t in transcript:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    if facts_total:
        print(f"must_mention facts found verbatim: {facts_hit}/{facts_total} (paraphrases need manual review: {out})")
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
