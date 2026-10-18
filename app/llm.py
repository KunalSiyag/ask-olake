"""OpenRouter chat client with model fallback, hedged requests, reasoning off, timeouts and retries."""

import asyncio
from collections.abc import AsyncIterator

from openai import APIStatusError, AsyncOpenAI

from app import config

_client = AsyncOpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.OPENROUTER_API_KEY or "missing",
    timeout=config.LLM_TIMEOUT_S,
    max_retries=2,  # SDK retries 429/5xx with backoff
)


def _request(messages: list[dict], reasoning_off: bool, model: str | None = None) -> dict:
    model = model or config.LLM_MODEL
    extra = {}
    fallbacks = [m for m in config.LLM_FALLBACK_MODELS if m != model]
    if fallbacks:
        extra["models"] = fallbacks
    if reasoning_off:
        extra["reasoning"] = {"effort": "none"}
    return dict(
        model=model,
        messages=messages,
        max_tokens=config.MAX_ANSWER_TOKENS,
        temperature=0.1,
        extra_body=extra,
    )


async def _complete_one(messages: list[dict], model: str | None = None) -> tuple[str, str]:
    try:
        resp = await _client.chat.completions.create(**_request(messages, reasoning_off=True, model=model))
    except APIStatusError as e:
        if e.status_code != 400:
            raise
        # some providers reject the reasoning field; retry without it
        resp = await _client.chat.completions.create(**_request(messages, reasoning_off=False, model=model))
    return (resp.choices[0].message.content or "").strip(), resp.model


async def complete(messages: list[dict]) -> tuple[str, str]:
    """Return (text, model_that_answered).

    Hedged request: OpenRouter's fallback only covers errors, not slowness, so if the
    primary model hasn't answered after HEDGE_AFTER_S we also ask the first fallback
    model and take whichever finishes first (extra cost only on slow requests)."""
    primary = asyncio.create_task(_complete_one(messages))
    if not config.LLM_FALLBACK_MODELS or config.HEDGE_AFTER_S <= 0:
        return await primary
    done, _ = await asyncio.wait({primary}, timeout=config.HEDGE_AFTER_S)
    if done:
        return primary.result()
    hedge = asyncio.create_task(_complete_one(messages, model=config.LLM_FALLBACK_MODELS[0]))
    pending = {primary, hedge}
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception() is None:
                for other in pending:
                    other.cancel()
                return task.result()
    return primary.result()  # both failed: surface the primary's error


async def stream(messages: list[dict]) -> AsyncIterator[tuple[str, str]]:
    """Yield (text_delta, model) pairs."""
    try:
        chunks = await _client.chat.completions.create(**_request(messages, reasoning_off=True), stream=True)
    except APIStatusError as e:
        if e.status_code != 400:
            raise
        chunks = await _client.chat.completions.create(**_request(messages, reasoning_off=False), stream=True)
    async for chunk in chunks:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content, chunk.model


REWRITE_PROMPT = """You turn a user's question about OLake into a search query for the OLake documentation.
OLake docs vocabulary: OLake Go (ingestion), sources/connectors (Postgres, MySQL, MongoDB, Oracle, MSSQL, DB2, Kafka, S3),
destinations/writers (Apache Iceberg, Parquet, S3, GCS, ADLS), catalogs (Glue, Hive, JDBC, REST, Nessie, Polaris, Unity, S3 Tables),
sync modes (Full Refresh, Full Refresh + Incremental, Full Refresh + CDC, CDC Only, Strict CDC), cursor field, state.json, streams.json,
source.json, destination.json, replication slot, publication, pgoutput, wal_level, binlog, oplog, replica set, OLake UI (Docker Compose
quickstart, docker-compose, localhost:8000), Helm/Kubernetes, OLake Fusion (Iceberg table maintenance, compaction Lite/Medium/Full).
Generic setup guides are called "Generic ... Setup Guide" (self-hosted / on-prem servers).
Reply with ONE line of 5-15 search keywords using the docs' terms (no sentences, no explanation)."""


async def rewrite_query(question: str) -> str | None:
    """Docs-vocabulary keywords for the question, or None if the call fails or is slow.
    Retrieval falls back to the original question, so this can only add recall."""
    try:
        resp = await asyncio.wait_for(_client.chat.completions.create(
            model=config.REWRITE_MODEL, max_tokens=60, temperature=0,
            messages=[{"role": "system", "content": REWRITE_PROMPT}, {"role": "user", "content": question[:1000]}],
            extra_body={"reasoning": {"effort": "none"}}), timeout=config.REWRITE_TIMEOUT_S)
        text = (resp.choices[0].message.content or "").strip().splitlines()[0][:300]
        return text or None
    except Exception:
        return None
