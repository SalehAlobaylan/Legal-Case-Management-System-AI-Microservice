#!/usr/bin/env python3
"""
Integration Test - Backend ↔ AI Service

This script demonstrates how the backend should call the AI microservice
and validates the response format.

Usage:
    python test_integration_manual.py
"""

import json

# Sample request that backend would send
sample_request = {
    "case_text": "Labor dispute: Employee claims wrongful termination without valid cause. Employee Mohammed Al-Amoudi was terminated immediately without notice or compensation.",
    "regulations": [
        {
            "id": 1,
            "title": "Saudi Labor Law",
            "category": "labor",
            "content_text": "Article 77: Employment contracts and termination procedures. An employee who is terminated without valid cause shall be entitled to compensation."
        },
        {
            "id": 2,
            "title": "Commercial Court Procedures", 
            "category": "commercial",
            "content_text": "Guidelines for commercial litigation and dispute resolution between commercial entities."
        },
        {
            "id": 3,
            "title": "Labor Dispute Resolution",
            "category": "labor",
            "content_text": "Procedures for resolving disputes between employers and employees, including mediation and arbitration."
        },
        {
            "id": 4,
            "title": "Administrative Law",
            "category": "administrative",
            "content_text": "Regulations governing government agencies and administrative procedures."
        },
        {
            "id": 5,
            "title": "Family Law",
            "category": "family",
            "content_text": "Rules for marriage, divorce, inheritance, and family matters."
        },
    ],
    "top_k": 10,
    "threshold": 0.3
}

# Expected response format
sample_response = {
    "related_regulations": [
        {
            "regulation_id": 1,
            "title": "Saudi Labor Law",
            "category": "labor",
            "similarity_score": 0.92
        },
        {
            "regulation_id": 3,
            "title": "Labor Dispute Resolution",
            "category": "labor",
            "similarity_score": 0.87
        },
        {
            "regulation_id": 2,
            "title": "Commercial Court Procedures",
            "category": "commercial",
            "similarity_score": 0.45
        }
    ],
    "query_length": 148,
    "candidates_count": 5
}


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("INTEGRATION TEST: Backend ↔ AI Service")
    print("=" * 80)
    
    print("\n📨 REQUEST (Backend → AI Service)")
    print("-" * 80)
    print("POST http://localhost:8000/similarity/find-related")
    print("\nPayload:")
    print(json.dumps(sample_request, indent=2, ensure_ascii=False))
    
    print("\n📦 EXPECTED RESPONSE (AI Service → Backend)")
    print("-" * 80)
    print("Status: 200 OK")
    print("\nResponse Body:")
    print(json.dumps(sample_response, indent=2, ensure_ascii=False))
    
    print("\n✅ RESPONSE VALIDATION")
    print("-" * 80)
    print("The response must contain:")
    print("  ✓ related_regulations: List of matched regulations")
    print("    - regulation_id (int): ID from regulations table")
    print("    - title (str): Regulation name")
    print("    - category (str): Regulation category")
    print("    - similarity_score (float): 0.0-1.0")
    print("  ✓ query_length (int): Length of input case text")
    print("  ✓ candidates_count (int): Total regulations evaluated")
    print("\nResults should be sorted by similarity_score (descending)")
    
    print("\n📋 ERROR HANDLING")
    print("-" * 80)
    print("Status 400: If case_text is empty or regulations list is empty")
    print("Status 500: If embedding/similarity service fails")
    print("Status 200: With empty related_regulations if no matches above threshold")
    
    print("\n🔧 HOW BACKEND SHOULD USE THIS")
    print("-" * 80)
    print("""
1. When user clicks "Generate Suggestions" on a case:
   - Fetch case details (title + description)
   - Fetch all regulations from DB
   - Call POST /similarity/find-related with case text + regulations
   
2. Backend should:
   - Pass case_text as: "{case.title} {case.description}"
   - Pass regulations with id, title, category, content_text
   - Set top_k (default 10) and threshold (default 0.3)
   - Handle errors gracefully (don't fail case creation)
   
3. Process response:
   - For each returned regulation in related_regulations
   - Insert into ai_links table with:
     * case_id
     * regulation_id
     * similarity_score
     * method: 'ai'
     * verified: false
   
4. Return to frontend:
   - List of AI suggestions with scores
   - User can verify (accept) or dismiss (delete)
""")
    
    print("\n🚀 TESTING MANUALLY")
    print("-" * 80)
    print("1. Ensure AI service is running: python -m uvicorn app.main:app --port 8000")
    print("2. Run a simple test:")
    print("""
import requests

response = requests.post(
    'http://localhost:8000/similarity/find-related',
    json={
        "case_text": "Labor termination dispute",
        "regulations": [
            {"id": 1, "title": "Labor Law", "category": "labor", "content_text": "..."},
            {"id": 2, "title": "Commercial Law", "category": "commercial", "content_text": "..."}
        ],
        "top_k": 5,
        "threshold": 0.3
    }
)

print(response.json())
""")
    
    print("\n" + "=" * 80)
    print("For full integration guide, see: INTEGRATION_GUIDE.md")
    print("=" * 80 + "\n")
