"""
Test script for the new find-related regulations endpoint.

This script tests the integration between backend and AI service.
"""

import sys
import json
import httpx
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio


async def test_find_related_endpoint():
    """Test the POST /similarity/find-related endpoint."""
    
    base_url = "http://localhost:8000"
    
    # Sample regulations
    regulations = [
        {
            "id": 1,
            "title": "Saudi Labor Law",
            "category": "labor",
            "content_text": "Article 77: Employment contracts and termination procedures. An employee who is terminated without valid cause shall be entitled to compensation as specified in this article."
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
    ]
    
    # Sample case text
    case_text = "Labor dispute: Employee claims wrongful termination without valid cause. Employee was terminated immediately without notice or compensation."
    
    # Prepare request
    payload = {
        "case_text": case_text,
        "regulations": regulations,
        "top_k": 10,
        "threshold": 0.3
    }
    
    print("=" * 80)
    print("Testing POST /similarity/find-related")
    print("=" * 80)
    print(f"\nCase Text: {case_text}")
    print(f"\nRegulations: {len(regulations)}")
    print(f"  - {', '.join([f'ID:{r['id']} ({r['category']})' for r in regulations])}")
    print(f"\nRequest Payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    
    async with httpx.AsyncClient() as client:
        try:
            print("\n" + "-" * 80)
            print("Sending request to AI service...")
            print("-" * 80)
            
            response = await client.post(
                f"{base_url}/similarity/find-related",
                json=payload,
                timeout=30.0
            )
            
            print(f"\nStatus Code: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print("\n✅ SUCCESS!")
                print(f"\nResponse:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
                
                # Validate response
                print("\n" + "-" * 80)
                print("Response Validation:")
                print("-" * 80)
                
                assert "related_regulations" in result, "Missing related_regulations"
                assert "query_length" in result, "Missing query_length"
                assert "candidates_count" in result, "Missing candidates_count"
                
                related = result["related_regulations"]
                print(f"✅ Found {len(related)} related regulations")
                
                for i, reg in enumerate(related, 1):
                    assert "regulation_id" in reg, f"Missing regulation_id in result {i}"
                    assert "title" in reg, f"Missing title in result {i}"
                    assert "similarity_score" in reg, f"Missing similarity_score in result {i}"
                    
                    score = reg["similarity_score"]
                    assert 0.0 <= score <= 1.0, f"Invalid similarity_score: {score}"
                    
                    print(f"  {i}. Regulation ID {reg['regulation_id']}: {reg['title']}")
                    print(f"     Score: {score:.4f} | Category: {reg.get('category', 'N/A')}")
                
                print(f"\n✅ Query length: {result['query_length']}")
                print(f"✅ Candidates evaluated: {result['candidates_count']}")
                
                # Check that results are sorted by score (descending)
                scores = [r["similarity_score"] for r in related]
                assert scores == sorted(scores, reverse=True), "Results not sorted by score"
                print("✅ Results correctly sorted by score (descending)")
                
                return True
            else:
                print("\n❌ ERROR!")
                print(f"\nResponse Body:")
                print(response.text)
                return False
                
        except httpx.ConnectError as e:
            print(f"\n❌ Connection Error: {e}")
            print("Make sure the AI service is running on http://localhost:8000")
            return False
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_validation_errors():
    """Test error handling."""
    
    base_url = "http://localhost:8000"
    
    print("\n" + "=" * 80)
    print("Testing Error Cases")
    print("=" * 80)
    
    test_cases = [
        {
            "name": "Empty case text",
            "payload": {
                "case_text": "",
                "regulations": [{"id": 1, "title": "Test", "category": "labor"}]
            },
            "expected_status": 400
        },
        {
            "name": "Empty regulations list",
            "payload": {
                "case_text": "Some case text",
                "regulations": []
            },
            "expected_status": 400
        },
    ]
    
    async with httpx.AsyncClient() as client:
        for test_case in test_cases:
            print(f"\nTest: {test_case['name']}")
            try:
                response = await client.post(
                    f"{base_url}/similarity/find-related",
                    json=test_case["payload"],
                    timeout=30.0
                )
                
                if response.status_code == test_case["expected_status"]:
                    print(f"✅ Got expected status {response.status_code}")
                else:
                    print(f"❌ Expected {test_case['expected_status']}, got {response.status_code}")
                    print(f"   Response: {response.text}")
            except Exception as e:
                print(f"❌ Error: {e}")


async def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "AI Microservice ↔ Backend Integration Test".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")
    
    # Test main functionality
    success = await test_find_related_endpoint()
    
    # Test error cases
    await test_validation_errors()
    
    # Summary
    print("\n" + "=" * 80)
    if success:
        print("✅ All tests completed!")
        print("\nThe integration endpoint is ready for backend use.")
        print("Backend should call: POST http://localhost:8000/similarity/find-related")
    else:
        print("❌ Tests failed. Please check the AI service.")
    print("=" * 80 + "\n")
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
