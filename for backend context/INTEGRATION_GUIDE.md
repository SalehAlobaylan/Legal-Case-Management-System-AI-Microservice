# AI Microservice ↔ Backend Integration Guide

This document explains how the deployed backend API should integrate with the AI microservice for case-regulation linking.

---

## Overview

The AI microservice provides semantic similarity matching between case facts and regulations. The backend uses this to:

1. **Generate AI Suggestions** - When a lawyer clicks "Generate Suggestions" on a case
2. **Find Related Laws** - Match case facts against all regulations
3. **Store Results** - Save matches to `ai_links` table with similarity scores
4. **Verify/Dismiss** - Allow lawyers to verify or dismiss suggestions

---

## Integration Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Next.js)                                         │
│  - Case Detail Page                                         │
│  - "Generate Suggestions" button                            │
└────────────────────┬────────────────────────────────────────┘
                     │ Click "Generate Suggestions"
                     │
┌────────────────────▼────────────────────────────────────────┐
│  Backend API (Fastify)                                      │
│  POST /api/ai-links/:caseId/generate                        │
│                                                              │
│  1. Fetch case details (title + description)                │
│  2. Fetch all active regulations from DB                    │
│  3. Call AI service POST /similarity/find-related           │
│  4. Store results in ai_links table                         │
│  5. Return results to frontend                              │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP POST
                     │
┌────────────────────▼────────────────────────────────────────┐
│  AI Microservice (FastAPI)                                  │
│  POST /similarity/find-related                              │
│                                                              │
│  1. Receive case text + regulation list                     │
│  2. Compute embeddings                                      │
│  3. Rank regulations by similarity (cosine)                 │
│  4. Return ranked list with scores                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Backend Implementation Steps

### 1. Create AI Links Service in Backend

**File:** `src/services/ai-client.service.ts`

```typescript
import axios, { AxiosInstance } from 'axios';
import { logger } from '../utils/logger';

interface RegulationCandidate {
  id: number;
  title: string;
  category?: string;
  content_text?: string;
}

interface FindRelatedRequest {
  case_text: string;
  regulations: RegulationCandidate[];
  top_k?: number;      // Default: 10
  threshold?: number;  // Default: 0.3
}

interface RelatedRegulation {
  regulation_id: number;
  title: string;
  category?: string;
  similarity_score: number;
}

interface FindRelatedResponse {
  related_regulations: RelatedRegulation[];
  query_length: number;
  candidates_count: number;
}

export class AIClientService {
  private client: AxiosInstance;
  private aiServiceUrl: string;
  private timeout: number = 30000; // 30 seconds

  constructor(aiServiceUrl: string = process.env.AI_SERVICE_URL || 'http://localhost:8000') {
    this.aiServiceUrl = aiServiceUrl;
    this.client = axios.create({
      baseURL: this.aiServiceUrl,
      timeout: this.timeout,
    });
  }

  /**
   * Find regulations related to a case
   * @param caseText Case title + description combined
   * @param regulations List of available regulations from DB
   * @param topK Maximum number of results to return (default: 10)
   * @param threshold Minimum similarity score to include (default: 0.3)
   * @returns Promise<FindRelatedResponse>
   */
  async findRelatedRegulations(
    caseText: string,
    regulations: RegulationCandidate[],
    topK: number = 10,
    threshold: number = 0.3,
  ): Promise<FindRelatedResponse> {
    try {
      logger.info('Calling AI service to find related regulations', {
        caseTextLength: caseText.length,
        regulationCount: regulations.length,
        topK,
        threshold,
      });

      const response = await this.client.post<FindRelatedResponse>(
        '/similarity/find-related',
        {
          case_text: caseText,
          regulations: regulations,
          top_k: topK,
          threshold: threshold,
        } as FindRelatedRequest,
      );

      logger.info('AI service returned results', {
        matchesFound: response.data.related_regulations.length,
        totalCandidates: response.data.candidates_count,
      });

      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        logger.error('AI service request failed', {
          status: error.response?.status,
          message: error.message,
          data: error.response?.data,
        });
        throw new Error(`AI service error: ${error.response?.status} ${error.message}`);
      }
      logger.error('Unexpected error calling AI service', { error });
      throw error;
    }
  }
}

export const aiClient = new AIClientService();
```

---

### 2. Create AI Links Route Handler

**File:** `src/routes/ai-links.ts`

```typescript
import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import { aiClient } from '../services/ai-client.service';
import { db } from '../db';
import { aiLinksTable, casesTable, regulationsTable } from '../db/schema';
import { logger } from '../utils/logger';
import { eq, and } from 'drizzle-orm';

export async function registerAILinksRoutes(app: FastifyInstance) {
  /**
   * POST /api/ai-links/:caseId/generate
   * 
   * Trigger AI to generate regulation matches for a case
   * 
   * Response: 200 OK with array of created AI links
   */
  app.post('/api/ai-links/:caseId/generate', async (req: FastifyRequest<{
    Params: { caseId: string };
  }>, reply: FastifyReply) => {
    try {
      const caseId = parseInt(req.params.caseId, 10);
      if (isNaN(caseId)) {
        return reply.status(400).send({ error: 'Invalid case ID' });
      }

      // 1. Fetch case
      const caseRecord = await db
        .select()
        .from(casesTable)
        .where(eq(casesTable.id, caseId))
        .limit(1);

      if (!caseRecord.length) {
        return reply.status(404).send({ error: 'Case not found' });
      }

      const caseData = caseRecord[0];

      // 2. Build case text for embedding
      const caseText = `${caseData.title}\n${caseData.description || ''}`;

      // 3. Fetch all active regulations
      const regulations = await db
        .select()
        .from(regulationsTable)
        .where(eq(regulationsTable.status, 'active'));

      if (!regulations.length) {
        return reply.status(400).send({ error: 'No regulations available for matching' });
      }

      logger.info('Triggering AI matching', {
        caseId,
        regulationCount: regulations.length,
      });

      // 4. Call AI service
      const aiResponse = await aiClient.findRelatedRegulations(
        caseText,
        regulations.map(r => ({
          id: r.id,
          title: r.title,
          category: r.category,
          content_text: r.content_text,
        })),
        10,  // top_k
        0.3, // threshold
      );

      // 5. Delete existing unverified links for this case
      await db
        .delete(aiLinksTable)
        .where(
          and(
            eq(aiLinksTable.caseId, caseId),
            eq(aiLinksTable.verified, false),
            eq(aiLinksTable.method, 'ai'),
          )
        );

      // 6. Insert new AI links
      const newLinks = await db
        .insert(aiLinksTable)
        .values(
          aiResponse.related_regulations.map(match => ({
            caseId,
            regulationId: match.regulation_id,
            similarityScore: match.similarity_score,
            method: 'ai',
            verified: false,
          }))
        )
        .returning();

      logger.info('AI links generated successfully', {
        caseId,
        linksCreated: newLinks.length,
      });

      return reply.status(200).send({
        links: newLinks.map(link => ({
          id: link.id,
          caseId: link.caseId,
          regulationId: link.regulationId,
          similarityScore: link.similarityScore,
          method: link.method,
          verified: link.verified,
          createdAt: link.createdAt,
        })),
      });
    } catch (error) {
      logger.error('Error generating AI links', { error });
      if (error instanceof Error && error.message.includes('AI service')) {
        // AI service unavailable - don't fail the request
        return reply.status(503).send({
          error: 'AI service unavailable',
          message: 'Please try again later',
        });
      }
      return reply.status(500).send({ error: 'Failed to generate AI links' });
    }
  });

  /**
   * GET /api/ai-links/:caseId
   * 
   * Fetch AI-generated links for a case (both verified and unverified)
   */
  app.get('/api/ai-links/:caseId', async (req: FastifyRequest<{
    Params: { caseId: string };
  }>, reply: FastifyReply) => {
    try {
      const caseId = parseInt(req.params.caseId, 10);
      if (isNaN(caseId)) {
        return reply.status(400).send({ error: 'Invalid case ID' });
      }

      const links = await db
        .select()
        .from(aiLinksTable)
        .where(eq(aiLinksTable.caseId, caseId))
        .orderBy(aiLinksTable.similarityScore); // Descending

      return reply.status(200).send({
        links: links.map(link => ({
          id: link.id,
          caseId: link.caseId,
          regulationId: link.regulationId,
          similarityScore: link.similarityScore,
          method: link.method,
          verified: link.verified,
          createdAt: link.createdAt,
        })),
      });
    } catch (error) {
      logger.error('Error fetching AI links', { error });
      return reply.status(500).send({ error: 'Failed to fetch AI links' });
    }
  });

  /**
   * PATCH /api/ai-links/:linkId/verify
   * 
   * Mark an AI link as verified by lawyer
   */
  app.patch('/api/ai-links/:linkId/verify', async (req: FastifyRequest<{
    Params: { linkId: string };
  }>, reply: FastifyReply) => {
    try {
      const linkId = parseInt(req.params.linkId, 10);
      if (isNaN(linkId)) {
        return reply.status(400).send({ error: 'Invalid link ID' });
      }

      await db
        .update(aiLinksTable)
        .set({ verified: true })
        .where(eq(aiLinksTable.id, linkId));

      return reply.status(200).send({ ok: true });
    } catch (error) {
      logger.error('Error verifying AI link', { error });
      return reply.status(500).send({ error: 'Failed to verify AI link' });
    }
  });

  /**
   * DELETE /api/ai-links/:linkId
   * 
   * Dismiss/remove an AI link
   */
  app.delete('/api/ai-links/:linkId', async (req: FastifyRequest<{
    Params: { linkId: string };
  }>, reply: FastifyReply) => {
    try {
      const linkId = parseInt(req.params.linkId, 10);
      if (isNaN(linkId)) {
        return reply.status(400).send({ error: 'Invalid link ID' });
      }

      await db
        .delete(aiLinksTable)
        .where(eq(aiLinksTable.id, linkId));

      return reply.status(204).send();
    } catch (error) {
      logger.error('Error deleting AI link', { error });
      return reply.status(500).send({ error: 'Failed to delete AI link' });
    }
  });
}
```

---

### 3. Register Route in Main App

**File:** `src/app.ts` or `src/server.ts`

```typescript
import { registerAILinksRoutes } from './routes/ai-links';

async function startServer() {
  const app = fastify({
    logger: true,
  });

  // ... other plugins ...

  // Register routes
  await registerAILinksRoutes(app);

  // ... other routes ...

  await app.listen({ port: 3001 });
}

startServer();
```

---

### 4. Add Environment Variables

**File:** `.env` or `.env.local`

```env
# AI Microservice Configuration
AI_SERVICE_URL=http://localhost:8000

# Or for deployed AI service:
# AI_SERVICE_URL=https://ai-service.example.com

# Backend API Configuration
BACKEND_API_URL=https://orca-app-uayze.ondigitalocean.app
```

---

## AI Microservice API Endpoint

### POST `/similarity/find-related`

Find regulations most relevant to a case text.

**Request:**

```json
{
  "case_text": "Labor dispute regarding wrongful termination of employee Mohammed Al-Amoudi",
  "regulations": [
    {
      "id": 1,
      "title": "Saudi Labor Law",
      "category": "labor",
      "content_text": "Article 77: Employment contracts are at-will unless otherwise stated..."
    },
    {
      "id": 2,
      "title": "Commercial Court Procedures",
      "category": "commercial",
      "content_text": "..."
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
    },
    {
      "regulation_id": 5,
      "title": "Labor Dispute Resolution",
      "category": "labor",
      "similarity_score": 0.87
    }
  ],
  "query_length": 58,
  "candidates_count": 2
}
```

---

## Error Handling Strategy

The backend should handle these scenarios gracefully:

| Scenario | Status | Action |
|----------|--------|--------|
| AI service unavailable | 503 | Return error to frontend, user can retry |
| AI service timeout | 504 | Return error, user can retry |
| Invalid case ID | 400 | Return validation error |
| No regulations in DB | 400 | Return error about missing data |
| No matches found | 200 | Return empty `related_regulations` array |
| Database error | 500 | Return error, log for debugging |

---

## Database Schema (ai_links Table)

```sql
CREATE TABLE ai_links (
  id SERIAL PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  regulation_id INTEGER NOT NULL REFERENCES regulations(id) ON DELETE CASCADE,
  similarity_score DECIMAL(5,4) NOT NULL, -- 0.0000 to 1.0000
  method VARCHAR(20) NOT NULL DEFAULT 'ai', -- 'ai', 'manual', 'hybrid'
  verified BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  
  UNIQUE(case_id, regulation_id), -- No duplicate links
  INDEX idx_case_id (case_id),
  INDEX idx_verified (verified)
);
```

---

## Deployment Configuration

### Local Development

```bash
# Terminal 1: Start AI microservice
cd ai_service
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2: Start Fastify backend
cd backend
npm run dev
# Backend will call AI service at http://localhost:8000
```

### Docker Compose

```yaml
version: '3.8'

services:
  ai-service:
    build: ./ai_service
    ports:
      - "8000:8000"
    environment:
      CORS_ORIGINS: '["http://localhost:3001", "https://orca-app-uayze.ondigitalocean.app"]'

  backend:
    build: ./backend
    ports:
      - "3001:3001"
    environment:
      AI_SERVICE_URL: http://ai-service:8000
      DATABASE_URL: postgresql://...
      BACKEND_API_URL: https://orca-app-uayze.ondigitalocean.app
    depends_on:
      - ai-service
      - database

  database:
    image: postgres:15-alpine
    environment:
      POSTGRES_PASSWORD: ...
```

### Deployed (DigitalOcean)

```env
# On deployed backend
AI_SERVICE_URL=https://ai-service.your-domain.com
BACKEND_API_URL=https://orca-app-uayze.ondigitalocean.app
```

---

## Testing the Integration

### 1. Verify AI Service is Running

```bash
curl http://localhost:8000/
# Response: {"app": "AI Microservice", "version": "0.1.0", "env": "development"}
```

### 2. Test Find Related Regulations Endpoint

```bash
curl -X POST http://localhost:8000/similarity/find-related \
  -H "Content-Type: application/json" \
  -d '{
    "case_text": "Employee was terminated without notice",
    "regulations": [
      {
        "id": 1,
        "title": "Saudi Labor Law",
        "category": "labor",
        "content_text": "Article 77 deals with termination requirements"
      },
      {
        "id": 2,
        "title": "Commercial Law",
        "category": "commercial",
        "content_text": "Commercial transactions..."
      }
    ],
    "top_k": 5,
    "threshold": 0.5
  }'
```

### 3. Test Backend Endpoint (Once Implemented)

```bash
# Assuming a case with ID=1 exists
curl -X POST http://localhost:3001/api/ai-links/1/generate \
  -H "Authorization: Bearer <JWT_TOKEN>"

# Response:
# {
#   "links": [
#     {
#       "id": 101,
#       "caseId": 1,
#       "regulationId": 1,
#       "similarityScore": 0.92,
#       "method": "ai",
#       "verified": false,
#       "createdAt": "2024-12-25T10:30:00Z"
#     }
#   ]
# }
```

---

## Performance Considerations

### Request Timeouts
- AI service default timeout: **30 seconds**
- Recommend backend timeout to frontend: **60 seconds** (allows AI processing + DB queries)

### Caching
- **Regulation embeddings**: Cache in Redis after first computation
- **Query results**: Don't cache (user may update regulations)

### Scaling
- AI service: Runs embeddings in parallel (vectorized with numpy)
- Backend: Use connection pools for database
- Load balancing: Place AI service behind nginx/reverse proxy

### Batch Processing (Future)
Instead of one-by-one requests:

```python
# Batch multiple cases in one AI call
# POST /similarity/find-related
{
  "cases": [
    {"case_id": 1, "text": "..."},
    {"case_id": 2, "text": "..."}
  ],
  "regulations": [...],
  "top_k": 10
}
```

---

## Monitoring & Logging

### What to Log

```typescript
// In ai-client.service.ts
logger.info('AI request', {
  caseId,
  regulationCount,
  topK,
  threshold,
  durationMs: Date.now() - start,
});

logger.error('AI request failed', {
  caseId,
  error: error.message,
  status: error.status,
  retryable: error.status >= 500,
});
```

### Metrics to Track

- Average AI response time
- Success rate of AI matching requests
- Number of regulations without matches
- Average similarity score of accepted links

---

## Security Notes

1. **API Key**: Currently using public API (`no authentication`)
   - If needed, add `Authorization: Bearer <api-key>` header

2. **Rate Limiting**: Add to backend routes to prevent abuse
   ```typescript
   app.register(require('@fastify/rate-limit'), {
     max: 100, // 100 requests
     timeWindow: '15 min'
   });
   ```

3. **Input Validation**: Always validate:
   - `case_text` length (max 10,000 chars)
   - `regulations` array size (max 500 items)
   - Similarity threshold range (0.0 - 1.0)

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Connection refused` | Verify AI service is running on port 8000 |
| `No matches found` | Lower threshold (default 0.3), add more regulations |
| `Timeout error` | AI service is slow, check for long requests |
| `Empty regulations list` | Load regulations from DB before calling AI |
| `High similarity to wrong laws` | May indicate low-quality regulation content |

---

## Next Steps

1. **Implement backend routes** using the code above
2. **Set environment variables** for AI service URL
3. **Test manually** with curl commands
4. **Add unit tests** for AI client service
5. **Monitor in production** for AI service availability
6. **Collect metrics** on AI suggestion quality
7. **Optimize** embeddings based on user feedback

---

## Contact & Support

For issues with:
- **AI Service**: Check logs in `ai_service/logs/`
- **Backend Integration**: Check backend service logs
- **Embeddings Quality**: Review case-regulation pairs that got low scores
