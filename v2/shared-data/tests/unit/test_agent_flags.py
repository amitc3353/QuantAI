"""Tests for _agent_flags — per-agent kill-switch helper.

Covers is_agent_enabled semantics (default ON, strict "1" matching) and
notify_once_per_day_disabled's daily-rate-limited Discord post.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def _reload_flags(monkeypatch):
    """Ensure each test sees a fresh os.environ for the *_ENABLED keys.

    Tests scope env via monkeypatch.setenv; this fixture pre-removes any
    inherited values so default-on tests aren't polluted by parent shell.
    """
    for agent in ("alpha", "beta", "gamma"):
        monkeypatch.delenv(f"{agent.upper()}_ENABLED", raising=False)


@pytest.fixture()
def sandbox_marker(monkeypatch, tmp_path):
    """Redirect the daily-marker file into tmp_path so tests don't touch /tmp."""
    import _agent_flags as af

    def _path(agent: str) -> Path:
        from datetime import date
        return tmp_path / f"quantai_{agent.lower()}_disabled_notified_{date.today().isoformat()}.flag"

    monkeypatch.setattr(af, "_marker_path", _path)
    return tmp_path


class TestIsAgentEnabled:
    def test_default_on_when_env_unset(self):
        import _agent_flags as af
        assert af.is_agent_enabled("alpha") is True
        assert af.is_agent_enabled("beta") is True
        assert af.is_agent_enabled("gamma") is True

    def test_explicit_one_is_on(self, monkeypatch):
        import _agent_flags as af
        monkeypatch.setenv("ALPHA_ENABLED", "1")
        assert af.is_agent_enabled("alpha") is True

    def test_zero_is_off(self, monkeypatch):
        import _agent_flags as af
        monkeypatch.setenv("BETA_ENABLED", "0")
        assert af.is_agent_enabled("beta") is False

    def test_non_one_strings_are_off(self, monkeypatch):
        """Strict equality with '1' — matches GAMMA_AB_TEST_ENABLED semantics."""
        import _agent_flags as af
        for val in ("true", "True", "yes", "on", "", "x", "01", " 1"):
            monkeypatch.setenv("GAMMA_ENABLED", val)
            assert af.is_agent_enabled("gamma") is False, (
                f"Unexpected truthy for value {val!r}"
            )

    def test_case_insensitive_agent_id(self):
        import _agent_flags as af
        assert af.is_agent_enabled("ALPHA") is True
        assert af.is_agent_enabled("Gamma") is True

    def test_unknown_agent_raises(self):
        import _agent_flags as af
        with pytest.raises(ValueError, match="unknown agent"):
            af.is_agent_enabled("delta")


class TestNotifyOncePerDayDisabled:
    def test_posts_first_call(self, sandbox_marker):
        import _agent_flags as af
        posted = []
        result = af.notify_once_per_day_disabled(
            "alpha", post_discord=lambda msg: posted.append(msg)
        )
        assert result is True
        assert len(posted) == 1
        assert "Alpha disabled" in posted[0]
        assert "ALPHA_ENABLED=0" in posted[0]
        # Marker file was created
        assert af._marker_path("alpha").exists()

    def test_skips_after_marker_exists(self, sandbox_marker):
        import _agent_flags as af
        # Pre-create the marker (simulates an earlier tick today)
        marker = af._marker_path("beta")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        posted = []
        result = af.notify_once_per_day_disabled(
            "beta", post_discord=lambda msg: posted.append(msg)
        )
        assert result is False
        assert posted == []

    def test_separate_agents_post_independently(self, sandbox_marker):
        import _agent_flags as af
        posts = []
        r1 = af.notify_once_per_day_disabled("alpha", post_discord=lambda m: posts.append(("a", m)))
        r2 = af.notify_once_per_day_disabled("beta", post_discord=lambda m: posts.append(("b", m)))
        r3 = af.notify_once_per_day_disabled("gamma", post_discord=lambda m: posts.append(("g", m)))
        assert r1 is True and r2 is True and r3 is True
        assert len(posts) == 3
        # Re-call alpha — should not double-post
        r4 = af.notify_once_per_day_disabled("alpha", post_discord=lambda m: posts.append(("a2", m)))
        assert r4 is False
        assert len(posts) == 3

    def test_safe_when_post_discord_none(self, sandbox_marker):
        import _agent_flags as af
        result = af.notify_once_per_day_disabled("gamma", post_discord=None)
        assert result is False
        # No marker should be created (the function bails early)
        assert not af._marker_path("gamma").exists()

    def test_safe_when_post_discord_raises(self, sandbox_marker):
        """If the Discord post itself fails, the function returns False
        and the marker file is still created so we don't retry-spam."""
        import _agent_flags as af

        def boom(_msg):
            raise RuntimeError("Discord 500")

        result = af.notify_once_per_day_disabled("alpha", post_discord=boom)
        assert result is False
        # Marker still exists so we won't retry on the next tick today
        assert af._marker_path("alpha").exists()

    def test_unknown_agent_raises(self):
        import _agent_flags as af
        with pytest.raises(ValueError, match="unknown agent"):
            af.notify_once_per_day_disabled("delta", post_discord=lambda m: None)
