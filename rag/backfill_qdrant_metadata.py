"""Rebuild PostgreSQL document metadata from Qdrant payloads.

This is idempotent and does not modify Qdrant points.
"""
import os
from collections import defaultdict

import psycopg2
from qdrant_client import QdrantClient


DATABASE_URL = os.environ["DATABASE_URL"]
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "documents")


def ensure_schema(conn) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE documents
                    ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'pending',
                    ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS index_error TEXT,
                    ADD COLUMN IF NOT EXISTS qdrant_collection TEXT NOT NULL DEFAULT 'documents'
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_index_status ON documents (index_status)"
            )


def read_documents(client: QdrantClient) -> dict[str, list[dict]]:
    documents: dict[str, list[dict]] = defaultdict(list)
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            document_id = payload.get("document_id")
            if document_id:
                documents[str(document_id)].append(payload)
        if offset is None:
            break
    return documents


def main() -> None:
    client = QdrantClient(url=QDRANT_URL)
    documents = read_documents(client)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        ensure_schema(conn)
        with conn:
            with conn.cursor() as cur:
                for document_id, chunks in documents.items():
                    chunks.sort(key=lambda item: int(item.get("chunk_index", 0)))
                    filename = next(
                        (str(item["filename"]) for item in chunks if item.get("filename")),
                        "unknown",
                    )
                    content = "\n\n".join(str(item.get("content", "")) for item in chunks)
                    cur.execute(
                        """
                        INSERT INTO documents
                            (id, filename, content, chunk_count, index_status,
                             indexed_at, index_error, qdrant_collection)
                        VALUES (%s, %s, %s, %s, 'indexed', NOW(), NULL, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            filename = EXCLUDED.filename,
                            content = EXCLUDED.content,
                            chunk_count = EXCLUDED.chunk_count,
                            index_status = 'indexed',
                            indexed_at = NOW(),
                            index_error = NULL,
                            qdrant_collection = EXCLUDED.qdrant_collection
                        """,
                        (
                            document_id,
                            filename,
                            content[:10000],
                            len(chunks),
                            QDRANT_COLLECTION,
                        ),
                    )
    finally:
        conn.close()

    chunk_count = sum(len(chunks) for chunks in documents.values())
    print(f"Backfilled {len(documents)} documents from {chunk_count} Qdrant points")


if __name__ == "__main__":
    main()
