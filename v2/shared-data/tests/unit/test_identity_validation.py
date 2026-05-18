"""Unit tests: identity file structural validation (Gap 11) + cron entries (Gap 12)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"
IDENTITY_FILES = {
    "agent_alpha": AGENTS_DIR / "AGENT_ALPHA_IDENTITY.md",
    "agent_beta": AGENTS_DIR / "AGENT_BETA_IDENTITY.md",
    "agent_gamma": AGENTS_DIR / "AGENT_GAMMA_IDENTITY.md",
}

# Required top-level sections in every identity file
REQUIRED_SECTIONS = [
    "## Who I Am",
    "## Core Principles",
    "## Performance Tracker",
    "## What I Do NOT Do",
]

# Required metadata header fields
REQUIRED_HEADER_FIELDS = ["**Version:**", "**Created:**"]


# ── Identity file structural validation ──────────────────────────────────────
class TestIdentityFiles:
    @pytest.mark.parametrize("agent,fp", IDENTITY_FILES.items())
    def test_file_exists(self, agent, fp):
        assert fp.exists(), f"{agent} identity file missing at {fp}"

    @pytest.mark.parametrize("agent,fp", IDENTITY_FILES.items())
    def test_file_not_empty(self, agent, fp):
        assert fp.stat().st_size > 0, f"{agent} identity file is empty"

    @pytest.mark.parametrize("agent,fp", IDENTITY_FILES.items())
    def test_required_sections_present(self, agent, fp):
        text = fp.read_text()
        for section in REQUIRED_SECTIONS:
            assert section in text, (
                f"{agent}: missing section '{section}' in {fp.name}"
            )

    @pytest.mark.parametrize("agent,fp", IDENTITY_FILES.items())
    def test_required_header_fields(self, agent, fp):
        text = fp.read_text()
        for field in REQUIRED_HEADER_FIELDS:
            assert field in text, (
                f"{agent}: missing header field '{field}' in {fp.name}"
            )

    @pytest.mark.parametrize("agent,fp", IDENTITY_FILES.items())
    def test_agent_account_cap_mentioned(self, agent, fp):
        text = fp.read_text()
        assert "50k" in text or "50,000" in text or "$50" in text, (
            f"{agent}: identity file should mention $50k account cap"
        )

    def test_alpha_has_debate_reference(self):
        fp = IDENTITY_FILES["agent_alpha"]
        text = fp.read_text()
        assert "debate" in text.lower() or "judge" in text.lower(), (
            "Alpha identity should reference its debate chamber"
        )

    def test_beta_has_regime_reference(self):
        fp = IDENTITY_FILES["agent_beta"]
        text = fp.read_text()
        assert "regime" in text.lower(), (
            "Beta identity should reference regime detection"
        )

    def test_gamma_has_rsi_pullback_reference(self):
        fp = IDENTITY_FILES["agent_gamma"]
        text = fp.read_text()
        assert "rsi" in text.lower() or "connors" in text.lower(), (
            "Gamma identity should reference RSI pullback / Connors"
        )

    def test_gamma_rsi_period_is_10(self):
        fp = IDENTITY_FILES["agent_gamma"]
        text = fp.read_text()
        assert "RSI(10)" in text or "10-period RSI" in text.lower(), (
            "Gamma must specify RSI period 10, not 14"
        )

    @pytest.mark.parametrize("agent,fp", IDENTITY_FILES.items())
    def test_no_broken_markdown_links(self, agent, fp):
        text = fp.read_text()
        broken = re.findall(r'\[([^\]]+)\]\(\s*\)', text)
        assert broken == [], (
            f"{agent}: broken markdown links (empty href): {broken}"
        )


# ── Cron entries verification (Gap 12) ───────────────────────────────────────
EXPECTED_CRON_PATTERNS = [
    # Self-learning: weekly synthesis, every Friday 20:45 UTC
    r"45\s+20\s+\*\s+\*\s+5.*weekly_synthesis",
    # Self-learning: collect_learning, every 5 min
    r"\*/5\s+\*\s+\*\s+\*\s+\*.*collect_learning",
    # Alpha pipeline
    r"\*/15\s+13-20\s+\*\s+\*\s+1-5.*run_pipeline",
    # Beta agent
    r"\*/15\s+13-20\s+\*\s+\*\s+1-5.*beta_agent",
    # Position monitor
    r"\*/2\s+13-20\s+\*\s+\*\s+1-5.*position_monitor",
    # Sentinel agent (replaced auto_heal 2026-05-03). Cron is wrapper-driven —
    # fires every 15 min in bracket windows; sentinel_agent.py --auto reads ET
    # clock and dispatches to apply/observe/None.
    # 2026-05-18: weekend cron (0,6) removed to cut Sentinel LLM spend while
    # Alpha+Beta are paused. Only weekday cron remains.
    r"\*/15\s+12-21\s+\*\s+\*\s+1-5.*sentinel_agent\.py\s+--auto",
    # system_monitor: deterministic 13-check health report, every 2 min, all days
    r"\*/2\s+\*\s+\*\s+\*\s+\*\s+python3\s+/home/trader/QuantAI/v2/shared-data/scripts/system_monitor\.py",
]


def _get_crontab() -> str | None:
    """Return the root crontab as a string, or None if unavailable.

    The pipeline crons run under root on this VPS. Tests run as trader, so we
    try `sudo crontab -l` first, then fall back to the trader crontab.
    """
    for cmd in (["sudo", "-n", "crontab", "-l"], ["crontab", "-l"]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except Exception:
            continue
    return None


@pytest.mark.parametrize("pattern", EXPECTED_CRON_PATTERNS)
def test_cron_entry_exists(pattern):
    """Verify each expected cron entry is present in the root crontab."""
    crontab = _get_crontab()
    if crontab is None:
        pytest.skip("Cannot read crontab (may need sudo) — skipping")

    assert re.search(pattern, crontab), (
        f"Missing cron entry matching pattern: {pattern!r}\n"
        f"Current crontab (first 20 lines):\n"
        + "\n".join(crontab.splitlines()[:20])
    )
