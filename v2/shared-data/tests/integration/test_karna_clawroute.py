"""Integration tests: KARNA routed through ClawRoute with Haiku model.

Validates that:
  1. KARNA's models.json has a 'clawroute' provider pointing to ClawRoute
  2. openclaw.json sets KARNA (agent 0) to use clawroute provider
  3. ClawRoute's model registry includes Haiku
  4. ClawRoute health endpoint responds (live service check)
  5. collect_llm_cost.py has karna_tracked=True
  6. Model configs are consistent across files
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ── Config file paths ────────────────────────────────────────────────

OPENCLAW_JSON = Path("/root/.openclaw/openclaw.json")
KARNA_MODELS_JSON = Path("/root/.openclaw/agents/karna/agent/models.json")
CLAWROUTE_MODELS_TS = Path("/home/openclaw/.openclaw/workspace/router/src/models.ts")
COLLECTOR_SCRIPT = Path("/home/trader/dashboard/collect_llm_cost.py")


def _read_json_sudo(path: Path) -> dict:
    """Read a root-owned JSON file via sudo."""
    result = subprocess.run(
        ["sudo", "cat", str(path)],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        pytest.skip(f"Cannot read {path}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _read_file_sudo(path: Path) -> str:
    """Read a root-owned file via sudo."""
    result = subprocess.run(
        ["sudo", "cat", str(path)],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        pytest.skip(f"Cannot read {path}: {result.stderr.strip()}")
    return result.stdout


# ── KARNA models.json ────────────────────────────────────────────────

class TestKarnaModelsJson:
    @pytest.fixture(autouse=True)
    def load_config(self):
        self.config = _read_json_sudo(KARNA_MODELS_JSON)

    def test_clawroute_provider_exists(self):
        """models.json must have a 'clawroute' provider."""
        providers = self.config.get("providers", {})
        assert "clawroute" in providers, (
            f"Expected 'clawroute' provider, found: {list(providers.keys())}"
        )

    def test_clawroute_base_url(self):
        """ClawRoute provider must point to localhost:18790."""
        cr = self.config["providers"]["clawroute"]
        assert cr["baseUrl"] == "http://127.0.0.1:18790/v1", (
            f"Unexpected baseUrl: {cr['baseUrl']}"
        )

    def test_clawroute_api_format(self):
        """ClawRoute uses OpenAI-compatible format."""
        cr = self.config["providers"]["clawroute"]
        assert cr["api"] == "openai-completions", (
            f"Expected 'openai-completions', got: {cr['api']}"
        )

    def test_clawroute_has_haiku_model(self):
        """ClawRoute provider must list a Haiku model."""
        cr = self.config["providers"]["clawroute"]
        model_ids = [m["id"] for m in cr.get("models", [])]
        haiku_models = [m for m in model_ids if "haiku" in m.lower()]
        assert haiku_models, f"No Haiku model found in clawroute models: {model_ids}"

    def test_clawroute_haiku_cost_set(self):
        """Haiku model should have non-zero cost entries."""
        cr = self.config["providers"]["clawroute"]
        haiku = next(m for m in cr["models"] if "haiku" in m["id"].lower())
        cost = haiku.get("cost", {})
        assert cost.get("input", 0) > 0, "Haiku input cost must be > 0"
        assert cost.get("output", 0) > 0, "Haiku output cost must be > 0"

    def test_clawroute_haiku_context_window(self):
        """Haiku should have 200k context window."""
        cr = self.config["providers"]["clawroute"]
        haiku = next(m for m in cr["models"] if "haiku" in m["id"].lower())
        assert haiku.get("contextWindow", 0) >= 200000

    def test_original_providers_preserved(self):
        """Adding clawroute must not remove existing providers."""
        providers = self.config.get("providers", {})
        for expected in ("deepseek", "litellm-anthropic", "litellm-openai"):
            assert expected in providers, (
                f"Provider '{expected}' missing after clawroute addition"
            )


# ── openclaw.json agent config ───────────────────────────────────────

class TestOpenClawConfig:
    @pytest.fixture(autouse=True)
    def load_config(self):
        self.config = _read_json_sudo(OPENCLAW_JSON)

    def _karna_agent(self):
        agents = self.config.get("agents", {}).get("list", [])
        for a in agents:
            if a.get("id") == "karna":
                return a
        pytest.fail("No agent with id='karna' found in openclaw.json")

    def test_karna_model_uses_clawroute(self):
        """KARNA agent must use clawroute provider."""
        karna = self._karna_agent()
        model = karna.get("model", {}).get("primary", "")
        assert model.startswith("clawroute/"), (
            f"KARNA model should start with 'clawroute/', got: {model}"
        )

    def test_karna_model_is_haiku(self):
        """KARNA agent must use a Haiku model."""
        karna = self._karna_agent()
        model = karna.get("model", {}).get("primary", "")
        assert "haiku" in model.lower(), (
            f"KARNA model should contain 'haiku', got: {model}"
        )

    def test_clawroute_model_in_defaults(self):
        """The clawroute model must be listed in agents.defaults.models."""
        defaults_models = self.config.get("agents", {}).get("defaults", {}).get("models", {})
        clawroute_keys = [k for k in defaults_models if k.startswith("clawroute/")]
        assert clawroute_keys, (
            f"No clawroute model in defaults.models, keys: {list(defaults_models.keys())}"
        )

    def test_karna_model_matches_models_json(self):
        """KARNA's model in openclaw.json must match a model in models.json."""
        karna = self._karna_agent()
        model_primary = karna.get("model", {}).get("primary", "")
        # Format: "clawroute/anthropic/claude-haiku-4-5-20251001"
        # Extract: provider="clawroute", model_id="anthropic/claude-haiku-4-5-20251001"
        parts = model_primary.split("/", 1)
        assert len(parts) == 2, f"Unexpected model format: {model_primary}"
        provider_name, model_id = parts[0], parts[1]

        models_config = _read_json_sudo(KARNA_MODELS_JSON)
        provider = models_config.get("providers", {}).get(provider_name)
        assert provider, f"Provider '{provider_name}' not found in models.json"

        available_ids = [m["id"] for m in provider.get("models", [])]
        assert model_id in available_ids, (
            f"Model '{model_id}' not in provider '{provider_name}' models: {available_ids}"
        )


# ── ClawRoute model registry ─────────────────────────────────────────

class TestClawRouteRegistry:
    @pytest.fixture(autouse=True)
    def load_source(self):
        self.source = _read_file_sudo(CLAWROUTE_MODELS_TS)

    def test_haiku_in_registry(self):
        """ClawRoute models.ts must include a Haiku model entry."""
        assert "claude-haiku-4-5-20251001" in self.source, (
            "Haiku model not found in ClawRoute models.ts"
        )

    def test_haiku_pricing_correct(self):
        """Haiku pricing: $0.80/1M input, $4.00/1M output."""
        assert "inputCostPer1M: 0.80" in self.source or "inputCostPer1M: 0.8" in self.source
        assert "outputCostPer1M: 4.00" in self.source or "outputCostPer1M: 4.0" in self.source

    def test_haiku_provider_is_anthropic(self):
        """Haiku entry should have provider 'anthropic'."""
        # Find the haiku block and check provider within nearby lines
        lines = self.source.split("\n")
        for i, line in enumerate(lines):
            if "claude-haiku-4-5-20251001" in line:
                # Check surrounding 5 lines for provider
                block = "\n".join(lines[max(0, i - 3):i + 8])
                assert "provider: 'anthropic'" in block, (
                    f"Haiku entry should have provider 'anthropic', block: {block}"
                )
                return
        pytest.fail("Haiku model not found in models.ts")


# ── ClawRoute live service ────────────────────────────────────────────

class TestClawRouteService:
    def test_health_endpoint(self):
        """ClawRoute must respond to /health with status ok."""
        try:
            result = subprocess.run(
                ["curl", "-sf", "http://127.0.0.1:18790/health"],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            pytest.skip("ClawRoute health endpoint timed out")
        if result.returncode != 0:
            pytest.skip("ClawRoute not reachable")
        data = json.loads(result.stdout)
        assert data.get("status") == "ok"
        assert data.get("enabled") is True


# ── Collector tracking flag ───────────────────────────────────────────

class TestCollectorKarnaFlag:
    def test_karna_tracked_is_true(self):
        """collect_llm_cost.py must have karna_tracked set to True."""
        if not COLLECTOR_SCRIPT.exists():
            pytest.skip("Collector script not found")
        content = COLLECTOR_SCRIPT.read_text()
        assert '"karna_tracked": True' in content or "'karna_tracked': True" in content, (
            "collect_llm_cost.py should have karna_tracked=True"
        )
