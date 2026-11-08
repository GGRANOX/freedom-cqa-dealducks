"""Единый адаптер к моделям: OpenAI, Gemini (Google Cloud / Vertex express), Groq.

call_json(provider, system, user, schema) -> LLMResponse(data, usage, cost, model)
Ответ всегда JSON по схеме (structured outputs), повтор при сетевых/5xx/429 ошибках.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field

import httpx

from backend import config


@dataclass
class LLMResponse:
    data: dict
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    raw_text: str = ""


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_model: dict = field(default_factory=dict)

    def add(self, r: LLMResponse) -> None:
        self.tokens_in += r.tokens_in
        self.tokens_out += r.tokens_out
        self.cost_usd += r.cost_usd
        self.calls += 1
        self.by_model[r.model] = self.by_model.get(r.model, 0) + 1


class LLMError(RuntimeError):
    pass


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=20.0))
    return _client


def _cost(model: str, tin: int, tout: int) -> float:
    pin, pout = config.price(model)
    return (tin * pin + tout * pout) / 1_000_000


def _record_spend(cost: float) -> None:
    try:
        from backend import storage
        storage.add_spend(cost)
    except Exception:  # noqa: BLE001 — учёт расходов не должен ломать оценку
        pass


def model_for(provider: str) -> str:
    return {"openai": config.OPENAI_MODEL, "gemini": config.GEMINI_MODEL, "groq": config.GROQ_MODEL}[provider]


def _parse(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s >= 0 and e > s:
            return json.loads(t[s:e + 1])
        raise


async def _openai_compatible(url: str, key: str, model: str, system: str, user: str,
                             schema: dict, name: str, reasoning: str | None) -> tuple[str, int, int]:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": name, "strict": True, "schema": schema}},
    }
    if reasoning:
        body["reasoning_effort"] = reasoning
    else:
        body["temperature"] = config.TEMPERATURE
    r = await client().post(url, headers={"Authorization": f"Bearer {key}"}, json=body)
    if r.status_code >= 400:
        raise LLMError(f"{r.status_code}: {r.text[:300]}")
    d = r.json()
    u = d.get("usage", {})
    return d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


async def _gemini(model: str, system: str, user: str, schema: dict) -> tuple[str, int, int]:
    url = (f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"
           f"?key={config.GEMINI_API_KEY}")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": config.TEMPERATURE, "responseMimeType": "application/json",
                             "responseJsonSchema": schema},
    }
    r = await client().post(url, json=body)
    if r.status_code >= 400:
        raise LLMError(f"{r.status_code}: {r.text[:300]}")
    d = r.json()
    parts = d["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    u = d.get("usageMetadata", {})
    out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return text, u.get("promptTokenCount", 0), out


class RateLimited(LLMError):
    pass


async def call_json(provider: str, system: str, user: str, schema: dict, name: str = "result",
                    retries: int = 5) -> LLMResponse:
    model = model_for(provider)
    last: Exception | None = None
    for attempt in range(retries):
        t0 = time.perf_counter()
        try:
            if provider == "openai":
                text, tin, tout = await _openai_compatible(
                    "https://api.openai.com/v1/chat/completions", config.OPENAI_API_KEY, model,
                    system, user, schema, name, config.OPENAI_REASONING)
            elif provider == "groq":
                text, tin, tout = await _openai_compatible(
                    "https://api.groq.com/openai/v1/chat/completions", config.GROQ_API_KEY, model,
                    system, user, schema, name, None)
            elif provider == "gemini":
                text, tin, tout = await _gemini(model, system, user, schema)
            else:
                raise LLMError(f"неизвестный провайдер {provider}")
            data = _parse(text)
            resp = LLMResponse(data=data, model=model, provider=provider, tokens_in=tin, tokens_out=tout,
                               cost_usd=_cost(model, tin, tout),
                               duration_ms=int((time.perf_counter() - t0) * 1000), raw_text=text)
            _record_spend(resp.cost_usd)
            return resp
        except (httpx.HTTPError, LLMError, json.JSONDecodeError, KeyError, IndexError) as e:
            last = e
            msg = str(e)
            if isinstance(e, LLMError) and msg[:3] in ("400", "401", "403", "404"):
                break  # ошибка запроса — повтор не поможет
            # экспоненциальная пауза с разбросом; на 429 (лимит провайдера) — дольше
            base = 4.0 if msg.startswith("429") else 1.5
            await asyncio.sleep(min(30.0, base * (2 ** attempt)) * (0.75 + random.random() / 2))
    raise LLMError(f"{provider}/{model}: {last}")
