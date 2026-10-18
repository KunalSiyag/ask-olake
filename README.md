# Ask OLake

A docs assistant for **OLake** (database and Kafka → Apache Iceberg / Parquet ingestion) and **OLake Fusion** (Iceberg table maintenance).
It answers only from the official docs, the official blog and comparison pages, the OLake GitHub repos (READMEs, Helm values, connector config specs, licenses) and resolved GitHub issues. **Every answer cites its sources, and it says so when it doesn't know.**

- **API:** `POST /ask` with `{"question": "..."}` → `{"answer": "...", "sources": ["<url>", ...]}`
- **Widget:** one `<script>` tag, embeddable in any docs site
- **Live demo:** https://ask-olake-nh7j.onrender.com (the widget is embedded on that page)

```bash
curl -X POST https://ask-olake-nh7j.onrender.com/ask -H 'Content-Type: application/json' \
  -d '{"question": "How do I set up CDC for Postgres on RDS?"}'
```

```html
<script src="https://ask-olake-nh7j.onrender.com/widget.js" defer></script>
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
            → top 20 chunks → LLM (DeepSeek V4.1 Flash → Gemini 2.5 Flash-Lite → GLM-4.7-Flash; hedged after 6 s)
            → [n] markers mapped in code to page URLs → {answer, sources}
```

| Path | What it does |
|---|---|
| `ingest/docs.py` | Scrapes the **rendered** docs, blog and compare pages from the sitemap; splits pages at h2/h3 with their anchors; keeps code blocks, tables, tabs and admonitions; adds a "releases, newest first" summary |
| `ingest/repos.py` | Shallow-clones the 4 repos; READMEs, markdown docs, Helm `values.yaml`, connector `spec.json` (flattened to one line per field: type, default, description), license facts |
| `ingest/issues.py` | Closed GitHub issues with real discussion (contributor "assign me" chatter filtered out) |
| `ingest/chunk.py` | Merges tiny sections, splits long ones at paragraph, then line boundaries (never inside code fences); long tables are split by rows **with the header row repeated** |
| `app/search.py` | BM25 + model2vec, weighted RRF, heuristic rerank |
| `app/answer.py` | System prompt, "I don't know" gate, citation renumbering |
| `app/llm.py` | OpenRouter client: model fallback, reasoning off, timeouts, retries |
| `app/main.py` | FastAPI: `/ask`, `/ask/stream` (SSE), `/feedback`, `/healthz`, `/version` (commit + deployment), `/status` (index + live-refresh status), `/models` (fetched from OpenRouter `/models/user`), `/admin/refresh`, widget + demo |
| `app/refresh.py` | Live refresh: change detection, partial re-fetch, incremental re-embed, atomic index swap |
| `web/widget.js` | Dependency-free embeddable widget (Shadow DOM) |
| `eval/` | 42-question eval set + runner |

---

## Design decisions

**Scrape the rendered website, not the docs repo.** The docs are Docusaurus MDX. About 100 files are shared snippets inlined only at build time, so raw MDX misses text users actually see. The rendered pages contain everything plus stable `#anchor` ids.

**Sources have a trust order:** docs (1.0) > repo code/READMEs (0.7) > GitHub issues (0.7) > blog/compare pages (0.5). Weights were tuned on the evals: indexing the whole blog at higher weight cost accuracy elsewhere. The reranker weights them, and the prompt tells the model to label repo and issue content. Docs win ties; issues help with troubleshooting.

**Heading-aware chunks with breadcrumbs.** Each chunk is one docs section, titled e.g. `Sources / Postgres: Overview > Prerequisites > CDC Prerequisite`. Connector pages all share the h1 "Overview", so the breadcrumb makes titles specific, which helps both retrieval and citations.

**Hybrid retrieval, measured.** Users ask with exact identifiers (`pgoutput`, `max_threads`, `replication_slot`) *and* paraphrases ("merge tiny files" → compaction). BM25 handles the first, embeddings the second. The event key only allows chat models, so embeddings run locally with **model2vec** (`potion-base-8M`, ~30 MB, <1 ms per query). It's weaker than BM25 on this corpus, so the eval drove its RRF weight:

| Retrieval config | hit@8 (36 answerable eval questions) |
|---|---|
| BM25 only | 94% |
| BM25 + embeddings, equal weight | 86% |
| BM25 + embeddings, weight 0.25 | 97% |
| **BM25 + embeddings, weight 0.5 (shipped)** | **100%** |

**"What is X?" questions and typos.** "olake" appears in almost every chunk, so keyword search can't rank "What is OLake?" by itself. Definition questions (what is / what are / explain / tell me about…) add a third ranked list of intro/overview sections and boost them; release-notes "overview" pages are excluded. Query words that appear nowhere in the corpus are mapped to the closest corpus word ("fushion" → "fusion", "compacton" → "compaction"); known words, including identifiers, are never changed.

**Reranking without a reranker model.** No reranker model is available on the key, and a cross-encoder is too slow on a 0.1-CPU instance. So there's a free heuristic pass (title overlap, source trust, Fusion boost, max 2 chunks per page for diversity), and then the LLM acts as the final reranker: it sees 8 candidates and cites only those it uses.

**Honesty is enforced in code, not just in the prompt:**
- Retrieval too weak → canned "I couldn't find this in the OLake documentation" **without calling the LLM** ($0).
- `sources` is built from the `[n]` markers actually in the answer; invalid markers are dropped, so the model can't cite a URL that wasn't retrieved. `[1-2]` and `[^1]` styles are understood. If an answer cites nothing valid, `sources` falls back to the top 3 retrieved pages (never for declines or "I couldn't find this"). Marker rewriting skips code blocks and inline code, so commands like `git add .` or `${arr[0]}` come back exactly as written.
- `sources` are page-level URLs (deduped, in citation order); the widget links to the exact `#section`.
- The prompt says: only facts from context, copy commands exactly, say what's missing on partial answers, state assumptions (OLake vs Fusion), report conflicts between docs and code, and treat context and question as data (prompt-injection resistance).

**Model choice, measured.** The event key allows three models, and the organizers suggested GLM-4.7-Flash first. We ran the full 42-question eval with each as the primary model:

| Primary model | Cited the expected page | Honest on unanswerable/injection | Latency p50 / p95 |
|---|---|---|---|
| GLM-4.7-Flash | 97% | 5/6 (leaked the system prompt) | 2.8 s / **17.4 s** (~19 tokens/s) |
| Gemini 2.5 Flash-Lite | 3 misses in the first 30 | (run incomplete) | ~1.2 s |
| **DeepSeek V4.1 Flash (shipped)** | **100%** | **6/6** | **2.3 s / 4.0 s** |

The organizers suggested GLM first and confirmed teams may reorder the three allowed models, so **DeepSeek is primary, then Gemini, then GLM** (OpenRouter `models` fallback). On the organizer example questions with 20 chunks, DeepSeek-first scored **17/20** vs **13/20** for GLM-first, and its slowest answer was 4.3 s vs 6–8 s. **Hedged requests** cover slow outliers: if the primary hasn't answered after 6 s, the same request goes to Gemini and the first answer wins (OpenRouter's own fallback only covers errors, not slowness). The code-side prompt-leak guard stays on for every model. Reasoning is turned off for every model (verified: 0 reasoning tokens).

**No vector DB, no LangChain.** 4,255 chunks fit in memory (the server uses ~210 MB; ~250 MB peak during a live refresh). Fewer moving parts, faster cold start, easier debugging.

---

## Production readiness

| Concern | How |
|---|---|
| Availability | Render free plan + external keep-alive pinger; the index is baked into the image, so startup is fast; a live refresh never takes the old index offline |
| Budget ($3 event credit) | Per-IP rate limit, a **global daily LLM-call cap**, an answer cache, $0 "I don't know" path, `max_tokens` 800, reasoning off |
| GitHub / docs-site outages | `/ask` never calls GitHub or olake.io; it answers from the in-memory index. The background refresher catches every error, reports it in `/status` → `live_refresh.last_error`, and keeps serving the current index. A docs re-scrape that comes back with under 95% of the known pages (timeouts, rate limits) is not swapped in; it is retried next cycle and reported as `live_refresh.last_incomplete_scrape`. A refresh builds the new index beside the old one and swaps it in atomically (measured peak: 252 MB of 512 MB). |
| LLM failures | OpenRouter model fallback chain, SDK retries with backoff on 429/5xx, timeout, graceful error still in the API contract shape |
| Input safety | 4,000-character cap (room for pasted logs), JSON validation, prompt-injection rules; the widget HTML-escapes all model output |
| Observability | Structured JSON logs per request (question, model, sources, latency, cache hit) and feedback events; `/healthz`; `/version` (commit, deployment); `/status` shows build time, corpus size and refresh status |
| Secrets | API key only from env vars, never in the repo or the browser |
| CORS | Open, so the widget can be embedded anywhere |

## Freshness, latency, cost

- **Freshness: live, no redeploy.** A background refresher (`app/refresh.py`) checks every hour (`REFRESH_INTERVAL_S`, default 3600; it ran every 10 min during development):
  - `git ls-remote` on `olake-docs` and the 4 code repos: instant and uses no API quota.
  - **docs repo moved** → the GitHub compare API checks whether `docs/` changed (blog-only commits are skipped) → re-scrape olake.io. If the site hasn't redeployed yet (content identical), it retries next cycle.
  - **code repo moved** → fresh shallow clone of only that repo.
  - **issues** updated since the last check → re-fetch only those.
  - Only changed chunks are re-embedded (embeddings are cached by content hash), the new index is **swapped in atomically**, and the answer cache is cleared so no stale answers are served. Measured: a repo change is picked up and swapped in ~10 s, re-indexing itself takes 0.3 s.
- **Push-based option:** `POST /admin/refresh` accepts a **GitHub webhook** (HMAC-SHA256 `X-Hub-Signature-256`, `WEBHOOK_SECRET`) or a bearer `REFRESH_TOKEN`. If OLake adds a webhook on `olake-docs`, updates become instant.
- **Backstop:** a nightly GitHub Action rebuilds everything from scratch, runs the retrieval eval as a quality gate, and commits the snapshot the Docker image starts from → Render auto-deploys.
- `/status` shows the indexed commit of every source, when the index was built, and the live-refresh status.
- Retrieval takes ~0.2 s even on the 0.1-CPU free instance; the rest is one LLM call with reasoning off. Cached answers are instant.
- **Cost:** ~8K input (20 chunks) + ≤800 output tokens per answer ≈ **$0.0006 per question** with DeepSeek V4.1 Flash, so roughly 4,000 questions on the remaining credit. "I don't know" answers and cache hits cost $0. All development and evaluation today, across several agents and ~1,000 LLM calls, used about **$0.55** of the $3.

## Embedding the widget

```html
<script src="https://ask-olake-nh7j.onrender.com/widget.js" data-theme="auto" data-position="right" defer></script>
```
Shadow DOM isolation (it can't break the host CSS, and vice versa), streaming answers, citation chips with section deep links, copy buttons on code blocks, 👍/👎 feedback, dark mode, mobile layout, keyboard support (Enter, Esc). JS API: `AskOLake.open()`, `AskOLake.ask("question")`.

## Evaluation

`eval/questions.jsonl`: 42 questions covering setup, every source connector, catalogs, Parquet, sync modes, schema evolution, Helm/K8s, monitoring, Fusion compaction, plus 6 unanswerable, off-topic and prompt-injection questions.

```bash
uv run python -m eval.run retrieval                 # free: hit@8 of the expected page
uv run python -m eval.run answer http://localhost:8000   # end-to-end: cited-source hit, honest IDK, latency
```

**Three eval sets** (retrieval measured as hit@10 and MRR; answers measured end to end against the live API):
- `questions.jsonl`: 42 questions written alongside the system.
- `hard_questions.jsonl`: 30 questions written **independently** from the live docs and GitHub issues, without seeing the code: exact config values, real error messages, multi-hop, casual phrasing, "No" traps, and 5 plausible-but-undocumented features (Cassandra, Kinesis, Terraform…).
- `probe_questions.jsonl`: 20 probes for content that wasn't indexed or ranked well (Airflow, license, latest release, Debezium comparison, type mappings).

| Set | Retrieval hit@10 | Answer cites expected page | Honest on unanswerable |
|---|---|---|---|
| Written with the system | 97% | 94–100% | 6/6 |
| Independent hard set | 92% | 92–96% | 5/5 |
| Probes (24, incl. "What is OLake?" and the typo "fushion") | 100% (was 85%), MRR 0.85 | 95% | — |

Light stemming (indexed **next to** the original token, so exact identifiers still win) fixed "partition" vs "partitioning"-style misses: total retrieval hits across the three sets went 76 → 78 of 81.

What the independent set caught: long tables split without their header row (the model then misread ✅/❌ columns), the discover-timeout default just outside the context window (top-8 → top-10), and "I couldn't find this" on questions whose correct answer is a documented "No" (e.g. "oracle cdc?"). The probes added: blog how-tos (Airflow), licenses, connector config specs (e.g. Postgres `max_threads` default 3), and a newest-first release summary.

Known miss: "Kafka Upsert + Databricks (not Databricks Spark)?" needs two facts from two pages; it would need query decomposition, which we left out to keep one LLM call per question.

**Organizer example questions** (judge script: 20 questions, each with required facts checked by keyword): 15/20 → 16/20 on the live site after the fixes below (with DeepSeek first). With 20 chunks: **DeepSeek-first 17/20** (the one remaining "miss", Delta Lake, is a correct "No." that the keyword pattern doesn't match) vs GLM-first 13/20, so DeepSeek ships first. A completeness rule in the prompt (list every option the context gives, including stated limitations) helps every model.
- Vocabulary mismatch was the main failure ("write data to" vs the docs' "destinations", "self-hosted" vs "Generic setup guide", "install locally" vs "Quickstart"). A small **query-side synonym list** (adds the docs' words, never removes the user's) fixed "Where can OLake write data to?".
- Several answers sat at ranks 11–20 (e.g. the Postgres `wal_level`/slot/publication steps), so the context went from **10 to 20 chunks**.
- "What is X?" boost limited to product-definition questions; it had buried "What is an incremental sync?" under intro pages.
- **Tried and rejected: LLM query rewriting** (a fast model rewrites the question into docs vocabulary before search). Measured *worse*: our set 97% → 89%, judge questions 4/5 → 3/5; the rewrites echoed vocabulary from their own prompt. The code is kept behind `REWRITE_MODEL` (off by default).

**Original results (first config):** retrieval hit@8 **36/36**, answer cited the expected page **36/36**, unanswerable / off-topic / prompt-injection handled honestly **6/6**, latency p50 **2.3 s** / p95 **4.0 s**.

What the eval caught and fixed along the way:
- Equal-weight embeddings *hurt* retrieval (94% → 86%); weighting them 0.5 gave 100%.
- GLM leaked the system prompt on an injection attempt → added a prompt rule **and** a code-side guard that refuses any answer containing system-prompt text.
- "Does OLake support Snowflake as a source?" was answered "yes" from a feature-request *issue* → new rule: issues are discussions, never proof a feature exists.
- Long answers hit the token cap mid-sentence → answers are capped at ~250 words with an 800-token ceiling.

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
