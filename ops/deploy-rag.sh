#!/usr/bin/env bash
#
# deploy-rag.sh — Deploy the RAG API stack (Postgres + pgvector + Qdrant + FastAPI)
# to the alexpavsky VPS via docker compose, then upload the seed documents.
#
# Usage:
#   ./ops/deploy-rag.sh
#
# Requires:
#   - SSH access to VPS as user "deploy" with sudo
#   - Docker + Docker Compose installed on the VPS (script will install if missing)
#   - .env file in repo root with DB_PASSWORD, GROQ_API_KEY, OPENROUTER_API_KEY, etc.
#   - ops/alexpavsky.com.nginx.conf updated to proxy /api/rag/* and /api/eval/*
#
set -euo pipefail

VPS="alexpavsky-prod"
REMOTE_DIR="/home/deploy/alexpavsky-rag"
REMOTE_ENV="/home/deploy/alexpavsky-rag.env"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found in repo root" >&2
    exit 1
fi

# Load local .env so we can forward keys to the VPS env file
set -a
source .env
set +a

echo "=== Step 1: Ensure Docker on VPS ==="
ssh "$VPS" bash -s <<'EOF'
if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker deploy
fi
if ! docker compose version >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y docker-compose-plugin
fi
echo "Docker $(docker --version)"
echo "Compose $(docker compose version | head -1)"
EOF

echo ""
echo "=== Step 2: Sync RAG code to VPS ==="
ssh "$VPS" "mkdir -p $REMOTE_DIR"
# NOTE: no trailing slashes on db/rag/ops — we want the directories preserved,
# not their contents flattened into the remote root.
rsync -rlptz --delete -e ssh \
    docker-compose.yml \
    db rag ops \
    "$VPS:$REMOTE_DIR/" \
    --exclude='__pycache__' --exclude='.DS_Store'

echo ""
echo "=== Step 3: Write production env file ==="
env_tmp="$(mktemp)"
cat > "$env_tmp" <<ENV
# RAG stack env (production)
DB_PASSWORD=${DB_PASSWORD:?missing in local .env}
PGADMIN_EMAIL=${PGADMIN_EMAIL:-alex@alexpavsky.com}
PGADMIN_PASSWORD=${PGADMIN_PASSWORD:?missing in local .env}

# LLM providers — used by rag-api for generation. The rotating call_llm()
# in rag/main.py walks this list in order and switches on rate limit / error.
OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
GROQ_API_KEY=${GROQ_API_KEY:-}
CEREBRAS_API_KEY=${CEREBRAS_API_KEY:-}
SAMBANOVA_API_KEY=${SAMBANOVA_API_KEY:-}
MISTRAL_API_KEY=${MISTRAL_API_KEY:-}
HF_TOKEN=${HF_TOKEN:-}
ENV
scp "$env_tmp" "$VPS:$REMOTE_ENV"
rm -f "$env_tmp"

echo ""
echo "=== Step 4: Start docker compose ==="
ssh "$VPS" bash -s <<EOF
cd $REMOTE_DIR
ln -sf $REMOTE_ENV .env
docker compose pull
docker compose up -d
sleep 10
docker compose ps
EOF

echo ""
echo "=== Step 5: Health check ==="
ssh "$VPS" "curl -sf http://localhost:8001/api/health || echo 'FAIL: RAG API not responding'"

echo ""
echo "=== Step 6: Nginx — proxy /api/rag/* and /api/eval/* to port 8001 ==="
echo "If nginx isn't already proxying, edit ops/alexpavsky.com.nginx.conf"
echo "and run ./ops/deploy-backend.sh to push it."

echo ""
echo "Deployment complete."
echo ""
echo "Next steps (manual):"
echo "  1. Confirm DNS for api.alexpavsky.com → \$(VPS IP)"
echo "  2. Configure nginx to proxy api.alexpavsky.com → http://localhost:8001"
echo "  3. Issue cert: ssh $VPS sudo certbot --nginx -d api.alexpavsky.com"
echo "  4. Run seed_documents.py against production:"
echo "     RAG_API_URL=https://api.alexpavsky.com python3 db/seed_documents.py"
