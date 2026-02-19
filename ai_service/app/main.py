import random

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import health, embeddings, similarity, find_related, regulation_extract

random.seed(42)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
    }


app.include_router(health.router, tags=["health"])
app.include_router(embeddings.router, tags=["embeddings"])
app.include_router(similarity.router, tags=["similarity"])
app.include_router(find_related.router, tags=["similarity"])
app.include_router(regulation_extract.router, tags=["regulation-extraction"])
