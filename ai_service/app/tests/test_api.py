from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_ok():
    r = client.get("/health/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert "service" in body
    assert "version" in body

def test_embed_basic():
    payload = {
        "texts": ["قضية عمالية", "commercial contract"],
        "normalize": True
    }
    r = client.post("/embed/", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert isinstance(body["embeddings"], list)
    assert len(body["embeddings"]) == 2
    dim = body["dimension"]
    assert isinstance(dim, int) and dim > 0
    assert all(len(vec) == dim for vec in body["embeddings"])

def test_similarity_basic():
    payload = {
        "queries": ["termination benefits"],
        "corpus": ["labor case", "sales contract", "end of service benefits", "visa rules"],
        "top_k": 3
    }
    r = client.post("/similarity/", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 1
    assert len(body["results"][0]) == 3
    first = body["results"][0][0]
    assert "doc" in first and "score" in first
    assert isinstance(first["doc"], str)
    assert isinstance(first["score"], float)


def test_find_related_with_explainability_fields():
    payload = {
        "case_text": "Labor dispute regarding termination compensation",
        "case_profile": {
            "case_type": "labor",
            "title": "Termination compensation claim",
        },
        "case_fragments": [
            {
                "fragment_id": "case:primary",
                "text": "Employee terminated without compensation under labor contract",
                "source": "case",
            }
        ],
        "regulations": [
            {
                "id": 11,
                "title": "Saudi Labor Law",
                "category": "labor_law",
                "regulation_version_id": 22,
                "candidate_chunks": [
                    {
                        "chunk_id": 501,
                        "chunk_index": 0,
                        "line_start": 20,
                        "line_end": 34,
                        "article_ref": "Article 77",
                        "text": "Termination without a valid reason requires compensation.",
                    }
                ],
            }
        ],
        "top_k": 5,
        "threshold": 0.3,
        "strict_mode": False,
    }
    r = client.post("/similarity/find-related", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "related_regulations" in body
    if body["related_regulations"]:
        first = body["related_regulations"][0]
        assert "score_breakdown" in first
        assert "line_matches" in first
        assert isinstance(first.get("warnings", []), list)


def test_regulation_summary_analysis_basic():
    payload = {
        "regulation_text": "يجب على المنشأة الالتزام بساعات العمل. يعاقب المخالف بغرامة مالية.",
        "regulation_title": "نظام العمل",
        "language_code": "ar",
    }
    r = client.post("/regulations/summary-analysis", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["summary"], str)
    assert isinstance(body["obligations"], list)
    assert isinstance(body["risk_flags"], list)
    assert isinstance(body["citations"], list)


def test_regulation_amendment_impact_basic():
    payload = {
        "regulation_title": "نظام العمل",
        "old_text": "يجب الالتزام بساعات العمل.",
        "new_text": "يجب الالتزام بساعات العمل. يعاقب المخالف بغرامة.",
        "from_version_label": "v1",
        "to_version_label": "v2",
        "language_code": "ar",
    }
    r = client.post("/regulations/amendment-impact", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["what_changed"], list)
    assert isinstance(body["legal_impact"], list)
    assert isinstance(body["affected_parties"], list)
