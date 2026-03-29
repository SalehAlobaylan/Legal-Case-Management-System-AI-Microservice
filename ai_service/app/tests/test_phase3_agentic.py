"""
Tests for Phase 3 agentic retrieval (experimental).
- Module unit tests (gap analysis parsing, formatting)
- Pipeline integration (disabled by default, fallback behavior)
- Backward compatibility (existing tests unaffected)
- Pipeline label generation with agentic flag
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---- Helpers ----

def _base_payload(**overrides):
    payload = {
        "case_text": "Labor dispute regarding termination compensation",
        "regulations": [
            {
                "id": 11,
                "title": "Saudi Labor Law",
                "category": "labor_law",
                "content_text": "Termination without a valid reason requires compensation.",
            },
            {
                "id": 12,
                "title": "Commercial Court Procedures",
                "category": "commercial_law",
                "content_text": "Guidelines for commercial litigation and dispute resolution.",
            },
        ],
        "top_k": 5,
        "threshold": 0.1,
        "strict_mode": False,
    }
    payload.update(overrides)
    return payload


# ==========================================================
# Agentic retriever module unit tests
# ==========================================================

class TestAgenticRetrieverParsing:
    """Unit tests for agentic_retriever internal helpers."""

    def test_strip_fences_json(self):
        from app.core.agentic_retriever import _strip_fences
        raw = '```json\n{"has_gaps": true}\n```'
        assert _strip_fences(raw) == '{"has_gaps": true}'

    def test_strip_fences_no_fences(self):
        from app.core.agentic_retriever import _strip_fences
        raw = '{"has_gaps": false}'
        assert _strip_fences(raw) == '{"has_gaps": false}'

    def test_parse_gap_analysis_valid(self):
        from app.core.agentic_retriever import _parse_gap_analysis
        raw = json.dumps({
            "has_gaps": True,
            "gap_description": "Missing criminal law regulations",
            "refined_queries": [
                "Saudi criminal law regarding fraud",
                "الاحتيال في النظام الجزائي السعودي",
            ],
            "missing_domains": ["criminal_law"],
        })
        result = _parse_gap_analysis(raw)
        assert result is not None
        assert result["has_gaps"] is True
        assert len(result["refined_queries"]) == 2
        assert "criminal_law" in result["missing_domains"]

    def test_parse_gap_analysis_no_gaps(self):
        from app.core.agentic_retriever import _parse_gap_analysis
        raw = json.dumps({
            "has_gaps": False,
            "gap_description": "",
            "refined_queries": [],
            "missing_domains": [],
        })
        result = _parse_gap_analysis(raw)
        assert result is not None
        assert result["has_gaps"] is False
        assert result["refined_queries"] == []

    def test_parse_gap_analysis_invalid_json(self):
        from app.core.agentic_retriever import _parse_gap_analysis
        result = _parse_gap_analysis("not json at all")
        assert result is None

    def test_parse_gap_analysis_strips_fences(self):
        from app.core.agentic_retriever import _parse_gap_analysis
        raw = '```json\n{"has_gaps": true, "gap_description": "test", "refined_queries": ["query one here"], "missing_domains": []}\n```'
        result = _parse_gap_analysis(raw)
        assert result is not None
        assert result["has_gaps"] is True

    def test_parse_gap_analysis_filters_short_queries(self):
        from app.core.agentic_retriever import _parse_gap_analysis
        raw = json.dumps({
            "has_gaps": True,
            "gap_description": "test",
            "refined_queries": ["ab", "valid query here", ""],
            "missing_domains": [],
        })
        result = _parse_gap_analysis(raw)
        assert result is not None
        # Only "valid query here" should pass the min-5-char filter
        assert len(result["refined_queries"]) == 1
        assert result["refined_queries"][0] == "valid query here"

    def test_format_found_regulations(self):
        from app.core.agentic_retriever import _format_found_regulations
        found = [
            {"title": "Labor Law", "category": "labor_law", "score": 0.85},
            {"title": "Commercial Law", "category": "commercial_law", "score": 0.62},
        ]
        text = _format_found_regulations(found)
        assert "Labor Law" in text
        assert "labor_law" in text
        assert "0.850" in text

    def test_format_found_regulations_empty(self):
        from app.core.agentic_retriever import _format_found_regulations
        text = _format_found_regulations([])
        assert "no regulations found" in text

    def test_format_found_regulations_truncates(self):
        from app.core.agentic_retriever import _format_found_regulations
        found = [
            {"title": f"Regulation {i}", "category": "cat", "score": 0.5}
            for i in range(20)
        ]
        text = _format_found_regulations(found, max_items=5)
        assert "... and 15 more" in text

    @pytest.mark.anyio
    async def test_analyze_gaps_disabled_returns_empty(self):
        from app.core.agentic_retriever import analyze_gaps_and_generate_queries
        queries, warnings = await analyze_gaps_and_generate_queries(
            "some case", [{"title": "Law", "category": "law", "score": 0.8}]
        )
        assert queries == []
        assert "agentic_disabled" in warnings


# ==========================================================
# Pipeline integration — backward compatibility
# ==========================================================

class TestAgenticBackwardCompat:
    """Agentic retrieval disabled by default — existing behavior preserved."""

    def test_default_pipeline_label_no_agentic(self):
        r = client.post("/similarity/find-related", json=_base_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["pipeline"] == "composite_v1"
        assert "agentic" not in body["pipeline"]

    def test_enable_agentic_ignored_when_disabled(self):
        """Request flag has no effect when server flag is off."""
        payload = _base_payload(enable_agentic=True)
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["pipeline"] == "composite_v1"

    def test_all_new_fields_optional(self):
        """Omitting enable_agentic doesn't break the request."""
        payload = _base_payload()
        assert "enable_agentic" not in payload
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200

    def test_existing_phase1_phase2_tests_pass(self):
        """Basic sanity check that Phase 1/2 features still work."""
        payload = _base_payload()
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert "related_regulations" in body
        assert "query_length" in body
        if body["related_regulations"]:
            reg = body["related_regulations"][0]
            assert "score_breakdown" in reg
            assert reg.get("verification") is None
            assert reg.get("reranker_score") is None


# ==========================================================
# Pipeline label generation
# ==========================================================

class TestAgenticPipelineLabels:
    """Pipeline labels correctly include agentic when active."""

    def test_label_agentic_only(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(False, False, False, use_agentic=True)
        assert label == "composite_agentic_v1"

    def test_label_agentic_with_rerank(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(True, False, False, use_agentic=True)
        assert label == "composite_agentic_rerank_v1"

    def test_label_full_pipeline_with_agentic(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(True, True, True, use_agentic=True)
        assert label == "hyde_composite_agentic_rerank_gemini_v1"

    def test_label_agentic_with_gemini(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(False, True, False, use_agentic=True)
        assert label == "composite_agentic_gemini_v1"

    def test_label_without_agentic_unchanged(self):
        """Existing labels are not affected by the new parameter."""
        from app.api.routes.find_related import _build_pipeline_label
        assert _build_pipeline_label(False, False, False) == "composite_v1"
        assert _build_pipeline_label(True, True, True) == "hyde_composite_rerank_gemini_v1"


# ==========================================================
# Eval fixture regression
# ==========================================================

class TestEvalFixtureStability:
    """Eval fixtures still pass through unmodified pipeline."""

    def test_labor_case_still_finds_expected_regulations(self):
        cases = json.loads(
            (FIXTURES_DIR / "find_related_cases.json").read_text()
        )
        expected = json.loads(
            (FIXTURES_DIR / "find_related_expected.json").read_text()
        )

        labor_case = next(
            c for c in cases if c["case_id"] == "eval_labor_termination"
        )
        labor_expected = next(
            e for e in expected if e["case_id"] == "eval_labor_termination"
        )

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

        returned_ids = [
            reg["regulation_id"] for reg in body["related_regulations"]
        ]
        for expected_id in labor_expected["expected_regulation_ids"]:
            assert expected_id in returned_ids

    def test_commercial_case_still_finds_expected_regulations(self):
        cases = json.loads(
            (FIXTURES_DIR / "find_related_cases.json").read_text()
        )
        expected = json.loads(
            (FIXTURES_DIR / "find_related_expected.json").read_text()
        )

        comm_case = next(
            c for c in cases if c["case_id"] == "eval_commercial_contract"
        )
        comm_expected = next(
            e for e in expected if e["case_id"] == "eval_commercial_contract"
        )

        payload = {
            "case_text": comm_case["case_text"],
            "regulations": comm_case["regulations"],
            "case_profile": {"case_type": comm_case.get("case_type")},
            "case_fragments": comm_case.get("case_fragments"),
            "top_k": 10,
            "threshold": 0.1,
            "strict_mode": False,
        }

        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()

        returned_ids = [
            reg["regulation_id"] for reg in body["related_regulations"]
        ]
        for expected_id in comm_expected["expected_regulation_ids"]:
            assert expected_id in returned_ids
