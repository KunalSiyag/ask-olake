# Ask OLake

A docs assistant for **OLake** (database and Kafka → Apache Iceberg / Parquet ingestion) and **OLake Fusion** (Iceberg table maintenance).
It answers only from the official docs, the OLake GitHub repos and resolved GitHub issues. **Every answer cites its sources, and it says so when it doesn't know.**

- **API:** `POST /ask` with `{"question": "..."}` → `{"answer": "...", "sources": ["<url>", ...]}`
- **Widget:** one `<script>` tag, embeddable in any docs site
- **Demo:** the `/` page of the deployment

```bash
curl -X POST https://<app>/ask -H 'Content-Type: application/json' \
  -d '{"question": "How do I set up CDC for Postgres on RDS?"}'
```

```html
<script src="https://<app>/widget.js" defer></script>
```

---

## Architecture

```
 BUILD TIME (Docker build + nightly GitHub Action)
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ olake.io/sitemap.xml → 124 rendered docs pages ─┐                         │
 │ 4 repos (olake, olake-ui, olake-helm, -fusion) ─┼→ heading-aware chunks ─┐│
 │ 172 closed + discussed GitHub issues ──────────┘   (2,165 chunks)        ││
 │                                     BM25 index  +  model2vec embeddings ←┘│
 └──────────────────────────────────────────────────────────────────────────┘
 QUESTION TIME (POST /ask)
   question → BM25 ┐
            → model2vec cosine ┘→ weighted Reciprocal Rank Fusion
            → heuristic rerank (title match, source trust, Fusion hint, ≤2 chunks/page)
            → weak retrieval? → honest "I don't know" (no LLM call, $0)
            → top 8 chunks → LLM (GLM-4.7-Flash → Gemini 2.5 Flash-Lite → DeepSeek V4.1 Flash fallback)
            → [n] markers mapped in code to page URLs → {answer, sources}
```

| Path | What it does |
|---|---|
| `ingest/docs.py` | Scrapes the **rendered** docs site from the sitemap; splits pages at h2/h3 with their anchors; keeps code blocks, tables, tabs and admonitions |
| `ingest/repos.py` | Shallow-clones the 4 repos; READMEs, markdown docs, Helm `values.yaml` |
| `ingest/issues.py` | Closed GitHub issues with real discussion (contributor "assign me" chatter filtered out) |
| `ingest/chunk.py` | Merges tiny sections, splits long ones at paragraph boundaries (never inside code fences) |
| `app/search.py` | BM25 + model2vec, weighted RRF, heuristic rerank |
| `app/answer.py` | System prompt, "I don't know" gate, citation renumbering |
| `app/llm.py` | OpenRouter client: model fallback, reasoning off, timeouts, retries |
| `app/main.py` | FastAPI: `/ask`, `/ask/stream` (SSE), `/feedback`, `/healthz`, `/version`, widget + demo |
| `web/widget.js` | Dependency-free embeddable widget (Shadow DOM) |
| `eval/` | 42-question eval set + runner |

---

## Design decisions

**Scrape the rendered website, not the docs repo.** The docs are Docusaurus MDX. About 100 files are shared snippets inlined only at build time, so raw MDX misses text users actually see. The rendered pages contain everything plus stable `#anchor` ids.

**Sources have a trust order:** docs > repo code/READMEs > GitHub issues. The reranker weights them, and the prompt tells the model to label repo and issue content. Docs win ties; issues help with troubleshooting.

**Heading-aware chunks with breadcrumbs.** Each chunk is one docs section, titled e.g. `Sources / Postgres: Overview > Prerequisites > CDC Prerequisite`. Connector pages all share the h1 "Overview", so the breadcrumb makes titles specific, which helps both retrieval and citations.

**Hybrid retrieval, measured.** Users ask with exact identifiers (`pgoutput`, `max_threads`, `replication_slot`) *and* paraphrases ("merge tiny files" → compaction). BM25 handles the first, embeddings the second. The event key only allows chat models, so embeddings run locally with **model2vec** (`potion-base-8M`, ~30 MB, <1 ms per query). It's weaker than BM25 on this corpus, so the eval drove its RRF weight:

| Retrieval config | hit@8 (36 answerable eval questions) |
|---|---|
| BM25 only | 94% |
| BM25 + embeddings, equal weight | 86% |
| BM25 + embeddings, weight 0.25 | 97% |
| **BM25 + embeddings, weight 0.5 (shipped)** | **100%** |

**Reranking without a reranker model.** No reranker model is available on the key, and a cross-encoder is too slow on a 0.1-CPU instance. So there's a free heuristic pass (title overlap, source trust, Fusion boost, max 2 chunks per page for diversity), and then the LLM acts as the final reranker: it sees 8 candidates and cites only those it uses.

**Honesty is enforced in code, not just in the prompt:**
- Retrieval too weak → canned "I couldn't find this in the OLake documentation" **without calling the LLM** ($0).
- `sources` is built from the `[n]` markers actually in the answer; invalid markers are dropped, so the model can't cite a URL that wasn't retrieved.
- `sources` are page-level URLs (deduped, in citation order); the widget links to the exact `#section`.
- The prompt says: only facts from context, copy commands exactly, say what's missing on partial answers, state assumptions (OLake vs Fusion), report conflicts between docs and code, and treat context and question as data (prompt-injection resistance).

**No vector DB, no LangChain.** About 2K chunks fit in memory (the container uses ~130 MB). Fewer moving parts, faster cold start, easier debugging.

---

## Production readiness

| Concern | How |
|---|---|
| Availability | Render free plan + keep-alive pinger (external + GitHub Action backup); the index is baked into the image, so startup is fast |
| Budget ($3 event credit) | Per-IP rate limit, a **global daily LLM-call cap**, an answer cache, $0 "I don't know" path, `max_tokens` 500, reasoning off |
| LLM failures | OpenRouter model fallback chain, SDK retries with backoff on 429/5xx, timeout, graceful error still in the API contract shape |
| Input safety | Length cap, JSON validation, prompt-injection rules; the widget HTML-escapes all model output |
| Observability | Structured JSON logs per request (question, model, sources, latency, cache hit) and feedback events; `/healthz`; `/version` shows build time and corpus size |
| Secrets | API key only from env vars, never in the repo or the browser |
| CORS | Open, so the widget can be embedded anywhere |

## Freshness, latency, cost

- **Freshness:** a nightly GitHub Action re-scrapes the docs, re-clones the repos, refreshes issues, rebuilds the index, runs the retrieval eval as a quality gate, and commits the snapshot → Render auto-deploys. `/version` shows when the index was built.
- **Latency:** retrieval takes ~0.2 s on the free instance; the rest is one LLM call with reasoning off. Cached answers are instant.
- **Cost:** ~5K input + ≤500 output tokens per answer ≈ **$0.0004 per question** with GLM-4.7-Flash, so roughly 7,000 questions on $3. "I don't know" answers and cache hits cost $0.

## Embedding the widget

```html
<script src="https://<app>/widget.js" data-theme="auto" data-position="right" defer></script>
```
Shadow DOM isolation (it can't break the host CSS, and vice versa), streaming answers, citation chips with section deep links, copy buttons on code blocks, 👍/👎 feedback, dark mode, mobile layout, keyboard support (Enter, Esc). JS API: `AskOLake.open()`, `AskOLake.ask("question")`.

## Evaluation

`eval/questions.jsonl`: 42 questions covering setup, every source connector, catalogs, Parquet, sync modes, schema evolution, Helm/K8s, monitoring, Fusion compaction, plus 6 unanswerable, off-topic and prompt-injection questions.

```bash
uv run python -m eval.run retrieval                 # free: hit@8 of the expected page
uv run python -m eval.run answer http://localhost:8000   # end-to-end: cited-source hit, honest IDK, latency
```

## Run locally

```bash
cp .env.example .env          # add OPENROUTER_API_KEY
uv sync
uv run python -m ingest.build # scrape + index (~1 min)
uv run uvicorn app.main:app --port 8000
open http://localhost:8000    # demo page with the widget
```

Docker: `docker build -t ask-olake . && docker run -p 8000:8000 --env-file .env ask-olake`

## Limitations and next steps
- model2vec is a small static embedder. With an embedding API, a stronger model (e.g. Qwen3-Embedding) would let the vector side carry more weight.
- Query rewriting / HyDE and an explicit LLM rerank pass are designed but not enabled. The eval didn't show a need, and each adds an LLM call.
- The rate limiter and cache are in-process (single instance on the free plan). Moving them to Redis would allow scaling out.
