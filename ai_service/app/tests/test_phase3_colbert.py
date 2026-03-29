"""
Tests for Phase 3 ColBERT / late-interaction reranking (experimental).

- ColBERTService unit tests (fake provider, MaxSim computation)
- Pipeline integration (disabled by default, fallback behavior)
- Backward compatibility (existing tests unaffected)
- Pipeline label generation with colbert flag
- Eval fixture stability
"""

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
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
        "top_k": 10,
        "threshold": 0.1,
        "strict_mode": False,
    }
    payload.update(overrides)
    return payload


# ==================================================================== #
#  ColBERTService unit tests                                            #
# ==================================================================== #

class TestColBERTService:
    """Unit tests for the ColBERTService class."""

    def test_fake_provider_init(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        assert svc.provider == "fake"
        assert svc.model is None

    def test_colbert_without_model_falls_back_to_fake(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="colbert", model=None)
        assert svc.provider == "fake"

    def test_char_ngrams_basic(self):
        from app.core.colbert_retriever import ColBERTService
        ngrams = ColBERTService._char_ngrams("hello world", n=4)
        assert len(ngrams) == len("hello world") - 4 + 1
        assert ngrams[0] == "hell"
        assert ngrams[-1] == "orld"

    def test_char_ngrams_short_text(self):
        from app.core.colbert_retriever import ColBERTService
        assert ColBERTService._char_ngrams("ab", n=4) == ["ab"]
        assert ColBERTService._char_ngrams("", n=4) == []

    def test_ngram_to_vector_deterministic(self):
        from app.core.colbert_retriever import ColBERTService
        v1 = ColBERTService._ngram_to_vector("test")
        v2 = ColBERTService._ngram_to_vector("test")
        assert v1 == v2
        assert len(v1) == ColBERTService._NGRAM_DIM
        # Check L2-normalized
        norm = sum(x * x for x in v1) ** 0.5
        assert abs(norm - 1.0) < 1e-5

    def test_ngram_different_inputs_differ(self):
        from app.core.colbert_retriever import ColBERTService
        v1 = ColBERTService._ngram_to_vector("aaaa")
        v2 = ColBERTService._ngram_to_vector("bbbb")
        assert v1 != v2

    def test_fake_token_encode_returns_2d_arrays(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        results = svc.encode_tokens(["hello world", "test"])
        assert len(results) == 2
        for arr in results:
            assert isinstance(arr, np.ndarray)
            assert arr.ndim == 2
            assert arr.shape[1] == ColBERTService._NGRAM_DIM

    def test_fake_token_encode_empty_text(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        results = svc.encode_tokens([""])
        assert len(results) == 1
        assert results[0].shape == (1, ColBERTService._NGRAM_DIM)

    def test_maxsim_identical(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        tokens = svc.encode_tokens(["hello world"])[0]
        score = svc.maxsim(tokens, tokens)
        # Identical tokens → MaxSim should be ~1.0
        assert score > 0.9

    def test_maxsim_different(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        q = svc.encode_tokens(["labor termination compensation"])[0]
        d = svc.encode_tokens(["completely unrelated astronomy topic"])[0]
        score_diff = svc.maxsim(q, d)

        q2 = svc.encode_tokens(["labor termination compensation"])[0]
        d2 = svc.encode_tokens(["labor dispute about employee termination"])[0]
        score_related = svc.maxsim(q2, d2)

        # Related text should score higher than unrelated
        assert score_related > score_diff

    def test_maxsim_empty(self):
        from app.core.colbert_retriever import ColBERTService
        empty = np.zeros((0, 32), dtype=np.float32)
        non_empty = np.ones((3, 32), dtype=np.float32)
        assert ColBERTService.maxsim(empty, non_empty) == 0.0
        assert ColBERTService.maxsim(non_empty, empty) == 0.0

    def test_score_pairs(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        pairs = [
            ("labor dispute", "labor termination law"),
            ("labor dispute", "astronomy research paper"),
        ]
        scores = svc.score_pairs(pairs)
        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)
        # Related pair should score higher
        assert scores[0] > scores[1]

    def test_score_pairs_empty(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        assert svc.score_pairs([]) == []

    def test_rerank_returns_sorted(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        docs = [
            "completely unrelated text about weather",
            "labor termination compensation for employees",
            "some other random topic",
        ]
        results = svc.rerank("labor termination", docs)
        assert len(results) == 3
        # Check sorted descending
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)
        # The labor-related doc should be ranked first
        assert results[0][0] == 1  # index of the labor doc

    def test_rerank_top_n(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        docs = ["doc a", "doc b", "doc c", "doc d"]
        results = svc.rerank("query", docs, top_n=2)
        assert len(results) == 2

    def test_rerank_empty(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        assert svc.rerank("query", []) == []

    def test_arabic_text_support(self):
        from app.core.colbert_retriever import ColBERTService
        svc = ColBERTService(provider="fake")
        pairs = [
            ("نزاع عمالي بشأن التعويض", "نظام العمل السعودي بشأن إنهاء العقد"),
            ("نزاع عمالي بشأن التعويض", "قانون الفضاء والطيران المدني"),
        ]
        scores = svc.score_pairs(pairs)
        assert len(scores) == 2
        # Arabic labor text should relate more to labor law
        assert scores[0] > scores[1]


# ==================================================================== #
#  Backward compatibility                                               #
# ==================================================================== #

class TestColBERTBackwardCompat:
    """ColBERT is disabled by default — existing behavior must be preserved."""

    def test_default_pipeline_label_no_colbert(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=False, use_llm=False, use_hyde=False,
            use_agentic=False, use_colbert=False,
        )
        assert label == "composite_v1"
        assert "colbert" not in label

    def test_enable_colbert_ignored_when_disabled(self):
        """Sending enable_colbert=True has no effect if server flag is off."""
        payload = _base_payload(enable_colbert=True)
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "colbert" not in (data.get("pipeline") or "")

    def test_all_new_fields_optional(self):
        """The new colbert_score field must be optional (absent or null)."""
        payload = _base_payload()
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        for reg in data["related_regulations"]:
            assert reg.get("colbert_score") is None

    def test_enable_colbert_field_optional_in_request(self):
        """Request without enable_colbert field should still work."""
        payload = _base_payload()
        assert "enable_colbert" not in payload
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200


# ==================================================================== #
#  ColBERT pipeline integration                                         #
# ==================================================================== #

class TestColBERTPipeline:
    """Integration tests with ColBERT enabled via mock settings."""

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_colbert_service")
    def test_colbert_enabled_adds_colbert_score(
        self, mock_get_colbert, mock_settings
    ):
        """When ColBERT is enabled, results should have colbert_score."""
        from app.config import settings as real_settings
        from app.core.colbert_retriever import ColBERTService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.colbert_enabled = True
        mock_settings.colbert_top_n = 15
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False
        mock_settings.cross_encoder_enabled = False
        mock_settings.agentic_retrieval_enabled = False

        mock_get_colbert.return_value = ColBERTService(provider="fake")

        payload = _base_payload(enable_colbert=True)
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert "colbert" in (data.get("pipeline") or "")
        for reg in data["related_regulations"]:
            assert reg.get("colbert_score") is not None
            assert isinstance(reg["colbert_score"], float)
            assert "colbert" in (reg.get("pipeline_stage") or "")

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_colbert_service")
    def test_colbert_respects_top_n(
        self, mock_get_colbert, mock_settings
    ):
        """ColBERT should truncate to colbert_top_n."""
        from app.config import settings as real_settings
        from app.core.colbert_retriever import ColBERTService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.colbert_enabled = True
        mock_settings.colbert_top_n = 1
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False
        mock_settings.cross_encoder_enabled = False
        mock_settings.agentic_retrieval_enabled = False

        mock_get_colbert.return_value = ColBERTService(provider="fake")

        payload = _base_payload(enable_colbert=True)
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Only 1 regulation should pass through ColBERT
        assert len(data["related_regulations"]) <= 1


# ==================================================================== #
#  Pipeline label generation                                            #
# ==================================================================== #

class TestColBERTPipelineLabels:
    """Test pipeline label generation with the colbert flag."""

    def test_label_colbert_only(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=False, use_llm=False, use_hyde=False,
            use_agentic=False, use_colbert=True,
        )
        assert label == "composite_colbert_v1"

    def test_label_colbert_with_rerank(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=True, use_llm=False, use_hyde=False,
            use_agentic=False, use_colbert=True,
        )
        assert label == "composite_colbert_rerank_v1"

    def test_label_colbert_with_agentic(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=False, use_llm=False, use_hyde=False,
            use_agentic=True, use_colbert=True,
        )
        assert label == "composite_agentic_colbert_v1"

    def test_label_full_pipeline_with_colbert(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=True, use_llm=True, use_hyde=True,
            use_agentic=True, use_colbert=True,
        )
        assert label == "hyde_composite_agentic_colbert_rerank_gemini_v1"

    def test_label_without_colbert_unchanged(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=True, use_llm=True, use_hyde=True,
            use_agentic=True, use_colbert=False,
        )
        assert label == "hyde_composite_agentic_rerank_gemini_v1"
        assert "colbert" not in label

    def test_label_colbert_with_gemini(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=False, use_llm=True, use_hyde=False,
            use_agentic=False, use_colbert=True,
        )
        assert label == "composite_colbert_gemini_v1"

    def test_label_hyde_colbert(self):
        from app.api.routes.find_related import _build_pipeline_label
        label = _build_pipeline_label(
            use_reranker=False, use_llm=False, use_hyde=True,
            use_agentic=False, use_colbert=True,
        )
        assert label == "hyde_composite_colbert_v1"


# ==================================================================== #
#  Eval fixture stability                                               #
# ==================================================================== #

class TestEvalFixtureWithColBERT:
    """Eval fixtures must still pass when ColBERT is enabled."""

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_colbert_service")
    def test_labor_case_through_colbert(self, mock_get_colbert, mock_settings):
        from app.config import settings as real_settings
        from app.core.colbert_retriever import ColBERTService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.colbert_enabled = True
        mock_settings.colbert_top_n = 15
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False
        mock_settings.cross_encoder_enabled = False
        mock_settings.agentic_retrieval_enabled = False

        mock_get_colbert.return_value = ColBERTService(provider="fake")

        cases = json.loads(
            (FIXTURES_DIR / "find_related_cases.json").read_text()
        )
        expected = json.loads(
            (FIXTURES_DIR / "find_related_expected.json").read_text()
        )
        case = next(c for c in cases if c["case_id"] == "eval_labor_termination")
        exp = next(e for e in expected if e["case_id"] == "eval_labor_termination")

        payload = {
            "case_text": case["case_text"],
            "regulations": case["regulations"],
            "top_k": 10,
            "threshold": 0.1,
            "strict_mode": False,
            "case_fragments": case.get("case_fragments"),
            "case_profile": {"case_type": case.get("case_type", "labor")},
            "enable_colbert": True,
        }
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        returned_ids = {r["regulation_id"] for r in data["related_regulations"]}
        for eid in exp["expected_regulation_ids"]:
            assert eid in returned_ids, (
                f"Expected regulation {eid} missing after ColBERT reranking"
            )

    @patch("app.api.routes.find_related.settings")
    @patch("app.api.routes.find_related.get_colbert_service")
    def test_commercial_case_through_colbert(self, mock_get_colbert, mock_settings):
        from app.config import settings as real_settings
        from app.core.colbert_retriever import ColBERTService

        for attr in dir(real_settings):
            if not attr.startswith("_"):
                try:
                    setattr(mock_settings, attr, getattr(real_settings, attr))
                except (AttributeError, TypeError):
                    pass
        mock_settings.colbert_enabled = True
        mock_settings.colbert_top_n = 15
        mock_settings.gemini_enabled = False
        mock_settings.hyde_enabled = False
        mock_settings.cross_encoder_enabled = False
        mock_settings.agentic_retrieval_enabled = False

        mock_get_colbert.return_value = ColBERTService(provider="fake")

        cases = json.loads(
            (FIXTURES_DIR / "find_related_cases.json").read_text()
        )
        expected = json.loads(
            (FIXTURES_DIR / "find_related_expected.json").read_text()
        )
        case = next(c for c in cases if c["case_id"] == "eval_commercial_contract")
        exp = next(e for e in expected if e["case_id"] == "eval_commercial_contract")

        payload = {
            "case_text": case["case_text"],
            "regulations": case["regulations"],
            "top_k": 10,
            "threshold": 0.1,
            "strict_mode": False,
            "case_fragments": case.get("case_fragments"),
            "case_profile": {"case_type": case.get("case_type", "commercial")},
            "enable_colbert": True,
        }
        resp = client.post("/similarity/find-related", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        returned_ids = {r["regulation_id"] for r in data["related_regulations"]}
        for eid in exp["expected_regulation_ids"]:
            assert eid in returned_ids, (
                f"Expected regulation {eid} missing after ColBERT reranking"
            )
