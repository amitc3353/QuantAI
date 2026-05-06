"""LLM client shim — routes all calls through ClawRoute (localhost:18790).

Drop-in replacements for two patterns:

  1. Anthropic SDK style (debate_chamber, sentinel_agent, services/cto_*):

         from _llm_client import Client
         client = Client()
         resp = client.messages.create(
             model="claude-sonnet-4-5",
             max_tokens=2000,
             system="You are...",
             messages=[{"role": "user", "content": "..."}],
         )
         text = resp.content[0].text

  2. Functional / async-friendly (orchestrator agents):

         from _llm_client import chat
         text = chat(
             messages=[{"role": "user", "content": "..."}],
             system=None,
             model="claude-haiku-4-5",
             max_tokens=60,
             timeout=8,
         )

Escape valve: LLM_BYPASS_CLAWROUTE=1 reverts to direct Anthropic API
(uses the anthropic SDK for Client, raw httpx for chat()). Use only during
incident response when ClawRoute is down.

The "model" arg is a hint — ClawRoute's tier classifier picks the actual
model. Set X-LLM-Tier header via tier="COMPLEX" kwarg to force a tier.
"""
import json
import os
from dataclasses import dataclass, field

import httpx


CLAWROUTE_BASE = os.environ.get("CLAWROUTE_BASE_URL", "http://127.0.0.1:18790/v1")
DEFAULT_TIMEOUT = float(os.environ.get("LLM_HTTP_TIMEOUT", "120"))


def _is_bypass() -> bool:
    return os.environ.get("LLM_BYPASS_CLAWROUTE") == "1"


@dataclass
class _ContentBlock:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list
    model: str = ""
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None


def _to_openai_messages(messages: list, system) -> list:
    out = []
    if system:
        out.append({"role": "system", "content": system if isinstance(system, str) else json.dumps(system)})
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):
            c = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
        out.append({"role": m["role"], "content": c})
    return out


def _post_clawroute(body: dict, timeout: float, tier: str | None = None) -> dict:
    # ClawRoute quirks:
    # (1) Auth middleware 500s on Bearer tokens it doesn't recognize, so omit it.
    # (2) ClawRoute forwards upstream `content-encoding: gzip` headers verbatim,
    #     but its middleware already decompressed the body. Both httpx and
    #     requests trip on this. iter_raw() bypasses content-decoding entirely.
    headers = {"Content-Type": "application/json"}
    if tier:
        headers["X-LLM-Tier"] = tier
    with httpx.stream(
        "POST",
        f"{CLAWROUTE_BASE}/chat/completions",
        json=body,
        headers=headers,
        timeout=timeout,
    ) as r:
        raw = b"".join(r.iter_raw())
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"ClawRoute {r.status_code}: {raw[:500].decode('utf-8', errors='replace')}",
                request=r.request,
                response=r,
            )
    return json.loads(raw)


def chat(
    messages: list,
    system: str | None = None,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 1024,
    timeout: float = DEFAULT_TIMEOUT,
    tier: str | None = None,
    **kwargs,
) -> str:
    """Functional helper — returns the assistant's text reply as a plain string.

    Suitable for one-shot completions where the caller doesn't need usage
    metadata or the full response object.
    """
    if _is_bypass():
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        body = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            body["system"] = system
        for k in ("temperature", "top_p", "stop_sequences"):
            if k in kwargs:
                body[k] = kwargs[k]
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text").strip()

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _to_openai_messages(messages, system),
    }
    for k in ("temperature", "top_p", "stop"):
        if k in kwargs:
            body[k] = kwargs[k]
    data = _post_clawroute(body, timeout=timeout, tier=tier)
    return data["choices"][0]["message"]["content"].strip()


class _Messages:
    def __init__(self, parent):
        self._p = parent

    def create(self, model: str, messages: list, max_tokens: int, system=None, **kwargs):
        return self._p._create(model=model, messages=messages, max_tokens=max_tokens, system=system, **kwargs)


class Client:
    """Drop-in for anthropic.Anthropic. Routes through ClawRoute by default."""

    def __init__(self, api_key: str | None = None):
        self.bypass = _is_bypass()
        self._real = None
        if self.bypass:
            import anthropic
            self._real = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""))
        self.messages = _Messages(self)

    def _create(self, model, messages, max_tokens, system=None, **kwargs):
        if self.bypass:
            return self._real.messages.create(
                model=model, messages=messages, max_tokens=max_tokens,
                system=system, **kwargs,
            )
        tier = kwargs.pop("tier", None)
        timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": _to_openai_messages(messages, system),
        }
        for k in ("temperature", "top_p", "stop"):
            if k in kwargs:
                body[k] = kwargs[k]
        data = _post_clawroute(body, timeout=timeout, tier=tier)
        choice = data["choices"][0]
        text = choice["message"]["content"]
        return _Response(
            content=[_ContentBlock(text=text)],
            model=data.get("model", model),
            usage=data.get("usage", {}),
            stop_reason=choice.get("finish_reason"),
        )
