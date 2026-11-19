#!/usr/bin/env bash
# Undo the last rollout: scripts/k3s/rollback.sh [backend|worker-parse|worker-light]
# (default: all three; the db StatefulSet is rolled out rarely - pass it by name)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

targets=("$@")
[[ ${#targets[@]} -eq 0 ]] && targets=(backend worker-parse worker-light)
for t in "${targets[@]}"; do
  $KUBECTL -n "$K3S_NS" rollout undo "deployment/$t"
  $KUBECTL -n "$K3S_NS" rollout status "deployment/$t" --timeout=180s
done

# caveat: an old image tag + a migrated schema must stay compatible; alembic
# does NOT downgrade automatically - check `alembic history` before rolling
# back across a migration if it was data-destructive
echo "rolled back: ${targets[*]}"
