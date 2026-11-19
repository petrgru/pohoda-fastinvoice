#!/usr/bin/env bash
# Stream logs: scripts/k3s/logs.sh [backend|worker-parse|worker-light|db|meilisearch] [-f] [--tail N]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

component=${1:-backend}
shift || true
case "$component" in
  backend | worker-parse | worker-light | meilisearch) selector="app=$component" ;;
  db) selector="app=db" ;;
  *) echo "unknown component: $component" >&2 && exit 1 ;;
esac
exec $KUBECTL -n "$K3S_NS" logs -l "$selector" --all-containers --prefix --tail="${LOG_TAIL:-100}" "$@"
