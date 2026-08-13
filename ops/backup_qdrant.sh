#!/usr/bin/env bash
set -euo pipefail

qdrant_url="${QDRANT_URL:-http://127.0.0.1:6333}"
collection="${QDRANT_COLLECTION:-documents}"
backup_dir="${1:-/Users/alexp/LocalApps/CodexTechnicalArtifacts/qdrant-backups/alexpavsky}"

mkdir -p "$backup_dir"
response="$(curl -fsS -X POST "$qdrant_url/collections/$collection/snapshots")"
snapshot_name="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])' <<<"$response")"
snapshot_path="$backup_dir/$snapshot_name"

curl -fsS "$qdrant_url/collections/$collection/snapshots/$snapshot_name" -o "$snapshot_path"
test -s "$snapshot_path"

shasum -a 256 "$snapshot_path" >"$snapshot_path.sha256"
printf 'Qdrant snapshot: %s\nChecksum: %s.sha256\n' "$snapshot_path" "$snapshot_path"
