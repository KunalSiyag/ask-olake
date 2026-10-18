"""OpenRouter chat client with model fallback, reasoning off, timeouts and retries."""

from collections.abc import AsyncIterator

from openai import APIStatusError, AsyncOpenAI

from app import config

_client = AsyncOpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.OPENROUTER_API_KEY or "missing",
    timeout=config.LLM_TIMEOUT_S,
    max_retries=2,  # SDK retries 429/5xx with backoff
)


def _request(messages: list[dict], reasoning_off: bool) -> dict:
    extra = {}
    if config.LLM_FALLBACK_MODELS:
        extra["models"] = config.LLM_FALLBACK_MODELS
    if reasoning_off:
        extra["reasoning"] = {"effort": "none"}
    return dict(
        model=config.LLM_MODEL,
        messages=messages,
        max_tokens=config.MAX_ANSWER_TOKENS,
        temperature=0.1,
        extra_body=extra,
    )


async def complete(messages: list[dict]) -> tuple[str, str]:
    """Return (text, model_that_answered)."""
    try:
        resp = await _client.chat.completions.create(**_request(messages, reasoning_off=True))
    except APIStatusError as e:
        if e.status_code != 400:
            raise
        # some providers reject the reasoning field; retry without it
        resp = await _client.chat.completions.create(**_request(messages, reasoning_off=False))
    return (resp.choices[0].message.content or "").strip(), resp.model


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
