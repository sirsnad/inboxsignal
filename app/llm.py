"""OpenRouter client wrapper.

All model calls go through complete_json: prompt-instructed JSON with lenient
parsing (works across providers regardless of structured-output support),
one retry on invalid output, then the caller's fallback.
"""

import json
import logging
import re

from openai import OpenAI

from . import config

log = logging.getLogger("signal.llm")

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Add it to .env (local) or the "
                "cloud environment's variables."
            )
        _client = OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
        )
    return _client


def _extract_json(text: str):
    """Pull the first JSON object out of a model response, tolerating prose
    and code fences around it."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        if start == -1:
            raise ValueError(f"no JSON object in response: {text[:200]!r}")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    break
        if candidate is None:
            raise ValueError(f"unbalanced JSON in response: {text[:200]!r}")
    return json.loads(candidate)


def complete_json(task: str, system: str, user: str, max_tokens: int = 1024) -> dict:
    """One call, one JSON dict back. task picks the model from config.MODELS.
    Raises on failure after one retry; callers decide the fallback."""
    model = config.MODELS[task]
    last_err: Exception | None = None
    for attempt in range(2):
        resp = client().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        try:
            return _extract_json(text)
        except ValueError as e:
            last_err = e
            log.warning("invalid JSON from %s (attempt %d): %s", model, attempt + 1, e)
    raise ValueError(f"model {model} returned no valid JSON: {last_err}")
