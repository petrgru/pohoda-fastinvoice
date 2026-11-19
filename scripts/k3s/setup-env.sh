#!/usr/bin/env bash
# First-run setup: creates k8s/k3s.env with generated secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET=${K3S_ENV:-"$ROOT/k8s/k3s.env"}

if [[ -e $TARGET ]]; then
  echo "$TARGET already exists - edit it, this script will not overwrite" >&2
  exit 1
fi

cp "$ROOT/k8s/env.example" "$TARGET"
gen() { openssl rand -hex 24; }

sed -i -E \
  -e "s/^SECRET_KEY=.*/SECRET_KEY=$(gen)/" \
  -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(gen)/" \
  -e "s/^MEILISEARCH_MASTER_KEY=.*/MEILISEARCH_MASTER_KEY=$(gen)/" \
  -e "s/^FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=$(gen)/" \
  "$TARGET"

echo "wrote $TARGET (gitignored) - review DOMAIN, SMTP_* and LLM_* before deploying"
