"""
Tests for the Phase 1 find-related pipeline enhancements:
- Pipeline metadata in responses
- Gemini verification fallback behavior
- Evaluation fixture-based regression checks
- Backward compatibility when flags are off
"""

import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---- Helpers ----

def _base_payload(**overrides):
    """Minimal valid find-related payload."""
    payload = {
        "case_text": "Labor dispute regarding termination compensation",
        "regulations": [
            {
                "id": 11,
                "title": "Saudi Labor Law",
                "category": "labor_law",
                "content_text": "Termination without a valid reason requires compensation.",
            }
        ],
        "top_k": 5,
        "threshold": 0.3,
        "strict_mode": False,
    }
    payload.update(overrides)
    return payload


# ---- Backward compatibility ----

def test_response_includes_pipeline_field():
    """New pipeline field is present and defaults to composite_v1 when Gemini is off."""
    r = client.post("/similarity/find-related", json=_base_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["pipeline"] == "composite_v1"
    # pipeline_warnings should be absent or null when no warnings
    assert body.get("pipeline_warnings") is None


def test_existing_fields_unchanged():
    """All pre-existing response fields still present and valid."""
    r = client.post("/similarity/find-related", json=_base_payload())
    assert r.status_code == 200
    body = r.json()
    assert "related_regulations" in body
    assert "query_length" in body
    assert "candidates_count" in body
    if body["related_regulations"]:
        reg = body["related_regulations"][0]
        assert "regulation_id" in reg
        assert "similarity_score" in reg
        assert "score_breakdown" in reg
        assert "line_matches" in reg
        # New optional fields should be absent or null when Gemini is off
        assert reg.get("verification") is None


def test_enable_llm_verification_ignored_when_gemini_disabled():
    """Request-level enable_llm_verification=true has no effect when gemini_enabled=false."""
    payload = _base_payload(enable_llm_verification=True)
    r = client.post("/similarity/find-related", json=payload)
    assert r.status_code == 200
    body = r.json()
    # Should still be composite_v1 since gemini_enabled is false by default
    assert body["pipeline"] == "composite_v1"


# ---- Gemini fallback behavior ----

@patch("app.api.routes.find_related.settings")
def test_gemini_enabled_but_no_key_falls_back(mock_settings):
    """When gemini_enabled=True but no API key, endpoint still works via fallback."""
    # Copy real settings and override just the Gemini flags
    from app.config import settings as real_settings
    for attr in dir(real_settings):
        if not attr.startswith("_"):
            try:
                setattr(mock_settings, attr, getattr(real_settings, attr))
            except (AttributeError, TypeError):
                pass
    mock_settings.gemini_enabled = True
    mock_settings.gemini_api_key = ""
    mock_settings.gemini_top_n_candidates = 15

    payload = _base_payload(enable_llm_verification=True)
    r = client.post("/similarity/find-related", json=payload)
    assert r.status_code == 200
    body = r.json()
    # Pipeline label reflects intent but warnings show fallback
    assert body["pipeline"] == "composite_gemini_v1"
    assert body.get("pipeline_warnings") is not None
    assert any("gemini" in w for w in body["pipeline_warnings"])


# ---- LLM verifier unit tests ----

def test_blend_scores_no_verification():
    """blend_scores returns original score when verification is None."""
    from app.core.llm_verifier import blend_scores
    score, llm = blend_scores(0.75, None)
    assert score == 0.75
    assert llm is None


def test_blend_scores_not_applicable():
    """blend_scores returns original score when not applicable."""
    from app.core.llm_verifier import blend_scores
    score, llm = blend_scores(0.75, {"applicable": False, "confidence": "high"})
    assert score == 0.75
    assert llm is None


def test_blend_scores_applicable():
    """blend_scores blends correctly when applicable."""
    from app.core.llm_verifier import blend_scores
    score, llm = blend_scores(0.80, {"applicable": True, "confidence": "high"})
    assert score != 0.80  # Should be blended
    assert llm == 1.0  # High confidence maps to 1.0
    # 0.85 * 0.80 + 0.15 * 1.0 = 0.83
    assert abs(score - 0.83) < 0.01


def test_parse_response_valid():
    """_parse_response handles well-formed Gemini JSON."""
    from app.core.llm_verifier import _parse_response
    raw = json.dumps({
        "results": [
            {
                "regulation_id": 1,
                "applicable": True,
                "confidence": "high",
                "relevant_articles": ["Article 77"],
                "explanation_ar": "ينطبق",
            },
            {
                "regulation_id": 2,
                "applicable": False,
                "confidence": "low",
                "relevant_articles": [],
                "explanation_ar": "لا ينطبق",
            },
        ]
    })
    results = _parse_response(raw, {1, 2, 3})
    assert 1 in results
    assert results[1]["applicable"] is True
    assert 2 in results
    assert results[2]["applicable"] is False
    assert 3 not in results


def test_parse_response_markdown_fences():
    """_parse_response strips markdown code fences."""
    from app.core.llm_verifier import _parse_response
    raw = '```json\n{"results": [{"regulation_id": 1, "applicable": true, "confidence": "medium", "relevant_articles": [], "explanation_ar": "test"}]}\n```'
    results = _parse_response(raw, {1})
    assert 1 in results


def test_parse_response_invalid_json():
    """_parse_response returns empty dict on invalid JSON."""
    from app.core.llm_verifier import _parse_response
    results = _parse_response("not json at all", {1})
    assert results == {}


# ---- Evaluation fixtures ----

def test_eval_fixtures_exist_and_are_valid():
    """Evaluation fixtures load correctly and have required fields."""
    cases_path = FIXTURES_DIR / "find_related_cases.json"
    expected_path = FIXTURES_DIR / "find_related_expected.json"

    assert cases_path.exists(), "find_related_cases.json missing"
    assert expected_path.exists(), "find_related_expected.json missing"

    cases = json.loads(cases_path.read_text())
    expected = json.loads(expected_path.read_text())

    assert len(cases) >= 2
    assert len(expected) >= 2

    for case in cases:
        assert "case_id" in case
        assert "case_text" in case
        assert "regulations" in case
        assert len(case["regulations"]) >= 2

    for exp in expected:
        assert "case_id" in exp
        assert "expected_regulation_ids" in exp


def test_eval_fixture_labor_case():
    """Run the labor termination eval case through the pipeline and check basic ranking."""
    cases = json.loads((FIXTURES_DIR / "find_related_cases.json").read_text())
    expected = json.loads((FIXTURES_DIR / "find_related_expected.json").read_text())

    labor_case = next(c for c in cases if c["case_id"] == "eval_labor_termination")
    labor_expected = next(e for e in expected if e["case_id"] == "eval_labor_termination")

    payload = {
        "case_text": labor_case["case_text"],
        "regulations": labor_case["regulations"],
        "case_profile": {"case_type": labor_case.get("case_type")},
        "case_fragments": labor_case.get("case_fragments"),
        "top_k": 10,
        "threshold": 0.1,
        "strict_mode": False,
    }

    r = client.post("/similarity/find-related", json=payload)
    assert r.status_code == 200
    body = r.json()

    returned_ids = [reg["regulation_id"] for reg in body["related_regulations"]]

    # At minimum, expected regulations should appear in results
    for expected_id in labor_expected["expected_regulation_ids"]:
        assert expected_id in returned_ids, (
            f"Expected regulation {expected_id} not in results: {returned_ids}"
        )
