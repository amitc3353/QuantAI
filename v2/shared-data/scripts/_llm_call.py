"""Retry + parse hardening envelope for all LLM calls.

Two entry points:
  call_llm_json()  — call LLM, parse JSON, retry on failure
  call_llm_text()  — call LLM, return raw text, retry on failure

Retry strategy: 3 attempts, 2s then 4s backoff.
On transient errors (timeout, 5xx, rate limit): retry.
On permanent errors (401/403): fail immediately.
On JSON parse failure: retry with corrective hint.
On all retries exhausted: log raw payload, Discord alert (rate-limited), return None.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

FAILURE_LOG = Path("/root/quantai-v2/shared-data/logs/llm_failures.jsonl")
DISCORD_ALERT_COOLDOWN_SECONDS = 3600

_RETRY_DELAYS = [2, 4]

_discord_alert_timestamps: dict[str, float] = {}


def _get_client():
    from _llm_client import Client
    return Client()


def _parse_json(text: str):
    """Parse JSON from LLM response text with progressive fallbacks.

    1. Direct json.loads
    2. Strip markdown fences, then json.loads
    3. Extract substring between first { and last }, then json.loads
    4. Extract substring between first [ and last ], then json.loads

    Raises ValueError if all fallbacks fail.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    if stripped != text:
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f"no valid JSON found in response: {text[:200]}")


def _is_permanent_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 403)
    return False


def _do_discord_post(msg: str) -> None:
    try:
        ch = os.environ.get("DISCORD_CHANNEL_ALERTS", "")
        if ch:
            from _discord import post_to_channel
            post_to_channel(ch, msg)
    except Exception:
        pass


def _discord_alert(msg: str, caller: str) -> None:
    now = time.monotonic()
    last = _discord_alert_timestamps.get(caller, 0)
    if now - last < DISCORD_ALERT_COOLDOWN_SECONDS:
        return
    _discord_alert_timestamps[caller] = now
    _do_discord_post(msg)


def _log_failure(caller: str, model: str, error: str, raw_response: str = "") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "model": model,
        "error": error,
        "raw_response": raw_response[:2000],
    }
    try:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILURE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.warning("_llm_call: failure log write failed: %s", e)


def call_llm_json(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    timeout: int = 120,
    max_retries: int = 3,
    caller: str = "",
    tier: str | None = None,
) -> dict | list | None:
    client = _get_client()
    last_error = ""
    last_raw = ""

    for attempt in range(max_retries):
        if attempt > 0:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            time.sleep(delay)

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                timeout=timeout,
                **({"tier": tier} if tier else {}),
            )
            text = resp.content[0].text
            if not text:
                last_error = "LLM returned empty text"
                last_raw = ""
                logging.warning("[%s] attempt %d: empty response", caller, attempt + 1)
                continue

            last_raw = text
            result = _parse_json(text)
            return result

        except ValueError as e:
            last_error = str(e)
            logging.warning("[%s] attempt %d: JSON parse failed: %s", caller, attempt + 1, e)
            user_suffix = "\n\nYour previous response was not valid JSON. Return ONLY a JSON object, no explanation."
            if user_suffix not in user:
                user = user + user_suffix
            continue

        except Exception as e:
            last_error = str(e)
            last_raw = ""
            logging.warning("[%s] attempt %d: LLM call failed: %s", caller, attempt + 1, e)
            if _is_permanent_error(e):
                break
            continue

    _log_failure(caller, model, last_error, last_raw)
    _discord_alert(
        f"🔴 LLM call failed after {max_retries} attempts\n"
        f"Caller: {caller}\nModel: {model}\nError: {last_error[:500]}",
        caller,
    )
    return None


def call_llm_text(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2500,
    timeout: int = 90,
    max_retries: int = 3,
    caller: str = "",
    tier: str | None = None,
) -> str | None:
    client = _get_client()
    last_error = ""

    for attempt in range(max_retries):
        if attempt > 0:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            time.sleep(delay)

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                timeout=timeout,
                **({"tier": tier} if tier else {}),
            )
            text = resp.content[0].text
            if not text:
                last_error = "LLM returned empty text"
                logging.warning("[%s] attempt %d: empty response", caller, attempt + 1)
                continue
            return text

        except Exception as e:
            last_error = str(e)
            logging.warning("[%s] attempt %d: LLM call failed: %s", caller, attempt + 1, e)
            if _is_permanent_error(e):
                break
            continue

    _log_failure(caller, model, last_error)
    _discord_alert(
        f"🔴 LLM call failed after {max_retries} attempts\n"
        f"Caller: {caller}\nModel: {model}\nError: {last_error[:500]}",
        caller,
    )
    return None
