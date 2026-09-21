#!/bin/bash
set -euo pipefail

VPS="alexpavsky-prod"
REMOTE_DIR="/var/www/alexpavsky.com/html"
REMOTE_ENV="/home/deploy/alexpavsky-backend.env"
REMOTE_NGINX_SOURCE="/home/deploy/alexpavsky.com.nginx.conf"

# --- GitHub and the VPS must never diverge -----------------------------------
# This script rsyncs a working directory, not a commit. That is how the repo
# fell months behind production: edits were deployed here and never pushed, so
# 15 functions lived only on the server and the frontend kept sending a stale
# auth header nobody could see in the repository.
#
# So: refuse to deploy anything GitHub does not already have.
# Override for a genuine emergency with ALLOW_DIRTY_DEPLOY=1, which is loud.
if [ -d .git ] && [ "${ALLOW_DIRTY_DEPLOY:-0}" != "1" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "DEPLOY BLOCKED: uncommitted changes in the working tree." >&2
    echo "Commit and push them first — the VPS must not run code GitHub lacks." >&2
    git status --short >&2
    exit 1
  fi

  branch="$(git rev-parse --abbrev-ref HEAD)"
  if ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    echo "DEPLOY BLOCKED: '$branch' has no upstream, so nothing was pushed." >&2
    echo "Run: git push -u origin $branch" >&2
    exit 1
  fi

  git fetch -q origin
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]; then
    echo "DEPLOY BLOCKED: HEAD differs from origin/$branch." >&2
    echo "  local:  $(git rev-parse --short HEAD)" >&2
    echo "  origin: $(git rev-parse --short '@{u}')" >&2
    echo "Push (or pull) so the deployed commit exists on GitHub." >&2
    exit 1
  fi

  echo "Deploying $(git rev-parse --short HEAD) from $branch — present on GitHub."
elif [ "${ALLOW_DIRTY_DEPLOY:-0}" = "1" ]; then
  echo "WARNING: ALLOW_DIRTY_DEPLOY=1 — deploying code that GitHub may not have." >&2
  echo "WARNING: push it immediately afterwards, or the repo drifts again." >&2
fi

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

rsync -rlptz --delete --no-owner --no-group -e "ssh" ./ "$VPS:$REMOTE_DIR/" \
  --exclude='.git' --exclude='.env' --exclude='deploy.sh' --exclude='deploy-all.sh' \
  --exclude='node_modules' --exclude='chat.db' --exclude='.DS_Store' --exclude='maintenance.flag'

env_tmp="$(mktemp)"
write_env() {
  printf '%s=%q\n' "$1" "$2" >> "$env_tmp"
}
write_env CHAT_PORT "8000"
write_env DATA_DIR "/home/deploy/alexpavsky-data"
write_env DATABASE_URL "${DATABASE_URL:-}"
write_env SQLITE_BACKUP_INTERVAL_SECONDS "${SQLITE_BACKUP_INTERVAL_SECONDS:-300}"
write_env GROQ_API_KEY "${GROQ_API_KEY:-}"
write_env OPENROUTER_API_KEY "${OPENROUTER_API_KEY:-}"
write_env GEMINI_API_KEY "${GEMINI_API_KEY:-}"
write_env HF_TOKEN "${HF_TOKEN:-}"
write_env MAINTENANCE_KEY "${MAINTENANCE_KEY:-}"
write_env ADMIN_EMAIL "${ADMIN_EMAIL:-alex@alexpavsky.com}"
write_env ADMIN_LOGIN_EMAILS "${ADMIN_LOGIN_EMAILS:-alex@alexpavsky.com,alex.pavsky@gmail.com}"
write_env SMTP_HOST "${SMTP_HOST:-}"
write_env SMTP_PORT "${SMTP_PORT:-465}"
write_env SMTP_USERNAME "${SMTP_USERNAME:-${SMTP_USER:-}}"
write_env SMTP_PASSWORD "${SMTP_PASSWORD:-${SMTP_PASS:-}}"
write_env SMTP_FROM_EMAIL "${SMTP_FROM_EMAIL:-${NEWSLETTER_FROM_EMAIL:-${SMTP_FROM:-}}}"
write_env SMTP_FROM_NAME "${SMTP_FROM_NAME:-${NEWSLETTER_FROM_NAME:-Alex Pavsky}}"
write_env SMTP_USE_SSL "${SMTP_USE_SSL:-1}"
write_env SMTP_USE_STARTTLS "${SMTP_USE_STARTTLS:-0}"
write_env BUTTONDOWN_USERNAME "${BUTTONDOWN_USERNAME:-alexp}"
write_env BUTTONDOWN_SUBSCRIBE_ENDPOINT "${BUTTONDOWN_SUBSCRIBE_ENDPOINT:-}"
write_env BUTTONDOWN_API_KEY "${BUTTONDOWN_API_KEY:-}"
write_env BUTTONDOWN_API_BASE_URL "${BUTTONDOWN_API_BASE_URL:-}"
write_env NEWSLETTER_TIMEZONE "${NEWSLETTER_TIMEZONE:-America/New_York}"
write_env NEWSLETTER_HOUR "${NEWSLETTER_HOUR:-9}"
write_env NEWSLETTER_MINUTE "${NEWSLETTER_MINUTE:-0}"
write_env NEWSLETTER_DIGEST_DAYS "${NEWSLETTER_DIGEST_DAYS:-2}"
write_env NEWSLETTER_MAX_ARTICLES "${NEWSLETTER_MAX_ARTICLES:-3}"
scp "$env_tmp" "$VPS:$REMOTE_ENV" >/dev/null
rm -f "$env_tmp"

ssh "$VPS" "cat > $REMOTE_NGINX_SOURCE" < ./ops/alexpavsky.com.nginx.conf
ssh "$VPS" "sudo tee /etc/nginx/sites-available/alexpavsky.com >/dev/null < $REMOTE_NGINX_SOURCE && sudo ln -sf /etc/nginx/sites-available/alexpavsky.com /etc/nginx/sites-enabled/alexpavsky.com && sudo nginx -t && sudo systemctl reload nginx"

ssh "$VPS" "set -a && source $REMOTE_ENV && set +a && pm2 delete alexpavsky-api >/dev/null 2>&1 || true && pm2 start /usr/bin/python3 --name alexpavsky-api -- $REMOTE_DIR/chat_server.py && pm2 save"

echo "Backend deployed and nginx updated."
