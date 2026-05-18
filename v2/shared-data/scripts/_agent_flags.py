"""Per-agent kill-switch helper (added 2026-05-11).

Each trading agent (Alpha / Beta / Gamma) reads its own ``<AGENT>_ENABLED``
flag from the environment via :func:`is_agent_enabled`. The flag is loaded
from ``/home/trader/QuantAI/.env`` by the agent script's existing auto-loader
before this module is imported.

Semantics
=========

* Default ON. Only the literal string ``"1"`` enables the agent; anything else
  (``"0"``, ``"true"``, empty, missing) disables it. Strict equality matches
  the existing ``GAMMA_AB_TEST_ENABLED`` flag convention.
* Disabling an agent stops NEW entries only. ``position_monitor.py`` runs
  independently and continues to exit existing positions on RSI/PnL/time/trend
  triggers.

Discord rate-limiting
=====================

:func:`notify_once_per_day_disabled` uses a per-day marker file under ``/tmp``
to ensure each disabled agent posts at most one "🚫 disabled" Discord notice
per calendar day, even though the cron may fire 32 times during market hours.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Callable, Optional

VALID_AGENTS = ("alpha", "beta", "gamma")


def is_agent_enabled(agent: str) -> bool:
    """Return True iff the env var ``<AGENT>_ENABLED`` is exactly ``"1"``.

    Default ON when the variable is missing entirely so existing
    deployments continue to work unchanged after this module ships.

    Args:
        agent: one of ``"alpha"``, ``"beta"``, ``"gamma"`` (case-insensitive)

    Raises:
        ValueError: on unknown agent identifier
    """
    a = agent.lower()
    if a not in VALID_AGENTS:
        raise ValueError(f"unknown agent {agent!r} (valid: {VALID_AGENTS})")
    key = f"{a.upper()}_ENABLED"
    return os.environ.get(key, "1") == "1"


def _marker_path(agent: str) -> Path:
    """Path of the daily marker file used by :func:`notify_once_per_day_disabled`.

    Exposed as a module-level helper so tests can monkeypatch it to a
    ``tmp_path`` sandbox without writing to real ``/tmp``.
    """
    return Path(
        f"/tmp/quantai_{agent.lower()}_disabled_notified_{date.today().isoformat()}.flag"
    )


def notify_once_per_day_disabled(
    agent: str,
    post_discord: Optional[Callable[[str], None]] = None,
) -> bool:
    """Post a "🚫 disabled" Discord notice — at most once per agent per day.

    The first call for a given (agent, today) writes a marker file and
    invokes ``post_discord``. Subsequent calls on the same calendar day
    silently return False so the operator isn't spammed by 32 ticks/day.

    Args:
        agent: ``"alpha"`` / ``"beta"`` / ``"gamma"``
        post_discord: callable accepting a single ``str`` message. Typically
            the agent's existing ``_post_discord`` helper. If ``None``, the
            function is a no-op (callers can pass ``None`` in tests).

    Returns:
        True iff a Discord post was attempted on this call. False if the
        marker already existed for today, or if ``post_discord`` is ``None``,
        or if the underlying post raised.
    """
    a = agent.lower()
    if a not in VALID_AGENTS:
        raise ValueError(f"unknown agent {agent!r} (valid: {VALID_AGENTS})")
    if post_discord is None:
        return False
    marker = _marker_path(a)
    if marker.exists():
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        # /tmp not writable — fall through and post anyway. Better noisy
        # than silent if the marker can't be persisted.
        pass
    msg = (
        f"🚫 Agent {a.title()} disabled (.env {a.upper()}_ENABLED=0). "
        f"New entries skipped; position_monitor still exits open trades."
    )
    try:
        post_discord(msg)
    except Exception:
        return False
    return True
