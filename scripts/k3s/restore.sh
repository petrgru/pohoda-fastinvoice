#!/usr/bin/env bash
# Restore a backup: scripts/k3s/restore.sh k8s/backups/fastinvoice-<stamp>.sql.gz
# DANGER: drops and recreates the app database; running pods keep their old
# connections - restart the workloads afterwards (deploy.sh --skip-build).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

file=${1:?usage: restore.sh <backup.sql.gz>}
[[ -f $file ]] || { echo "no such file: $file" >&2 && exit 1; }

# no other session may hold the database while it is recreated
$KUBECTL -n "$K3S_NS" scale deployment/backend --replicas=0
$KUBECTL -n "$K3S_NS" scale deployment/worker-parse --replicas=0
$KUBECTL -n "$K3S_NS" scale deployment/worker-light --replicas=0

gunzip -c "$file" |
  $KUBECTL -n "$K3S_NS" exec -i statefulset/db -- \
    psql -q -U postgres -c "DROP DATABASE IF EXISTS app;" -v ON_ERROR_STOP=1
gunzip -c "$file" |
  $KUBECTL -n "$K3S_NS" exec -i statefulset/db -- \
    psql -q -U postgres -v ON_ERROR_STOP=1

# alembic_version came with the dump - scale back, no re-migration needed
$KUBECTL -n "$K3S_NS" scale deployment/backend --replicas=1
$KUBECTL -n "$K3S_NS" scale deployment/worker-parse --replicas=1
$KUBECTL -n "$K3S_NS" scale deployment/worker-light --replicas=1
$KUBECTL -n "$K3S_NS" rollout status deployment/backend --timeout=300s
echo "restored from $file"
