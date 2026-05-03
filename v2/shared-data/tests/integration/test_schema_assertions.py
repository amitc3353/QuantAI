"""Integration tests for schema assertion helpers (Gap 7).

Validates that the assertion helpers themselves correctly accept valid inputs
and reject invalid ones — so they can be trusted as test oracles.
"""
from __future__ import annotations

import pytest

from conftest import (
    assert_diagnosis_schema,
    assert_learning_state_schema,
    assert_open_item_schema,
    assert_review_schema,
)


# ── assert_diagnosis_schema ──────────────────────────────────────────────────
class TestAssertDiagnosisSchema:
    def test_valid_with_gaps(self, sample_diagnosis):
        assert_diagnosis_schema(sample_diagnosis)  # must not raise

    def test_valid_empty_gaps(self):
        assert_diagnosis_schema({
            "gaps_identified": [],
            "no_gaps_note": "No gaps found",
        })

    def test_rejects_missing_gaps_identified(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema({"no_gaps_note": None})

    def test_rejects_missing_no_gaps_note(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema({"gaps_identified": []})

    def test_rejects_invalid_dimension(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema({
                "gaps_identified": [{
                    "dimension": "totally_invalid",
                    "request": "x",
                    "evidence": "y",
                    "priority": "would_help",
                }],
                "no_gaps_note": None,
            })

    def test_rejects_invalid_priority(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema({
                "gaps_identified": [{
                    "dimension": "data_freshness",
                    "request": "x",
                    "evidence": "y",
                    "priority": "kinda_matters",
                }],
                "no_gaps_note": None,
            })

    def test_rejects_non_numeric_impact(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema({
                "gaps_identified": [{
                    "dimension": "data_freshness",
                    "request": "x",
                    "evidence": "y",
                    "priority": "would_help",
                    "estimated_impact_dollars": "a lot",
                }],
                "no_gaps_note": None,
            })

    def test_allows_null_impact(self):
        assert_diagnosis_schema({
            "gaps_identified": [{
                "dimension": "data_freshness",
                "request": "x",
                "evidence": "y",
                "priority": "would_help",
                "estimated_impact_dollars": None,
            }],
            "no_gaps_note": None,
        })

    def test_rejects_non_dict(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema([])

    def test_rejects_empty_request(self):
        with pytest.raises(AssertionError):
            assert_diagnosis_schema({
                "gaps_identified": [{
                    "dimension": "data_freshness",
                    "request": "",
                    "evidence": "y",
                    "priority": "would_help",
                }],
                "no_gaps_note": None,
            })


# ── assert_review_schema ─────────────────────────────────────────────────────
class TestAssertReviewSchema:
    def test_valid_review(self, sample_review):
        assert_review_schema(sample_review)

    def test_rejects_invalid_thesis_outcome(self):
        with pytest.raises(AssertionError):
            r = {
                "thesis_outcome": "dunno",
                "thesis_assessment": "x",
                "regime_assessment": "x",
                "greeks_notes": "N/A",
                "timing_assessment": "x",
                "lessons": [],
                "parameter_suggestions": [],
            }
            assert_review_schema(r)

    def test_rejects_missing_field(self):
        with pytest.raises(AssertionError):
            assert_review_schema({
                "thesis_outcome": "confirmed",
                # missing all other required fields
            })

    def test_rejects_non_list_lessons(self):
        with pytest.raises(AssertionError):
            r = {
                "thesis_outcome": "confirmed",
                "thesis_assessment": "x",
                "regime_assessment": "x",
                "greeks_notes": "N/A",
                "timing_assessment": "x",
                "lessons": "a string instead of a list",
                "parameter_suggestions": [],
            }
            assert_review_schema(r)


# ── assert_open_item_schema ──────────────────────────────────────────────────
class TestAssertOpenItemSchema:
    def test_valid_cap_item(self):
        assert_open_item_schema({
            "id": "cap-2026-04-27-agent_alpha-data-abc123",
            "date": "2026-04-27",
            "agent": "agent_alpha",
            "type": "capability_request",
            "title": "Need better VIX data",
            "priority": "would_help",
            "status": "open",
        })

    def test_valid_param_item(self):
        assert_open_item_schema({
            "id": "param-2026-04-27-agent_gamma-rsi-abc123",
            "date": "2026-04-27",
            "agent": "agent_gamma",
            "type": "parameter_suggestion",
            "title": "rsi_threshold: 30 → 28",
            "priority": "would_help",
            "status": "open",
        })

    def test_rejects_non_open_status(self):
        with pytest.raises(AssertionError):
            assert_open_item_schema({
                "id": "cap-2026-04-27-agent_alpha-data-abc123",
                "date": "2026-04-27",
                "agent": "agent_alpha",
                "type": "capability_request",
                "title": "x",
                "priority": "would_help",
                "status": "resolved",
            })

    def test_rejects_invalid_prefix(self):
        with pytest.raises(AssertionError):
            assert_open_item_schema({
                "id": "unknown-prefix-abc",
                "date": "2026-04-27",
                "agent": "agent_alpha",
                "type": "capability_request",
                "title": "x",
                "priority": "would_help",
                "status": "open",
            })


# ── assert_learning_state_schema ─────────────────────────────────────────────
class TestAssertLearningStateSchema:
    def test_valid_idle_state(self):
        assert_learning_state_schema({
            "last_updated": "2026-05-02T12:00:00",
            "status": "idle",
            "data": {
                "open_items": [],
                "resolved_items": [],
                "stats": {"total_open": 0, "total_resolved": 0},
            },
        })

    def test_rejects_missing_status(self):
        with pytest.raises(AssertionError):
            assert_learning_state_schema({
                "last_updated": "2026-05-02T12:00:00",
                "data": {"open_items": [], "resolved_items": [], "stats": {}},
            })

    def test_rejects_invalid_status(self):
        with pytest.raises(AssertionError):
            assert_learning_state_schema({
                "last_updated": "2026-05-02T12:00:00",
                "status": "broken",
                "data": {"open_items": [], "resolved_items": [], "stats": {}},
            })

    def test_validates_nested_open_items(self):
        with pytest.raises(AssertionError):
            assert_learning_state_schema({
                "last_updated": "2026-05-02T12:00:00",
                "status": "ok",
                "data": {
                    "open_items": [{"id": "bad-prefix-x", "status": "open",
                                    "date": "2026-04-27", "agent": "agent_alpha",
                                    "type": "capability_request", "title": "x",
                                    "priority": "would_help"}],
                    "resolved_items": [],
                    "stats": {"total_open": 1, "total_resolved": 0},
                },
            })
