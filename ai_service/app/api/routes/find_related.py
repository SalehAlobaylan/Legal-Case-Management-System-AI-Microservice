"""
Backend integration endpoint for finding related regulations.

This endpoint is designed specifically for the backend API to call:
- Input: Case text + list of regulations from database
- Output: Ranked list of regulations with similarity scores and IDs
"""

from __future__ import annotations

from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException
from app.core.similarity import SimilarityService
from app.api.schemas.requests import FindRelatedRequest
from app.api.schemas.responses import FindRelatedResponse, RelatedRegulation
from app.utils.logger import logger

router = APIRouter()


@router.post("/similarity/find-related", response_model=FindRelatedResponse)
async def find_related_regulations(payload: FindRelatedRequest) -> FindRelatedResponse:
    """
    Find regulations most relevant to a given case.
    
    This endpoint is optimized for backend integration.
    
    **Request:**
    ```json
    {
      "case_text": "Labor dispute regarding wrongful termination of employee...",
      "regulations": [
        {
          "id": 1,
          "title": "Saudi Labor Law",
          "category": "labor",
          "content_text": "Labor law article 77..."
        },
        ...
      ],
      "top_k": 10,
      "threshold": 0.3
    }
    ```
    
    **Response:**
    ```json
    {
      "related_regulations": [
        {
          "regulation_id": 1,
          "title": "Saudi Labor Law",
          "category": "labor",
          "similarity_score": 0.92
        },
        ...
      ],
      "query_length": 245,
      "candidates_count": 50
    }
    ```
    
    Args:
        payload: FindRelatedRequest with case text and regulation candidates
        
    Returns:
        FindRelatedResponse with ranked regulations and metadata
        
    Raises:
        HTTPException 400: If case_text is empty or regulations list is empty
        HTTPException 500: If embedding/similarity service fails
    """
    try:
        # Validate inputs
        if not payload.case_text or not payload.case_text.strip():
            raise HTTPException(
                status_code=400,
                detail="case_text cannot be empty"
            )
        
        if not payload.regulations:
            raise HTTPException(
                status_code=400,
                detail="regulations list cannot be empty"
            )
        
        # Extract regulation texts for similarity computation
        # Use title + category + content for better matching
        regulation_texts = []
        regulation_ids = []
        regulation_metadata = {}
        
        for reg in payload.regulations:
            reg_id = reg.id
            regulation_ids.append(reg_id)
            
            # Build comprehensive text for embedding
            text_parts = [reg.title]
            if reg.category:
                text_parts.append(f"({reg.category})")
            if reg.content_text:
                text_parts.append(reg.content_text)
            
            full_text = " ".join(text_parts)
            regulation_texts.append(full_text)
            
            # Store metadata for response
            regulation_metadata[reg_id] = {
                "title": reg.title,
                "category": reg.category,
            }
        
        logger.info(
            f"Finding related regulations",
            extra={
                "case_text_len": len(payload.case_text),
                "num_candidates": len(payload.regulations),
                "top_k": payload.top_k,
                "threshold": payload.threshold,
            }
        )
        
        # Compute similarity
        service = SimilarityService()
        ranked_results = service.rank(
            queries=[payload.case_text],
            corpus=regulation_texts,
            top_k=payload.top_k,
        )
        
        # ranked_results is List[List[(doc, score)]]
        # Since we have 1 query, we get 1 result list
        if not ranked_results or not ranked_results[0]:
            return FindRelatedResponse(
                related_regulations=[],
                query_length=len(payload.case_text),
                candidates_count=len(payload.regulations),
            )
        
        matched_pairs = ranked_results[0]  # List of (text, score) tuples
        text_to_indices = defaultdict(deque)
        for idx, text in enumerate(regulation_texts):
            text_to_indices[text].append(idx)

        # Build response, filtering by threshold
        related_regulations = []
        for matched_text, score in matched_pairs:
            if score < payload.threshold:
                continue

            indices = text_to_indices.get(matched_text)
            if not indices:
                continue

            reg_id = regulation_ids[indices.popleft()]
            metadata = regulation_metadata[reg_id]
            
            related_regulations.append(
                RelatedRegulation(
                    regulation_id=reg_id,
                    title=metadata["title"],
                    category=metadata["category"],
                    similarity_score=float(score),
                )
            )
        
        logger.info(
            f"Found {len(related_regulations)} related regulations",
            extra={
                "case_text_len": len(payload.case_text),
                "total_candidates": len(payload.regulations),
                "matches": len(related_regulations),
            }
        )
        
        return FindRelatedResponse(
            related_regulations=related_regulations,
            query_length=len(payload.case_text),
            candidates_count=len(payload.regulations),
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error finding related regulations: {str(e)}",
            extra={"error_type": type(e).__name__}
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to find related regulations: {str(e)}"
        )
