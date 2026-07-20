#!/usr/bin/env python3
"""
seed_documents.py — Upload 10 QA/AI knowledge documents into the RAG vector DB.

Usage:
    python3 db/seed_documents.py

Requires RAG API running at http://localhost:8001
"""
import os
import sys
import time
import urllib.request
import urllib.parse
import json

RAG_API = os.environ.get("RAG_API_URL", "http://localhost:8001").rstrip("/")

DOC_DIR = "/Users/alexp/llm-wiki/raw"
DOCUMENTS = [
    f"{DOC_DIR}/01_Senior_QA_Automation_AI_Assisted_Testing_Handbook.docx",
    f"{DOC_DIR}/02_Playwright_TypeScript_Framework_Architecture.docx",
    f"{DOC_DIR}/03_LLM_Evaluation_RAG_Testing_QA_Guide.docx",
    f"{DOC_DIR}/04_MCP_Servers_Agentic_Testing_SDET_Guide.docx",
    f"{DOC_DIR}/05_Cloud_Kubernetes_Observability_Security_QA_Guide.docx",
    f"{DOC_DIR}/Advanced LLM Red Teaming and Adversarial Testing Strategies for QA Engineers.docx",
    f"{DOC_DIR}/Architecting LLM-as-a-Judge Evaluation Frameworks for Generative AI Features.docx",
    f"{DOC_DIR}/Data Integrity and Distributed Systems Automation_ GraphQL, Kafka, and Database Validation.docx",
    f"{DOC_DIR}/Enterprise Playwright Automation Architecture for Scalable SaaS Platforms.docx",
    f"{DOC_DIR}/Implementing Model Context Protocol (MCP) in Agentic Test Orchestration.docx",
]

def check_health():
    # nginx routes /api/rag/* to RAG (8001) but /api/health to chat_server (8000),
    # so use /api/rag/metrics as the readiness probe — it round-trips through the
    # same path the uploads will use.
    try:
        with urllib.request.urlopen(f"{RAG_API}/api/rag/metrics", timeout=5) as r:
            data = json.loads(r.read())
            total = data.get("aggregate", {}).get("total_queries", "?")
            print(f"  RAG reachable, total_queries={total}")
            return True
    except Exception as e:
        print(f"  ERROR: RAG API not reachable — {e}")
        return False

def upload_docx(filepath: str) -> dict:
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        data = f.read()

    boundary = "----PythonSeedBoundary7777"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n"
        f"\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{RAG_API}/api/rag/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

def main():
    print("=" * 60)
    print("RAG Document Seeder — alexpavsky.com")
    print("=" * 60)
    print()

    print("Checking RAG API health...")
    if not check_health():
        print("\nERROR: Start Docker first: docker compose up -d")
        sys.exit(1)

    print("  Embeddings: local sentence-transformers all-MiniLM-L6-v2")

    print(f"\nUploading {len(DOCUMENTS)} documents...\n")

    results = []
    for i, path in enumerate(DOCUMENTS, 1):
        name = os.path.basename(path)
        print(f"[{i}/{len(DOCUMENTS)}] {name[:55]}...")
        if not os.path.exists(path):
            print(f"  SKIP: file not found")
            continue
        try:
            t0 = time.time()
            res = upload_docx(path)
            elapsed = time.time() - t0
            chunks = res.get("chunk_count", "?")
            doc_id = res.get("document_id", "?")[:8]
            print(f"  OK  — {chunks} chunks, id={doc_id}... ({elapsed:.1f}s)")
            results.append({"file": name, "chunks": chunks, "status": "ok"})
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"file": name, "status": "error", "error": str(e)})

    print()
    print("=" * 60)
    print("Summary:")
    ok = sum(1 for r in results if r["status"] == "ok")
    total_chunks = sum(r.get("chunks", 0) for r in results if r["status"] == "ok")
    print(f"  Uploaded:     {ok}/{len(DOCUMENTS)} documents")
    print(f"  Total chunks: {total_chunks} (stored in pgvector + Qdrant)")
    print()
    print("Test RAG query:")
    print('  curl -X POST http://localhost:8001/api/rag/query \\')
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"query": "What is prompt injection?"}\'')
    print("=" * 60)

if __name__ == "__main__":
    main()
