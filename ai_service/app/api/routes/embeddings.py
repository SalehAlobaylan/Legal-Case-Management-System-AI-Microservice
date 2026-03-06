from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import get_embedding_service
from app.api.schemas.requests import EmbedRequest
from app.api.schemas.responses import EmbeddingResponse

router = APIRouter()


@router.post("/embed/", response_model=EmbeddingResponse)
async def embed_texts(payload: EmbedRequest) -> EmbeddingResponse:
    """
    POST /embed/

    Request body:
      {
        "texts": ["...", "..."],
        "normalize": true
      }

    Response body:
      {
        "embeddings": [[...], [...]],
        "dimension": 4,
        "count": 2
      }
    """
    service = get_embedding_service()
    vectors = service.embed_documents(payload.texts, normalize=payload.normalize)

    dim = len(vectors[0]) if vectors else 0

    return EmbeddingResponse(
        embeddings=vectors,
        dimension=dim,
        count=len(vectors),
    )
