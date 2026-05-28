"""Retry + parse hardening envelope for all LLM calls.

Two entry points:
  call_llm_json()  — call LLM, parse JSON, retry on failure
  call_llm_text()  — call LLM, return raw text, retry on failure

Retry strategy: 3 attempts with adaptive backoff.
On 429 (rate limit): respect Retry-After header, exponential backoff (30s/60s/120s),
  cross-run state file prevents repeated hammering across process restarts.
On transient errors (timeout, 5xx): retry with default delays (2s/4s).
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
USAGE_LOG = Path("/root/quantai-v2/shared-data/logs/llm_usage.jsonl")
DISCORD_ALERT_COOLDOWN_SECONDS = 3600

# Pricing per 1M tokens (USD). Updated when Anthropic changes pricing.
_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5":          {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-20250514":  {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514":    {"input": 15.00, "output": 75.00},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts. Falls back to Sonnet pricing for unknown models."""
    p = _PRICING.get(model)
    if p is None:
        # Try prefix match: "claude-sonnet-4-6-20260101" → "claude-sonnet-4-6"
        for key in _PRICING:
            if model.startswith(key):
                p = _PRICING[key]
                break
    if p is None:
        p = {"input": 3.0, "output": 15.0}  # default to Sonnet pricing
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


def _log_usage(caller: str, model_requested: str, resp, latency_ms: int, tier: str) -> None:
    """Append one usage record to llm_usage.jsonl. Never raises."""
    try:
        usage = getattr(resp, "usage", None) or {}
        if isinstance(usage, dict):
            # ClawRoute returns OpenAI keys; bypass returns Anthropic keys
            inp = usage.get("input_tokens") or usage.get("prompt_tokens", 0)
            out = usage.get("output_tokens") or usage.get("completion_tokens", 0)
        else:
            # Usage might be an object with attributes
            inp = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
        actual_model = getattr(resp, "model", model_requested) or model_requested
        cost = _estimate_cost(actual_model, inp, out)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "caller": caller,
            "model_requested": model_requested,
            "model_actual": actual_model,
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": round(cost, 8),
            "latency_ms": latency_ms,
            "tier": tier or "",
        }
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Never break the caller

_RETRY_DELAYS = [2, 4]
_RATE_LIMIT_STATE = Path("/root/quantai-v2/shared-data/cache/llm_rate_limit_state.json")

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


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if the exception is a 429 rate-limit error."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _get_retry_delay(exc: Exception, attempt: int) -> float:
    """Return delay in seconds. Respects Retry-After header for 429s."""
    if _is_rate_limit_error(exc):
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after:
            try:
                return min(float(retry_after), 120)  # Cap at 2 minutes
            except ValueError:
                pass
        # No header — exponential backoff: 30s, 60s, 120s
        return min(30 * (2 ** attempt), 120)
    # Default delays for non-429 errors
    return _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]


def _check_rate_limit_backoff(caller: str) -> float | None:
    """If a recent 429 was recorded, return seconds to wait. None if clear."""
    try:
        if not _RATE_LIMIT_STATE.exists():
            return None
        data = json.loads(_RATE_LIMIT_STATE.read_text())
        last_429 = data.get("last_429_ts", "")
        if not last_429:
            return None
        last_dt = datetime.fromisoformat(last_429)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        backoff = data.get("backoff_seconds", 300)
        if elapsed < backoff:
            remaining = backoff - elapsed
            logging.warning(
                "[%s] rate-limit backoff active (%.0fs remaining) — skipping LLM call",
                caller, remaining,
            )
            return remaining
        # Backoff expired — clear state
        _RATE_LIMIT_STATE.unlink(missing_ok=True)
        return None
    except Exception:
        return None


def _record_rate_limit(backoff_seconds: int = 300) -> None:
    """Record a 429 hit for cross-run backoff."""
    try:
        _RATE_LIMIT_STATE.parent.mkdir(parents=True, exist_ok=True)
        _RATE_LIMIT_STATE.write_text(json.dumps({
            "last_429_ts": datetime.now(timezone.utc).isoformat(),
            "backoff_seconds": backoff_seconds,
        }))
    except Exception:
        pass


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
    # Cross-run rate-limit guard
    remaining = _check_rate_limit_backoff(caller)
    if remaining is not None:
        _log_failure(caller, model, f"rate-limit backoff active ({remaining:.0f}s remaining)")
        return None

    client = _get_client()
    last_error = ""
    last_raw = ""

    for attempt in range(max_retries):
        try:
            _t0 = time.time()
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                timeout=timeout,
                **({"tier": tier} if tier else {}),
            )
            _latency = int((time.time() - _t0) * 1000)
            text = resp.content[0].text
            if not text:
                last_error = "LLM returned empty text"
                last_raw = ""
                logging.warning("[%s] attempt %d: empty response", caller, attempt + 1)
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue

            last_raw = text
            result = _parse_json(text)
            _log_usage(caller, model, resp, _latency, tier or "")
            return result

        except ValueError as e:
            last_error = str(e)
            logging.warning("[%s] attempt %d: JSON parse failed: %s", caller, attempt + 1, e)
            user_suffix = "\n\nYour previous response was not valid JSON. Return ONLY a JSON object, no explanation."
            if user_suffix not in user:
                user = user + user_suffix
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            time.sleep(delay)
            continue

        except Exception as e:
            last_error = str(e)
            last_raw = ""
            if _is_permanent_error(e):
                logging.warning("[%s] attempt %d: permanent error: %s", caller, attempt + 1, e)
                break
            if _is_rate_limit_error(e):
                _record_rate_limit(300)  # 5-min cross-run backoff
            delay = _get_retry_delay(e, attempt)
            logging.warning(
                "[%s] attempt %d: LLM call failed (%s), retrying in %.0fs",
                caller, attempt + 1, e, delay,
            )
            time.sleep(delay)
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
    # Cross-run rate-limit guard
    remaining = _check_rate_limit_backoff(caller)
    if remaining is not None:
        _log_failure(caller, model, f"rate-limit backoff active ({remaining:.0f}s remaining)")
        return None

    client = _get_client()
    last_error = ""

    for attempt in range(max_retries):
        try:
            _t0 = time.time()
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                timeout=timeout,
                **({"tier": tier} if tier else {}),
            )
            _latency = int((time.time() - _t0) * 1000)
            text = resp.content[0].text
            if not text:
                last_error = "LLM returned empty text"
                logging.warning("[%s] attempt %d: empty response", caller, attempt + 1)
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue
            _log_usage(caller, model, resp, _latency, tier or "")
            return text

        except Exception as e:
            last_error = str(e)
            if _is_permanent_error(e):
                logging.warning("[%s] attempt %d: permanent error: %s", caller, attempt + 1, e)
                break
            if _is_rate_limit_error(e):
                _record_rate_limit(300)  # 5-min cross-run backoff
            delay = _get_retry_delay(e, attempt)
            logging.warning(
                "[%s] attempt %d: LLM call failed (%s), retrying in %.0fs",
                caller, attempt + 1, e, delay,
            )
            time.sleep(delay)
            continue

    _log_failure(caller, model, last_error)
    _discord_alert(
        f"🔴 LLM call failed after {max_retries} attempts\n"
        f"Caller: {caller}\nModel: {model}\nError: {last_error[:500]}",
        caller,
    )
    return None
