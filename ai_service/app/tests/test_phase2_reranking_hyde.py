"""
Tests for Phase 2 pipeline enhancements:
- Cross-encoder reranking (fake provider for deterministic tests)
- HyDE query expansion (fallback behavior when disabled)
- Combined pipeline labels
- Backward compatibility when all flags are off
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
    """Minimal valid find-related payload."""
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
# Cross-encoder reranker unit tests
# ==========================================================

class TestRerankerService:
    """Unit tests for the RerankerService."""

    def test_fake_reranker_score_pairs(self):
        from app.core.reranker import RerankerService
        svc = RerankerService(provider="fake")
        pairs = [
            ("labor dispute termination", "labor law termination compensation"),
            ("labor dispute termination", "commercial court procedures"),
        ]
        scores = svc.score_pairs(pairs)
        assert len(scores) == 2
        # Both should be floats in [0, 1]
        for s in scores:
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0
        # The labor pair should score higher than commercial
        assert scores[0] > scores[1]

    def test_fake_reranker_empty_input(self):
        from app.core.reranker import RerankerService
        svc = RerankerService(provider="fake")
        assert svc.score_pairs([]) == []
        assert svc.rerank("query", []) == []

    def test_fake_reranker_rerank_returns_sorted(self):
        from app.core.reranker import RerankerService
        svc = RerankerService(provider="fake")
        docs = [
            "commercial court procedures",
            "labor law termination compensation",
            "administrative government procedures",
        ]
        result = svc.rerank("labor dispute termination", docs)
        assert len(result) == 3
        # Should be sorted descending by score
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)

    def test_fake_reranker_rerank_top_n(self):
        from app.core.reranker import RerankerService
        svc = RerankerService(provider="fake")
        docs = ["doc a", "doc b", "doc c", "doc d"]
        result = svc.rerank("query text", docs, top_n=2)
        assert len(result) == 2

    def test_get_reranker_service_singleton(self):
        from app.core.reranker import get_reranker_service
        svc1 = get_reranker_service()
        svc2 = get_reranker_service()
        assert svc1 is svc2
        assert svc1.provider == "fake"


# ==========================================================
# HyDE unit tests
# ==========================================================

class TestHyDE:
    """Unit tests for HyDE module."""

    def test_detect_arabic(self):
        from app.core.hyde import _detect_arabic
        assert _detect_arabic("نزاع عمالي") is True
        assert _detect_arabic("Labor dispute") is False
        # Mixed — less than 30% Arabic
        assert _detect_arabic("Labor dispute نزاع") is False

    @pytest.mark.anyio
    async def test_hyde_disabled_returns_none(self):
        from app.core.hyde import generate_hypothetical_regulation
        # hyde_enabled is False by default
        text, warnings = await generate_hypothetical_regulation("some case text")
        assert text is None
        assert "hyde_disabled" in warnings


# ==========================================================
# Pipeline integration — backward compatibility
# ==========================================================

class TestBackwardCompat:
    """Verify that all flags off produces the same behavior as Phase 1."""

    def test_default_pipeline_label_unchanged(self):
        r = client.post("/similarity/find-related", json=_base_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["pipeline"] == "composite_v1"

    def test_new_request_fields_are_optional(self):
        """Omitting enable_cross_encoder and enable_hyde doesn't break anything."""
        payload = _base_payload()
        assert "enable_cross_encoder" not in payload
        assert "enable_hyde" not in payload
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200

    def test_reranker_score_absent_when_disabled(self):
        r = client.post("/similarity/find-related", json=_base_payload())
        assert r.status_code == 200
        body = r.json()
        for reg in body["related_regulations"]:
            assert reg.get("reranker_score") is None

    def test_explicit_false_flags_work(self):
        payload = _base_payload(
            enable_cross_encoder=False,
            enable_hyde=False,
            enable_llm_verification=False,
        )
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["pipeline"] == "composite_v1"


# ==========================================================
# Cross-encoder pipeline integration
# ==========================================================

class TestCrossEncoderPipeline:
    """Test cross-encoder integration when enabled via settings mock."""

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_reranker_service")
    def test_cross_encoder_enabled_adds_reranker_score(
        self, mock_get_reranker, mock_settings
    ):
        from app.config import settings as real_settings
        from app.core.reranker import RerankerService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.cross_encoder_enabled = True
        mock_settings.cross_encoder_top_n = 15
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False

        # Use a real fake reranker instance
        mock_get_reranker.return_value = RerankerService(provider="fake")

        payload = _base_payload(enable_cross_encoder=True)
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert "rerank" in body["pipeline"]
        for reg in body["related_regulations"]:
            assert reg.get("reranker_score") is not None
            assert isinstance(reg["reranker_score"], float)
            assert "rerank" in (reg.get("pipeline_stage") or "")

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_reranker_service")
    def test_cross_encoder_respects_top_n(
        self, mock_get_reranker, mock_settings
    ):
        from app.config import settings as real_settings
        from app.core.reranker import RerankerService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.cross_encoder_enabled = True
        mock_settings.cross_encoder_top_n = 1  # Only keep top 1
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False

        mock_get_reranker.return_value = RerankerService(provider="fake")

        # Send 2 regulations but reranker should keep only 1
        payload = _base_payload(enable_cross_encoder=True)
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()
        # At most 1 result after reranking
        assert len(body["related_regulations"]) <= 1


# ==========================================================
# HyDE pipeline integration
# ==========================================================

class TestHyDEPipeline:
    """Test HyDE integration behavior in the endpoint."""

    def test_hyde_flag_ignored_when_disabled(self):
        """enable_hyde=True in request has no effect when hyde_enabled=False."""
        payload = _base_payload(enable_hyde=True)
        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()
        # Should still be composite_v1 since hyde_enabled=False in config
        assert body["pipeline"] == "composite_v1"


# ==========================================================
# Pipeline label logic
# ==========================================================

class TestPipelineLabels:
    """Test that pipeline labels are built correctly."""

    def test_label_composite_only(self):
        from app.api.routes.find_related import _build_pipeline_label
        assert _build_pipeline_label(False, False, False) == "composite_v1"

    def test_label_with_rerank(self):
        from app.api.routes.find_related import _build_pipeline_label
        assert _build_pipeline_label(True, False, False) == "composite_rerank_v1"

    def test_label_with_gemini(self):
        from app.api.routes.find_related import _build_pipeline_label
        assert _build_pipeline_label(False, True, False) == "composite_gemini_v1"

    def test_label_with_hyde(self):
        from app.api.routes.find_related import _build_pipeline_label
        assert _build_pipeline_label(False, False, True) == "hyde_composite_v1"

    def test_label_full_pipeline(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(True, True, True)
        assert label == "hyde_composite_rerank_gemini_v1"

    def test_label_rerank_and_gemini(self):
        from app.api.routes.find_related import _build_pipeline_label
        assert _build_pipeline_label(True, True, False) == "composite_rerank_gemini_v1"


# ==========================================================
# Evaluation fixture regression with reranker
# ==========================================================

class TestEvalFixtureWithReranker:
    """Ensure eval fixtures still pass through the reranker path."""

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_reranker_service")
    def test_labor_case_through_reranker(
        self, mock_get_reranker, mock_settings
    ):
        from app.config import settings as real_settings
        from app.core.reranker import RerankerService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.cross_encoder_enabled = True
        mock_settings.cross_encoder_top_n = 15
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False

        mock_get_reranker.return_value = RerankerService(provider="fake")

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
            "enable_cross_encoder": True,
        }

        r = client.post("/similarity/find-related", json=payload)
        assert r.status_code == 200
        body = r.json()

        returned_ids = [
            reg["regulation_id"] for reg in body["related_regulations"]
        ]

        for expected_id in labor_expected["expected_regulation_ids"]:
            assert expected_id in returned_ids, (
                f"Expected regulation {expected_id} not in reranked results: "
                f"{returned_ids}"
            )
