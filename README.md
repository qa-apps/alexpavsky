# Alex Pavsky Portfolio Site

Public source for alexpavsky.com, including the portfolio UI, AI chat backend,
RAG service, article feed, auth, newsletter, and evaluation lab features.

## Security and Operations

- Secrets are loaded from environment variables and are not committed.
- `.env.example` contains placeholders only.
- Runtime databases, chat logs, local exports, and deployment artifacts are
  ignored by Git.
- Production deployment configuration is intentionally kept outside this public
  repository.

## Local Development

```bash
cp .env.example .env
docker compose up -d
python3 chat_server.py
```

Open `index.html` or serve the directory with your preferred local static server.

## RAG Storage

- Qdrant is the only vector and chunk retrieval store.
- PostgreSQL stores document metadata, indexing status, queries, and eval runs.
- Uploads use stable Qdrant point IDs and retry failed upserts three times.
- Create a collection snapshot with `ops/backup_qdrant.sh` before migrations or bulk ingestion.
- Backfill PostgreSQL metadata from an existing Qdrant collection with
  `docker compose exec -T rag-api python /app/backfill_qdrant_metadata.py`.
