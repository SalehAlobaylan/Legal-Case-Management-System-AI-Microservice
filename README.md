# Legal Case Management System – AI Microservice

[![CI](https://github.com/oShuail/Legal-Case-Management-System-AI-Microservice/actions/workflows/ci.yml/badge.svg)](https://github.com/oShuail/Legal-Case-Management-System-AI-Microservice/actions/workflows/ci.yml)

**Status:** ✅ Production Ready | **Backend Integration:** Ready  
**Master API:** https://orca-app-uayze.ondigitalocean.app/

This repository contains the **AI microservice** for the _Legal Case Management System (LCMS)_ project.

The service provides AI-powered semantic matching between legal cases and regulations using multilingual embeddings (Arabic/English).

---

## 🎯 What It Does

1. **Analyzes legal cases** - Receives case text (title + description)
2. **Matches against regulations** - Compares against all available laws/regulations
3. **Ranks by relevance** - Returns matches with confidence scores (0.0-1.0)
4. **Enables quick linking** - Backend stores results as case-regulation relationships

**Use Case:** When a lawyer creates a labor dispute case, they click "Generate AI Suggestions" and the system finds all relevant labor laws automatically.

---

## 🚀 Quick Start

### 1. Start the Service
```bash
cd ai_service
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. View API Documentation
Open: **http://localhost:8000/docs** (Swagger UI)

### 3. Test the Integration Endpoint
See [QUICKSTART.md](QUICKSTART.md) for sample requests

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| **[QUICKSTART.md](QUICKSTART.md)** | Get running in 5 minutes ⭐ |
| **[INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md)** | Complete backend integration (500+ lines) ⭐ |
| **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** | What was implemented and why |
| **[VERIFICATION_CHECKLIST.md](VERIFICATION_CHECKLIST.md)** | Quality assurance checklist |
| **[context/API_SPECIFICATION.md](context/API_SPECIFICATION.md)** | Backend API contract |
| **[context/AI_API_SPECIFICATION.md](context/AI_API_SPECIFICATION.md)** | AI service endpoints |
| **[context/LCMS_Comprehensive_Context.md](context/LCMS_Comprehensive_Context.md)** | System architecture |

---

## 📋 API Endpoints

### ⭐ NEW: POST `/similarity/find-related`

Find regulations related to a case. **For backend integration.**

**Request:**
```json
{
  "case_text": "Labor dispute regarding wrongful termination",
  "regulations": [
    {
      "id": 1,
      "title": "Saudi Labor Law",
      "category": "labor",
      "content_text": "Article 77 regarding termination..."
    }
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
    }
  ],
  "query_length": 48,
  "candidates_count": 1
}
```

### Existing Endpoints

- **POST `/embed/`** - Generate embeddings for text
- **POST `/similarity/`** - Rank documents by similarity (text-based)
- **POST `/regulations/extract`** - Fetch + extract regulation text (parser + OCR fallback)
- **GET `/`** - Root endpoint
- **GET `/health/`** - Health check

---

## 🏗️ Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Framework | FastAPI | 0.115.6 |
| Server | Uvicorn | latest |
| Python | Python | 3.12 |
| Model | BAAI/bge-m3 | multilingual |
| Embeddings | sentence-transformers | 3.3.1 |
| Config | Pydantic v2 | latest |
| Testing | pytest | 8.3.5 |
| Logging | loguru | latest |
| Containerization | Docker | latest |

---

## 📁 Project Structure

```
ai_service/
├── app/
│   ├── main.py                      # FastAPI app
│   ├── config.py                    # Settings (environment variables)
│   ├── api/
│   │   ├── routes/
│   │   │   ├── health.py
│   │   │   ├── embeddings.py
│   │   │   ├── similarity.py
│   │   │   └── find_related.py      # ⭐ NEW endpoint for backend
│   │   └── schemas/
│   │       ├── requests.py
│   │       └── responses.py
│   ├── core/
│   │   ├── embeddings.py
│   │   ├── models.py
│   │   └── similarity.py
│   ├── tests/
│   │   ├── test_api.py
│   │   ├── test_similarity_core.py
│   │   ├── test_integration.py      # ⭐ NEW
│   │   └── test_integration_manual.py # ⭐ NEW
│   └── utils/
│       └── logger.py
│
├── models/
│   └── models--BAAI--bge-m3/        # Pre-downloaded embedding model
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pytest.ini
└── README.md
```

---

## 🔧 Configuration

### Environment Variables

```env
# App info
APP_NAME=AI Microservice
APP_VERSION=0.1.0
ENV=development
DEBUG=true
LOG_LEVEL=INFO

# Server
HOST=0.0.0.0
PORT=8000

# Embeddings
EMBEDDINGS_PROVIDER=bge
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DEVICE=cpu

# CORS
CORS_ORIGINS=["http://localhost:3000","http://localhost:5173"]

# Backend API (new)
BACKEND_API_URL=https://orca-app-uayze.ondigitalocean.app
BACKEND_API_KEY=

# Regulation extraction / OCR
OCR_PRIMARY_PROVIDER=alapi
OCR_SECONDARY_PROVIDER=none
ALAPI_BASE_URL=https://alapi.deep.sa
ALAPI_API_KEY=
ALAPI_OCR_PATH=/ocr
SOURCE_WHITELIST_DOMAINS=laws.boe.gov.sa,laws.moj.gov.sa,boe.gov.sa,moj.gov.sa
EXTRACTION_TIMEOUT_SECONDS=30
EXTRACTION_MAX_BYTES=15000000
EXTRACTION_MAX_CHARS=120000
OCR_MIN_TEXT_CHARS=400
OCR_STRICT_MODE=false
```

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Test integration endpoint
python app/tests/test_integration_manual.py

# Run async tests
python app/tests/test_integration.py
```

---

## 🚀 Deployment

### Local Development
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker
```bash
docker build -t ai-service .
docker run -p 8000:8000 ai-service
```

### Docker Compose
```bash
docker-compose up -d
```

---

## 📊 Performance

| Metric | Value |
|--------|-------|
| Model Size | ~560MB |
| Memory | ~2GB |
| First Request | ~5-10s (model load) |
| Typical Request | 200-500ms |
| Max Regulations | 50+ |

---

## 🔒 Security

- ✅ Input validation (type & length checking)
- ✅ CORS properly configured
- ✅ Error messages safe (no leaks)
- ✅ No SQL injection risks
- ✅ Rate limiting compatible

---

## 🎯 Backend Integration

The backend should implement:

1. **AI Client Service** - HTTP wrapper for `/similarity/find-related`
2. **Route Handler** - `POST /api/ai-links/:caseId/generate`
3. **Database Storage** - Save results to `ai_links` table
4. **Error Handling** - Graceful degradation if AI service unavailable

**See [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) for complete TypeScript examples.**

---

## ✅ What's New (v1.1.0)

- ✨ **New Endpoint:** `POST /similarity/find-related` for backend integration
- 📝 **Comprehensive Docs:** 1500+ lines of integration guides
- 🧪 **Test Scripts:** Integration test suite
- 🔒 **Better Error Handling:** Descriptive messages & logging
- ⚙️ **Configuration:** Environment-based setup for backend API

See [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) for details.

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| Port already in use | Use different port: `--port 8001` |
| Model not found | Auto-downloads on first run (~560MB) |
| No matches | Lower threshold, add regulation content |
| Slow performance | First request loads model (normal) |

---

## 📞 Support

1. **Quick Start?** → [QUICKSTART.md](QUICKSTART.md)
2. **Backend Integration?** → [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md)
3. **API Questions?** → http://localhost:8000/docs
4. **Issues?** → Check logs & [VERIFICATION_CHECKLIST.md](VERIFICATION_CHECKLIST.md)

---

## 📜 License

Copyright © 2026 Legal Case Management System. All rights reserved.

---

**Status:** ✅ Production Ready  
**Last Updated:** January 31, 2026  
**Backend Integration:** Ready (see [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md))

**Ready to integrate?** Start with [QUICKSTART.md](QUICKSTART.md)!
      __init__.py
      embeddings.py        # Embedding backend + service
      similarity.py        # SimilarityService (cosine similarity)
    utils/
      __init__.py
      logger.py            # loguru configuration
    tests/
      __init__.py
      test_api.py          # API-level tests
      test_similarity_core.py  # Core similarity tests

plans/
  ai-microservice-implementation-plan.md  # Planning document (not used by code)

Dockerfile
docker-compose.yml
requirements.txt
pytest.ini
.env.example
.gitignore
