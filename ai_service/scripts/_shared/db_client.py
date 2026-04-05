"""Direct PostgreSQL connection for the fine-tuning pipeline.

Reads DATABASE_URL from the environment (same value the backend uses).
"""

from __future__ import annotations

import os
import struct
from typing import Any, List, Optional

from loguru import logger

try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
except ImportError:
    psycopg2 = None  # type: ignore


def _get_database_url() -> str:
    """Resolve DATABASE_URL from env or .env file."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url

    # Try loading from the AI microservice .env
    from ai_service.scripts._shared.paths import AI_SERVICE_ROOT

    env_path = AI_SERVICE_ROOT.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    raise RuntimeError(
        "DATABASE_URL not found. Set it in your environment or in "
        "Legal-Case-Management-System-AI-Microservice/.env"
    )


def get_connection():
    """Return a psycopg2 connection to the project database."""
    if psycopg2 is None:
        raise ImportError(
            "psycopg2 is required for DB access. "
            "Install it with: pip install psycopg2-binary"
        )
    url = _get_database_url()
    logger.info("Connecting to database...")
    return psycopg2.connect(url)


def load_regulations(conn=None) -> List[dict]:
    """Load all active regulations from the DB.

    Returns list of dicts: {id, title, category}.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, category FROM regulations WHERE status = 'active'"
            )
            rows = [dict(r) for r in cur.fetchall()]
        logger.info(f"Loaded {len(rows)} active regulations from DB")
        return rows
    finally:
        if close:
            conn.close()


def load_regulation_chunks_by_article(
    regulation_id: int,
    article_ref: str,
    conn=None,
) -> List[dict]:
    """Find regulation chunks matching a specific article reference.

    Returns list of dicts: {id, content, article_ref, chunk_index}.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Try exact match first, then LIKE match
            cur.execute(
                """
                SELECT id, content, article_ref, chunk_index
                FROM regulation_chunks
                WHERE regulation_id = %s
                  AND article_ref IS NOT NULL
                  AND article_ref ILIKE %s
                ORDER BY chunk_index
                """,
                (regulation_id, f"%{article_ref}%"),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        if close:
            conn.close()


def load_first_chunk_for_regulation(
    regulation_id: int,
    conn=None,
) -> Optional[dict]:
    """Load the first chunk of the latest version of a regulation.

    Returns dict {id, content, article_ref} or None.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT rc.id, rc.content, rc.article_ref
                FROM regulation_chunks rc
                JOIN regulation_versions rv
                  ON rc.regulation_version_id = rv.id
                WHERE rc.regulation_id = %s
                ORDER BY rv.version_number DESC, rc.chunk_index ASC
                LIMIT 1
                """,
                (regulation_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        if close:
            conn.close()


def _parse_pgvector(value: str) -> List[float]:
    """Parse a pgvector string like '[0.1,0.2,...]' into a list of floats."""
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    if isinstance(value, (bytes, memoryview)):
        # Binary format from pgvector
        data = bytes(value)
        dim = struct.unpack_from("<H", data, 0)[0]
        # Skip the 4-byte header (2 bytes dim + 2 bytes unused)
        floats = struct.unpack_from(f"<{dim}f", data, 4)
        return list(floats)
    s = str(value).strip().strip("[]")
    if not s:
        return []
    return [float(x.strip()) for x in s.split(",")]


def load_all_chunk_embeddings(conn=None) -> List[dict]:
    """Load all regulation chunks that have embeddings.

    Returns list of dicts: {id, regulation_id, content, category, embedding}.
    The embedding is parsed from pgvector format to List[float].

    NOTE: This can be memory-heavy for large datasets. Consider using
    a server-side cursor for very large corpora.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rc.id, rc.regulation_id, rc.content,
                       r.category, rc.embedding::text
                FROM regulation_chunks rc
                JOIN regulations r ON rc.regulation_id = r.id
                WHERE rc.embedding IS NOT NULL
                """
            )
            rows = []
            for row in cur:
                rows.append({
                    "id": row[0],
                    "regulation_id": row[1],
                    "content": row[2],
                    "category": row[3],
                    "embedding": _parse_pgvector(row[4]),
                })
        logger.info(f"Loaded {len(rows)} chunk embeddings from DB")
        return rows
    finally:
        if close:
            conn.close()


def load_chunks_by_regulation(regulation_id: int, conn=None) -> List[dict]:
    """Load all chunks for a specific regulation.

    Returns list of dicts: {id, content, article_ref, chunk_index}.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT rc.id, rc.content, rc.article_ref, rc.chunk_index
                FROM regulation_chunks rc
                JOIN regulation_versions rv
                  ON rc.regulation_version_id = rv.id
                WHERE rc.regulation_id = %s
                ORDER BY rv.version_number DESC, rc.chunk_index ASC
                """,
                (regulation_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        if close:
            conn.close()
